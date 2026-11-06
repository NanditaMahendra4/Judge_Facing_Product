"""Command line: ``python -m benchbrief {serve,seed,brief}``."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import ingest, pipeline, store
from .extract import ClaudeExtractor, RuleExtractor, default_extractor

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# Fictional cases for demos. Not real people or real cause numbers.
SEED = [
    ("SC-2026-0412", "Riverbend Rentals v. Whitfield", [("answer_tenant.txt", "2026-03-20", None)]),
    ("PO-2026-0077", "In re Protective Order: Reyes v. Lane", [("protective_order.txt", "2026-07-01", None)]),
    ("DR-2026-0150", "In re Custody of a Minor: Hall / Moreno", [
        ("continue_1.txt", "2026-03-03", None),
        ("parenting_time.txt", "2026-03-15", None),
        ("continue_2.txt", "2026-04-10", None),
        ("continue_3.txt", "2026-05-02", None),
    ]),
]

TITLES = {
    "answer_tenant.txt": "Answer to Landlord Complaint",
    "protective_order.txt": "Petition for Order of Protection",
    "continue_1.txt": "Motion to Continue Hearing",
    "continue_2.txt": "Motion to Continue",
    "continue_3.txt": "Motion to Continue Custody Hearing",
    "parenting_time.txt": "Motion for Parenting Time",
}


def _extractor(name: str | None):
    if name == "rules":
        return RuleExtractor()
    if name == "claude":
        return ClaudeExtractor()
    return default_extractor()


def main() -> None:
    ap = argparse.ArgumentParser(prog="benchbrief")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    sd = sub.add_parser("seed", help="load the fictional sample docket")
    sd.add_argument("--extractor", choices=["auto", "claude", "rules"], default="auto")
    b = sub.add_parser("brief", help="print a brief for one file as JSON")
    b.add_argument("path")
    b.add_argument("--extractor", choices=["auto", "claude", "rules"], default="auto")
    args = ap.parse_args()

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("benchbrief.app:app", host=args.host, port=args.port)
    elif args.cmd == "seed":
        ex = _extractor(args.extractor)
        with store.connect() as conn:
            for number, caption, filings in SEED:
                cid = store.upsert_case(conn, number, caption)
                for fname, filed_on, filer in filings:
                    doc = ingest.load(SAMPLES / fname, TITLES[fname])
                    pipeline.file_and_brief(conn, cid, doc.title, filed_on, doc, ex, filer)
        print(f"Seeded {len(SEED)} cases into {store.db_path()} using the '{ex.name}' extractor.")
    elif args.cmd == "brief":
        doc = ingest.load(args.path)
        brief, report = pipeline.build_brief(doc, _extractor(args.extractor))
        print(json.dumps({"brief": brief.model_dump(), "withheld": report.dropped}, indent=2))


if __name__ == "__main__":
    main()
