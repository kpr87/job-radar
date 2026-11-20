"""Dice.com through Dice's own public MCP server (https://mcp.dice.com/mcp, no key, no login).

Dice publishes one tool, search_jobs (reference: https://www.dice.com/about/mcp). This module speaks the
MCP "streamable HTTP" transport directly with httpx: initialize, then one tools/call per search.
Dice is where most corp-to-corp and other contract roles are posted, so each search asks for
full-time, contract and third-party postings (set in config.yaml under sources.dice)."""
from __future__ import annotations

import json
import logging
import time

import httpx

from ..models import Job, employment_label
from ..textparse import parse_dt, parse_location, parse_salary_text, strip_html

log = logging.getLogger("jobradar")
URL = "https://mcp.dice.com/mcp"
PROTOCOL = "2025-06-18"


class DiceError(RuntimeError):
    pass


def posted_date_for(hours: float) -> str:
    """Dice accepts ONE (1 day), THREE (3 days) or SEVEN (7 days)."""
    if hours <= 30:       # a day plus the overlap for late starts
        return "ONE"
    return "THREE" if hours <= 72 else "SEVEN"


class McpSession:
    """Just enough of an MCP client for one stateless tool: JSON-RPC over POST, JSON or SSE replies."""

    def __init__(self, client: httpx.Client, url: str = URL):
        self.client, self.url, self.session_id, self._id = client, url, None, 0

    def _post(self, payload: dict) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        for attempt in range(4):
            resp = self.client.post(self.url, json=payload, headers=headers, timeout=httpx.Timeout(60.0, connect=10.0))
            if resp.status_code != 429:
                break
            time.sleep(2 ** attempt)             # Dice asks for exponential backoff on HTTP 429
        if resp.status_code == 429:
            raise DiceError("Dice rate limit (HTTP 429) after 4 tries")
        resp.raise_for_status()
        self.session_id = resp.headers.get("mcp-session-id") or self.session_id
        if "id" not in payload:                  # a notification has no reply
            return None
        if "text/event-stream" in resp.headers.get("content-type", ""):
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if msg.get("id") == payload["id"]:
                        return msg
            raise DiceError("Dice sent an event stream with no answer in it")
        return resp.json()

    def call(self, method: str, params: dict) -> dict:
        self._id += 1
        msg = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}) or {}
        if msg.get("error"):
            raise DiceError(f"Dice MCP error: {msg['error'].get('message', msg['error'])}")
        return msg.get("result") or {}

    def open(self) -> None:
        self.call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                 "clientInfo": {"name": "job-radar", "version": "0.2"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def tool(self, name: str, arguments: dict) -> dict:
        result = self.call("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content") or [])
            raise DiceError(f"Dice {name} failed: {' '.join(text.split())[:200]}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for c in result.get("content") or []:
            if c.get("type") == "text":
                try:
                    data = json.loads(c["text"])
                    if isinstance(data, dict):
                        return data
                except ValueError:
                    continue
        raise DiceError("Dice answered without job data")


def build_searches(cfg: dict, lookback_hours: float) -> list[dict]:
    d = cfg["sources"]["dice"]
    out = []
    for kw in d["keywords"]:
        for s in d["searches"]:
            args = {"keyword": kw, "jobs_per_page": d.get("jobs_per_page", 50),
                    "posted_date": posted_date_for(lookback_hours),
                    "employment_types": d.get("employment_types") or ["FULLTIME", "CONTRACTS", "THIRD_PARTY"]}
            if s.get("workplace_types"):
                args["workplace_types"] = s["workplace_types"]
            if s.get("location"):
                args["location"] = s["location"]
                if s.get("radius_mi"):
                    args |= {"radius": s["radius_mi"], "radius_unit": "mi"}
            out.append({"label": f"{kw} | {s['label']}", "args": args})
    return out


def fetch(client: httpx.Client, cfg: dict, lookback_hours: float) -> tuple[list[Job], list[str], int]:
    """Returns (jobs, errors, searches that answered)."""
    d = cfg["sources"]["dice"]
    session = McpSession(client, d.get("url", URL))
    try:
        session.open()
    except Exception as e:
        return [], [f"Dice MCP server could not be reached: {e}"], 0
    jobs, errors, ok = [], [], 0
    pages = d.get("pages_per_search", 1)
    for s in build_searches(cfg, lookback_hours):
        try:
            found = 0
            for page in range(1, pages + 1):
                data = session.tool("search_jobs", s["args"] | {"page_number": page})
                rows = data.get("data") or []
                jobs.extend(to_job(r) for r in rows if r.get("title") and r.get("detailsPageUrl"))
                found += len(rows)
                if page >= ((data.get("meta") or {}).get("pageCount") or 1):
                    break
                time.sleep(d.get("pause_seconds", 0.5))
            ok += 1
            log.info("dice        %-55s %d jobs", s["label"][:55], found)
        except DiceError as e:
            errors.append(f"Dice '{s['label']}': {e}")
            if "429" in str(e):
                errors.append("Dice rate limit reached; stopping Dice for this run")
                break
        except Exception as e:
            errors.append(f"Dice '{s['label']}': {e}")
        time.sleep(d.get("pause_seconds", 0.5))
    return jobs, errors, ok


def to_job(r: dict) -> Job:
    loc = ((r.get("jobLocation") or {}).get("displayName") or "") if isinstance(r.get("jobLocation"), dict) \
        else str(r.get("jobLocation") or "")
    city, state, country = parse_location(loc)
    wps = r.get("workplaceTypes") or []
    wps = [wps] if isinstance(wps, str) else wps
    wp_text = " ".join(str(w) for w in wps).lower()
    # A posting tagged both Remote and Hybrid is treated as hybrid; the description then decides.
    hint = "hybrid" if "hybrid" in wp_text else ("remote" if "remote" in wp_text else ("onsite" if "site" in wp_text else None))
    remote = True if (hint == "remote" or (r.get("isRemote") is True and hint is None)) else None
    desc = strip_html(r.get("summary") or r.get("description") or "")
    pay = str(r.get("salary") or "")
    smin, smax = parse_salary_text(pay) if pay else (None, None)
    if smin is None:
        smin, smax = parse_salary_text(desc)
    if pay and "Pay:" not in desc:
        desc += f"\n\nPay (as listed on Dice): {pay}"
    emp_raw = str(r.get("employmentType") or "")
    return Job(
        source="dice",
        source_id=str(r.get("id") or r.get("detailsPageUrl")),
        company=r.get("companyName") or "",
        title=r.get("title") or "",
        url=r.get("detailsPageUrl") or "",
        description=desc,
        location_raw=loc,
        city=city, state=state, country=country or "US",     # Dice lists US jobs only
        is_remote=remote,
        workplace_hint=hint,
        salary_min=smin, salary_max=smax,
        posted_at=parse_dt(r.get("postedDate") or r.get("modifiedDate")),
        publisher="Dice",
        employment_type=employment_label(emp_raw, r.get("title"), desc) or (emp_raw.title() if emp_raw else None),
        extra={"easy_apply": r.get("easyApply"), "employment_raw": emp_raw},
    )
