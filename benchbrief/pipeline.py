"""Filing to stored, guarded brief."""
from __future__ import annotations

import json
import time
from datetime import date

from . import checks, guardrails, store
from .extract import Extractor, default_extractor
from .ingest import Document, Sentence
from .repeats import PriorFiling, describe, find_repeats
from .schema import Brief


def doc_to_json(doc: Document) -> str:
    return json.dumps({"title": doc.title, "pages": [[s.text for s in page] for page in doc.pages]})


def doc_from_json(raw: str) -> Document:
    data = json.loads(raw)
    doc = Document(title=data["title"])
    for p, page in enumerate(data["pages"], start=1):
        doc.pages.append([Sentence(id=f"p{p}.s{i}", page=p, index=i, text=t) for i, t in enumerate(page, start=1)])
    return doc


def build_brief(doc: Document, extractor: Extractor) -> tuple[Brief, guardrails.GuardReport]:
    draft = extractor.extract(doc)
    draft.flags = checks.merge_flags(checks.run_all(doc, draft), draft.flags)
    return guardrails.enforce(draft, doc)


def file_and_brief(conn, case_id: int, title: str, filed_on: str, doc: Document,
                   extractor: Extractor | None = None, filer: str | None = None) -> int:
    """Store a filing; generate its brief only if the case has briefs enabled."""
    enabled = conn.execute("SELECT brief_enabled FROM cases WHERE id=?", (case_id,)).fetchone()["brief_enabled"]
    fid = conn.execute(
        "INSERT INTO filings(case_id, filer, title, filed_on, doc_json) VALUES (?,?,?,?,?)",
        (case_id, filer or "Unknown", title, filed_on, doc_to_json(doc)),
    ).lastrowid
    store.audit(conn, "filed", case_id, fid)
    if enabled:
        generate(conn, fid, extractor)
    return fid


def generate(conn, filing_id: int, extractor: Extractor | None = None) -> None:
    row = conn.execute("SELECT * FROM filings WHERE id=?", (filing_id,)).fetchone()
    extractor = extractor or default_extractor()
    doc = doc_from_json(row["doc_json"])
    t0 = time.perf_counter()
    try:
        brief, report = build_brief(doc, extractor)
    except Exception as exc:  # surface to staff, never show a partial brief
        store.audit(conn, "brief_failed", row["case_id"], filing_id, {"error": str(exc)})
        return
    secs = time.perf_counter() - t0
    filer = row["filer"] if row["filer"] != "Unknown" else brief.filer
    conn.execute(
        "UPDATE filings SET brief_json=?, guard_json=?, extractor=?, seconds=?, filer=? WHERE id=?",
        (brief.model_dump_json(), json.dumps(report.__dict__), extractor.name, secs, filer, filing_id),
    )
    store.audit(conn, "brief_generated", row["case_id"], filing_id,
                {"extractor": extractor.name, "kept": report.kept, "dropped": len(report.dropped)})


def request_text(brief: Brief) -> str:
    return " ".join(c.quote for c in brief.asking_for)


def repeat_note(conn, filing_id: int) -> tuple[str | None, list[int]]:
    row = conn.execute("SELECT * FROM filings WHERE id=?", (filing_id,)).fetchone()
    if not row["brief_json"]:
        return None, []
    brief = Brief.model_validate_json(row["brief_json"])
    if not brief.asking_for:
        return None, []
    priors = []
    for p in conn.execute(
        "SELECT * FROM filings WHERE case_id=? AND lower(filer)=lower(?) AND id<>? AND brief_json IS NOT NULL "
        "AND (filed_on < ? OR (filed_on = ? AND id < ?)) ORDER BY filed_on",
        (row["case_id"], row["filer"], filing_id, row["filed_on"], row["filed_on"], filing_id),
    ):
        pb = Brief.model_validate_json(p["brief_json"])
        priors.append(PriorFiling(p["id"], p["filed_on"], pb.filing_type, request_text(pb)))
    matches = find_repeats(request_text(brief), brief.filing_type, priors)
    return describe(matches, fmt_date), [m.prior_id for m in matches]


def fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"
