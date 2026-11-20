"""Lever public postings API: GET api.lever.co/v0/postings/{token}?mode=json (no key)."""
from __future__ import annotations

import httpx

from ..models import Job, employment_label
from ..textparse import annualize, parse_dt, parse_location, parse_salary_text

URL = "https://api.lever.co/v0/postings/{token}"


def fetch(client: httpx.Client, company: dict, cfg: dict) -> list[Job]:
    resp = client.get(URL.format(token=company["token"]), params={"mode": "json"})
    resp.raise_for_status()
    return [to_job(p, company) for p in resp.json()]


def to_job(p: dict, company: dict) -> Job:
    cats = p.get("categories") or {}
    loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
    city, state, country = parse_location(loc)
    desc = "\n\n".join(x for x in [
        p.get("descriptionPlain"),
        *[f"{l.get('text', '')}\n{l.get('content', '')}" for l in p.get("lists") or []],
        p.get("additionalPlain"),
    ] if x)
    sr = p.get("salaryRange") or {}
    interval = (sr.get("interval") or "").replace("per-", "").upper()
    smin, smax = annualize(sr.get("min"), interval or "YEAR"), annualize(sr.get("max"), interval or "YEAR")
    if smin is None and smax is None:
        smin, smax = parse_salary_text(desc)
    wt = (p.get("workplaceType") or "").lower()
    return Job(
        source="lever",
        source_id=str(p.get("id")),
        company=company.get("name") or company["token"],
        title=p.get("text", ""),
        url=p.get("hostedUrl", ""),
        description=desc,
        location_raw=loc,
        city=city, state=state, country=country,
        is_remote=True if wt == "remote" else None,
        workplace_hint=wt or None,
        salary_min=smin, salary_max=smax,
        posted_at=parse_dt(p.get("createdAt")),
        publisher="Lever",
        employment_type=employment_label(cats.get("commitment"), p.get("text"), desc),
    )
