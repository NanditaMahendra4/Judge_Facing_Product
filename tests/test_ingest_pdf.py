import shutil
import tempfile
import unittest
from pathlib import Path

from benchbrief import ingest


def _make_pdf(page_lines: list[list[str]]) -> bytes:
    """Minimal valid PDF; each page is a list of text lines (empty list = no text layer)."""
    objs: list[bytes] = []
    n = len(page_lines)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(page_lines):
        content_id = 5 + 2 * i
        objs.append(
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
             f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>").encode()
        )
        if lines:
            ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
            for l in lines:
                esc = l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                ops.append(f"({esc}) Tj T*")
            ops.append("ET")
            stream = "\n".join(ops).encode()
        else:
            stream = b"0.5 g 100 100 200 200 re f"  # graphics only, no text
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\nendstream".encode()[:-9]
    out += b"\n%%EOF\n"
    return bytes(out)


class PdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.text_pdf = cls.dir / "text.pdf"
        cls.text_pdf.write_bytes(_make_pdf([
            ["1. I live at 12 Oak St. in the unit.", "The rent is $850.50 each month."],
            ["2. The landlord came at 4:30 p.m. on May 1.", "He did not knock."],
        ]))
        cls.scan_pdf = cls.dir / "scan.pdf"
        cls.scan_pdf.write_bytes(_make_pdf([[]]))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_text_pdf_pages_and_ids(self):
        doc = ingest.from_pdf(self.text_pdf)
        self.assertEqual(len(doc.pages), 2)
        self.assertTrue(all(s.id.startswith("p1.s") for s in doc.pages[0]))
        self.assertTrue(all(s.id.startswith("p2.s") for s in doc.pages[1]))
        self.assertIn("p1.s1", [s.id for s in doc.sentences])
        self.assertEqual(ingest.load(self.text_pdf).title, "text")

    def test_pdf_no_decimal_or_time_split(self):
        texts = [s.text for s in ingest.from_pdf(self.text_pdf).sentences]
        self.assertTrue(any("$850.50" in t for t in texts))
        self.assertTrue(any("4:30 p.m. on May 1" in t for t in texts))

    def test_scan_raises(self):
        with self.assertRaises(ValueError) as cm:
            ingest.from_pdf(self.scan_pdf)
        self.assertIn("OCR", str(cm.exception))


class CleanupTests(unittest.TestCase):
    def test_hyphenated_break_joined(self):
        self.assertEqual(ingest.split_sentences("The eviction-\nnotice and evic-\ntion."),
                         ["The evictionnotice and eviction."])

    def test_hyphen_before_capital_kept(self):
        out = ingest.split_sentences("He met Anne-\nMarie today.")
        self.assertEqual(out, ["He met Anne- Marie today."])

    def test_hard_wrap_joined(self):
        self.assertEqual(ingest.split_sentences("The landlord\nentered the unit.\nI was afraid."),
                         ["The landlord entered the unit.", "I was afraid."])

    def test_page_headers_footers_removed(self):
        pages = [f"Smith v. Jones\n{b}\nPage {i} of 3" for i, b in enumerate(
            ["First body here.", "Second body here.", "Third body here."], 1)]
        doc = ingest.from_pages("t", pages)
        texts = [s.text for s in doc.sentences]
        self.assertEqual(texts, ["First body here.", "Second body here.", "Third body here."])

    def test_lone_page_number_removed(self):
        doc = ingest.from_pages("t", ["Hello there.\n7"])
        self.assertEqual([s.text for s in doc.sentences], ["Hello there."])

    def test_invisible_and_nbsp(self):
        out = ingest.split_sentences("Rent​ is due.﻿")
        self.assertEqual(out, ["Rent is due."])

    def test_numbered_paragraphs(self):
        out = ingest.split_sentences("1. I live here.\n2. He came by.")
        self.assertEqual(out, ["1. I live here.", "2. He came by."])

    def test_crlf(self):
        doc = ingest.from_text("t", "1. I live here.\r\n2. He came by.\r\n\r\nEnd.\fSecond page.\r\n")
        self.assertEqual(len(doc.pages), 2)
        self.assertEqual([s.text for s in doc.pages[0]], ["1. I live here.", "2. He came by.", "End."])

    def test_decimals_and_times(self):
        out = ingest.split_sentences("He paid $850.50 on time. He came at 4:30 p.m. on May 1.")
        self.assertEqual(out, ["He paid $850.50 on time.", "He came at 4:30 p.m. on May 1."])


if __name__ == "__main__":
    unittest.main()
