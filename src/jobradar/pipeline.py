"""One daily run: collect, dedupe, filter, score, alert, digest, export."""
from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import notify
from .collectors import collect_ats, dice, http_client, jsearch, linkedin
from .filters import apply_filters
from .models import _norm
from .store import Store, now_iso

log = logging.getLogger("jobradar")
ROOT = Path(os.environ.get("JOBRADAR_HOME", Path.cwd()))


def load_yaml(name: str) -> dict:
    p = ROOT / name
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


class Run:
    def __init__(self, dry_run: bool = False, client=None, scorer=None, now: datetime | None = None,
                 force_jsearch: bool = False, force_collect: bool | None = None, final: bool = True,
                 wait_minutes: float | None = None, sleep=time.sleep):
        self.cfg = load_yaml("config.yaml")
        self.companies = load_yaml("companies.yaml").get("companies") or []
        self.feedback = load_yaml("feedback.yaml")
        self.store = Store(ROOT / "data" / "jobs.db")
        self.out = ROOT / "out"
        self.dry_run = dry_run
        self.force_jsearch = force_jsearch
        self.force_collect = force_jsearch if force_collect is None else force_collect
        self.final = final                    # last run of the day: email what is ready even if a batch is still out
        self.wait_minutes = wait_minutes      # how long this run may wait for Claude batches (None: config)
        self._sleep = sleep
        self.client = client
        self.scorer = scorer
        self.now = now or datetime.now(timezone.utc)
        self.errors: list[str] = []          # this run's problems, for the run summary
        self._err_records: list[list] = []   # [time, message, source, job key] kept for the digest
        self.counts = {"collected": 0, "new": 0, "candidates": 0, "triaged": 0, "assessed": 0, "alerted": 0,
                       "jsearch_calls": 0, "linkedin_results": 0, "dice_searches": 0, "batches_pending": 0}

    # ---------- feedback ----------
    @staticmethod
    def _url_id(url: str | None) -> str:
        """A URL reduced to what identifies the posting, so a pasted link still matches when it carries
        tracking parameters. LinkedIn links are reduced to the job number."""
        u = (url or "").strip()
        m = re.search(r"linkedin\.com/.*?(?:jobs/view/(?:[^/?#]*-)?|currentJobId=)(\d{6,})", u)
        if m:
            return "linkedin:" + m.group(1)
        u = re.sub(r"^https?://(www\.)?", "", u.split("#")[0].split("?")[0]).rstrip("/")
        return u.lower()

    def apply_feedback(self) -> list[str]:
        notes = []
        index: dict[str, list[str]] = {}
        for r in self.store.db.execute("SELECT key, url FROM jobs"):
            index.setdefault(self._url_id(r["url"]), []).append(r["key"])
            index.setdefault(r["key"], []).append(r["key"])

        def mark(url: str, status: str) -> list[str]:
            keys = index.get((url or "").strip()) or index.get(self._url_id(url)) or []
            for k in keys:
                self.store.db.execute("UPDATE jobs SET status=? WHERE key=?", (status, k))
            if not keys and url:
                log.warning("feedback.yaml: no stored job matches %s (copy the link from the digest email)", url)
            return keys

        for url in self.feedback.get("applied") or []:
            mark(url, "applied")
        for item in self.feedback.get("not_interested") or []:
            url, reason = (item.get("url"), item.get("reason", "")) if isinstance(item, dict) else (item, "")
            keys = mark(url, "not_interested")
            row = self.store.db.execute("SELECT title, company FROM jobs WHERE key=?", (keys[0],)).fetchone() if keys else None
            if row and reason:
                notes.append(f"{row['title']} at {row['company']}: {reason}")
            elif reason:
                notes.append(reason)
        blocked = {_norm(c) for c in self.feedback.get("blocked_companies") or []}
        if blocked:   # also applies to postings stored before the company was blocked
            for r in self.store.rows("status IN ('candidate','awaiting_deep','scored')"):
                if _norm(r["company"]) in blocked:
                    self.store.update(r["key"], status="filtered", filter_reason="company blocked in feedback.yaml")
        self.store.commit()
        return notes

    # ---------- collect ----------
    def collect(self) -> list:
        client = self.client or http_client()
        src = self.cfg["sources"]
        windows: dict[str, int] = {}
        jobs = []

        js = src["jsearch"]
        if js.get("enabled"):
            backfill, hours = self.lookback("jsearch")
            windows["jsearch"] = hours
            last = self.store.get_meta("jsearch_last_run")
            due = self.force_jsearch or last is None or \
                (self.now - datetime.fromisoformat(last)) >= timedelta(hours=js.get("every_hours", 1), minutes=-15)
            if due:
                found, errs, calls, ok = jsearch.fetch(client, self.cfg, hours, backfill)
                jobs += found
                for e in errs:
                    self._err(e, "jsearch")
                self.counts["jsearch_calls"] = calls
                if ok:   # only a run that actually got answers counts toward the every_hours schedule
                    self._succeeded("jsearch", bool(found), "JSearch")
                    self.store.set_meta("jsearch_last_run", self.now.isoformat())
                self._add_usage("jsearch_usage", calls)
            else:
                log.info("JSearch not due this hour (every_hours=%s)", js.get("every_hours"))

        li = src.get("linkedin") or {}
        if li.get("enabled"):
            _, hours = self.lookback("linkedin")
            windows["linkedin"] = hours
            found, errs, billed = linkedin.fetch(client, self.cfg, hours)
            jobs += found
            for e in errs:
                self._err(e, "linkedin")
            if found or not errs:
                self._succeeded("linkedin", bool(found), "LinkedIn")
            self.counts["linkedin_results"] = billed
            self._add_usage("linkedin_usage", billed)

        di = src.get("dice") or {}
        if di.get("enabled"):
            _, hours = self.lookback("dice")
            windows["dice"] = hours
            found, errs, ok = dice.fetch(client, self.cfg, hours)
            jobs += found
            for e in errs:
                self._err(e, "dice")
            if ok:
                self._succeeded("dice", bool(found), "Dice")
            self.counts["dice_searches"] = ok

        if src["ats_boards"].get("enabled") and self.companies:
            _, hours = self.lookback("ats")
            windows["ats"] = hours
            self.cfg["freshness"]["effective_by_source"] = windows     # the Workday reader uses its window
            companies = []
            for c in self.companies:
                name = c.get("name") or c.get("token") or c.get("tenant") or "?"
                first_time = src["ats_boards"].get("new_company_backfill", False) and \
                    self.store.get_meta(f"ats_seen:{name}") is None
                companies.append({**c, "_backfill": True} if first_time else c)
            found, errs, ok_names = collect_ats(client, companies, self.cfg)
            jobs += found
            for name, e in errs:
                self._err(e, f"ats:{name}")
            for name in ok_names:
                self._mark_ok(f"ats:{name}")
                self.store.set_meta(f"ats_seen:{name}", self.now.isoformat())
            if ok_names:
                self.store.set_meta("last_success:ats", self.now.isoformat())

        fr = self.cfg["freshness"]
        fr["effective_by_source"] = windows
        fr["effective_hours"] = max(windows.values(), default=fr.get("max_age_hours", 24))
        log.info("Look-back windows (hours): %s", ", ".join(f"{k} {v}" for k, v in windows.items()) or "none")
        self.counts["collected"] = len(jobs)
        return jobs

    def _succeeded(self, source: str, found_any: bool, label: str) -> None:
        """The source answered. Its next look-back starts from now. An answer with no postings at all is
        reported as a problem, because a broken scraper or search looks exactly like a quiet day."""
        self._mark_ok(source)
        self.store.set_meta(f"last_success:{source}", self.now.isoformat())
        if not found_any:
            self._err(f"{label} answered but returned no postings at all; check its searches in config.yaml", source)

    def _add_usage(self, meta_key: str, n: int) -> None:
        if n:
            month = self.now.strftime("%Y-%m")
            used = json.loads(self.store.get_meta(meta_key, "{}"))
            used[month] = used.get(month, 0) + n
            self.store.set_meta(meta_key, json.dumps(used))

    def lookback(self, source: str = "jsearch") -> tuple[bool, int]:
        """(first successful run of this source?, hours to look back).
        Each source keeps its own clock, so one that was down for days catches up when it returns."""
        fr = self.cfg["freshness"]
        normal, first = fr.get("max_age_hours", 24), fr.get("first_run_max_age_hours", 168)
        if not fr.get("catch_up", False):
            # Always the same window, whatever happened on earlier days. The small overlap covers a run that
            # starts late (GitHub schedules often do); a posting seen twice is stored once.
            return False, int(normal + fr.get("overlap_hours", 2))
        last = self.store.get_meta(f"last_success:{source}")
        if last is None and source != "dice":      # databases from before per-source clocks; Dice is new, so it backfills
            last = self.store.get_meta("last_success")
        if last is None:
            return True, first
        gap = (self.now - datetime.fromisoformat(last)).total_seconds() / 3600
        return False, int(min(first, max(normal, gap + 1)))

    # ---------- dedupe + filter ----------
    def ingest(self, jobs: list) -> None:
        batch = set()
        for job in jobs:
            if not job.title or not job.url:
                continue
            k = job.key
            if k in batch:
                continue
            batch.add(k)
            if self.store.known(k):
                self.store.touch(k)
                continue
            fr = apply_filters(job, self.cfg, self.feedback, self.now)
            self.store.insert(job, fr)
            self.counts["new"] += 1
            self.counts["candidates"] += fr.status == "candidate"
        self.store.commit()

    # ---------- score ----------
    def score(self, feedback_notes: list[str]) -> None:
        from .scorer import Scorer

        sc = self.cfg["scoring"]
        if self.scorer is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                self._err("ANTHROPIC_API_KEY not set; scoring skipped", "claude")
                return
            self.scorer = Scorer(self.cfg, ROOT / "profile" / "profile.md", feedback_notes)
        # Some models refuse a forced tool_choice. What was learned about each model is kept between runs,
        # so a batch is built the right way the first time instead of failing and being redone with direct calls.
        known = json.loads(self.store.get_meta("claude_models") or "{}")
        if hasattr(self.scorer, "_auto_choice"):
            self.scorer._auto_choice.update(known.get("auto_choice", []))
        self._models_checked = set(known.get("checked", []))
        try:
            self._score(sc)
        finally:
            auto = sorted(getattr(self.scorer, "_auto_choice", []))
            self.store.set_meta("claude_models", json.dumps({"checked": sorted(self._models_checked), "auto_choice": auto}))
        self.counts["batches_pending"] = len(self._batches_pending())

    def _score(self, sc: dict) -> None:
        bc = sc.get("batch") or {}
        if bc.get("enabled") and hasattr(self.scorer, "batch_submit"):
            wait = bc.get("poll_minutes", 6) if self.wait_minutes is None else self.wait_minutes
            budget = [float(wait)]                       # minutes this run may still wait, shared by both passes
            if self._batch_phase("triage", budget):      # the full assessment needs the quick pass first
                self._batch_phase("deep", budget)
        else:
            for row in self._rows_for("triage"):
                self._score_one("triage", row)
            self.store.commit()
            for row in self._rows_for("deep"):
                self._score_one("deep", row)
            self.store.commit()

    def _rows_for(self, kind: str) -> list:
        """Postings waiting for a pass. A limit of null in config.yaml means no limit."""
        sc = self.cfg["scoring"]
        if kind == "triage":
            where, limit = "status='candidate' ORDER BY first_seen DESC", sc.get("max_triage_per_run")
        else:
            where, limit = "status='awaiting_deep' ORDER BY triage_score DESC, first_seen DESC", sc.get("max_deep_per_run")
        return self.store.rows(where + (" LIMIT ?" if limit else ""), (limit,) if limit else ())

    def _score_one(self, kind: str, row, reply: dict | None = None) -> None:
        """Apply one posting's result. Without a reply (direct mode, or a failed batch request) Claude is called now."""
        step = "triage" if kind == "triage" else "assessment"
        try:
            if reply is None:
                reply = self.scorer.triage(row) if kind == "triage" else self.scorer.assess(row)
            (self._apply_triage if kind == "triage" else self._apply_assessment)(row, reply)
        except Exception as e:      # one bad reply skips this job only; it is retried next run
            self._score_failed(row, step, e)
            return
        self.counts["triaged" if kind == "triage" else "assessed"] += 1
        self._mark_ok("claude")

    def _apply_triage(self, row, reply: dict) -> None:
        from .scorer import clean_triage
        sc = self.cfg["scoring"]
        floor = (self.cfg.get("salary") or {}).get("min_annual_usd")    # None: pay is shown, never used to reject
        t = clean_triage(reply)
        fields = {"triage_score": t["score"], "triage_reason": t["reason"]}
        if t["location_check"] == "out_of_area":
            fields |= {"status": "rejected_by_claude", "filter_reason": "Claude: outside search area. " + t["reason"]}
        elif floor and t.get("pay_below_floor"):
            fields |= {"status": "rejected_by_claude", "filter_reason": "Claude: pay below minimum"}
        elif t["score"] < sc["deep_min_triage"]:
            fields |= {"status": "triaged_low"}
        else:
            fields |= {"status": "awaiting_deep"}
        self.store.update(row["key"], **fields)

    def _apply_assessment(self, row, reply: dict) -> None:
        from .scorer import clean_assessment, final_rank
        floor = (self.cfg.get("salary") or {}).get("min_annual_usd")
        a = clean_assessment(reply)
        fields = {
            "summary": a["summary"], "fit_pct": a["hiring_manager_fit_pct"], "ats_pct": a["keyword_match_pct"],
            "seniority_pct": a["seniority_pay_pct"], "verdict": a["verdict"], "gaps": a["top_gaps"],
            "hard_gaps": a["hard_gaps"], "pitch": a["pitch"], "work_model": a["work_model"] or row["work_model"],
        }
        pay_top = a.get("pay_top_annual_usd")
        if a["location_check"] == "out_of_area":
            fields |= {"status": "rejected_by_claude", "filter_reason": "Claude: outside search area"}
        elif floor and pay_top and pay_top < floor:
            fields |= {"status": "rejected_by_claude", "filter_reason": f"Claude: pay tops out at ${pay_top:,.0f}"}
        else:
            fields |= {"status": "scored", "rank": final_rank(a, row["posted_at"], self.cfg),
                       "location_unclear": int(a["location_check"] == "unclear")}
            if a["location_check"] == "remote_us" and not row["location_label"]:
                fields["location_label"] = "Remote US"
        self.store.update(row["key"], **fields)

    # ---------- score through the Message Batches API ----------
    def _batches_pending(self) -> dict[str, dict]:
        out = {}
        for kind in ("triage", "deep"):
            raw = self.store.get_meta(f"batch:{kind}")
            if raw:
                out[kind] = json.loads(raw)
        return out

    def _batch_phase(self, kind: str, budget: list[float]) -> bool:
        """Run one pass through a batch. Returns True when the pass is complete for every waiting posting,
        False when a batch is still being processed (its id is kept, and a later run collects it)."""
        bc = self.cfg["scoring"].get("batch") or {}
        step = "quick pass" if kind == "triage" else "full assessment"
        for _ in range(2):          # first an unfinished batch from an earlier run, then this run's postings
            pending = self._batches_pending().get(kind)
            if not pending:
                rows = self._rows_for(kind)
                if not rows:
                    return True
                model = self.cfg["scoring"]["triage_model" if kind == "triage" else "deep_model"]
                if model not in self._models_checked:
                    # First use of this model: score one posting directly. That call finds out whether the model
                    # accepts a forced tool_choice, so the batch that follows is built correctly.
                    self._score_one(kind, rows[0])
                    self.store.commit()
                    self._models_checked.add(model)
                    rows = rows[1:]
                    if not rows:
                        return True
                try:
                    batch_id = self.scorer.batch_submit(kind, rows)
                except Exception as e:      # the batch could not be created: score these directly instead
                    self._err(f"Claude batch for the {step} could not be created ({e}); scored directly", "claude")
                    for row in rows:
                        self._score_one(kind, row)
                    self.store.commit()
                    return True
                pending = {"id": batch_id, "keys": [r["key"] for r in rows], "at": self.now.isoformat()}
                self.store.set_meta(f"batch:{kind}", json.dumps(pending))
                log.info("Claude %s: batch %s submitted with %d postings", step, batch_id, len(rows))
            if not self._batch_wait(kind, pending, budget, bc.get("poll_seconds", 30)):
                return False
            self._batch_collect(kind, pending)
        return not self._batches_pending().get(kind)

    def _batch_wait(self, kind: str, pending: dict, budget: list[float], interval: float) -> bool:
        while True:
            try:
                if self.scorer.batch_ended(pending["id"]):
                    return True
            except Exception as e:
                age_h = (self.now - datetime.fromisoformat(pending["at"])).total_seconds() / 3600
                if age_h > 30:      # past the 24-hour batch limit and unreadable: let the postings be sent again
                    self._err(f"Claude batch {pending['id']} could not be read after {age_h:.0f} hours; resubmitting", "claude")
                    self.store.set_meta(f"batch:{kind}", "")
                else:
                    self._err(f"Claude batch {pending['id']} could not be checked: {e}", "claude")
                return False
            if budget[0] <= 0:
                log.info("Claude batch %s is still processing; a later run will collect it", pending["id"])
                return False
            self._sleep(interval)
            budget[0] -= interval / 60

    def _batch_collect(self, kind: str, pending: dict) -> None:
        results = self.scorer.batch_results(pending["id"])
        waiting = "candidate" if kind == "triage" else "awaiting_deep"
        retried = 0
        for key in pending["keys"]:
            rows = self.store.rows("key=?", (key,))
            if not rows or rows[0]["status"] != waiting:
                continue            # marked applied, blocked or removed while the batch was out
            state, value = results.get(key, ("error", "no result in the batch"))
            if state == "ok":
                self._score_one(kind, rows[0], value)
            else:                   # errored, expired or unreadable: one direct call, which also handles tool_choice fallbacks
                retried += 1
                log.info("batch result for %s unusable (%s); calling Claude directly", rows[0]["title"], value)
                self._score_one(kind, rows[0])
        self.store.set_meta(f"batch:{kind}", "")
        self.store.commit()
        log.info("Claude batch %s collected: %d postings, %d retried directly", pending["id"], len(pending["keys"]), retried)

    def _score_failed(self, row, step: str, err: Exception) -> None:
        attempts = (row["score_attempts"] or 0) + 1
        fields = {"score_attempts": attempts}
        if attempts >= 3:
            fields |= {"status": "score_failed", "filter_reason": f"Claude {step} failed 3 times: {err}"}
        self.store.update(row["key"], **fields)
        self.store.commit()
        self._err(f"{step} failed for {row['title']} at {row['company']} (attempt {attempts} of 3): {err}",
                  "claude", row["key"])
        log.warning("%s failed for %s at %s (attempt %d): %s", step, row["title"], row["company"], attempts, err)

    # ---------- notify ----------
    def alerts(self) -> None:
        a = self.cfg["alerts"]
        if a.get("instant_min_score") is None:
            return
        since = (self.now - timedelta(hours=48)).isoformat()
        rows = self.store.rows("status='scored' AND rank>=? AND alerted_at IS NULL AND first_seen>=? ORDER BY rank DESC",
                               (a["instant_min_score"], since))
        rows = rows[:15]     # the rest go out with the next alert
        if not rows:
            return
        subject, body, text = notify.alert_email(rows)
        if self.dry_run:
            (self.out / "emails").mkdir(parents=True, exist_ok=True)
            (self.out / "emails" / "alert.html").write_text(body)
            sent = True
        else:
            sent = notify.send(a["to"], subject, body, text, self.out / "emails")
        if sent:
            for r in rows:
                self.store.update(r["key"], alerted_at=now_iso())
            self.store.commit()
            self.counts["alerted"] = len(rows)

    def digest(self, force: bool = False) -> bool:
        a = self.cfg["alerts"]
        tz = ZoneInfo(a["timezone"])
        local = self.now.astimezone(tz)
        today = local.date().isoformat()
        every_run = a.get("digest_every_run", False)
        if not (force or every_run) and (local.hour != a["digest_hour_local"] or self.store.get_meta("last_digest") == today):
            return False
        last = self.store.get_meta("last_digest_at")
        since = last if last and (self.now - datetime.fromisoformat(last)) < timedelta(days=7) \
            else (self.now - timedelta(hours=24)).isoformat()
        week_ago = (self.now - timedelta(days=7)).isoformat()
        rows = self.store.rows("status='scored' AND rank>=? AND digested_at IS NULL AND first_seen>=? "
                               "ORDER BY rank DESC LIMIT 40", (a["digest_min_score"], week_ago))
        latest: dict[str, tuple[str, int]] = {}
        for t, e in self.open_problems(since):
            n = latest.get(e, ("", 0))[1] + 1
            latest[e] = (max(t, latest.get(e, ("", 0))[0]), n)
        recent = []
        for e, (t, n) in sorted(latest.items(), key=lambda kv: kv[1][0], reverse=True):
            when = datetime.fromisoformat(t).astimezone(tz).strftime("%b %d %I:%M %p").replace(" 0", " ")
            recent.append(f"Last seen {when}" + (f" ({n} runs)" if n > 1 else "") + f": {e}")
        near = self.store.rows("status='triaged_low' AND triage_score>=? AND first_seen>=? ORDER BY triage_score DESC, "
                               "first_seen DESC LIMIT 12", (self.cfg["scoring"]["deep_min_triage"] - 1, since))
        subject, body, text = notify.digest_email(rows, self.store.stats_since(since),
                                                  self.store.filter_reasons_since(since), recent, local.strftime("%b %d"),
                                                  a.get("strong_match_score", 8.0), near)
        if self.dry_run:
            (self.out / "emails").mkdir(parents=True, exist_ok=True)
            (self.out / "emails" / "digest.html").write_text(body)
            sent = True
        else:
            sent = notify.send(a["to"], subject, body, text, self.out / "emails")
        if sent:
            self.store.set_meta("last_digest", today)
            self.store.set_meta("last_digest_at", self.now.isoformat())
            for r in rows:
                self.store.update(r["key"], digested_at=now_iso())
            self.store.commit()
        return sent

    # ---------- problems ----------
    def _err(self, msg: str, source: str, key: str | None = None) -> None:
        self.errors.append(msg)
        self._err_records.append([self.now.isoformat(), msg, source, key])

    def _mark_ok(self, source: str) -> None:
        """Remember when a source last worked; older problems from it are then treated as fixed."""
        self.store.set_meta(f"ok:{source}", self.now.isoformat())

    def _recent_errors(self) -> list[list]:
        out = []
        for x in json.loads(self.store.get_meta("recent_errors", "[]")):
            if len(x) == 2:   # saved by an older version: work out the source from the text
                t, msg = x
                src = "jsearch" if msg.startswith("JSearch") else (
                    "claude" if ("triage failed" in msg or "assessment failed" in msg or "ANTHROPIC" in msg) else "other")
                x = [t, msg, src, None]
            out.append(x)
        return out

    def open_problems(self, since: str) -> list[tuple[str, str]]:
        """Problems from the window that have NOT been fixed by a later success."""
        cleared = self.store.get_meta("errors_cleared_at", "")
        out = []
        for t, msg, src, key in self._recent_errors():
            if t < since or (cleared and t <= cleared):
                continue
            if key:   # tied to one job: open only while that job is still waiting to be scored, or failed for good
                r = self.store.db.execute("SELECT status FROM jobs WHERE key=?", (key,)).fetchone()
                if r is None or r["status"] not in ("candidate", "awaiting_deep", "score_failed"):
                    continue
                if r["status"] != "score_failed" and (self.store.get_meta(f"ok:{src}") or "") > t:
                    continue   # the job is queued again and Claude is working now
            elif (self.store.get_meta(f"ok:{src}") or "") > t:
                continue       # the same source has worked since
            out.append((t, msg))
        return out

    def clear_problems(self) -> None:
        self.store.set_meta("errors_cleared_at", self.now.isoformat())
        log.info("Problem list cleared; only problems from now on will be reported")

    def _save_errors(self) -> None:
        keep_since = (self.now - timedelta(hours=24)).isoformat()
        errs = [e for e in self._recent_errors() if e[0] >= keep_since] + self._err_records
        self._err_records = []      # saved; a second call in the same run adds only what is new
        self.store.set_meta("recent_errors", json.dumps(errs[-150:]))

    # ---------- export ----------
    def export(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        since = (self.now - timedelta(days=7)).isoformat()
        rows = self.store.rows("status IN ('scored','applied') AND first_seen>=? ORDER BY rank DESC", (since,))
        cols = ["rank", "verdict", "fit_pct", "ats_pct", "title", "company", "location_label", "work_model",
                "salary_note", "skills", "posted_at", "first_seen", "status", "url", "summary", "pitch", "gaps", "hard_gaps", "employment_type", "publisher"]
        with open(self.out / "ranked.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([r[c] for c in cols])
        e = html.escape
        trs = "".join(
            f"<tr><td>{r['rank']:.1f}</td><td>{e(r['verdict'] or '')}</td><td><a href='{e(r['url'])}'>{e(r['title'])}</a></td>"
            f"<td>{e(r['company'])}</td><td>{e(r['location_label'] or '')}</td><td>{e(r['salary_note'] or '')}</td>"
            f"<td>{(r['first_seen'] or '')[:10]}</td></tr>" for r in rows)
        (self.out / "report.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>Job Radar, last 7 days</title>"
            "<style>body{font-family:Arial,sans-serif;margin:24px}td,th{padding:6px 10px;border-bottom:1px solid #ddd;text-align:left}</style>"
            f"<h2>Job Radar: ranked jobs, last 7 days ({len(rows)})</h2><table><tr><th>Rank</th><th>Verdict</th><th>Role</th>"
            f"<th>Company</th><th>Location</th><th>Pay</th><th>Found</th></tr>{trs}</table>")

    # ---------- orchestrate ----------
    def collect_due(self) -> bool:
        """Sources are read once per cycle (collect_every_hours). The other runs of the day only pick up
        Claude batches, so paid sources are not called again."""
        if self.force_collect:
            return True
        last = self.store.get_meta("last_collect_at")
        every = self.cfg.get("schedule", {}).get("collect_every_hours", 20)
        return last is None or (self.now - datetime.fromisoformat(last)) >= timedelta(hours=every)

    def execute(self, digest_force: bool = False, clear_errors: bool = False) -> dict:
        if clear_errors:
            self.clear_problems()
        notes = self.apply_feedback()
        if self.collect_due():
            self.ingest(self.collect())
            self.store.set_meta("last_collect_at", self.now.isoformat())
            self.store.set_meta("digest_due", "1")
        else:
            log.info("Sources already read this cycle; this run only continues the scoring")
        self.score(notes)
        pending = self._batches_pending()
        due = self.store.get_meta("digest_due") == "1"
        send = digest_force or (due and (not pending or self.final))
        if send and pending:
            n = sum(len(p["keys"]) for p in pending.values())
            self._err(f"{n} postings were still being scored by Claude when this email went out; "
                      "they will be in the next one", "claude-batch")
        mail_error = None
        try:
            if send:
                self.alerts()
            self._save_errors()
            if send and self.digest(force=True):
                self.store.set_meta("digest_due", "0")
            elif not send:
                log.info("Digest held: %s", "Claude is still scoring" if pending else "nothing new since the last one")
        except Exception as e:      # a mail failure must not lose the report or the run summary
            mail_error = e
            self._err(f"Email could not be sent: {e}", "email")
            self._save_errors()
        self.export()
        self.store.prune((self.now - timedelta(days=30)).isoformat())
        self.summary()
        if mail_error:
            raise mail_error        # fail the run so GitHub reports it
        return self.counts

    def summary(self) -> None:
        c = self.counts
        usage = json.loads(self.store.get_meta("jsearch_usage", "{}")).get(self.now.strftime("%Y-%m"), 0)
        lines = [
            f"Collected {c['collected']} postings, {c['new']} new, {c['candidates']} passed filters.",
            f"Claude triaged {c['triaged']}, assessed {c['assessed']}; {c['alerted']} sent as alerts.",
            f"JSearch requests this run {c['jsearch_calls']}, this month {usage}.",
            f"LinkedIn (Apify) results this run {c['linkedin_results']}, this month "
            f"{json.loads(self.store.get_meta('linkedin_usage', '{}')).get(self.now.strftime('%Y-%m'), 0)}.",
            f"Dice searches answered this run {c['dice_searches']}.",
            f"Claude batches still processing: {c['batches_pending']}.",
        ] + [f"Problem: {e}" for e in self.errors]
        for line in lines:
            log.info(line)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write("### Job Radar run\n" + "\n".join(f"- {l}" for l in lines) + "\n")
