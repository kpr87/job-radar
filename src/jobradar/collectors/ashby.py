"""Ashby public job board API: GET api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true (no key)."""
from __future__ import annotations

import httpx

from ..models import Job, employment_label
from ..textparse import parse_dt, parse_location, parse_salary_text, strip_html

URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def fetch(client: httpx.Client, company: dict, cfg: dict) -> list[Job]:
    resp = client.get(URL.format(token=company["token"]), params={"includeCompensation": "true"})
    resp.raise_for_status()
    return [to_job(j, company) for j in resp.json().get("jobs", []) if j.get("isListed", True)]


def to_job(j: dict, company: dict) -> Job:
    loc = j.get("location") or ""
    addr = ((j.get("address") or {}).get("postalAddress") or {})
    city, state, country = parse_location(loc)
    if addr.get("addressRegion"):
        _, st, _ = parse_location(addr["addressRegion"])
        state = state or st
        city = city or addr.get("addressLocality")
        country = country or ("US" if state else None)
    desc = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml"))
    comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
    smin, smax = parse_salary_text(comp) if comp else (None, None)
    if smin is None:
        smin, smax = parse_salary_text(desc)
    wt = (j.get("workplaceType") or "").lower()
    return Job(
        source="ashby",
        source_id=str(j.get("id") or j.get("jobUrl")),
        company=company.get("name") or company["token"],
        title=j.get("title", ""),
        url=j.get("jobUrl") or j.get("applyUrl") or "",
        description=desc,
        location_raw=loc,
        city=city, state=state, country=country,
        is_remote=True if j.get("isRemote") or wt == "remote" else None,
        workplace_hint=wt or None,
        salary_min=smin, salary_max=smax,
        posted_at=parse_dt(j.get("publishedAt")),
        publisher="Ashby",
        employment_type=employment_label(j.get("employmentType"), j.get("title"), desc),
    )
