"""Turn a filing (PDF or text) into pages and addressable sentences.

Every sentence gets a stable id like ``p2.s4`` (page 2, sentence 4). All
downstream output must cite these ids, so the judge can click straight back to
the exact sentence in the source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Page breaks in plain-text filings: form feed, or a line like "--- Page 3 ---".
_PAGE_BREAK = re.compile(r"\f|^\s*-{2,}\s*page\s+\d+\s*-{2,}\s*$", re.IGNORECASE | re.MULTILINE)

# Split after . ! ? when followed by whitespace + capital/digit/quote/paren.
# Avoid splitting on common abbreviations used in filings.
_ABBREV = r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bNo\.)(?<!\bSt\.)(?<!\bAve\.)(?<!\bp\.)(?<!\bpp\.)(?<!\bvs\.)(?<!\bv\.)(?<!\bInc\.)(?<!\bApt\.)(?<!\bJr\.)(?<!\bSr\.)(?<!\b[A-Z]\.)"
_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]?" + _ABBREV + r"\s+(?=[\"'(\[]?[A-Z0-9])")


@dataclass
class Sentence:
    id: str
    page: int
    index: int
    text: str


@dataclass
class Document:
    title: str
    pages: list[list[Sentence]] = field(default_factory=list)

    @property
    def sentences(self) -> list[Sentence]:
        return [s for page in self.pages for s in page]

    def get(self, sid: str) -> Sentence | None:
        return self._index().get(sid)

    def _index(self) -> dict[str, Sentence]:
        if not hasattr(self, "_idx"):
            self._idx = {s.id: s for s in self.sentences}
        return self._idx

    def to_prompt_text(self) -> str:
        """Render with ids inline, which is what the model sees and cites."""
        out = []
        for n, page in enumerate(self.pages, start=1):
            out.append(f"=== Page {n} ===")
            out.extend(f"[{s.id}] {s.text}" for s in page)
        return "\n".join(out)


_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
_PAGE_NUM_LINE = re.compile(
    r"^\s*(?:page\s+\d+(?:\s+of\s+\d+)?|[-\u2013\u2014]?\s*\d{1,4}\s*[-\u2013\u2014]?|\d+\s*/\s*\d+)\s*$",
    re.IGNORECASE,
)
_HYPHEN_BREAK = re.compile(r"(?<=[A-Za-z])-[ \t]*\n[ \t]*(?=[a-z])")


def _normalize(text: str) -> str:
    """Clean extraction artifacts: line endings, invisible chars, nbsp, hyphen breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE.sub("", text)
    text = re.sub(r"[\u00a0\u202f\u2007]", " ", text)
    text = _HYPHEN_BREAK.sub("", text)
    return text


def _strip_page_numbers(text: str) -> str:
    return "\n".join(l for l in text.split("\n") if not _PAGE_NUM_LINE.match(l))


def _line_key(line: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line.strip()).lower())


def _strip_repeated_edges(pages: list[str]) -> list[str]:
    """Drop first/last lines that repeat on at least half of the pages (headers/footers)."""
    if len(pages) < 2:
        return pages
    edges: list[set[str]] = []
    for p in pages:
        lines = [l for l in p.split("\n") if l.strip()]
        keys = set()
        if lines:
            keys.add(_line_key(lines[0]))
            keys.add(_line_key(lines[-1]))
        edges.append(keys)
    counts: dict[str, int] = {}
    for keys in edges:
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
    bad = {k for k, c in counts.items() if c >= 2 and c * 2 >= len(pages)}
    if not bad:
        return pages
    out = []
    for p in pages:
        lines = p.split("\n")
        idx = [i for i, l in enumerate(lines) if l.strip()]
        drop = set()
        if idx:
            for i in (idx[0], idx[-1]):
                if _line_key(lines[i]) in bad:
                    drop.add(i)
        out.append("\n".join(l for i, l in enumerate(lines) if i not in drop))
    return out


def split_sentences(text: str) -> list[str]:
    text = _normalize(text)
    text = re.sub(r"[ \t]+", " ", text)
    # Treat blank lines and numbered paragraph starts as hard boundaries.
    blocks = re.split(r"\n\s*\n|\n(?=\s*\d+\.\s)", text)
    out: list[str] = []
    for block in blocks:
        block = " ".join(line.strip() for line in block.splitlines()).strip()
        if not block:
            continue
        m = re.match(r"(\d+\.)\s+", block)  # keep "1." attached to its paragraph
        prefix, rest = (m.group(1) + " ", block[m.end():]) if m else ("", block)
        parts = [p.strip() for p in _SENT_SPLIT.split(rest) if p.strip()]
        if prefix and parts:
            parts[0] = prefix + parts[0]
        out.extend(parts)
    return out


def from_pages(title: str, page_texts: list[str]) -> Document:
    doc = Document(title=title)
    page_texts = [_strip_page_numbers(_normalize(t)) for t in page_texts]
    page_texts = _strip_repeated_edges(page_texts)
    for pnum, ptext in enumerate(page_texts, start=1):
        sents = [
            Sentence(id=f"p{pnum}.s{i}", page=pnum, index=i, text=t)
            for i, t in enumerate(split_sentences(ptext), start=1)
        ]
        doc.pages.append(sents)
    return doc


def from_text(title: str, text: str) -> Document:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pages = [p for p in _PAGE_BREAK.split(text) if p.strip()] or [text]
    return from_pages(title, pages)


def from_pdf(path: str | Path, title: str | None = None) -> Document:
    import pdfplumber  # local import: only needed for PDFs

    with pdfplumber.open(str(path)) as pdf:
        pages = [(page.extract_text() or "") for page in pdf.pages]
    if not any(p.strip() for p in pages):
        raise ValueError(
            "No text layer found in this PDF (likely a scan). Run OCR first; "
            "Bench Brief will not guess at unreadable pages."
        )
    return from_pages(title or Path(path).stem, pages)


def load(path: str | Path, title: str | None = None) -> Document:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return from_pdf(path, title)
    return from_text(title or path.stem, path.read_text(encoding="utf-8"))
