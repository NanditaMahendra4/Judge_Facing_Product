"""Extended tests for the deterministic checks. Run: python -m unittest discover -s tests -v"""
import unittest

from benchbrief import checks, ingest
from benchbrief.schema import Brief, Flag


def doc(text):
    return ingest.from_text("t", text)


def brief(**kw):
    base = dict(filer="X", filing_type="Motion", what_happened=[], when=[], where=[], who=[],
                asking_for=[], evidence=[], flags=[])
    base.update(kw)
    return Brief(**base)


class DateParsingTests(unittest.TestCase):
    def test_formats_normalize_to_same_key(self):
        want = (2026, 3, 2)
        for raw in ["March 2, 2026", "March 2nd, 2026", "Mar. 2, 2026", "Mar 2, 2026", "2 March 2026",
                    "2nd of March 2026", "2026-03-02", "3/2/2026", "3/2/26", "March 2, '26"]:
            self.assertEqual(checks._parse_date(raw), want, raw)

    def test_yearless(self):
        self.assertEqual(checks._parse_date("March 2nd"), (None, 3, 2))
        self.assertEqual(checks._parse_date("Sept. 5"), (None, 9, 5))

    def test_invalid_dates_not_found(self):
        dates, unclear = checks._find_dates("It was on February 30, 2026.")
        self.assertEqual(dates, [])
        self.assertTrue(unclear)

    def test_may_modal_not_a_date(self):
        dates, _ = checks._find_dates("May I ask the court to help? I may 5 times.")
        self.assertEqual(dates, [])


class DateConflictTests(unittest.TestCase):
    def conflicts(self, text):
        return checks.date_conflicts(doc(text))

    def test_cross_format_same_date_no_conflict(self):
        for other in ["Mar. 2, 2026", "2 March 2026", "2026-03-02", "March 2nd", "3/2/26"]:
            self.assertEqual(self.conflicts(f"The incident was on March 2, 2026. The incident was on {other}."), [], other)

    def test_conflict_across_new_formats(self):
        flags = self.conflicts("The incident was on 2 March 2026.\n--- Page 2 ---\nThe incident was on 2026-03-12.")
        self.assertEqual(len(flags), 1)
        self.assertIn("2 March 2026, p. 1", flags[0].text)
        self.assertIn("2026-03-12, p. 2", flags[0].text)
        self.assertEqual(flags[0].cites, ["p1.s1", "p2.s1"])

    def test_two_digit_year_conflict(self):
        flags = self.conflicts("The incident was on 3/2/26. The incident was on 3/12/26.")
        self.assertEqual(len(flags), 1)

    def test_yearless_matches_dated_same_day(self):
        self.assertEqual(self.conflicts("The incident was on March 2, 2026. The incident was March 2nd."), [])

    def test_different_year_conflicts(self):
        self.assertEqual(len(self.conflicts("The incident was on March 2, 2026. The incident was on March 2, 2025.")), 1)

    def test_hearing_vs_incident_not_compared(self):
        self.assertEqual(self.conflicts("The incident was on March 2, 2026. The hearing is on March 20, 2026."), [])

    def test_hearing_dates_do_conflict_with_each_other(self):
        flags = self.conflicts("The hearing is on March 20, 2026. The hearing was on April 4, 2026.")
        self.assertEqual(len(flags), 1)
        self.assertIn("for the hearing", flags[0].text)

    def test_same_date_repeated_cites_all(self):
        flags = self.conflicts(
            "The incident was on March 2, 2026. The incident was on March 12, 2026.\n"
            "--- Page 2 ---\nThe incident was on March 2, 2026."
        )
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].cites, ["p1.s1", "p1.s2", "p2.s1"])
        self.assertIn("March 2, 2026, pp. 1, 2", flags[0].text)

    def test_every_cite_has_page_ref(self):
        flags = self.conflicts("The incident was on March 2, 2026.\n--- Page 2 ---\nThe incident occurred on March 12, 2026.\n--- Page 3 ---\nThe incident was on April 1, 2026.")
        self.assertEqual(len(flags), 1)
        for n in ("p. 1", "p. 2", "p. 3"):
            self.assertIn(n, flags[0].text)
        self.assertEqual(len(flags[0].cites), 3)

    def test_different_incident_not_merged(self):
        for phrase in ["a different incident", "another incident", "a separate incident", "an earlier incident"]:
            text = f"The incident was on March 2, 2026. There was {phrase} on March 12, 2026."
            self.assertEqual(self.conflicts(text), [], phrase)

    def test_ordinal_events_keyed_separately(self):
        text = "The first incident was on March 2, 2026. The second incident was on March 12, 2026."
        self.assertEqual(self.conflicts(text), [])

    def test_same_ordinal_with_two_dates_conflicts(self):
        text = "The second incident was on March 12, 2026.\n--- Page 2 ---\nThe second incident was on April 12, 2026."
        flags = self.conflicts(text)
        self.assertEqual(len(flags), 1)
        self.assertIn("for the second incident", flags[0].text)
        self.assertIn("p. 1", flags[0].text)
        self.assertIn("p. 2", flags[0].text)

    def test_ordinal_not_mixed_with_plain(self):
        text = "The incident was on March 2, 2026. The second incident was on March 12, 2026."
        self.assertEqual(self.conflicts(text), [])

    def test_plural_events_skipped(self):
        self.assertEqual(self.conflicts("The incident was on March 2, 2026. The incidents continued on March 12, 2026."), [])

    def test_again_sentence_skipped(self):
        self.assertEqual(self.conflicts("The incident was on March 2, 2026. The incident happened again on March 12, 2026."), [])

    def test_multiple_dates_in_sentence_skipped(self):
        self.assertEqual(self.conflicts("The incident was on March 2, 2026. The incident ran from March 3, 2026 to March 5, 2026."), [])

    def test_hedged_or_rescheduled_skipped(self):
        self.assertEqual(self.conflicts("The hearing is on March 20, 2026. The hearing was moved to April 3, 2026."), [])
        self.assertEqual(self.conflicts("The incident was on March 2, 2026. The incident was on or about March 3, 2026."), [])

    def test_without_notice_is_not_notice_event(self):
        text = ("The incident was on March 2, 2026 when he came in without notice. "
                "I got a notice on March 5, 2026. The incident was on March 2nd.")
        self.assertEqual(self.conflicts(text), [])

    def test_nearest_noun_gets_the_date(self):
        # "hearing" is the event dated here, not "incident".
        text = "The incident was on March 2, 2026. The hearing about the incident is on March 20, 2026."
        self.assertEqual(self.conflicts(text), [])

    def test_invalid_date_sentence_ignored(self):
        self.assertEqual(self.conflicts("The incident was on March 2, 2026. The incident was on February 30, 2026."), [])


