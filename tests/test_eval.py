"""Run: python -m unittest discover -s tests -v"""
import json
import unittest

from evals import run_eval


class EvalHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_eval.evaluate("rules")
        cls.rows = {r["sample"]: r for r in cls.result["samples"]}

    def test_no_errors_and_all_samples_covered(self):
        self.assertEqual(self.result["overall"]["errors"], 0)
        self.assertEqual(set(self.rows), set(run_eval.load_gold()))
        self.assertEqual(len(self.rows), 6)

    def test_citation_validity_is_100_percent(self):
        self.assertEqual(self.result["overall"]["citation_validity"], 1.0)

    def test_neutrality_audit_is_clean(self):
        self.assertEqual(self.result["overall"]["neutrality_violations"], 0)

    def test_answer_tenant_flag_recall(self):
        self.assertEqual(self.rows["answer_tenant"]["flag_recall"], 1.0)

    def test_cli_exit_code_and_json(self):
        self.assertTrue(run_eval.passed(self.result))
        json.dumps(self.result)  # machine-readable
        self.assertEqual(run_eval.main(["--extractor", "rules", "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
