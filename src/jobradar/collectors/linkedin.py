"""LinkedIn jobs through an Apify actor (default: curious_coder/linkedin-jobs-scraper, billed per result).

Flow: build LinkedIn job-search URLs -> start one actor run with a billing cap -> wait -> read its dataset.
The actor reads LinkedIn's public job search; no LinkedIn login or cookies are used."""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlencode

import httpx

from ..models import Job, employment_label
from ..textparse import parse_dt, parse_location, parse_salary_text, strip_html

log = logging.getLogger("jobradar")
API = "https://api.apify.com/v2"
WORKPLACE = {"remote": "2", "onsite_hybrid": "1,3", "any": ""}   # LinkedIn f_WT: 1 onsite, 2 remote, 3 hybrid


def search_urls(cfg: dict, lookback_hours: float) -> list[str]:
    li = cfg["sources"]["linkedin"]
    seconds = int(lookback_hours * 3600)
    urls = []
    for kw in li["keyword_groups"]:
        for s in li["searches"]:
            params = {"keywords": kw, "location": s["location"], "f_TPR": f"r{seconds}", "sortBy": "DD"}
            wt = WORKPLACE.get(s.get("workplace", "any"), "")
            if wt:
                params["f_WT"] = wt
            urls.append("https://www.linkedin.com/jobs/search/?" + urlencode(params))
    return urls


def fetch(client: httpx.Client, cfg: dict, lookback_hours: float) -> tuple[list[Job], list[str], int]:
    """Returns (jobs, errors, results billed)."""
    token = (os.environ.get("APIFY_TOKEN") or "").strip()
    if not token:
        return [], ["APIFY_TOKEN not set; LinkedIn skipped"], 0
    li = cfg["sources"]["linkedin"]
    headers = {"Authorization": f"Bearer {token}"}
    urls = search_urls(cfg, lookback_hours)
    run_input = {"urls": urls, "limitPerSource": li.get("max_jobs_per_search", 100), "scrapeCompany": False}
    cap = li.get("max_results_per_run", 800)
    try:
        r = client.post(f"{API}/acts/{li['actor']}/runs", params={"maxItems": cap}, json=run_input, headers=headers)
        if r.status_code in (401, 403):
            return [], [f"Apify rejected the token (HTTP {r.status_code}): {' '.join(r.text.split())[:200]}"], 0
        if r.status_code == 402:
            return [], [f"Apify account is out of credit (HTTP 402): {' '.join(r.text.split())[:200]}"], 0
        r.raise_for_status()
        run = r.json()["data"]
        log.info("linkedin    Apify run %s started with %d searches (cap %d results)", run["id"], len(urls), cap)

        deadline = time.monotonic() + li.get("max_wait_minutes", 20) * 60
        while run["status"] in ("READY", "RUNNING") and time.monotonic() < deadline:
            run = client.get(f"{API}/actor-runs/{run['id']}", params={"waitForFinish": 60}, headers=headers,
                             timeout=httpx.Timeout(90.0, connect=10.0)).json()["data"]
        errors = []
        if run["status"] in ("READY", "RUNNING"):
            client.post(f"{API}/actor-runs/{run['id']}/abort", headers=headers)
            errors.append(f"LinkedIn Apify run took longer than {li.get('max_wait_minutes', 20)} minutes; used partial results")
        elif run["status"] != "SUCCEEDED":
            errors.append(f"LinkedIn Apify run ended with status {run['status']}; used whatever it collected")

        items = client.get(f"{API}/datasets/{run['defaultDatasetId']}/items",
                           params={"clean": "true", "format": "json"}, headers=headers).json()
    except Exception as e:
        return [], [f"LinkedIn via Apify failed: {e}"], 0

    jobs = [to_job(it) for it in items if it.get("title") and (it.get("link") or it.get("applyUrl"))]
    log.info("linkedin    %d jobs returned", len(jobs))
    if len(items) >= cap:
        errors.append(f"LinkedIn hit the {cap}-result cap for this run, so some postings were not read; "
                      "raise max_results_per_run or narrow keyword_groups in config.yaml")
    return jobs, errors, len(items)


def to_job(it: dict) -> Job:
    loc = it.get("location") or ""
    city, state, country = parse_location(loc)
    wps = it.get("workplaceTypes") or []
    wps = [wps] if isinstance(wps, str) else wps
    wp_text = " ".join(str(w) for w in wps).lower()
    hint = "remote" if "remote" in wp_text else ("hybrid" if "hybrid" in wp_text else ("onsite" if "site" in wp_text else None))
    desc = it.get("descriptionText") or strip_html(it.get("descriptionHtml"))
    sal = it.get("salaryInfo") or []
    sal_text = " - ".join(sal) if isinstance(sal, list) else str(sal)
    smin, smax = parse_salary_text(sal_text) if sal_text else (None, None)
    if smin is None:
        smin, smax = parse_salary_text(desc)
    posted = parse_dt(it.get("postedAtTimestamp")) or _date_end(it.get("postedAt"))
    return Job(
        source="linkedin",
        source_id=str(it.get("id") or it.get("link")),
        company=it.get("companyName") or "",
        title=it.get("title") or "",
        url=it.get("link") or it.get("applyUrl") or "",
        description=desc,
        location_raw=loc,
        city=city, state=state, country=country or ("US" if "united states" in loc.lower() else None),
        is_remote=True if (hint == "remote" or it.get("workRemoteAllowed") is True and hint is None) else None,
        workplace_hint=hint,
        salary_min=smin, salary_max=smax,
        posted_at=posted,
        publisher="LinkedIn",
        employment_type=employment_label(it.get("employmentType"), it.get("title"), desc),
        extra={"applicants": it.get("applicantsCount"), "seniority": it.get("seniorityLevel"),
               "employment_type": it.get("employmentType"), "apply_url": it.get("applyUrl")},
    )


def _date_end(v):
    """A date-only postedAt ('2026-10-31') is treated as the end of that day, so the freshness check is not too strict."""
    if not v:
        return None
    dt = parse_dt(f"{v}T23:59:00+00:00") if len(str(v)) == 10 else parse_dt(v)
    return dt
