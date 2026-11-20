from __future__ import annotations

import logging

import httpx

from ..models import Job
from . import ashby, dice, greenhouse, jsearch, lever, linkedin, workday

log = logging.getLogger("jobradar")

ATS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday}


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": "job-radar/0.1 (personal job search)"},
        follow_redirects=True,
    )


def collect_ats(client: httpx.Client, companies: list[dict], cfg: dict) -> tuple[list[Job], list[tuple[str, str]], list[str]]:
    """Returns (jobs, [(company, error)], [companies read successfully])."""
    jobs: list[Job] = []
    errors: list[tuple[str, str]] = []
    ok: list[str] = []
    for c in companies:
        name = c.get("name") or c.get("token") or c.get("tenant") or "?"
        mod = ATS.get(c.get("ats", ""))
        if not mod:
            errors.append((name, f"{name}: unknown ats {c.get('ats')!r}"))
            continue
        try:
            found = mod.fetch(client, c, cfg)
            if c.get("_backfill"):
                for j in found:
                    j.extra["backfill"] = True
            log.info("%-10s %-30s %d jobs", c["ats"], name, len(found))
            jobs.extend(found)
            ok.append(name)
        except Exception as e:  # one broken board must not stop the run
            errors.append((name, f"{name} ({c['ats']}): {e}"))
            log.warning("ATS fetch failed for %s: %s", name, e)
    return jobs, errors, ok
