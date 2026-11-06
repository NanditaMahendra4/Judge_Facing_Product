"""Deterministic gap and conflict checks that run on every filing.

These don't depend on the model, so the most common flags (conflicting
dates or amounts, events with no date, no stated request) are reproducible
and auditable. Model-generated flags are merged in and de-duplicated.
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict

from .extract import DATE_RE, MONTHS, RELIEF_RE
from .ingest import Document
from .schema import Brief, Flag

# Nouns for events whose date matters. Two different dates on the same event noun is a conflict.
EVENT_NOUNS = [
    "incident", "assault", "attack", "fight", "accident", "eviction", "lockout", "notice",
    "hearing", "visit", "visitation", "exchange", "pickup", "payment", "move-out", "move-in",
    "lease", "threat", "break-in", "shutoff", "repair",
]
_NOUN_ALT = "|".join(re.escape(n) for n in EVENT_NOUNS)
_EVENT_RE = re.compile(r"\b(" + _NOUN_ALT + r")(s?)\b", re.IGNORECASE)

# Words that, right before an event noun, make it a specific numbered event or a different one.
_ORDINALS = {
    "first": "first", "initial": "first", "1st": "first",
    "second": "second", "2nd": "second",
    "third": "third", "3rd": "third",
    "fourth": "fourth", "4th": "fourth",
    "fifth": "fifth", "5th": "fifth",
}
# Qualifiers meaning "some other event": never merged with any other mention.
_OTHER_EVENT_WORDS = {
    "another", "other", "different", "separate", "next", "later", "last", "previous", "prior",
    "earlier", "former", "latter", "new", "final", "additional", "further", "similar",
    "subsequent", "following", "original", "unrelated", "recent", "more",
}
# Words that may sit between an ordinal and the noun ("second violent incident") but are not adjectives.
_NOT_ADJECTIVE = {
    "the", "a", "an", "this", "that", "my", "his", "her", "their", "our", "your", "its", "of", "on",
    "in", "at", "to", "and", "or", "for", "with", "by", "is", "was", "were", "are", "be", "two",
    "three", "four", "five", "six", "both", "few", "several", "many", "some", "any", "no", "all",
    "one",
}
# Sentence-level cues that the sentence is about a repeat or a different occasion.
_DIFFERENT_EVENT_CUE_RE = re.compile(
    r"\b(?:again|once more|similar(?:ly)?|same thing|repeated(?:ly)?|"
    r"(?:a|an|the|that|this) (?:different|another|other|separate|earlier|later|previous|prior|following) "
    r"(?:day|date|occasion|time|night|morning|evening|afternoon|week))\b",
    re.IGNORECASE,
)
# Sentences whose date is about moving/ending/approximating, not stating when something happened.
_SOFT_DATE_RE = re.compile(
    r"\b(?:on or about|around|approximately|approx|roughly|sometime|maybe|probably|i think|i believe|"
    r"not sure|originally|re-?scheduled?|reset|continued|continue|postponed?|moved|move|new date|instead|"
    r"ends?|expires?|expir\w*|due|deadline|until|through)\b",
    re.IGNORECASE,
)
# "without notice", "advance notice" etc. are not the event "notice".
_NOT_EVENT_RE = re.compile(
    r"\b(?:without|no|any|advance|prior|short|written|proper|further|until further|public|"
    r"adequate|reasonable|legal|formal)\s+notice\b",
    re.IGNORECASE,
)

# "the hearing about the incident": the second noun is only referred to, not the dated event.
_REFERENCE_BEFORE_RE = re.compile(
    r"\b(?:about|regarding|concerning|over|after|before|following|since|during|because of|due to|from|of)\s+"
    r"(?:(?:the|a|an|my|his|her|their|that|this)\s+)?(?:\w+\s+)?$",
    re.IGNORECASE,
)

_RELATIVE_TIME_RE = re.compile(r"\b(yesterday|last (?:week|month|year)|(?:a few|several|two|three) (?:days|weeks|months) (?:later|ago|after)|recently|the other day)\b", re.IGNORECASE)
_UNDATED_RE = re.compile(
    r"\b(second|third|fourth|fifth|2nd|3rd|4th|5th|another|next|later|last|other|different|additional)\s+"
    r"(?:(?!the\b|a\b|an\b|of\b|on\b|in\b|and\b|to\b)[a-z-]+\s+)?(" + _NOUN_ALT + r")s?\b",
    re.IGNORECASE,
)
# Sentences that talk about wanting/fearing/preventing an event are not accounts of one.
_NOT_AN_ACCOUNT_RE = re.compile(
    r"\b(?:if|whether|prevent|avoid|in case|unless|afraid|fear|fears|fearful|worried|worry|"
    r"hope|hopes|risk|chance|possibility|please)\b",
    re.IGNORECASE,
)

# --- dates ---------------------------------------------------------------------------------
_MON = (
    r"(?:(?i:January|February|March|April|June|July|August|September|October|November|December)"
    r"|May|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\b\.?"
)
_YEAR = r"(?:(?:19|20)\d{2}(?!\d)|'\d{2}(?!\d))"
_DAY = r"\d{1,2}(?:st|nd|rd|th)?\b"
_DATE_FORMS = re.compile(
    rf"(?P<mdy>\b(?P<m1>{_MON})\s+(?P<d1>{_DAY})(?:,?\s*(?P<y1>{_YEAR}))?)"
    rf"|(?P<dmy>\b(?P<d2>{_DAY})\s+(?:of\s+)?(?P<m2>{_MON})(?:,?\s*(?P<y2>{_YEAR}))?)"
    r"|(?P<iso>(?<![\d-])(?P<y3>\d{4})-(?P<m3>\d{2})-(?P<d3>\d{2})(?![\d-]))"
    r"|(?P<num>(?<![\d/])(?P<m4>\d{1,2})/(?P<d4>\d{1,2})/(?P<y4>\d{4}|\d{2})(?![\d/]))"
)
# "in March", "during May 2026", "early April": a month reference even without a day.
_MONTH_REF_RE = re.compile(
    rf"\b(?:in|during|since|of|early|late|mid-?|last|this|next|by|from)\s+{_MON}(?:\s+{_YEAR})?"
)
_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)")
_MONEY_NOUNS = ["rent", "deposit", "damages", "late fee", "fee", "owed", "balance", "repair", "support"]
_MONTH_NUM = {m.lower()[:3]: i for i, m in enumerate(MONTHS.split("|"), start=1)}
_MONTH_NUM["sep"] = 9

# Amount-comparison guards: only compare plain statements of one amount.
_MONTHLY_PHRASE_RE = re.compile(r"\b(?:a|per|each|every)\s+month\b|\bmonthly\b|/\s*mo(?:nth)?\b", re.IGNORECASE)
_NUMBER_WORD = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|few|couple|multiple|many)"
_NOT_COMPARABLE_AMOUNT_RE = re.compile(
    r"\b(?:total|totals|totaling|totalling|altogether|combined|in all|overall|sum|each|every|per|weekly|"
    r"biweekly|bi-weekly|daily|yearly|annual|annually|an? (?:week|day|year|hour)|hourly|twice|times|"
    r"installments?|interest|paid|paying|partial(?:ly)?|half|towards?|increased?|raised?|reduced?|"
    r"discount(?:ed)?|credit(?:ed)?|refund(?:ed)?|waived?|originally|previous(?:ly)?|prior|used to|"
    r"at least|up to|more than|less than|about|approximately|around|roughly|minimum|maximum|plus|million|thousand)\b"
    rf"|\bfor\s+(?:\w+\s+){{1,3}}?(?:months?|weeks?|days?|years?)\b"
    rf"|\b{_NUMBER_WORD}\s+(?:months?|weeks?|days?|years?)\b",
    re.IGNORECASE,
)


def _valid_ymd(y: int | None, m: int, d: int) -> bool:
    if not 1 <= m <= 12 or d < 1:
        return False
    if y is None:
        return d <= calendar.monthrange(2000, m)[1]
    return d <= calendar.monthrange(y, m)[1]


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.lstrip("'")
    y = int(raw)
    return y + 2000 if y < 100 else y


def _match_key(m: re.Match) -> tuple | None:
    """(year|None, month, day) for a _DATE_FORMS match, or None if it is not a real date."""
    g = m.groupdict()
    if g["mdy"]:
        mo, d, y = _MONTH_NUM[g["m1"].lower()[:3]], int(re.match(r"\d+", g["d1"]).group()), _year(g["y1"])
    elif g["dmy"]:
        mo, d, y = _MONTH_NUM[g["m2"].lower()[:3]], int(re.match(r"\d+", g["d2"]).group()), _year(g["y2"])
    elif g["iso"]:
        mo, d, y = int(g["m3"]), int(g["d3"]), int(g["y3"])
    else:
        mo, d, y = int(g["m4"]), int(g["d4"]), _year(g["y4"])
    return (y, mo, d) if _valid_ymd(y, mo, d) else None


def _parse_date(raw: str) -> tuple:
    """Normalize to a comparable key; year may be unknown (None)."""
    raw = raw.strip().rstrip(".")
    m = _DATE_FORMS.fullmatch(raw)
    if m:
        key = _match_key(m)
        if key:
            return key
    return (None, None, raw.lower())


def _find_dates(text: str) -> tuple[list[tuple[str, tuple]], bool]:
    """All real dates in a sentence as (as-written, key), plus whether anything date-like
    was seen that could not be understood (so callers can stay quiet instead of guessing)."""
    found: list[tuple[str, tuple]] = []
    unclear = False
    spans = []
    for m in _DATE_FORMS.finditer(text):
        spans.append(m.span())
        key = _match_key(m)
        if key is None:
            unclear = True
        else:
            found.append((m.group(0).strip().rstrip(".,"), key))
    for m in DATE_RE.finditer(text):
        if not any(s <= m.start() and m.end() <= e for s, e in spans):
            unclear = True
    return found, unclear


# The sentence splitter can cut "Mar. 12, 2026" after the abbreviation; don't call that "undated".
_CUT_MONTH_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.[\"')\]]?\s*$")


def _has_date_reference(text: str) -> bool:
    return bool(_DATE_FORMS.search(text) or DATE_RE.search(text) or _MONTH_REF_RE.search(text)
                or _CUT_MONTH_RE.search(text))


def _same_date(a: tuple, b: tuple) -> bool:
    if a[1:] != b[1:]:
        return False
    return a[0] is None or b[0] is None or a[0] == b[0]


def _pg(sid: str) -> str:
    return "p. " + sid.split(".")[0][1:]


def _pages(sids: list[str]) -> str:
    nums: list[str] = []
    for sid in sids:
        n = sid.split(".")[0][1:]
        if n not in nums:
            nums.append(n)
    return ("p. " if len(nums) == 1 else "pp. ") + ", ".join(nums)


def _qualifier(text: str, start: int) -> str | None:
    """'' for a plain mention, an ordinal ('second') for a numbered event, None for 'another/different…'."""
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9'-]+", text[:start])[-2:]]
    for i in (1, 2):  # directly before the noun, or one adjective away
        if len(words) < i:
            break
        w = words[-i]
        if i == 2 and words[-1] in _NOT_ADJECTIVE:
            break
        if w in _ORDINALS:
            return _ORDINALS[w]
        if w in _OTHER_EVENT_WORDS:
            return None
    return ""


def _nearest(text: str, spans: list[tuple[int, int, object]], target: tuple[int, int]):
    """The item whose span is closest to target (earlier one wins ties)."""
    def dist(item):
        s, e, _ = item
        if e <= target[0]:
            return target[0] - e
        if s >= target[1]:
            return s - target[1]
        return 0
    return min(spans, key=lambda it: (dist(it), it[0])) if spans else None


def _sorted_unique(sids: list[str], doc: Document) -> list[str]:
    order = {s.id: i for i, s in enumerate(doc.sentences)}
    return sorted(set(sids), key=lambda x: order.get(x, 10**9))


def date_conflicts(doc: Document) -> list[Flag]:
    # event key -> list of (date key, as written, sentence id)
    by_event: dict[str, list[tuple[tuple, str, str]]] = defaultdict(list)
    for s in doc.sentences:
        dates, unclear = _find_dates(s.text)
        distinct_dates = []
        for raw, key in dates:
            if not any(key == k for _, k in distinct_dates):
                distinct_dates.append((raw, key))
        if unclear or len(distinct_dates) != 1:
            continue
        if _SOFT_DATE_RE.search(s.text) or _DIFFERENT_EVENT_CUE_RE.search(s.text):
            continue
        masked = _NOT_EVENT_RE.sub(lambda m: " " * len(m.group(0)), s.text)
        mentions = [(m.start(), m.end(), m) for m in _EVENT_RE.finditer(masked)
                    if not _REFERENCE_BEFORE_RE.search(masked[:m.start()])]
        dm = next(_DATE_FORMS.finditer(s.text))
        pick = _nearest(s.text, mentions, dm.span())
        if pick is None:
            continue
        m = pick[2]
        if m.group(2):  # plural ("the incidents"): more than one event
            continue
        qual = _qualifier(s.text, m.start())
        if qual is None:  # "another incident", "a different incident": cannot be matched up
            continue
        noun = m.group(1).lower()
        key = f"{qual} {noun}" if qual else noun
        raw, dkey = distinct_dates[0]
        by_event[key].append((dkey, raw, s.id))

    flags = []
    for ev, mentions in by_event.items():
        groups: list[list[tuple[tuple, str, str]]] = []
        for men in mentions:
            for g in groups:
                if _same_date(men[0], g[0][0]):
                    g.append(men)
                    break
            else:
                groups.append([men])
        if len(groups) >= 2:
            parts = " vs. ".join(f"{g[0][1]}, {_pages([x[2] for x in g])}" for g in groups)
            cites = _sorted_unique([x[2] for g in groups for x in g], doc)
            flags.append(Flag(
                kind="conflict", source="rule",
                text=f"Different dates given for the {ev} ({parts}).",
                cites=cites,
            ))
    return flags


def undated_events(doc: Document) -> list[Flag]:
    # Numbered events that are dated somewhere in the filing are not "undated".
    dated_keys: set[str] = set()
    for s in doc.sentences:
        if _has_date_reference(s.text):
            for m in _UNDATED_RE.finditer(s.text):
                dated_keys.add(f"{m.group(1).lower()} {m.group(2).lower()}")
    flags = []
    for s in doc.sentences:
        if _has_date_reference(s.text):
            continue
        m = _UNDATED_RE.search(s.text)
        if m:
            what = f"{m.group(1).lower()} {m.group(2).lower()}"
            if what in dated_keys or _NOT_AN_ACCOUNT_RE.search(s.text) or RELIEF_RE.search(s.text):
                continue
            flags.append(Flag(kind="gap", source="rule", text=f"No date stated for the {what} ({_pg(s.id)}).", cites=[s.id]))
        else:
            r = _RELATIVE_TIME_RE.search(s.text)
            if r:
                flags.append(Flag(kind="gap", source="rule", text=f"Only a relative time (“{r.group(1)}”) is given; no calendar date ({_pg(s.id)}).", cites=[s.id]))
    return flags


def amount_conflicts(doc: Document) -> list[Flag]:
    by_noun: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for s in doc.sentences:
        found = list(_MONEY_RE.finditer(s.text))
        values = {f"{float(m.group(1).replace(',', '')):.2f}" for m in found}
        if len(values) != 1:
            continue
        if _NOT_COMPARABLE_AMOUNT_RE.search(_MONTHLY_PHRASE_RE.sub(" ", s.text)):
            continue
        low = s.text.lower()
        nouns = []
        for noun in _MONEY_NOUNS:
            for nm in re.finditer(rf"\b{re.escape(noun)}\b", low):
                nouns.append((nm.start(), nm.end(), noun))
        pick = _nearest(low, nouns, found[0].span())
        if pick is None:
            continue
        by_noun[pick[2]].setdefault(next(iter(values)), []).append(s.id)
    flags = []
    for noun, amts in by_noun.items():
        if len(amts) >= 2:
            parts = " vs. ".join(f"${a}, {_pages(sids)}" for a, sids in amts.items())
            cites = _sorted_unique([sid for sids in amts.values() for sid in sids], doc)
            flags.append(Flag(kind="conflict", source="rule", text=f"Different amounts given for {noun} ({parts}).", cites=cites))
    return flags


def missing_request(doc: Document, brief: Brief) -> list[Flag]:
    if brief.asking_for or any(RELIEF_RE.search(s.text) for s in doc.sentences):
        return []
    last = doc.sentences[-1].id if doc.sentences else "p1.s1"
    return [Flag(kind="gap", source="rule", text="No specific request to the court could be found in the filing.", cites=[last])]


def _dedupe(flags: list[Flag]) -> list[Flag]:
    seen_text: set[str] = set()
    seen_cites: set[tuple] = set()
    out = []
    for f in flags:
        key = frozenset(f.cites)
        if f.text in seen_text or (key and (f.kind, key) in seen_cites):
            continue
        seen_text.add(f.text)
        seen_cites.add((f.kind, key))
        out.append(f)
    return out


def run_all(doc: Document, brief: Brief) -> list[Flag]:
    return _dedupe(date_conflicts(doc) + amount_conflicts(doc) + undated_events(doc) + missing_request(doc, brief))


def merge_flags(rule_flags: list[Flag], model_flags: list[Flag]) -> list[Flag]:
    """Rule flags first; drop model flags that cite exactly the same sentences as a rule flag."""
    seen = {frozenset(f.cites) for f in rule_flags if f.cites}
    texts = {f.text.strip().lower() for f in rule_flags}
    merged = list(rule_flags)
    for f in model_flags:
        key = frozenset(f.cites)
        norm = f.text.strip().lower()
        if (key and key in seen) or norm in texts:
            continue
        if key:
            seen.add(key)
        texts.add(norm)
        merged.append(f)
    return merged
