"""Greenhouse public Job Board API: GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true (no key)."""
from __future__ import annotations

import httpx

from ..models import Job, employment_label
from ..textparse import parse_dt, parse_location, parse_salary_text, strip_html

URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch(client: httpx.Client, company: dict, cfg: dict) -> list[Job]:
    resp = client.get(URL.format(token=company["token"]), params={"content": "true"})
    resp.raise_for_status()
    return [to_job(j, company) for j in resp.json().get("jobs", [])]


def to_job(j: dict, company: dict) -> Job:
    loc = (j.get("location") or {}).get("name", "")
    city, state, country = parse_location(loc)
    desc = strip_html(j.get("content"))
    smin, smax = parse_salary_text(desc)
    return Job(
        source="greenhouse",
        source_id=str(j.get("id")),
        company=company.get("name") or company["token"],
        title=j.get("title", ""),
        url=j.get("absolute_url", ""),
        description=desc,
        location_raw=loc,
        city=city, state=state, country=country,
        is_remote=True if "remote" in loc.lower() else None,
        salary_min=smin, salary_max=smax,
        posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
        publisher="Greenhouse",
        employment_type=employment_label(None, j.get("title"), desc),
    )
