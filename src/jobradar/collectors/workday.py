"""Workday career sites: the JSON endpoints the careers page itself calls.
List:   POST https://{host}/wday/cxs/{tenant}/{site}/jobs  {"searchText": ..., "limit": 20, "offset": 0}
Detail: GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}
Only postings whose title passes the title filter get a detail call, to keep requests low."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from ..models import Job, employment_label
from ..textparse import parse_location, parse_salary_text, strip_html


def fetch(client: httpx.Client, company: dict, cfg: dict) -> list[Job]:
    from ..filters import title_ok, window_hours  # local import avoids a cycle

    base = f"https://{company['host']}/wday/cxs/{company['tenant']}/{company['site']}"
    terms = cfg["sources"]["ats_boards"].get("workday_search_terms") or [""]
    max_pages = cfg["sources"]["ats_boards"].get("workday_max_pages", 5)   # 20 postings a page
    backfill = bool(company.get("_backfill"))      # company just added: read all open roles once
    seen: dict[str, dict] = {}
    for term in terms:
        for page in range(max_pages):
            resp = client.post(f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": term})
            resp.raise_for_status()
            data = resp.json()
            postings = data.get("jobPostings", [])
            for p in postings:
                if p.get("externalPath"):
                    seen.setdefault(p["externalPath"], p)
            if len(postings) < 20 or (page + 1) * 20 >= (data.get("total") or 0):
                break

    jobs = []
    for path, p in seen.items():
        ok, _ = title_ok(p.get("title", ""), cfg)
        if not ok:
            continue
        posted = posted_on(p.get("postedOn", ""))
        if not backfill and posted and posted < datetime.now(timezone.utc) - timedelta(hours=window_hours(cfg, "workday") + 24):
            continue
        d = client.get(f"{base}{path}").json().get("jobPostingInfo", {})
        jobs.append(to_job(p, d, company))
    return jobs


def posted_on(text: str) -> datetime | None:
    """Workday shows 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'."""
    now = datetime.now(timezone.utc)
    t = text.lower()
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*days?", t)
    return now - timedelta(days=int(m.group(1))) if m else None


def to_job(p: dict, d: dict, company: dict) -> Job:
    loc = d.get("location") or p.get("locationsText") or ""
    extra = d.get("additionalLocations") or []
    city, state, country = parse_location(loc)
    desc = strip_html(d.get("jobDescription"))
    smin, smax = parse_salary_text(desc)
    rt = (d.get("remoteType") or "").lower()
    hint = "remote" if "remote" in rt else ("hybrid" if "hybrid" in rt else ("onsite" if "site" in rt or "office" in rt else None))
    return Job(
        source="workday",
        source_id=d.get("jobReqId") or p.get("externalPath", ""),
        company=company.get("name") or company["tenant"],
        title=d.get("title") or p.get("title", ""),
        url=d.get("externalUrl") or f"https://{company['host']}/en-US/{company['site']}{p.get('externalPath', '')}",
        description=desc + ("\n\nOther locations: " + "; ".join(extra) if extra else ""),
        location_raw=loc,
        city=city, state=state, country=country,
        is_remote=True if hint == "remote" else None,
        workplace_hint=hint,
        salary_min=smin, salary_max=smax,
        posted_at=posted_on(p.get("postedOn", "")) or _iso(d.get("startDate")),
        publisher="Workday",
        employment_type=employment_label(d.get("timeType"), d.get("title") or p.get("title"), desc),
    )


def _iso(v):
    from ..textparse import parse_dt
    return parse_dt(v)
