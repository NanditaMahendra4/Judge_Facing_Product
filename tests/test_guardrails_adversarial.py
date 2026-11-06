"""Adversarial tests for benchbrief.guardrails. Run: python -m unittest discover -s tests -v"""
import unittest

from benchbrief import guardrails, ingest
from benchbrief.schema import Brief, Claim, EvidenceItem, Flag

TEXT = (
    "The incident was on March 2, 2026 at my apartment. "
    "The landlord shut off the heat for three days. "
    "I did not hit him. "
    "Armstrong Realty owns the building. "
    "I ask the court to dismiss the case. "
    "Yes."
)


def brief(**kw) -> Brief:
    base = dict(filer="X", filing_type="Motion", what_happened=[], when=[], where=[], who=[],
                asking_for=[], evidence=[], flags=[])
    base.update(kw)
    return Brief(**base)


class Base(unittest.TestCase):
    def setUp(self):
        self.doc = ingest.from_text("t", TEXT)
        self.ids = [s.id for s in self.doc.sentences]

    def run_claim(self, text, quote, cites):
        out, rep = guardrails.enforce(brief(what_happened=[Claim(text=text, cites=cites, quote=quote)]), self.doc)
        return out.what_happened, rep

    def kept(self, text, quote, cites):
        got, rep = self.run_claim(text, quote, cites)
        self.assertEqual(len(got), 1, rep.dropped)

    def dropped(self, text, quote, cites, reason=None):
        got, rep = self.run_claim(text, quote, cites)
        self.assertEqual(got, [], "should have been dropped")
        if reason:
            self.assertIn(reason, rep.dropped[0]["reason"])


class TestSetup(Base):
    def test_sentence_ids(self):
        self.assertEqual(self.ids[:3], ["p1.s1", "p1.s2", "p1.s3"])
        self.assertEqual(len(self.ids), 6)


class QuoteTests(Base):
    def test_real_quote_kept(self):
        self.kept("Filer states the heat was shut off.", "shut off the heat for three days", ["p1.s2"])

    def test_trivial_quotes_dropped(self):
        for q in ("I", "did not", "hit him", "not hit"):
            self.dropped("Filer states he was violent.", q, ["p1.s3"], "too short")
        for q in ("the", "for three", "off the"):
            self.dropped("Filer states heat.", q, ["p1.s2"], "too short")

    def test_trivial_quote_cannot_support_paraphrase(self):
        self.dropped("Filer states she hit him.", "the", ["p1.s2"])

    def test_whole_short_sentence_allowed(self):
        self.kept("Filer answers yes.", "Yes.", ["p1.s6"])
        self.kept("Filer answers yes.", "yes", ["p1.s6"])

    def test_substring_of_short_sentence_still_needs_substance(self):
        doc = ingest.from_text("t", "I agree. Next.")
        out, _ = guardrails.enforce(brief(who=[Claim(text="Filer agrees.", cites=["p1.s1"], quote="agree")]), doc)
        self.assertEqual(len(out.who), 1)  # sentence itself is trivially short

    def test_short_fragment_of_long_sentence_dropped(self):
        self.dropped("Filer states a date.", "March 2", ["p1.s1"], "too short")

    def test_three_word_quote_ok(self):
        self.kept("Filer states a date.", "March 2, 2026", ["p1.s1"])

    def test_empty_quote_dropped(self):
        self.dropped("Filer states a date.", "   ", ["p1.s1"])
        self.dropped("Filer states a date.", "...", ["p1.s1"])

    def test_quote_spanning_sentence_boundary(self):
        self.kept("Filer states heat and denial.", "my apartment. The landlord shut off the heat", ["p1.s1", "p1.s2"])
        self.kept("Filer states heat and denial.", "my apartment The landlord shut off the heat", ["p1.s1", "p1.s2"])

    def test_boundary_quote_with_cites_out_of_order(self):
        self.kept("Filer states heat and denial.", "my apartment. The landlord shut off", ["p1.s2", "p1.s1"])

    def test_cannot_stitch_non_adjacent_sentences(self):
        self.dropped("Filer states x.", "my apartment I did not hit him", ["p1.s1", "p1.s3"], "not found")

    def test_boundary_quote_without_citing_both(self):
        self.dropped("Filer states x.", "my apartment. The landlord shut off the heat", ["p1.s1"], "not found")

    def test_partial_word_quote_dropped(self):
        self.dropped("Filer states x.", "ndlord shut off the heat", ["p1.s2"], "not found")

    def test_reordered_or_altered_quote_dropped(self):
        self.dropped("x", "I did hit him", ["p1.s3"], "not found")
        self.dropped("x", "heat for four days", ["p1.s2"], "not found")

    def test_negation_dropped_is_not_found(self):
        self.dropped("Filer states he hit him.", "I did hit him", ["p1.s3"])

    def test_unknown_and_missing_cites(self):
        self.dropped("x", "March 2, 2026", ["p1.s1", "p9.s9"], "unknown sentence")
        self.dropped("x", "March 2, 2026", [], "no citation")

    def test_duplicate_cites_ok(self):
        self.kept("Filer states a date.", "March 2, 2026", ["p1.s1", "p1.s1"])

    def test_empty_text_dropped(self):
        self.dropped("   ", "March 2, 2026", ["p1.s1"], "empty")


