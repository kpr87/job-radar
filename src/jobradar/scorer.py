"""Two-pass Claude scoring. Pass 1 (small model) triages every candidate; pass 2 (larger model) gives
the full assessment for the ones worth it. Output is forced through a tool schema so it is always JSON."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("jobradar")

LOCATION_ENUM = ["local", "remote_us", "out_of_area", "unclear"]

TRIAGE_TOOL = {
    "name": "record_triage",
    "description": "Record a quick fit score for one job posting.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 10,
                      "description": "0 = irrelevant, 5 = adjacent, 7 = solid, 9+ = near-perfect fit"},
            "reason": {"type": "string", "description": "Under 25 words."},
            "location_check": {"type": "string", "enum": LOCATION_ENUM},
            "pay_below_floor": {"type": "boolean", "description": "True only if a minimum pay is given in the rules and the posting states pay whose top is below it. Otherwise false."},
        },
        "required": ["score", "reason", "location_check", "pay_below_floor"],
    },
}

ASSESS_TOOL = {
    "name": "record_assessment",
    "description": "Record the full assessment of one job posting against the candidate profile.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One line: what the role is, team and level."},
            "keyword_match_pct": {"type": "integer", "minimum": 0, "maximum": 100,
                                  "description": "Share of the posting's hard skills, tools and standards the candidate can truthfully show."},
            "hiring_manager_fit_pct": {"type": "integer", "minimum": 0, "maximum": 100,
                                       "description": "Honest chance a skeptical hiring manager sees a strong fit. Use the calibration examples."},
            "seniority_pay_pct": {"type": "integer", "minimum": 0, "maximum": 100,
                                  "description": "How well the role's level matches the level the profile targets. Use pay or hourly rate only as a hint of level."},
            "verdict": {"type": "string", "enum": ["apply", "stretch", "skip"]},
            "top_gaps": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "hard_gaps": {"type": "array", "items": {"type": "string"},
                          "description": "Only gaps from the profile's hard-gap list that the posting REQUIRES. Empty if none."},
            "pitch": {"type": "string", "description": "One sentence: the angle to lead with in the resume for this job."},
            "location_check": {"type": "string", "enum": LOCATION_ENUM},
            "work_model": {"type": "string", "enum": ["remote", "hybrid", "onsite"]},
            "pay_top_annual_usd": {"type": ["number", "null"], "description": "Top of stated base pay range, annualized, or null."},
        },
        "required": ["summary", "keyword_match_pct", "hiring_manager_fit_pct", "seniority_pay_pct", "verdict",
                     "top_gaps", "hard_gaps", "pitch", "location_check", "work_model", "pay_top_annual_usd"],
    },
}

INSTRUCTIONS = """You screen job postings for one specific candidate and score them honestly.
Rules:
- Judge only from the posting text and the candidate profile. Do not assume experience the profile does not show.
- Be skeptical: a keyword overlap is not a fit. Weigh what the role mainly does day to day.
- Location rules: {location_rule}
  location_check: local (hybrid/onsite in area), remote_us, out_of_area, or unclear.
