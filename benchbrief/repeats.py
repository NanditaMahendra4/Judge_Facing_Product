"""Group repeat motions from the same filer.

The comparison is deterministic and explainable: the content words of what each
filing asks for, compared with Jaccard overlap, plus a bonus when both filings
call themselves the same thing. The note links to the earlier filing so the
judge can check it directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STOP = set("""
a an the and or but if to of in on at for from by with without about into over under
i me my mine we our you your he him his she her they them their it its this that these those
is am are was were be been being have has had do does did will would can could should may might must
please court judge honor ask asking request requesting requests want would like need order grant
that so not no as also than then there here who whom which what when where why how all any
filer asks states says motion petition
""".split())


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z'-]+", text.lower())
    return {w.rstrip("s") for w in words if w not in STOP and len(w) > 2}


def similarity(a: str, b: str) -> float:
    wa, wb = content_words(a), content_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


@dataclass
class PriorFiling:
    id: int
    filed_on: str  # ISO date
    filing_type: str
    request_text: str


@dataclass
class RepeatMatch:
    prior_id: int
    prior_filed_on: str
    score: float


THRESHOLD = 0.4


def find_repeats(request_text: str, filing_type: str, priors: list[PriorFiling]) -> list[RepeatMatch]:
    matches = []
    for p in priors:
        score = similarity(request_text, p.request_text)
        if filing_type and p.filing_type and filing_type.lower() == p.filing_type.lower():
            score += 0.15
        if score >= THRESHOLD:
            matches.append(RepeatMatch(p.id, p.filed_on, round(min(score, 1.0), 2)))
    return sorted(matches, key=lambda m: m.prior_filed_on)


def describe(matches: list[RepeatMatch], fmt_date) -> str | None:
    if not matches:
        return None
    first = matches[0]
    if len(matches) == 1:
        return f"This repeats the {fmt_date(first.prior_filed_on)} request."
    n = len(matches) + 1
    return f"This is the {_ordinal(n)} similar request from this filer since {fmt_date(first.prior_filed_on)}."


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
