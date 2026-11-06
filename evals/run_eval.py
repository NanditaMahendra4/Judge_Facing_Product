"""Offline evaluation harness for Bench Brief extraction quality.

    python -m evals.run_eval --extractor rules [--json]
    python -m evals.run_eval --extractor claude   # needs `anthropic` + credentials

Gold files live in evals/gold/<sample>.json and are keyed to sentence ids.
Exit status is non-zero if citation validity < 100% or the neutrality audit
finds anything, or if any sample fails to produce a brief.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchbrief import guardrails, ingest, pipeline
from benchbrief.schema import Brief

ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = Path(__file__).resolve().parent / "gold"
SAMPLES_DIR = ROOT / "samples"


def make_extractor(name: str):
    if name == "rules":
        from benchbrief.extract import RuleExtractor
        return RuleExtractor()
    if name == "claude":
        from benchbrief.extract import ClaudeExtractor  # imports anthropic only here
        return ClaudeExtractor()
    raise ValueError(f"unknown extractor: {name}")


def load_gold() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(GOLD_DIR.glob("*.json"))}


def _flag_matches(flag, spec: dict) -> bool:
    """kind equal; cite sets overlap (when the spec names cites); all substrings present."""
    if flag.kind != spec["kind"]:
        return False
    cites = set(spec.get("cites") or [])
    if cites and not (cites & set(flag.cites)):
        return False
    text = flag.text.lower()
    return all(m.lower() in text for m in spec.get("must_mention", []))


def _ratio(num: int, den: int):
    return None if den == 0 else num / den


def _all_items(brief: Brief):
    """Yield (section, cites, quote, neutrality-checked text) for every output item."""
    for sec in ("what_happened", "when", "where", "who", "asking_for"):
        for c in getattr(brief, sec):
            yield sec, c.cites, c.quote, c.text
    for e in brief.evidence:
        yield "evidence", e.cites, e.quote, e.claimed_to_show
    for f in brief.flags:
        yield "flags", f.cites, None, f.text


def audit_citations(brief: Brief, doc) -> tuple[int, int]:
    items = list(_all_items(brief))
    ok = sum(1 for _, cites, quote, _t in items if guardrails._check_cites(doc, cites, quote) is None)
    return ok, len(items)


def audit_neutrality(brief: Brief) -> list[str]:
    """Same rule as the guardrails (evaluative words are allowed only when they
    come from the filer's own quoted words), re-run over every output text."""
    bad = []
    for sec, _cites, quote, text in _all_items(brief):
        problem = (guardrails._neutral_problem(text) if quote is None
                   else guardrails._neutral_problem_outside_quote(text, quote))
        if problem:
            bad.append(f"{sec}: {problem}")
    for e in brief.evidence:  # labels are also output text
        if guardrails._neutral_problem(e.label):
            bad.append(f"evidence label: {e.label}")
    return bad


def eval_sample(name: str, gold: dict, extractor) -> dict:
    doc = ingest.load(SAMPLES_DIR / f"{name}.txt")
    brief, report = pipeline.build_brief(doc, extractor)

    exp = gold.get("expected_flags", [])
    flags = brief.flags
    matched_gold = sum(1 for g in exp if any(_flag_matches(f, g) for f in flags))
    matched_pred = sum(1 for f in flags if any(_flag_matches(f, g) for g in exp))
    forbidden_hits = [f.text for f in flags if any(_flag_matches(f, g) for g in gold.get("forbidden_flags", []))]

    relief_gold = set(gold.get("relief_sentence_ids", []))
    relief_pred = {c for cl in brief.asking_for for c in cl.cites}
    labels_pred = [e.label.lower() for e in brief.evidence]
    ev_gold = gold.get("evidence_labels", [])
    ev_hit = sum(1 for g in ev_gold if any(g.lower() in p or p in g.lower() for p in labels_pred))

    cit_ok, cit_total = audit_citations(brief, doc)
    neutral = audit_neutrality(brief)
    return {
        "sample": name,
        "flag_gold": len(exp), "flag_pred": len(flags),
        "flag_gold_matched": matched_gold, "flag_pred_matched": matched_pred,
        "flag_recall": _ratio(matched_gold, len(exp)),
        "flag_precision": _ratio(matched_pred, len(flags)),
        "forbidden_hits": forbidden_hits,
        "relief_gold": len(relief_gold), "relief_hit": len(relief_gold & relief_pred),
        "relief_recall": _ratio(len(relief_gold & relief_pred), len(relief_gold)),
        "evidence_gold": len(ev_gold), "evidence_hit": ev_hit,
        "evidence_recall": _ratio(ev_hit, len(ev_gold)),
        "filer": brief.filer, "filer_expected": gold.get("filer"),
        "filer_match": brief.filer.strip() == (gold.get("filer") or "").strip(),
        "guardrail_withheld": len(report.dropped),
        "citations_ok": cit_ok, "citations_total": cit_total,
        "citation_validity": 1.0 if cit_total == 0 else cit_ok / cit_total,
        "neutrality_violations": len(neutral), "neutrality_detail": neutral,
        "error": None,
    }


def _sum(rows, k):
    return sum(r[k] for r in rows)


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["error"]]
    cit_total = _sum(ok, "citations_total")
    return {
        "samples": len(rows), "errors": len(rows) - len(ok),
        "flag_recall": _ratio(_sum(ok, "flag_gold_matched"), _sum(ok, "flag_gold")),
        "flag_precision": _ratio(_sum(ok, "flag_pred_matched"), _sum(ok, "flag_pred")),
        "forbidden_hits": sum(len(r["forbidden_hits"]) for r in ok),
        "relief_recall": _ratio(_sum(ok, "relief_hit"), _sum(ok, "relief_gold")),
        "evidence_recall": _ratio(_sum(ok, "evidence_hit"), _sum(ok, "evidence_gold")),
        "filer_exact_match": _ratio(sum(r["filer_match"] for r in ok), len(ok)),
        "guardrail_withheld": _sum(ok, "guardrail_withheld"),
        "citation_validity": 1.0 if cit_total == 0 else _sum(ok, "citations_ok") / cit_total,
        "neutrality_violations": _sum(ok, "neutrality_violations"),
    }


