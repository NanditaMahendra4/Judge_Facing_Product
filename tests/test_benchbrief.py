"""Run: python -m unittest discover -s tests -v"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from benchbrief import checks, guardrails, ingest, pipeline, store
from benchbrief.extract import ClaudeExtractor, RuleExtractor
from benchbrief.repeats import PriorFiling, find_repeats
from benchbrief.schema import Brief, Claim, EvidenceItem, Flag

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TWO_PAGE = "The incident was on March 2, 2026 at my apartment.\n--- Page 2 ---\nThe incident was on March 12, 2026. I ask the court to dismiss."


def empty_brief(**kw) -> Brief:
    base = dict(filer="X", filing_type="Motion", what_happened=[], when=[], where=[], who=[], asking_for=[], evidence=[], flags=[])
    base.update(kw)
    return Brief(**base)


class IngestTests(unittest.TestCase):
    def test_pages_and_ids(self):
        doc = ingest.from_text("t", TWO_PAGE)
        self.assertEqual(len(doc.pages), 2)
        self.assertEqual([s.id for s in doc.sentences], ["p1.s1", "p2.s1", "p2.s2"])

    def test_abbreviations_do_not_split(self):
        out = ingest.split_sentences("I saw Mr. Smith on Main St. near Apt. 4. Then he left.")
        self.assertEqual(out, ["I saw Mr. Smith on Main St. near Apt. 4.", "Then he left."])

    def test_roundtrip_json(self):
        doc = ingest.from_text("t", TWO_PAGE)
        again = pipeline.doc_from_json(pipeline.doc_to_json(doc))
        self.assertEqual([(s.id, s.text) for s in again.sentences], [(s.id, s.text) for s in doc.sentences])


class GuardrailTests(unittest.TestCase):
    def setUp(self):
        self.doc = ingest.from_text("t", TWO_PAGE)

    def test_valid_claim_kept(self):
        b = empty_brief(when=[Claim(text="Filer states the incident was March 2, 2026.", cites=["p1.s1"], quote="March 2, 2026")])
        out, rep = guardrails.enforce(b, self.doc)
        self.assertEqual(len(out.when), 1)
        self.assertEqual(rep.dropped, [])

    def test_fabricated_quote_dropped(self):
        b = empty_brief(what_happened=[Claim(text="Filer states he hit her.", cites=["p1.s1"], quote="he hit me")])
        out, rep = guardrails.enforce(b, self.doc)
        self.assertEqual(out.what_happened, [])
        self.assertIn("quote not found", rep.dropped[0]["reason"])

    def test_unknown_cite_dropped(self):
        b = empty_brief(who=[Claim(text="x", cites=["p9.s1"], quote="x")])
        out, rep = guardrails.enforce(b, self.doc)
        self.assertEqual(out.who, [])
        self.assertIn("unknown sentence", rep.dropped[0]["reason"])

    def test_uncited_dropped(self):
        b = empty_brief(who=[Claim(text="x", cites=[], quote="x")])
        out, _ = guardrails.enforce(b, self.doc)
        self.assertEqual(out.who, [])

    def test_non_neutral_language_dropped(self):
        b = empty_brief(
            what_happened=[Claim(text="Filer's account is not credible.", cites=["p1.s1"], quote="March 2, 2026")],
            flags=[Flag(kind="conflict", text="Dates conflict, so the court should deny the motion.", cites=["p1.s1"])],
        )
        out, rep = guardrails.enforce(b, self.doc)
        self.assertEqual(out.what_happened, [])
        self.assertEqual(out.flags, [])
        self.assertEqual(len(rep.dropped), 2)

    def test_filers_own_words_allowed(self):
        doc = ingest.from_text("t", "I think he is clearly lying about the rent.")
        b = empty_brief(what_happened=[Claim(text="Filer states he is clearly lying about the rent.", cites=["p1.s1"], quote="he is clearly lying about the rent")])
        out, _ = guardrails.enforce(b, doc)
        self.assertEqual(len(out.what_happened), 1)

    def test_evidence_must_be_cited(self):
        b = empty_brief(evidence=[EvidenceItem(label="Exhibit Z", filed_by="Filer", claimed_to_show="x", cites=["p1.s1"], quote="photos of damage")])
        out, _ = guardrails.enforce(b, self.doc)
        self.assertEqual(out.evidence, [])


class CheckTests(unittest.TestCase):
    def test_date_conflict(self):
        doc = ingest.from_text("t", TWO_PAGE)
        flags = checks.date_conflicts(doc)
        self.assertEqual(len(flags), 1)
        self.assertIn("p. 1", flags[0].text)
        self.assertIn("p. 2", flags[0].text)
        self.assertEqual(flags[0].cites, ["p1.s1", "p2.s1"])

    def test_same_date_different_format_not_conflict(self):
        doc = ingest.from_text("t", "The incident was on March 2, 2026. The incident on 3/2/2026 scared me.")
        self.assertEqual(checks.date_conflicts(doc), [])

    def test_undated_second_incident(self):
        doc = ingest.from_text("t", "Then there was a second incident in the hallway.")
        flags = checks.undated_events(doc)
        self.assertEqual(flags[0].kind, "gap")
        self.assertIn("second incident", flags[0].text)

    def test_amount_conflict(self):
        doc = ingest.from_text("t", "My rent is $850 a month. The landlord says rent is $900.")
        self.assertEqual(len(checks.amount_conflicts(doc)), 1)

    def test_missing_request(self):
        doc = ingest.from_text("t", "The landlord was rude to me.")
        flags = checks.missing_request(doc, empty_brief())
        self.assertEqual(flags[0].kind, "gap")

    def test_tenant_sample_flags(self):
        doc = ingest.load(SAMPLES / "answer_tenant.txt")
        brief, _ = pipeline.build_brief(doc, RuleExtractor())
        texts = " | ".join(f.text for f in brief.flags)
        self.assertIn("Different dates given for the incident", texts)
        self.assertIn("Different amounts given for rent", texts)
        self.assertIn("No date stated for the second incident", texts)


class RepeatTests(unittest.TestCase):
    def test_similar_requests_match(self):
        priors = [PriorFiling(1, "2026-03-03", "Motion to Continue", "continue the custody hearing to a later date")]
        m = find_repeats("please continue the custody hearing to a later date", "Motion to Continue", priors)
        self.assertEqual([x.prior_id for x in m], [1])

    def test_different_request_no_match(self):
        priors = [PriorFiling(1, "2026-03-03", "Motion to Continue", "continue the custody hearing to a later date")]
        m = find_repeats("order parenting time every other weekend", "Motion for Parenting Time", priors)
        self.assertEqual(m, [])


class FakeMessages:
    def __init__(self, brief, stop_reason="end_turn"):
        self.brief, self.stop_reason, self.calls = brief, stop_reason, []

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.brief)


class ClaudeExtractorTests(unittest.TestCase):
    def test_request_shape_and_guarding(self):
        doc = ingest.from_text("t", TWO_PAGE)
        model_out = empty_brief(
            when=[Claim(text="Filer states March 2, 2026.", cites=["p1.s1"], quote="March 2, 2026"),
                  Claim(text="Filer states April 1.", cites=["p1.s1"], quote="April 1")],  # fabricated
        )
        fake = FakeMessages(model_out)
        ex = ClaudeExtractor(client=SimpleNamespace(messages=fake))
        brief, rep = pipeline.build_brief(doc, ex)
        call = fake.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertIs(call["output_format"], Brief)
        self.assertIn("[p2.s1]", call["messages"][0]["content"])
        self.assertEqual(len(brief.when), 1)
        self.assertEqual(len(rep.dropped), 1)
        self.assertTrue(any(f.source == "rule" for f in brief.flags))

    def test_refusal_raises(self):
        ex = ClaudeExtractor(client=SimpleNamespace(messages=FakeMessages(None, "refusal")))
        with self.assertRaises(RuntimeError):
            ex.extract(ingest.from_text("t", "x."))


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENCHBRIEF_DB"] = os.path.join(self.tmp.name, "t.db")
        os.environ["BENCHBRIEF_EXTRACTOR"] = "rules"
        from starlette.testclient import TestClient
        from benchbrief.app import app
        self.client = TestClient(app)
        with store.connect() as conn:
            self.cid = store.upsert_case(conn, "T-1", "Test v. Test")

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, text, filed_on="2026-03-03"):
        return self.client.post(f"/case/{self.cid}/upload", data={"text": text, "filed_on": filed_on, "filer": "Pat Doe"})

    def test_upload_brief_and_source(self):
        r = self.upload(TWO_PAGE)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Different dates given for the incident", r.text)
        self.assertIn("/source?hl=p1.s1,p2.s1#p1-s1", r.text)
        src = self.client.get(r.url.path + "/source")
        self.assertIn('id="p2-s1"', src.text)

    def test_repeat_note(self):
        self.upload("I ask the court to continue the custody hearing to a later date.")
        r = self.upload("I ask the court to continue the custody hearing to a later date again.", "2026-04-10")
        self.assertIn("This repeats the March 3, 2026 request.", r.text)

    def test_toggle_off_skips_generation_and_redirects(self):
        self.client.post(f"/case/{self.cid}/toggle", data={"enabled": "0"})
        r = self.upload(TWO_PAGE)
        self.assertTrue(r.url.path.endswith("/source"))
        with store.connect() as conn:
            self.assertIsNone(conn.execute("SELECT brief_json FROM filings").fetchone()["brief_json"])

    def test_review_timing_metrics(self):
        r = self.upload(TWO_PAGE)
        fid = r.url.path.split("/")[2]
        self.client.post(f"/filing/{fid}/done", data={"seconds": "95", "mode": "brief", "useful": "yes"})
        m = self.client.get("/metrics")
        self.assertIn("95s", m.text)


if __name__ == "__main__":
    unittest.main()
