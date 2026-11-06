"""Hard guardrails applied to every draft brief before display.

1. Citation integrity: every cited id must exist, and the quote must appear
   verbatim (whitespace and punctuation normalized) in the cited sentences.
   Anything that fails is dropped. "Nothing summarized without a citation."
2. Neutral language: lines that recommend, score credibility, predict, or
   characterize a party are dropped.

Dropped items are logged in the report so the pilot team can review what the
model tried to say, not shown to the judge.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .ingest import Document
from .schema import Brief, Claim, EvidenceItem, Flag

# Terms that signal evaluation or framing rather than organization. Matched as
# whole words on a normalized copy of the text (see _lint_norm), so "strong"
# does not fire inside "armstrong". "alleges"/"states" are neutral attribution
# and are deliberately absent; "allegedly" in the brief writer's voice is framing.
NON_NEUTRAL = [
    # credibility / honesty
    r"credib\w*", r"incredible", r"believ(?:able|ably)", r"unbelievabl[ey]", r"(?:un)?reliabl[ey]",
    r"(?:un)?truthful\w*", r"trustworth\w*", r"lying", r"liar", r"lied", r"dishonest\w*",
    r"disingenuous\w*", r"decei(?:t\w*|ve\w*)", r"decept\w*", r"mislead\w*", r"misleading\w*",
    r"fabricat\w*", r"fake\w*", r"bogus", r"sham", r"pretext\w*", r"contrived", r"bad faith",
    r"self-serving", r"exaggerat\w*", r"manipulat\w*", r"dubious", r"questionable", r"doubtful",
    r"implausibl[ey]", r"plausibl[ey]", r"suspicious\w*", r"unconvincing", r"convincing\w*",
    r"persuasive\w*", r"compelling\w*", r"baseless", r"unsupported", r"groundless",
    # merit / strength
    r"frivolous", r"meritless", r"(?:has|have|lacks?|without|no) merit", r"meritorious", r"vexatious",
    r"weak\w*", r"strong(?:er|est|ly)?", r"strength\w*",
    # framing of the filer's assertions
    r"allegedly", r"alleg\w* (?:falsely|without (?:any )?(?:evidence|proof|support|basis|merit))",
    r"purport\w*", r"conveniently", r"convenient\w*", r"apparent(?:ly)?", r"it appears?", r"appears? to\b",
    r"seems? to\b", r"seemed to\b", r"seemingly", r"evidently", r"supposed(?:ly)?", r"so-called", r"pretend\w*",
    r"fail(?:s|ed|ing)? to\b", r"failure to\b", r"merely", r"insists?",
    r"inconsistent (?:story|stories|account|version|statement)s?", r"changing (?:story|stories)",
    r"shifting (?:story|stories|account)s?", r"contradictory (?:story|stories|account|version|statement)s?",
    r"admit(?:s|ted|ting)?", r"admission\w*", r"confess\w*", r"conced(?:es|ed|ing)?",
    r"clearly", r"obviously", r"undoubtedly", r"unquestionabl[ey]", r"plainly", r"manifestly",
    r"patently", r"blatant\w*", r"egregious\w*", r"outrageous\w*", r"absurd\w*", r"ridiculous\w*",
    r"ludicrous\w*", r"troubling", r"disturbing",
    # characterizations of people
    r"victim\w*", r"perpetrator\w*", r"abuser\w*", r"abusive", r"offender\w*", r"culprit\w*",
    r"predator\w*", r"aggressor\w*", r"harasser\w*", r"guilty", r"innocent", r"deadbeat\w*",
    # advice, obligation, prediction, outcome
    r"should", r"must", r"ought", r"deserv\w*", r"recommend\w*", r"likely", r"unlikely", r"probabl[ey]",
    r"(?:will|would|could|may) (?:likely |probably )?(?:win|lose|prevail|succeed|fail)\w*",
    r"(?:winner|loser)s?", r"prevail\w*", r"warrants? (?:dismissal|denial|granting)",
    r"(?:court|judge) (?:will|would)\b",
    r"(?:motion|petition|request|case|claim|complaint) (?:will|would|can|could|may) (?:likely )?be (?:grant|den|dismiss)\w*",
]
_NON_NEUTRAL_RE = re.compile(r"(?<!\w)(?:" + "|".join(NON_NEUTRAL) + r")(?!\w)", re.IGNORECASE)

# Zero-width / invisible / bidi-control characters that can hide inside words.
_INVISIBLE_RE = re.compile("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufeff\ufe00-\ufe0f\uffa0]")
_QUOTE_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u02bc": "'", "\u2032": "'", "`": "'", "\u00b4": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
})
# Common Cyrillic/Greek look-alikes folded to Latin, so "cr\u0435dible" cannot dodge the lint.
_HOMOGLYPHS = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0445": "x", "\u0443": "y",
    "\u0456": "i", "\u0458": "j", "\u04bb": "h", "\u0455": "s", "\u043a": "k", "\u043c": "m", "\u0442": "t",
    "\u03bf": "o", "\u03b1": "a", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k", "\u03bd": "v", "\u03c1": "p",
    "\u03c5": "u", "\u0391": "a", "\u0392": "b", "\u0395": "e", "\u0399": "i", "\u039a": "k", "\u039c": "m",
    "\u039d": "n", "\u039f": "o", "\u03a1": "p", "\u03a4": "t", "\u03a7": "x", "\u0410": "a", "\u0412": "b",
    "\u0415": "e", "\u041a": "k", "\u041c": "m", "\u041d": "h", "\u041e": "o", "\u0420": "p", "\u0421": "c",
    "\u0422": "t", "\u0425": "x",
})

# A quote needs this much substance to count as support (unless it is a whole cited sentence).
MIN_QUOTE_WORDS = 3
MIN_QUOTE_CHARS = 12
_STOPWORDS = frozenset(
    "a an the i me my we us our you your he him his she her it its they them their this that these those "
    "is am are was were be been being do does did have has had of in on at to for from by with and or but "
    "as if so not no yes".split()
)


def _clean(s: str) -> str:
    """NFKC, drop invisible characters, unify quotes and dashes, lower-case."""
    s = unicodedata.normalize("NFKC", s or "")
    s = _INVISIBLE_RE.sub("", s)
    return s.translate(_QUOTE_MAP).lower()


def _norm(s: str) -> str:
    """Comparison form for quote matching: case, punctuation, whitespace, unicode tricks removed."""
    s = _clean(s)
    s = s.replace("'", "")  # don't / don't / dont compare equal
    s = re.sub(r"[^\w\s$%./]", " ", s)
    s = re.sub(r"(?<!\w)[./]|[./](?!\w)", " ", s)  # keep 3/2/2026 and 2.5; drop sentence punctuation
    return re.sub(r"\s+", " ", s).strip()


def _lint_norm(s: str) -> str:
    s = _clean(s).translate(_HOMOGLYPHS)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class GuardReport:
    dropped: list[dict] = field(default_factory=list)
    kept: int = 0

    def drop(self, section: str, item, reason: str) -> None:
        self.dropped.append({"section": section, "text": getattr(item, "text", None) or getattr(item, "label", ""), "reason": reason})


def _words_ok(q: str) -> bool:
    words = q.split()
    return (len(words) >= MIN_QUOTE_WORDS or len(q) >= MIN_QUOTE_CHARS) and any(w not in _STOPWORDS for w in words)


def _contains(haystack: str, needle: str) -> bool:
    """Whole-token containment on already-normalized strings."""
    return f" {needle} " in f" {haystack} "


_NEGATIONS = {"not", "never", "no", "nor", "didnt", "dont", "doesnt", "wasnt", "werent", "isnt", "arent",
               "cannot", "cant", "couldnt", "wouldnt", "wont", "havent", "hasnt", "hadnt", "neither", "deny", "denies", "denied"}


def _negated_everywhere(hay: str, q: str) -> bool:
    """True if every occurrence of q in hay is preceded (within 3 tokens) by a negation q itself lacks.

    Stops "I did not hit him" from being quoted as "hit him".
    """
    if any(t.replace("'", "") in _NEGATIONS for t in q.split()):
        return False
    toks, qt = hay.split(), q.split()
    starts = [i for i in range(len(toks) - len(qt) + 1) if toks[i:i + len(qt)] == qt]
    if not starts:
        return False
    return all(any(t.replace("'", "") in _NEGATIONS for t in toks[max(0, i - 3):i]) for i in starts)


def _check_cites(doc: Document, cites: list[str], quote: str | None) -> str | None:
    if not cites:
        return "no citation"
    missing = [c for c in cites if doc.get(c) is None]
    if missing:
        return f"unknown sentence id(s): {', '.join(missing)}"
    if quote is not None:
        q = _norm(quote)
        if not q:
            return "quote not found verbatim in cited sentence(s)"
        # Put cited sentences in document order and group them into runs of
        # directly adjacent sentences. A quote may span a sentence boundary,
        # but never a gap: it cannot be stitched from non-adjacent sentences.
        position = {s.id: i for i, s in enumerate(doc.sentences)}
        ordered = sorted(set(cites), key=lambda c: position.get(c, 0))
        runs: list[list[str]] = []
        for c in ordered:
            if runs and position.get(c, 0) == position.get(runs[-1][-1], 0) + 1:
                runs[-1].append(c)
            else:
                runs.append([c])
        hays = [_norm(" ".join(doc.get(c).text for c in run)) for run in runs]
        hits = [h for h in hays if _contains(h, q)]
        if not hits:
            return "quote not found verbatim in cited sentence(s)"
        if not _words_ok(q):
            # A trivial fragment ("the", "I", "he hit") proves nothing. It only
            # counts if it is a whole cited sentence, or the sentence itself is
            # that short.
            ok = False
            for c in ordered:
                sn = _norm(doc.get(c).text)
                if sn == q or (not _words_ok(sn) and _contains(sn, q)):
                    ok = True
                    break
            if not ok:
                return "quote too short to support the line"
        if all(_negated_everywhere(h, q) for h in hits):
            return "quote omits a negation that precedes it in the source"
    return None


def _violations(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", m.group(0)) for m in _NON_NEUTRAL_RE.finditer(_lint_norm(text))]


def _neutral_problem(*texts: str) -> str | None:
    for t in texts:
        v = _violations(t or "")
        if v:
            return f"non-neutral wording: '{v[0]}'"
    return None


def _neutral_problem_outside_quote(text: str, quote: str) -> str | None:
    """Allow evaluative words only if they come from the filer's own words.

    Every evaluative match must appear in the claim's quote; one allowed word
    does not excuse a second one that is not in the filer's words.
    """
    allowed = set(_violations(quote or ""))
    for v in _violations(text or ""):
        if v not in allowed:
            return f"non-neutral wording: '{v}'"
    return None


def enforce(brief: Brief, doc: Document) -> tuple[Brief, GuardReport]:
    rep = GuardReport()

    def keep_claims(section: str, claims: list[Claim]) -> list[Claim]:
        out = []
        for c in claims:
            reason = _check_cites(doc, c.cites, c.quote)
            if not reason and not _norm(c.text):
                reason = "empty claim text"
            reason = reason or _neutral_problem_outside_quote(c.text, c.quote)
            if reason:
                rep.drop(section, c, reason)
            else:
                out.append(c)
        return out

    def keep_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
        out = []
        for e in items:
            reason = _check_cites(doc, e.cites, e.quote)
            for field_text in (e.claimed_to_show, e.label, e.filed_by):
                reason = reason or _neutral_problem_outside_quote(field_text, e.quote)
            if reason:
                rep.drop("evidence", e, reason)
            else:
                out.append(e)
        return out

    def keep_flags(flags: list[Flag]) -> list[Flag]:
        out = []
        for f in flags:
            reason = _check_cites(doc, f.cites, None) or _neutral_problem(f.text)
            if reason:
                rep.drop("flags", f, reason)
            else:
                out.append(f)
        return out

    filing_type = brief.filing_type
    problem = _neutral_problem(filing_type)
    if problem:
        rep.dropped.append({"section": "filing_type", "text": filing_type,
                            "reason": f"{problem} (replaced with 'Not stated')"})
        filing_type = "Not stated"

    cleaned = Brief(
        filer=brief.filer,
        filing_type=filing_type,
        what_happened=keep_claims("what_happened", brief.what_happened),
        when=keep_claims("when", brief.when),
        where=keep_claims("where", brief.where),
        who=keep_claims("who", brief.who),
        asking_for=keep_claims("asking_for", brief.asking_for),
        evidence=keep_evidence(brief.evidence),
        flags=keep_flags(brief.flags),
    )
    rep.kept = sum(len(getattr(cleaned, k)) for k in ("what_happened", "when", "where", "who", "asking_for", "evidence", "flags"))
    return cleaned, rep