class UnicodeTests(unittest.TestCase):
    def check(self, source, quote, expect=True):
        doc = ingest.from_text("t", source)
        r = guardrails._check_cites(doc, ["p1.s1"], quote)
        self.assertEqual(r is None, expect, r)

    def test_curly_quotes_both_ways(self):
        self.check("He said “don’t touch my things” to me.", "\"don't touch my things\"")
        self.check("He said \"don't touch my things\" to me.", "“don’t touch my things”")

    def test_nbsp_in_source_or_quote(self):
        self.check("He took the car keys from me.", "took the car keys")
        self.check("He took the car keys from me.", "took the car keys")

    def test_zero_width_in_source_or_quote(self):
        self.check("He took the car ke​ys from me.", "took the car keys")
        self.check("He took the car keys from me.", "took the car ke‍ys﻿")
        self.check("He took the car keys from me.", "took⁠ the car keys­")

    def test_fabricated_quote_with_tricks_still_fails(self):
        self.check("He took the car keys from me.", "took the car​ keys and hit me", False)
        self.check("He took the car keys from me.", "hit me​ with a bat", False)

    def test_dashes_and_hyphens(self):
        self.check("It was a self‑serving, non–binding offer.", "self-serving non-binding offer")

    def test_fullwidth_forms(self):
        self.check("The rent is ＄850 per month.", "rent is $850 per month")

    def test_homoglyph_in_quote_cannot_fake_match(self):
        # Cyrillic 'a' in "mаrch"
        self.check("The incident was on March 2, 2026.", "was on mаrch 2, 2026", False)

    def test_dates_with_slashes_survive(self):
        self.check("It happened on 3/2/2026 at noon.", "happened on 3/2/2026 at noon")
        self.check("It happened on 3/2/2026 at noon.", "happened on 3/12/2026 at noon", False)