class UndatedTests(unittest.TestCase):
    def undated(self, text):
        return checks.undated_events(doc(text))

    def test_second_incident_flagged(self):
        flags = self.undated("Then there was a second incident in the hallway.")
        self.assertEqual(len(flags), 1)
        self.assertIn("No date stated for the second incident (p. 1)", flags[0].text)
        self.assertEqual(flags[0].cites, ["p1.s1"])

    def test_second_time_i_asked_not_flagged(self):
        self.assertEqual(self.undated("The second time I asked, he said no."), [])
        self.assertEqual(self.undated("The other time I called nobody answered."), [])

    def test_dated_second_incident_not_flagged_in_any_format(self):
        for d in ["March 12, 2026", "Mar. 12, 2026", "12 March 2026", "2026-03-12", "3/12/26"]:
            self.assertEqual(self.undated(f"The second incident was on {d}."), [], d)

    def test_abbreviated_month_split_by_ingest_not_flagged(self):
        self.assertEqual(self.undated("The second incident was on Mar. 12, 2026."), [])

    def test_month_reference_not_flagged(self):
        self.assertEqual(self.undated("The second incident was in March 2026."), [])

    def test_dated_elsewhere_not_flagged(self):
        text = "There was a second incident at the store. The second incident happened on March 12, 2026."
        self.assertEqual(self.undated(text), [])

    def test_hypothetical_and_request_not_flagged(self):
        self.assertEqual(self.undated("I am afraid of another incident."), [])
        self.assertEqual(self.undated("I ask the court to schedule another hearing."), [])
        self.assertEqual(self.undated("Please set another hearing."), [])

    def test_one_flag_per_sentence(self):
        flags = self.undated("There was a second incident and then a third incident, and yesterday too.")
        self.assertEqual(len(flags), 1)

    def test_relative_time_flagged_with_page(self):
        flags = self.undated("First line.\n--- Page 2 ---\nA few weeks later he texted me.")
        self.assertEqual(len(flags), 1)
        self.assertIn("p. 2", flags[0].text)
        self.assertEqual(flags[0].cites, ["p2.s1"])

    def test_relative_time_with_date_not_flagged(self):
        self.assertEqual(self.undated("Yesterday, March 5, 2026, he came by."), [])


