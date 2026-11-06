import os
import tempfile
import unittest

from benchbrief import store

TEXT = "The incident was on March 2, 2026 at my apartment. I ask the court to dismiss."


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BENCHBRIEF_DB"] = os.path.join(self.tmp.name, "h.db")
        os.environ["BENCHBRIEF_EXTRACTOR"] = "rules"
        from starlette.testclient import TestClient
        from benchbrief.app import app
        self.client = TestClient(app)
        with store.connect() as conn:
            self.cid = store.upsert_case(conn, "H-1", "Hard v. Ening")

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, **kw):
        data = {"filed_on": "2026-03-03", "filer": "Pat"}
        data.update(kw.pop("data", {}))
        return self.client.post(f"/case/{self.cid}/upload", data=data, **kw)

    def test_missing_ids_404(self):
        self.assertEqual(self.client.post("/filing/999/done", data={"seconds": "1"}).status_code, 404)
        self.assertEqual(self.client.post("/case/999/toggle", data={"enabled": "0"}).status_code, 404)
        self.assertEqual(self.client.post("/case/999/upload", data={"text": TEXT, "filed_on": "2026-01-01"}).status_code, 404)
        self.assertEqual(self.client.post("/filing/999/regenerate").status_code, 404)
        self.assertEqual(self.client.get("/filing/999").status_code, 404)
        self.assertEqual(self.client.get("/filing/999/source").status_code, 404)

    def test_filed_on_validation(self):
        for bad in ["", "yesterday", "2026-13-01", "2026-02-30", "20260303"]:
            r = self.post(data={"text": TEXT, "filed_on": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("YYYY-MM-DD", r.text)

    def test_upload_type_and_size(self):
        r = self.post(files={"file": ("a.exe", b"x", "application/octet-stream")})
        self.assertEqual(r.status_code, 415)
        r = self.post(files={"file": ("a.txt", b"a" * (15 * 1024 * 1024 + 1), "text/plain")})
        self.assertEqual(r.status_code, 413)

    def test_txt_utf8_and_latin1(self):
        r = self.post(files={"file": ("u.txt", "Café was closed. I ask for relief.".encode(), "text/plain")})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Café", self.client.get(r.url.path + "/source").text)
        r = self.post(files={"file": ("l.txt", "Café was open. I ask for relief.".encode("latin-1"), "text/plain")})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Café", self.client.get(r.url.path + "/source").text)

    def test_security_headers_and_cache(self):
        r = self.post(data={"text": TEXT})
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["referrer-policy"], "no-referrer")
        self.assertIn("script-src 'self'", r.headers["content-security-policy"])
        self.assertNotIn("<script>", r.text)
        src = self.client.get(r.url.path + "/source")
        self.assertEqual(src.headers["cache-control"], "no-store")
        js = self.client.get("/static/app.js")
        self.assertEqual(js.status_code, 200)
        self.assertIn(r"/^p\d+\.s\d+$/", js.text)

    def test_accessibility_markup(self):
        r = self.post(data={"text": TEXT})
        self.assertIn('class="skip"', r.text)
        self.assertIn('aria-label="Source: page 1, sentence 1"', r.text)
        self.assertIn("<legend>", r.text)
        self.assertIn("data-print", r.text)
        self.assertIn("noprint", r.text)

    def test_autoescape(self):
        r = self.post(data={"text": TEXT, "title": "<script>alert(1)</script>"})
        self.assertNotIn("<script>alert(1)</script>", r.text)
        self.assertIn("&lt;script&gt;", r.text)

    def test_done_records_review(self):
        r = self.post(data={"text": TEXT})
        fid = r.url.path.rsplit("/", 1)[1]
        r = self.client.post(f"/filing/{fid}/done", data={"seconds": "12", "mode": "brief", "useful": "yes"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