class NeutralTests(Base):
    BAD = [
        "Filer alleges without evidence that the heat was off.",
        "Filer purports that the heat was off.",
        "Filer conveniently remembers the date.",
        "Filer fails to give a date.",
        "Filer failed to attach a lease.",
        "Filer admits the date.",
        "Filer apparently was home.",
        "It appears the heat was off.",
        "Filer allegedly lost heat.",
        "Filer gives an inconsistent story.",
        "Filer is a victim of the landlord.",
        "The landlord is a perpetrator.",
        "Landlord is an abuser.",
        "The court should dismiss the case.",
        "The court must grant the motion.",
        "Filer deserves relief.",
        "The motion will likely be granted.",
        "Filer will win.",
        "Filer seems to be home.",
        "Filer is credible.",
        "Filer is not CREDIBLE.",
        "Filer's claim is strong.",
        "Filer's claim is weakly supported.",
        "The filer's account is self-serving.",
        "Filer insists the heat was off.",
        "The statement is obviously false.",
        "Filer states this is not​ credible.",
        "Filer is crеdible.",  # Cyrillic e
        "Filer is cred­ible.",  # soft hyphen
        "Filer is ｃredible.",  # fullwidth c
    ]

    def test_bad_wording_dropped(self):
        for t in self.BAD:
            with self.subTest(t=t):
                self.dropped(t, "shut off the heat for three days", ["p1.s2"], "non-neutral")

    GOOD = [
        "Filer alleges the heat was shut off.",
        "Filer states the heat was shut off for three days.",
        "Filer says Armstrong Realty owns the building.",
        "Filer asks the court to dismiss the case.",
        "Filer states the strongroom was locked.",
        "Filer describes the incident on March 2, 2026.",
        "Filer states the landlord mustered no reply.",
        "Filer refers to a shoulder injury.",
        "Filer notes the landlord shut off the heat.",
        "Filer states the weekend was cold.",
        "Filer states the failure rate was not given.",
    ]

    def test_neutral_wording_kept(self):
        for t in self.GOOD:
            with self.subTest(t=t):
                self.kept(t, "shut off the heat for three days", ["p1.s2"])

    def test_word_boundaries(self):
        self.assertIsNone(guardrails._neutral_problem("Mr. Armstrong and Ms. Stronghold"))
        self.assertIsNone(guardrails._neutral_problem("The shoulder was sore on the weekend."))
        self.assertIsNotNone(guardrails._neutral_problem("a strong claim"))
        self.assertIsNotNone(guardrails._neutral_problem("a STRONG claim"))

    def test_alleges_is_neutral_allegedly_is_not(self):
        self.assertIsNone(guardrails._neutral_problem("Filer alleges the heat was off."))
        self.assertIsNotNone(guardrails._neutral_problem("Filer allegedly lost the heat."))

    def test_filers_own_words_allowed(self):
        doc = ingest.from_text("t", "The court should dismiss this because he is a liar and a victim of fraud.")
        q = "The court should dismiss this because he is a liar and a victim of fraud"
        out, _ = guardrails.enforce(brief(asking_for=[Claim(
            text="Filer states the court should dismiss because he is a liar and a victim of fraud.",
            cites=["p1.s1"], quote=q)]), doc)
        self.assertEqual(len(out.asking_for), 1)

    def test_filers_own_words_do_not_excuse_other_words(self):
        doc = ingest.from_text("t", "The court should dismiss this case because of the landlord's conduct.")
        q = "The court should dismiss this case because of the landlord's conduct"
        out, rep = guardrails.enforce(brief(asking_for=[Claim(
            text="Filer states the court should dismiss and the claim is credible.",
            cites=["p1.s1"], quote=q)]), doc)
        self.assertEqual(out.asking_for, [])
        self.assertIn("credible", rep.dropped[0]["reason"])

    def test_word_in_quote_only_as_substring_does_not_excuse(self):
        doc = ingest.from_text("t", "The tenant is a victimless bystander in this matter.")
        q = "The tenant is a victimless bystander in this matter"
        out, _ = guardrails.enforce(brief(who=[Claim(text="Filer says the tenant is a victim.", cites=["p1.s1"], quote=q)]), doc)
        self.assertEqual(out.who, [])

    def test_first_allowed_second_not(self):
        doc = ingest.from_text("t", "I think he is clearly lying about the rent.")
        q = "he is clearly lying about the rent"
        out, _ = guardrails.enforce(brief(what_happened=[Claim(
            text="Filer states he is clearly lying and is obviously dishonest.", cites=["p1.s1"], quote=q)]), doc)
        self.assertEqual(out.what_happened, [])

    def test_flags_linted(self):
        out, rep = guardrails.enforce(brief(flags=[
            Flag(kind="conflict", text="Filer must explain the different dates.", cites=["p1.s1"]),
            Flag(kind="conflict", text="Different dates given for the incident (March 2, p. 1).", cites=["p1.s1"]),
        ]), self.doc)
        self.assertEqual(len(out.flags), 1)
        self.assertEqual(rep.kept, 1)