class AmountTests(unittest.TestCase):
    def amounts(self, text):
        return checks.amount_conflicts(doc(text))

    def test_basic_conflict_with_pages(self):
        flags = self.amounts("The landlord says rent is $900.\n--- Page 2 ---\nMy rent is $850 a month.")
        self.assertEqual(len(flags), 1)
        self.assertIn("p. 1", flags[0].text)
        self.assertIn("p. 2", flags[0].text)
        self.assertEqual(flags[0].cites, ["p1.s1", "p2.s1"])

    def test_multiplier_and_period_hints_skipped(self):
        for other in [
            "The rent for two months was $1,700.",
            "Total rent owed is $1,700.",
            "The rent is $425 each week.",
            "The rent is $200 a week.",
            "The rent is $1,700 for 2 months.",
            "The rent is $10,200 per year.",
            "The rent is $850 per week.",
            "The rent is $1,700 combined.",
            "The rent was $900 originally.",
            "I paid $400 of the rent.",
            "The rent increased by $50 to a new figure.",
        ]:
            self.assertEqual(self.amounts(f"My rent is $850 a month. {other}"), [], other)

    def test_monthly_phrases_remain_comparable(self):
        for other in ["Rent is $900 per month.", "Rent is $900 monthly.", "Rent is $900 each month.", "Rent is $900 every month."]:
            self.assertEqual(len(self.amounts(f"My rent is $850 a month. {other}")), 1, other)

    def test_same_amount_different_format_no_conflict(self):
        self.assertEqual(self.amounts("My rent is $850. The rent is $850.00. Rent is $ 850."), [])

    def test_comma_amounts(self):
        flags = self.amounts("The damages are $1,200. The damages are $1,500.50.")
        self.assertEqual(len(flags), 1)
        self.assertIn("$1200.00", flags[0].text)
        self.assertIn("$1500.50", flags[0].text)

    def test_multiple_amounts_in_sentence_skipped(self):
        self.assertEqual(self.amounts("My rent is $850. The rent went from $800 to $900."), [])

    def test_different_nouns_not_compared(self):
        self.assertEqual(self.amounts("My rent is $850. The deposit was $900."), [])

    def test_nearest_noun_used(self):
        # amount belongs to the deposit even though "rent" appears too
        self.assertEqual(self.amounts("My rent is $850. I gave a deposit of $900 before the rent started."), [])

    def test_repeated_amount_cites_all_sentences(self):
        flags = self.amounts("The rent is $850. The rent is $900.\n--- Page 2 ---\nThe rent is $850.")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].cites, ["p1.s1", "p1.s2", "p2.s1"])
        self.assertIn("pp. 1, 2", flags[0].text)


class MissingRequestTests(unittest.TestCase):
    def test_flagged_when_absent(self):
        flags = checks.missing_request(doc("The landlord was rude."), brief())
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].cites, ["p1.s1"])

    def test_not_flagged_with_request(self):
        self.assertEqual(checks.missing_request(doc("I am asking the court to dismiss."), brief()), [])


class RunAllAndMergeTests(unittest.TestCase):
    def test_run_all_no_duplicates(self):
        d = doc("The incident was on March 2, 2026. The incident was on March 12, 2026. "
                "Rent is $850. Rent is $900. A second incident happened. I ask the court to help.")
        flags = checks.run_all(d, brief())
        texts = [f.text for f in flags]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(len(flags), 3)
        self.assertTrue(all(f.source == "rule" and f.cites for f in flags))

    def test_every_cite_exists_and_is_in_text(self):
        d = doc("The incident was on March 2, 2026.\n--- Page 2 ---\nThe incident was on 12 March 2026. Rent is $1. Rent is $2.")
        for f in checks.run_all(d, brief()):
            for c in f.cites:
                self.assertIsNotNone(d.get(c))
            if f.kind == "conflict":
                for c in f.cites:
                    self.assertIn("p. " + c.split(".")[0][1:], f.text.replace("pp. ", "p. ").replace(", ", ", p. ") if "pp." in f.text else f.text)

    def test_clean_filing_has_no_flags(self):
        d = doc("The incident was on March 2, 2026. I ask the court to dismiss the case.")
        self.assertEqual(checks.run_all(d, brief()), [])

    def test_merge_drops_same_cites(self):
        rule = [Flag(kind="conflict", text="R", cites=["p1.s1", "p2.s1"], source="rule")]
        model = [Flag(kind="conflict", text="M", cites=["p2.s1", "p1.s1"]), Flag(kind="gap", text="N", cites=["p3.s1"])]
        merged = checks.merge_flags(rule, model)
        self.assertEqual([f.text for f in merged], ["R", "N"])

    def test_merge_drops_same_text(self):
        rule = [Flag(kind="gap", text="No request found.", cites=["p1.s1"], source="rule")]
        model = [Flag(kind="gap", text="no request found. ", cites=["p1.s2"])]
        self.assertEqual(len(checks.merge_flags(rule, model)), 1)

    def test_merge_keeps_distinct_uncited_model_flags(self):
        model = [Flag(kind="gap", text="A", cites=[]), Flag(kind="gap", text="B", cites=[])]
        self.assertEqual(len(checks.merge_flags([], model)), 2)

    def test_merge_dedupes_among_model_flags(self):
        model = [Flag(kind="gap", text="A", cites=["p1.s1"]), Flag(kind="gap", text="B", cites=["p1.s1"])]
        self.assertEqual(len(checks.merge_flags([], model)), 1)


if __name__ == "__main__":
    unittest.main()
