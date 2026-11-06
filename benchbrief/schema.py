"""The Bench Brief data model.

Every factual line is a ``Claim`` that carries citations into the filing and a
verbatim quote. The guardrails reject any claim whose quote cannot be found in
the sentences it cites.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Claim(BaseModel):
    text: str = Field(description="One neutral line restating what the filing says, attributed to the filer.")
    cites: list[str] = Field(description="Sentence ids from the filing, e.g. ['p2.s4'].")
    quote: str = Field(description="A short verbatim excerpt from the cited sentence(s) that supports the line.")


class EvidenceItem(BaseModel):
    label: str = Field(description="How the filing names the item, e.g. 'Exhibit A' or 'text messages'.")
    filed_by: str = Field(description="Who submitted it, as stated in the record.")
    claimed_to_show: str = Field(description="What the filer says it shows. Never whether it does.")
    cites: list[str]
    quote: str


class Flag(BaseModel):
    kind: Literal["conflict", "gap"]
    text: str = Field(description="Neutral description, e.g. 'Two different dates given for the incident (p. 2 vs. p. 4).'")
    cites: list[str]
    source: Literal["rule", "model"] = "model"


class Brief(BaseModel):
    """The structured extraction, in the fixed order the judge sees it."""

    filer: str = Field(description="Name of the person who filed this, as written.")
    filing_type: str = Field(description="What the filing calls itself, e.g. 'Motion to Continue'. 'Not stated' if absent.")
    what_happened: list[Claim]
    when: list[Claim]
    where: list[Claim]
    who: list[Claim]
    asking_for: list[Claim]
    evidence: list[EvidenceItem]
    flags: list[Flag]


SECTIONS: list[tuple[str, str]] = [
    ("what_happened", "What happened (filer's account)"),
    ("when", "When"),
    ("where", "Where"),
    ("who", "Who was involved"),
    ("asking_for", "What the filer is asking the court to do"),
]
