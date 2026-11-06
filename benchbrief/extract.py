"""Extractors turn a Document into a draft Brief.

* ``ClaudeExtractor``: the production path, using the Anthropic SDK with
  structured outputs.
* ``RuleExtractor``: a deterministic, offline fallback. It quotes whole
  sentences, so it is crude but cannot invent anything. Use it for demos,
  tests, or air-gapped installs before a model is approved.

Every draft goes through ``guardrails.enforce`` before anyone sees it, whichever
extractor produced it.
"""
from __future__ import annotations

import os
import re
from typing import Protocol

from .ingest import Document, Sentence
from .schema import Brief, Claim, EvidenceItem

SYSTEM_PROMPT = """\
You prepare a Bench Brief: a one-page, strictly neutral organization of a \
self-represented litigant's filing, read by a judge before a hearing.

Your only job is to organize what the filing says. Rules:
1. Never recommend a ruling, assess credibility or merit, predict an outcome, \
or characterize a party (no "credible", "frivolous", "likely", "clearly", \
"admits", "claims without proof", and so on).
2. Attribute everything to the filer ("Filer states...", "Filer says..."). \
Do not present the filer's account as established fact, and do not soften or \
sharpen it.
3. Every Claim, EvidenceItem and Flag must cite sentence ids exactly as they \
appear in square brackets (for example "p2.s4"). Each `quote` must be copied \
verbatim from the cited sentence(s). If you cannot cite it, leave it out.
4. Put items in the order the events happened, where the filing makes that clear.
5. Flags: note only (a) conflicts inside the filing, such as two different \
dates or amounts for the same thing, and (b) gaps, meaning a fact the filing \
refers to but never states (for example "no date stated for the second \
incident"). Word flags as neutral questions of fact, citing every sentence \
involved. Do not flag legal insufficiency.
6. Evidence: list only items the filing says are attached or submitted, with \
what the filer says each item shows. Do not judge whether it shows that.
7. Use empty lists for sections the filing does not address. Do not pad them.
"""


class Extractor(Protocol):
    name: str

    def extract(self, doc: Document) -> Brief: ...


class ClaudeExtractor:
    """Structured extraction through the Anthropic SDK (``pip install anthropic``).

    Credentials come from the environment (``ANTHROPIC_API_KEY`` or an
    ``ant auth login`` profile). For court deployment, point ``base_url`` at
    your approved gateway, or swap in ``AnthropicBedrockMantle`` /
    ``AnthropicVertex`` / ``AnthropicFoundry`` so data stays in your cloud
    tenancy.
    """

    name = "claude"

    def __init__(self, model: str | None = None, effort: str = "high", client=None):
        if client is None:
            import anthropic  # deferred so the rest of the app runs without the SDK

            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("BENCHBRIEF_MODEL", "claude-opus-5-5")
        self.effort = effort

    def extract(self, doc: Document) -> Brief:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": self.effort},
            messages=[{
                "role": "user",
                "content": (
                    f"Filing title: {doc.title}\n\n"
                    "Filing text, with sentence ids in brackets:\n\n"
                    f"{doc.to_prompt_text()}"
                ),
            }],
            output_format=Brief,
            # If a safety classifier declines, retry on the server's
            # default fallback model inside the same call.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Model declined to process this filing; route to manual review.")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise RuntimeError("Extraction incomplete; route to manual review.")
        return response.parsed_output


