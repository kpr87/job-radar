"""Rule filters that run before any Claude call."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .geo import location_verdict
from .models import Job, _norm
from .textparse import detect_work_model


@dataclass
class FilterResult:
    status: str            # candidate, filtered
    reason: str            # why it was filtered, or the location label when it passed
    work_model: str
    location_label: str
    location_unclear: bool = False
    salary_note: str = ""
    skills: list[str] = field(default_factory=list)


_REQUIRED_WORDS = re.compile(r"\b(required|require|requires|must|mandatory|minimum|essential|need to have|needs to have)\b", re.I)
_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _term_pattern(term: str) -> re.Pattern:
    """'event-driven' also matches 'event driven'; 'REST API' also matches 'REST APIs'."""
    if term not in _PATTERN_CACHE:
        alts = []
        for name in (t.strip() for t in term.split("|")):
            body = re.escape(name).replace(r"\ ", r"[\s-]?").replace(r"\-", r"[\s-]?")
            start = r"\b" if name[:1].isalnum() else ""
            end = r"s?\b" if name[-1:].isalnum() else ""
            alts.append(start + body + end)
        _PATTERN_CACHE[term] = re.compile("|".join(alts), re.I)
    return _PATTERN_CACHE[term]


def skills_in(text: str, terms: list[str]) -> list[str]:
    return [t.split("|")[0].strip() for t in terms if _term_pattern(t).search(text)]


def required_blockers(text: str, terms: list[str], own_skills: list[str] | None = None) -> list[str]:
    """Blocking terms that appear in the same sentence as a 'required' word.
    A sentence that also names one of the candidate's own core skills is an either/or list
    ("MuleSoft, Boomi or Workato required") and does not block."""
    hits = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text or ""):
        if not _REQUIRED_WORDS.search(sentence):
            continue
        found = [t for t in terms if _term_pattern(t).search(sentence) and t not in hits]
        if found and own_skills and any(_term_pattern(s).search(sentence) for s in own_skills):
            continue
        hits += found
    return hits


def skills_check(job: Job, cfg: dict) -> tuple[bool, str, list[str]]:
    sg = cfg.get("skills_gate") or {}
    if not sg.get("enabled"):
        return True, "", []
    text = f"{job.title}\n{job.description}"
    blockers = required_blockers(job.description, sg.get("drop_if_required") or [], sg.get("core") or [])
    if blockers:
        return False, "requires " + ", ".join(blockers), []
    core = skills_in(text, sg.get("core") or [])
    support = skills_in(text, sg.get("supporting") or [])
    matched = core + support
    # Dice's search returns a summary, not the whole posting, so its bar for "too short to check" is higher.
    short = (cfg["sources"].get("dice") or {}).get("snippet_chars", 400) if job.source == "dice" \
        else sg.get("short_description_chars", 80)
    if len((job.description or "").strip()) < short:
        return True, "description too short to check", matched   # a snippet, not the posting: let Claude judge
    if len(core) < sg.get("min_core", 1) or len(matched) < sg.get("min_total", 2):
        found = ", ".join(matched) or "none"
        return False, f"skills check: too few of your skills (found {found})", matched
    return True, "", matched


def title_ok(title: str, cfg: dict) -> tuple[bool, str]:
    t = title or ""
    if any(re.search(p, t, re.I) for p in cfg["titles"]["exclude"]):
        return False, "title excluded"
    if not any(re.search(p, t, re.I) for p in cfg["titles"]["include"]):
        return False, "title not a target role"
    return True, ""


def salary_check(job: Job, cfg: dict) -> tuple[bool, str]:
    """Pay is a filter only when salary.min_annual_usd is set; otherwise it is just shown in the digest."""
    floor = (cfg.get("salary") or {}).get("min_annual_usd")
    top = job.salary_max or job.salary_min
    if top is None:
        return (cfg["salary"].get("keep_when_missing", True) if floor else True), "salary not listed"
    if floor and top < floor:
        return False, f"pay tops out at ${top:,.0f}"
    lo = job.salary_min or top
    note = f"${lo:,.0f} to ${top:,.0f}" if lo != top else f"${top:,.0f}"
    if floor and job.salary_min and job.salary_min < floor:
        note += f" (low end below ${floor:,.0f})"
    return True, note


ATS_SOURCES = {"greenhouse", "lever", "ashby", "workday"}


def window_hours(cfg: dict, source: str | None = None) -> int:
    """Look-back window. Each source has its own, so one that was down catches up when it comes back."""
    fr = cfg["freshness"]
    by_source = fr.get("effective_by_source") or {}
    key = "ats" if source in ATS_SOURCES else source
    return by_source.get(key) or fr.get("effective_hours") or fr["max_age_hours"]


def fresh_ok(job: Job, cfg: dict, now: datetime | None = None) -> bool:
    if job.posted_at is None or job.extra.get("backfill"):
        return True      # backfill: a company just added to companies.yaml shows all its open roles once
    now = now or datetime.now(timezone.utc)
    return job.posted_at >= now - timedelta(hours=window_hours(cfg, job.source))


def apply_filters(job: Job, cfg: dict, feedback: dict, now: datetime | None = None) -> FilterResult:
    wm = detect_work_model(job.title, job.location_raw, job.description, job.is_remote, job.workplace_hint)

    def fail(reason: str, label: str = "") -> FilterResult:
        return FilterResult("filtered", reason, wm, label)

    blocked = {_norm(c) for c in feedback.get("blocked_companies") or []}
    if _norm(job.company) in blocked:
        return fail("company blocked in feedback.yaml")
    ok, why = title_ok(job.title, cfg)
    if not ok:
        return fail(why)
    if not fresh_ok(job, cfg, now):
        return fail(f"older than {window_hours(cfg, job.source)} hours")
    verdict, detail = location_verdict(job, wm, cfg)
    if verdict == "fail":
        return fail(detail)
    wanted = [w.lower() for w in (cfg.get("employment") or {}).get("exclude") or []]
    if job.employment_type and any(w in job.employment_type.lower() for w in wanted):
        return fail(f"employment type not wanted ({job.employment_type})", detail)
    ok, note = salary_check(job, cfg)
    if not ok:
        return fail(note, detail)
    ok, why, skills = skills_check(job, cfg)
    if not ok:
        r = fail(why, detail)
        r.skills = skills
        return r
    return FilterResult("candidate", detail, wm, detail, location_unclear=(verdict == "unclear"),
                        salary_note=note, skills=skills)