- Postings often mislabel remote: read the description for in-office days and office cities.
- {pay_rule}
- Employment type: the profile says which kinds of employment the candidate wants (full-time, contract and so on).
  Never lower a score only because a posting comes from a staffing agency: score the underlying role,
  and name the agency and the contract terms (C2C / W2, length, hourly rate) in the reason or summary when stated.
  If a contract posting says "W2 only" or "no C2C", start the summary with "W2 only:"."""


def location_rule(cfg: dict | None) -> str:
    """The search area from config.yaml, written out for Claude."""
    loc = (cfg or {}).get("location") or {}
    models = loc.get("work_models") or ["remote", "hybrid", "onsite"]
    parts = []
    if "remote" in models:
        states = (loc.get("remote") or {}).get("allowed_residency_states") or []
        tail = f" unless they require living outside {', '.join(states)}" if states else ""
        parts.append(f"remote roles anywhere in the US are fine{tail}.")
    else:
        parts.append("remote roles are not wanted.")
    local = [m for m in models if m != "remote"]
    areas = []
    for a in loc.get("local_areas") or []:
        areas.append(f"within {a['radius_mi']} miles of {a['name']}" if "center" in a else f"anywhere in {a['name']}")
    if local and areas:
        parts.append(f"{' or '.join(m.capitalize() if i == 0 else m for i, m in enumerate(local))} roles must be "
                     + ", or ".join(areas) + ".")
    elif local:
        parts.append("Hybrid or onsite roles are judged by the location the profile gives.")
    else:
        parts.append("Hybrid and onsite roles are not wanted.")
    return " ".join(parts)[0].upper() + " ".join(parts)[1:] if parts else ""

PAY_RULE_NONE = "Pay is not a criterion. Never reject or lower a score because of salary, hourly rate or missing pay."
PAY_RULE_FLOOR = ("Minimum base pay is USD {floor:,.0f} a year (hourly contract rates count at 2,080 hours). "
                  "Missing pay is not a reason to lower the score.")


def _system(profile: str, feedback_notes: list[str], floor: float | None = None, cfg: dict | None = None) -> list[dict]:
    rules = INSTRUCTIONS.format(pay_rule=PAY_RULE_FLOOR.format(floor=floor) if floor else PAY_RULE_NONE,
                                location_rule=location_rule(cfg))
    text = profile
    if feedback_notes:
        text += "\n\n## Roles the candidate rejected before (score similar roles lower)\n" + "\n".join(f"- {n}" for n in feedback_notes[:25])
    return [
        {"type": "text", "text": rules},
        {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}},
    ]


def _field(row, name: str):
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def _posting(row, max_chars: int) -> str:
    pay = row["salary_note"] or "not listed"
    return (
        f"Title: {row['title']}\nCompany: {row['company']}\nLocation: {row['location'] or 'not stated'}\n"
        f"Work model (from filter): {row['work_model']}\nEmployment type: {_field(row, 'employment_type') or 'not stated'}\n"
        f"Pay: {pay}\nSource: {row['publisher'] or row['source']}\n\n"
        f"Description:\n{(row['description'] or '')[:max_chars]}"
    )


class Scorer:
    def __init__(self, cfg: dict, profile_path: str | Path, feedback_notes: list[str], client=None):
        self.cfg = cfg["scoring"]
        floor = (cfg.get("salary") or {}).get("min_annual_usd")
        if client is None:
            import anthropic
            client = anthropic.Anthropic(max_retries=3)
        self.client = client
        self.system = _system(Path(profile_path).read_text(), feedback_notes, floor, cfg)
        self._auto_choice: set[str] = set()   # models that reject a forced tool_choice

    def _kwargs(self, model: str, tool: dict, content: str, max_tokens: int, forced: bool, system=None) -> dict:
        kwargs = dict(model=model, max_tokens=max_tokens, system=system or self.system, tools=[tool],
                      messages=[{"role": "user", "content": content}])
        if forced:
            kwargs["tool_choice"] = {"type": "tool", "name": tool["name"]}
        else:
            kwargs["tool_choice"] = {"type": "auto"}
            kwargs["messages"][0]["content"] = (
                content + f"\n\nReply only by calling the {tool['name']} tool, with every field filled in.")
        return kwargs

    def _create(self, model: str, tool: dict, content: str, max_tokens: int, forced: bool):
        return self.client.messages.create(**self._kwargs(model, tool, content, max_tokens, forced))

    @staticmethod
    def _extract(resp, max_tokens: int) -> dict:
        """The tool input from a reply, or the first JSON object in its text."""
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"reply cut off at {max_tokens} tokens")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        found = _json_in(text)
        if found is not None:
            return found
        raise RuntimeError("model answered without the tool: " + " ".join(text.split())[:160])

    def _call(self, model: str, tool: dict, content: str, max_tokens: int) -> dict:
        forced = model not in self._auto_choice
        try:
            resp = self._create(model, tool, content, max_tokens, forced)
        except Exception as e:
            # Some models (for example ones that always think first) refuse a forced tool_choice.
            if forced and getattr(e, "status_code", None) == 400 and "tool_choice" in str(e):
                log.info("%s does not accept a forced tool_choice; asking for the tool instead", model)
                self._auto_choice.add(model)
                resp = self._create(model, tool, content, max_tokens, forced=False)
            else:
                raise
        return self._extract(resp, max_tokens)

    def _spec(self, kind: str, row) -> tuple[str, dict, str, int]:
        """(model, tool, user content, max_tokens) for one posting. kind is "triage" or "deep"."""
        if kind == "triage":
            return (self.cfg["triage_model"], TRIAGE_TOOL,
                    "Triage this posting.\n\n" + _posting(row, self.cfg.get("triage_chars", 8000)), 2048)
        return (self.cfg["deep_model"], ASSESS_TOOL,
                "Assess this posting in full.\n\n" + _posting(row, self.cfg["description_chars"]), 8000)

    def triage(self, row) -> dict:
        return self._call(*self._spec("triage", row))

    def assess(self, row) -> dict:
        return self._call(*self._spec("deep", row))

    # ---------- Message Batches API: same requests, half the price, answered later ----------
    def batch_submit(self, kind: str, rows: list) -> str:
        """Send one request per posting as a Message Batch and return the batch id.
        Each request's custom_id is the posting's key, which is how results are matched back."""
        # A batch can take longer than the default 5-minute prompt cache, so the shared profile is cached for 1 hour.
        system = [dict(b, cache_control={"type": "ephemeral", "ttl": "1h"}) if "cache_control" in b else b
                  for b in self.system]
        requests = []
        for row in rows:
            model, tool, content, max_tokens = self._spec(kind, row)
            requests.append({"custom_id": row["key"],
                             "params": self._kwargs(model, tool, content, max_tokens, model not in self._auto_choice, system)})
        return self.client.messages.batches.create(requests=requests).id

    def batch_ended(self, batch_id: str) -> bool:
        return self.client.messages.batches.retrieve(batch_id).processing_status == "ended"

    def batch_results(self, batch_id: str) -> dict[str, tuple[str, object]]:
        """{posting key: ("ok", reply dict) or ("error", reason)}. Errored, canceled and expired requests are not billed."""
        out: dict[str, tuple[str, object]] = {}
        for entry in self.client.messages.batches.results(batch_id):
            r = entry.result
            if r.type == "succeeded":
                try:
                    out[entry.custom_id] = ("ok", self._extract(r.message, r.message.usage.output_tokens + 1))
                except Exception as e:
                    out[entry.custom_id] = ("error", str(e))
            else:
                detail = ""
                try:
                    detail = ": " + str(r.error.error.message)
                except AttributeError:
                    pass
                out[entry.custom_id] = ("error", f"batch request {r.type}{detail}")
        return out


def _json_in(text: str) -> dict | None:
    """Pull the first JSON object out of a text reply (fallback when no tool call came back)."""
    import json
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        return obj
                except ValueError:
                    pass
                break
        start = text.find("{", start + 1)
    return None


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def _enum(v, allowed: list[str], default: str) -> str:
    v = str(v or "").strip().lower()
    return v if v in allowed else default


def _list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()][:5]
    return [str(v)] if v else []


def clean_triage(d: dict) -> dict:
    """Fill gaps in a triage reply. Raises ValueError if the score itself is missing."""
    if "score" not in d:
        raise ValueError(f"triage reply had no score (got keys: {', '.join(d) or 'none'})")
    return {
        "score": _int(d.get("score"), 0, 0, 10),
        "reason": str(d.get("reason") or "")[:300],
        "location_check": _enum(d.get("location_check"), LOCATION_ENUM, "unclear"),
        "pay_below_floor": bool(d.get("pay_below_floor", False)),
    }


def clean_assessment(d: dict) -> dict:
    """Fill gaps in an assessment reply. Raises ValueError if the core percentages are missing."""
    if "hiring_manager_fit_pct" not in d or "keyword_match_pct" not in d:
        raise ValueError(f"assessment reply incomplete (got keys: {', '.join(d) or 'none'})")
    pay = d.get("pay_top_annual_usd")
    try:
        pay = float(pay) if pay not in (None, "", "null") else None
    except (TypeError, ValueError):
        pay = None
    return {
        "summary": str(d.get("summary") or "")[:400],
        "keyword_match_pct": _int(d.get("keyword_match_pct"), 0, 0, 100),
        "hiring_manager_fit_pct": _int(d.get("hiring_manager_fit_pct"), 0, 0, 100),
        "seniority_pay_pct": _int(d.get("seniority_pay_pct"), 60, 0, 100),
        "verdict": _enum(d.get("verdict"), ["apply", "stretch", "skip"], "stretch"),
        "top_gaps": _list(d.get("top_gaps"))[:3],
        "hard_gaps": _list(d.get("hard_gaps")),
        "pitch": str(d.get("pitch") or "")[:400],
        "location_check": _enum(d.get("location_check"), LOCATION_ENUM, "unclear"),
        "work_model": _enum(d.get("work_model"), ["remote", "hybrid", "onsite"], ""),
        "pay_top_annual_usd": pay,
    }


def freshness_pct(posted_at_iso: str | None, now: datetime | None = None) -> float:
    if not posted_at_iso:
        return 60.0
    now = now or datetime.now(timezone.utc)
    hours = (now - datetime.fromisoformat(posted_at_iso)).total_seconds() / 3600
    if hours <= 24:
        return 100.0
    if hours <= 48:
        return 70.0
    return 40.0


def final_rank(a: dict, posted_at_iso: str | None, cfg: dict) -> float:
    w = cfg["scoring"]["weights"]
    pct = (w["hiring_manager_fit"] * a["hiring_manager_fit_pct"]
           + w["keyword_match"] * a["keyword_match_pct"]
           + w["seniority_pay"] * a["seniority_pay_pct"]
           + w["freshness"] * freshness_pct(posted_at_iso))
    rank = round(pct / 10, 1)
    if a.get("hard_gaps"):
        rank = min(rank, cfg["scoring"]["hard_gap_cap"])
    return rank
