"""JSearch (OpenWeb Ninja / RapidAPI): live Google for Jobs results covering LinkedIn, Indeed,
Glassdoor, ZipRecruiter and company career pages."""
from __future__ import annotations

import logging
import os

import httpx

from ..models import Job, employment_label
from ..textparse import annualize, parse_dt, parse_location, parse_salary_text, state_code

log = logging.getLogger("jobradar")

ENDPOINTS = {
    "openwebninja": ("https://api.openwebninja.com/jsearch/search", "x-api-key", None),
    "rapidapi": ("https://jsearch.p.rapidapi.com/search", "X-RapidAPI-Key", "jsearch.p.rapidapi.com"),
}


def date_posted_for(hours: float) -> str:
    """JSearch accepts all, today, 3days, week, month."""
    if hours < 24:        # "today" can mean since midnight, so a full day asks for 3days and the filter trims it
        return "today"
    if hours <= 72:
        return "3days"
    return "week" if hours <= 168 else "month"


def build_requests(cfg: dict, lookback_hours: float, backfill: bool) -> list[dict]:
    js = cfg["sources"]["jsearch"]
    reqs = []
    for q in js["queries"]:
        for loc in js["locations"]:
            text = f"{q} {loc['where']}".strip()
            params = {
                "query": text + (" remote" if loc.get("remote_only") else ""),
                "page": 1,
                "num_pages": js.get("first_run_pages", 3) if backfill else js.get("pages_per_search", 1),
                "country": "us",
                "date_posted": date_posted_for(lookback_hours),
            }
            if loc.get("remote_only"):
                params["work_from_home"] = "true"
            reqs.append({"label": f"{q} | {loc['label']}", "params": params})
    return reqs


def fetch(client: httpx.Client, cfg: dict, lookback_hours: float, backfill: bool) -> tuple[list[Job], list[str], int, int]:
    raw = os.environ.get("JSEARCH_API_KEY") or ""
    key = raw.strip().strip('"').strip("'")
    if key.lower().startswith("x-api-key:"):
        key = key.split(":", 1)[1].strip()
    if not key:
        return [], ["JSEARCH_API_KEY not set; JSearch skipped"], 0, 0
    if key != raw:
        log.info("JSearch key had extra spaces, quotes or a label around it; cleaned before use")
    log.info("JSearch key length %d (the value itself is never logged)", len(key))
    url, header, host = ENDPOINTS[cfg["sources"]["jsearch"].get("endpoint", "openwebninja")]
    headers = {header: key}
    if host:
        headers["X-RapidAPI-Host"] = host
    jobs, errors, calls, ok = [], [], 0, 0
    for r in build_requests(cfg, lookback_hours, backfill):
        try:
            resp = client.get(url, params=r["params"], headers=headers)
            calls += r["params"]["num_pages"]
            if resp.status_code == 429:
                errors.append("JSearch quota or rate limit reached (HTTP 429); stopping JSearch for this run")
                break
            if resp.status_code in (401, 403):
                detail = " ".join(resp.text.split())[:200] or "no message"
                which = cfg["sources"]["jsearch"].get("endpoint", "openwebninja")
                errors.append(f"JSearch rejected the request (HTTP {resp.status_code}, endpoint={which}). "
                              f"JSearch says: {detail}. Check JSEARCH_API_KEY, your JSearch plan, and that "
                              "'endpoint' in config.yaml matches where the key came from (openwebninja or rapidapi)")
                break
            resp.raise_for_status()
            ok += 1
            data = resp.json().get("data") or []
            jobs.extend(to_job(d) for d in data)
            log.info("jsearch     %-55s %d jobs", r["label"], len(data))
        except Exception as e:
            errors.append(f"JSearch '{r['label']}': {e}")
    return jobs, errors, calls, ok


def to_job(d: dict) -> Job:
    city, state, country = d.get("job_city"), state_code(d.get("job_state")), None
    loc_raw = d.get("job_location") or ", ".join(x for x in [d.get("job_city"), d.get("job_state")] if x)
    if not state:
        pc, ps, country = parse_location(loc_raw)
        city, state = city or pc, ps
    cc = (d.get("job_country") or "").upper()
    country = "US" if cc in {"US", "USA"} else ("NON_US" if cc else (country or ("US" if state else None)))

    period = d.get("job_salary_period")
    smin, smax = annualize(d.get("job_min_salary"), period), annualize(d.get("job_max_salary"), period)
    if smin is None and smax is None:
        smin, smax = parse_salary_text(d.get("job_description"))

    return Job(
        source="jsearch",
        source_id=str(d.get("job_id", "")),
        company=d.get("employer_name") or "",
        title=d.get("job_title") or "",
        url=d.get("job_apply_link") or d.get("job_google_link") or "",
        description=d.get("job_description") or "",
        location_raw=loc_raw or "",
        city=city,
        state=state,
        country=country,
        lat=d.get("job_latitude"),
        lon=d.get("job_longitude"),
        is_remote=d.get("job_is_remote"),
        salary_min=smin,
        salary_max=smax,
        posted_at=parse_dt(d.get("job_posted_at_datetime_utc") or d.get("job_posted_at_timestamp")),
        publisher=d.get("job_publisher"),
        employment_type=employment_label(d.get("job_employment_type") or " ".join(d.get("job_employment_types") or []),
                                         d.get("job_title"), d.get("job_description")),
    )