# --------------------------------------------------------------------------- #
# Offline rule-based extractor
# --------------------------------------------------------------------------- #

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(
    rf"\b(?:(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,\s*\d{{4}})?|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})\b",
    re.IGNORECASE,
)
RELIEF_RE = re.compile(
    r"\b(I (?:am )?(?:also )?(?:ask|request|want|need|would like)|asking (?:the|this) court|(?:I am|I.m) asking for|"
    r"please (?:order|grant|give|let|stop|dismiss|continue|move|postpone)|"
    r"respectfully request|I pray|move[sd]? (?:the court|for)|wherefore)\b",
    re.IGNORECASE,
)
EVIDENCE_RE = re.compile(r"\b(exhibit\s+[A-Z0-9]+|attached|enclos(?:ed|ing)|photos?|receipts?|screenshots?)\b", re.IGNORECASE)
PLACE_RE = re.compile(
    r"\b(\d+\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct|Blvd)\b|"
    r"apartment|apt\.?|parking lot|my (?:house|home|unit)|the (?:store|school|office|courthouse))",
    re.IGNORECASE,
)
PERSON_RE = re.compile(
    r"\b(landlord|tenant|respondent|petitioner|plaintiff|defendant|my (?:ex|son|daughter|child|children|wife|husband|neighbor|boss)|"
    r"officer|manager|(?:Mr|Mrs|Ms|Dr)\.\s+[A-Z][a-z]+)\b",
    re.IGNORECASE,
)
CAPTION_RE = re.compile(r"\b(cause no|case no|in the .* court|county of|state of indiana)\b", re.IGNORECASE)
TITLE_RE = re.compile(r"^((?:emergency\s+)?(?:motion|petition|complaint|answer|response)\b[^.\n]{0,60}?)(?=\s+(?:my name|i |on |the )|$)", re.IGNORECASE)
FILER_RE = re.compile(r"\b(?i:my name is|I,|filed by|submitted by)\s+([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+)+)")


def _claim(s: Sentence, prefix: str = "Filer states: ") -> Claim:
    return Claim(text=prefix + s.text, cites=[s.id], quote=s.text)


class RuleExtractor:
    name = "rules"

    def extract(self, doc: Document) -> Brief:
        # Skip captions and all-caps headings: they are not part of the account.
        sents = [s for s in doc.sentences if not CAPTION_RE.search(s.text) and not s.text.isupper()]
        when, where, who, asking, evidence, happened = [], [], [], [], [], []
        for s in sents:
            t = s.text
            if RELIEF_RE.search(t):
                asking.append(_claim(s, "Filer asks: "))
                continue
            if EVIDENCE_RE.search(t):
                exhibits = re.findall(r"\bexhibit\s+[A-Z0-9]+\b", t, re.IGNORECASE)
                noun = re.search(r"\b(photos?|pictures?|receipts?|screenshots?|text messages|emails?|letters?|records?|logs?|reports?|lease)\b", t, re.IGNORECASE)
                labels = [e.title() for e in exhibits] or [(noun.group(1) if noun else EVIDENCE_RE.search(t).group(1)).capitalize()]
                for label in labels:
                    evidence.append(EvidenceItem(label=label, filed_by="Filer", claimed_to_show=t, cites=[s.id], quote=t))
                continue
            if DATE_RE.search(t):
                when.append(_claim(s))
            if PLACE_RE.search(t):
                where.append(_claim(s))
            if PERSON_RE.search(t):
                who.append(_claim(s))
            if len(t.split()) >= 6 and not t.endswith(":"):
                happened.append(_claim(s))

        full = "\n".join(s.text for s in doc.sentences)
        filer = FILER_RE.search(full)
        ftype = next((m for s in doc.sentences[:6] if (m := TITLE_RE.search(s.text))), None)
        return Brief(
            filer=filer.group(1) if filer else "Not stated",
            filing_type=ftype.group(1).strip().title() if ftype else "Not stated",
            what_happened=happened[:8],
            when=when[:6],
            where=where[:4],
            who=who[:6],
            asking_for=asking[:5],
            evidence=evidence[:10],
            flags=[],  # rule-based flags come from checks.py for every extractor
        )


def default_extractor() -> Extractor:
    choice = os.environ.get("BENCHBRIEF_EXTRACTOR", "auto")
    if choice == "rules":
        return RuleExtractor()
    if choice == "claude":
        return ClaudeExtractor()
    # auto: use Claude only when the SDK is installed and credentials are configured.
    has_creds = any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"))
    if has_creds:
        try:
            return ClaudeExtractor()
        except ImportError:
            pass
    return RuleExtractor()