class EvidenceAndFieldTests(Base):
    def ev(self, **kw):
        base = dict(label="Exhibit A", filed_by="Filer", claimed_to_show="Filer says it shows the heat bill.",
                    cites=["p1.s2"], quote="shut off the heat for three days")
        base.update(kw)
        return EvidenceItem(**base)

    def run_ev(self, **kw):
        return guardrails.enforce(brief(evidence=[self.ev(**kw)]), self.doc)

    def test_clean_evidence_kept(self):
        out, rep = self.run_ev()
        self.assertEqual(len(out.evidence), 1)
        self.assertEqual(rep.kept, 1)

    def test_label_linted(self):
        out, rep = self.run_ev(label="Proof of the landlord's fabricated notice")
        self.assertEqual(out.evidence, [])
        self.assertEqual(rep.dropped[0]["section"], "evidence")
        self.assertIn("non-neutral", rep.dropped[0]["reason"])

    def test_label_with_filers_own_word_allowed(self):
        out, _ = self.run_ev(label="Victim statement", quote="The victim statement shows the heat was off",
                             cites=["p1.s2"])
        # quote isn't in the source, so dropped for that reason instead
        self.assertEqual(out.evidence, [])
        doc = ingest.from_text("t", "I attach my victim statement from the police.")
        e = self.ev(label="Victim statement", cites=["p1.s1"], quote="my victim statement from the police")
        out, _ = guardrails.enforce(brief(evidence=[e]), doc)
        self.assertEqual(len(out.evidence), 1)

    def test_filed_by_linted(self):
        out, _ = self.run_ev(filed_by="The abuser")
        self.assertEqual(out.evidence, [])

    def test_claimed_to_show_linted(self):
        out, _ = self.run_ev(claimed_to_show="Filer says it clearly proves the heat was off.")
        self.assertEqual(out.evidence, [])

    def test_evidence_fabricated_quote(self):
        out, _ = self.run_ev(quote="photos of damage to the unit")
        self.assertEqual(out.evidence, [])

    def test_evidence_trivial_quote(self):
        out, _ = self.run_ev(quote="the")
        self.assertEqual(out.evidence, [])

    def test_non_neutral_filing_type_replaced(self):
        out, rep = guardrails.enforce(brief(filing_type="Meritless Motion, Obviously Denied"), self.doc)
        self.assertEqual(out.filing_type, "Not stated")
        self.assertEqual(rep.dropped[0]["section"], "filing_type")
        self.assertEqual(rep.dropped[0]["text"], "Meritless Motion, Obviously Denied")
        self.assertIn("non-neutral", rep.dropped[0]["reason"])
        self.assertEqual(rep.kept, 0)

    def test_neutral_filing_type_untouched(self):
        for t in ("Motion to Continue", "Answer to Complaint", "Not stated", "Petition for Protection from Abuse",
                  "Emergency Motion for Strong Armstrong Order".replace("Strong ", "")):
            out, rep = guardrails.enforce(brief(filing_type=t), self.doc)
            self.assertEqual(out.filing_type, t)
            self.assertEqual(rep.dropped, [])

    def test_hidden_char_filing_type_replaced(self):
        out, _ = guardrails.enforce(brief(filing_type="Motion (not cred​ible)"), self.doc)
        self.assertEqual(out.filing_type, "Not stated")

    def test_filer_not_altered(self):
        out, _ = guardrails.enforce(brief(filer="Pat Strong"), self.doc)
        self.assertEqual(out.filer, "Pat Strong")


class ReportCompatTests(Base):
    def test_report_shape(self):
        b = brief(
            when=[Claim(text="Filer states a date.", cites=["p1.s1"], quote="March 2, 2026")],
            who=[Claim(text="x", cites=["p1.s1"], quote="nope nope nope")],
        )
        out, rep = guardrails.enforce(b, self.doc)
        self.assertIsInstance(rep, guardrails.GuardReport)
        self.assertEqual(rep.kept, 1)
        self.assertEqual(len(rep.dropped), 1)
        self.assertEqual(set(rep.dropped[0]), {"section", "text", "reason"})
        self.assertEqual(rep.dropped[0]["section"], "who")
        self.assertIsInstance(out, Brief)

    def test_private_helpers_signatures(self):
        self.assertIsNone(guardrails._check_cites(self.doc, ["p1.s1"], None))
        self.assertIsNone(guardrails._neutral_problem_outside_quote("Filer states a date.", "March 2, 2026"))


if __name__ == "__main__":
    unittest.main()


class NegationDropTests(unittest.TestCase):
    def test_quote_dropping_negation_rejected(self):
        from benchbrief import guardrails, ingest
        doc = ingest.from_text("t", "I did not hit him at the bus stop that night.")
        self.assertIn("negation", guardrails._check_cites(doc, ["p1.s1"], "hit him at the bus stop"))
        self.assertIsNone(guardrails._check_cites(doc, ["p1.s1"], "did not hit him at the bus stop"))

    def test_unnegated_occurrence_allowed(self):
        from benchbrief import guardrails, ingest
        doc = ingest.from_text("t", "He says I hit him at the bus stop, but I did not hit him at the bus stop.")
        self.assertIsNone(guardrails._check_cites(doc, ["p1.s1"], "hit him at the bus stop"))