def evaluate(extractor="rules") -> dict:
    ext = make_extractor(extractor) if isinstance(extractor, str) else extractor
    rows = []
    for name, gold in load_gold().items():
        try:
            rows.append(eval_sample(name, gold, ext))
        except Exception as exc:  # a failed extraction is a result, not a crash
            rows.append({"sample": name, "error": f"{type(exc).__name__}: {exc}"})
    return {"extractor": getattr(ext, "name", str(extractor)), "samples": rows, "overall": summarize(rows)}


def passed(result: dict) -> bool:
    o = result["overall"]
    return o["errors"] == 0 and o["citation_validity"] >= 1.0 and o["neutrality_violations"] == 0


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def format_table(result: dict) -> str:
    head = ["sample", "flagR", "flagP", "bad", "reliefR", "evidR", "filer", "withheld", "citeOK", "neutral"]
    lines = [[*head]]
    for r in result["samples"]:
        if r["error"]:
            lines.append([r["sample"], "ERROR: " + r["error"]])
            continue
        lines.append([r["sample"], _pct(r["flag_recall"]), _pct(r["flag_precision"]), str(len(r["forbidden_hits"])),
                      _pct(r["relief_recall"]), _pct(r["evidence_recall"]), "yes" if r["filer_match"] else "NO",
                      str(r["guardrail_withheld"]), _pct(r["citation_validity"]), str(r["neutrality_violations"])])
    o = result["overall"]
    lines.append(["OVERALL", _pct(o["flag_recall"]), _pct(o["flag_precision"]), str(o["forbidden_hits"]),
                  _pct(o["relief_recall"]), _pct(o["evidence_recall"]), _pct(o["filer_exact_match"]),
                  str(o["guardrail_withheld"]), _pct(o["citation_validity"]), str(o["neutrality_violations"])])
    widths = [max(len(l[i]) for l in lines if len(l) == len(head)) for i in range(len(head))]
    out = [f"Extractor: {result['extractor']}"]
    for n, l in enumerate(lines):
        if len(l) == len(head):
            out.append("  ".join(c.ljust(widths[i]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(l)))
        else:
            out.append("  ".join(l))
        if n == 0 or n == len(lines) - 2:
            out.append("-" * len(out[1]))
    out.append("")
    out.append("flagR/flagP: flag recall/precision (kind + cite overlap); bad: forbidden flags raised; "
               "reliefR: relief sentences found; evidR: evidence labels found;")
    out.append("withheld: items dropped by guardrails; citeOK: citation validity; neutral: neutrality audit violations (want 0).")
    for r in result["samples"]:
        for t in r.get("forbidden_hits", []) or []:
            out.append(f"  forbidden flag in {r['sample']}: {t}")
        for t in r.get("neutrality_detail", []) or []:
            out.append(f"  neutrality in {r['sample']}: {t}")
    out.append("RESULT: " + ("PASS" if passed(result) else "FAIL"))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.run_eval", description=__doc__.split("\n")[0])
    ap.add_argument("--extractor", choices=["rules", "claude"], required=True)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)
    try:
        result = evaluate(args.extractor)
    except ImportError as exc:
        print(f"Cannot use the {args.extractor} extractor: {exc}. Install the SDK or use --extractor rules.", file=sys.stderr)
        return 2
    result["passed"] = passed(result)
    print(json.dumps(result, indent=2) if args.json else format_table(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
