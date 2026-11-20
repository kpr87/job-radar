from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    source: str                     # jsearch, linkedin, dice, greenhouse, lever, ashby, workday
    source_id: str
    company: str
    title: str
    url: str
    description: str = ""
    location_raw: str = ""
    city: str | None = None
    state: str | None = None        # two-letter US code when known
    country: str | None = None      # "US" when known
    lat: float | None = None
    lon: float | None = None
    is_remote: bool | None = None   # what the source claims
    workplace_hint: str | None = None  # source field such as "hybrid" or "onsite"
    salary_min: float | None = None    # annual USD
    salary_max: float | None = None
    posted_at: datetime | None = None
    publisher: str | None = None
    employment_type: str | None = None  # Full-time, Contract, Contract (C2C), Contract (W2), Part-time
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity across sources so a syndicated posting is scored once."""
        place = "remote" if self.is_remote else (self.city or self.state or self.location_raw or "")
        basis = "|".join([_norm(self.company), _norm_title(self.title), _norm(place)])
        return hashlib.sha1(basis.encode()).hexdigest()[:16]


_SUFFIXES = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|the|group|holdings|plc|na|n\.a)\b\.?")


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    s = _SUFFIXES.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_EMP_FIELD = [   # a source's own employment field
    (r"corp[\s-]*to[\s-]*corp|\bc2c\b|third[\s_-]*party", "Contract (C2C)"),
    (r"\bw-?2\b", "Contract (W2)"),
    (r"\b1099\b|independent", "Contract (1099)"),
    (r"contract[\s-]*to[\s-]*hire|\bc2h\b|temp[\s-]*to[\s-]*perm", "Contract to hire"),
    (r"contract|temporary|\btemp\b", "Contract"),
    (r"full[\s_-]*time|permanent|direct hire", "Full-time"),
    (r"part[\s_-]*time", "Part-time"),
    (r"intern", "Internship"),
]
_NO_C2C = re.compile(r"\bno\s+(c2c|corp[\s-]*to[\s-]*corp|third[\s-]*part)|\bw-?2\s+only\b|\bc2c\s+(is\s+)?not\b|\bnot\s+(open to\s+)?c2c\b", re.I)
_EMP_TEXT = [    # only the specific contract kinds are read from free text; the bare word "contract" is too common
    (r"corp[\s-]*to[\s-]*corp|\bc2c\b", "Contract (C2C)"),
    (r"contract[\s-]*to[\s-]*hire|\bc2h\b|temp[\s-]*to[\s-]*perm", "Contract to hire"),
    (r"\bw-?2\s+(contract|hourly|only)|\bon\s+w-?2\b|\bcontract\b.{0,20}\bw-?2\b", "Contract (W2)"),
]


def employment_label(field: str | None, *texts: str | None) -> str | None:
    """Employment type from the source's own field, refined from the title and posting text.
    "Contract" from the field becomes Contract (C2C) / (W2) / to hire when the text says which."""
    label = None
    for pattern, name in _EMP_FIELD:
        if field and re.search(pattern, str(field), re.I):
            label = name
            break
    if label not in (None, "Contract", "Contract (C2C)"):
        return label
    text = " ".join(str(t)[:6000] for t in texts if t)
    if _NO_C2C.search(text):
        return "Contract (W2)"
    if label == "Contract (C2C)":
        return label
    for pattern, name in _EMP_TEXT:
        if re.search(pattern, text, re.I):
            return name
    if label is None and re.search(r"\(contract\)|\bcontract (role|position|opportunity|assignment|duration)\b|\b\d+\+?[\s-]*months?\s+contract\b", text, re.I):
        return "Contract"
    return label


def _norm_title(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)          # drop "(Remote)", "[Hybrid]"
    s = re.sub(r"\b(remote|hybrid|onsite|on-site)\b", " ", s)
    s = re.sub(r"\bsr\b\.?", "senior", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()
