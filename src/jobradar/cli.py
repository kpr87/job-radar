from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import yaml

from .pipeline import ROOT, Run, load_yaml


def parse_careers_url(url: str) -> dict | None:
    u = url.strip()
    m = re.search(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", u)
    if m:
        return {"ats": "greenhouse", "token": m.group(1)}
    m = re.search(r"jobs\.lever\.co/([A-Za-z0-9_-]+)", u)
    if m:
        return {"ats": "lever", "token": m.group(1)}
    m = re.search(r"jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)", u)
    if m:
        return {"ats": "ashby", "token": m.group(1)}
    m = re.search(r"([A-Za-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", u)
    if m:
        return {"ats": "workday", "host": f"{m.group(1)}.{m.group(2)}.myworkdayjobs.com",
                "tenant": m.group(1), "site": m.group(3)}
    return None


def cmd_add_company(args) -> int:
    entry = parse_careers_url(args.url)
    if not entry:
        print("Not a Greenhouse, Lever, Ashby or Workday careers URL. Open the company's job list and copy that URL.")
        return 1
    entry = {"name": args.name or (entry.get("token") or entry.get("tenant")).replace("-", " ").title(), **entry}
    if args.check:
        from .collectors import ATS, http_client
        cfg = load_yaml("config.yaml")
        try:
            n = len(ATS[entry["ats"]].fetch(http_client(), entry, cfg))
            print(f"OK: {n} open jobs found")
        except Exception as e:
            print(f"Could not read that board: {e}")
            return 1
    path = ROOT / "companies.yaml"
    data = load_yaml("companies.yaml")
    existing = data.get("companies") or []
    if any({k: v for k, v in c.items() if k != "name"} == {k: v for k, v in entry.items() if k != "name"} for c in existing):
        print("Already in companies.yaml")
        return 0
    line = "  - " + yaml.safe_dump(entry, default_flow_style=True, sort_keys=False).strip() + "\n"
    text = path.read_text()
    text = text.replace("companies: []", "companies:") if "companies: []" in text else text
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + line)
    print("Added:", line.strip())
    return 0


def cmd_calibrate(args) -> int:
    from .scorer import Scorer, final_rank

    cfg = load_yaml("config.yaml")
    scorer = Scorer(cfg, ROOT / "profile" / "profile.md", [])
    files = sorted((ROOT / "calibration").glob("*.yaml"))
    print(f"{'Posting':45} {'Expected':>8} {'Claude':>7} {'Diff':>6}  Verdict  Rank")
    diffs = []
    for f in files:
        c = yaml.safe_load(f.read_text())
        if not (c.get("posting") or "").strip():
            print(f"{c['company'] + ' ' + c['title']:45.45} skipped: paste the posting text into {f.name}")
            continue
        row = {"title": c["title"], "company": c["company"], "location": c.get("location", ""),
               "work_model": c.get("work_model", ""), "salary_note": c.get("salary", ""), "publisher": "calibration",
               "source": "calibration", "description": c["posting"]}
        a = scorer.assess(row)
        d = a["hiring_manager_fit_pct"] - c["expected_fit"]
        diffs.append(abs(d))
        print(f"{c['company'] + ' ' + c['title']:45.45} {c['expected_fit']:>7}% {a['hiring_manager_fit_pct']:>6}% {d:>+5}  "
              f"{a['verdict']:8} {final_rank(a, None, cfg):.1f}")
        if args.verbose:
            print("   gaps:", "; ".join(a["top_gaps"]), "| hard:", "; ".join(a["hard_gaps"]) or "none")
    if diffs:
        print(f"\nMean absolute difference: {sum(diffs) / len(diffs):.1f} points (target: 5 or less)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jobradar", description="Daily job search ranked against your profile")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, filter, score, alert (the daily job)")
    r.add_argument("--dry-run", action="store_true", help="write emails to out/emails instead of sending")
    r.add_argument("--digest-now", action="store_true", help="also send the daily digest now")
    r.add_argument("--force-collect", "--force-jsearch", dest="force_collect", action="store_true",
                   help="read every source now, even if they were already read this cycle")
    r.add_argument("--final", action="store_true",
                   help="last run of the cycle: send the digest with what is ready even if a Claude batch is still out")
    r.add_argument("--wait-minutes", type=float, default=None,
                   help="how long this run may wait for Claude batches (default: scoring.batch.poll_minutes)")
    r.add_argument("--direct", action="store_true", help="score with direct Claude calls instead of batches (full price, immediate)")
    r.add_argument("--clear-errors", action="store_true", help="forget all earlier problems before this run")
    a = sub.add_parser("add-company", help="add a company careers board to companies.yaml")
    a.add_argument("url")
    a.add_argument("--name")
    a.add_argument("--check", action="store_true", help="test the board before adding")
    c = sub.add_parser("calibrate", help="score your past postings and compare with your own fit estimates")
    c.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.cmd == "run":
        run = Run(dry_run=args.dry_run, force_jsearch=args.force_collect, force_collect=args.force_collect,
                  final=args.final or args.direct, wait_minutes=args.wait_minutes)
        if args.direct:
            run.cfg["scoring"].setdefault("batch", {})["enabled"] = False
        run.execute(digest_force=args.digest_now, clear_errors=args.clear_errors)
        return 0
    if args.cmd == "add-company":
        return cmd_add_company(args)
    if args.cmd == "calibrate":
        return cmd_calibrate(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
