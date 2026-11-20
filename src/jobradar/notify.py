"""Email through Gmail SMTP using an app password (GMAIL_USER, GMAIL_APP_PASSWORD)."""
from __future__ import annotations

import html
import json
import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("jobradar")

VERDICT_COLOR = {"apply": "#0F6E56", "stretch": "#854F0B", "skip": "#A32D2D"}


def send(to: str, subject: str, html_body: str, text_body: str, outbox: Path | None = None) -> bool:
    user, pwd = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if outbox is not None:
        outbox.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() else "_" for c in subject)[:60]
        (outbox / f"{safe}.html").write_text(html_body)
    if not (user and pwd):
        log.warning("GMAIL_USER / GMAIL_APP_PASSWORD not set; email not sent: %s", subject)
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, f"Job Radar <{user}>", to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, pwd)
        s.send_message(msg)
    log.info("email sent: %s", subject)
    return True


def _gaps(row) -> list[str]:
    try:
        return json.loads(row["gaps"] or "[]")
    except json.JSONDecodeError:
        return []


def _hard(row) -> list[str]:
    try:
        return json.loads(row["hard_gaps"] or "[]")
    except json.JSONDecodeError:
        return []


def _card(row) -> str:
    e = html.escape
    verdict = row["verdict"] or ""
    color = VERDICT_COLOR.get(verdict, "#5F5E5A")
    gaps = _gaps(row)
    hard = _hard(row)
    gap_html = "".join(f"<li>{e(g)}</li>" for g in gaps) or "<li>None noted</li>"
    hard_html = f'<p style="margin:4px 0;color:#A32D2D"><b>Hard gap:</b> {e("; ".join(hard))}</p>' if hard else ""
    skills_html = ('<p style="margin:4px 0;color:#444441"><b>Your skills in the posting:</b> '
                   + e(row["skills"]) + "</p>") if row["skills"] else ""
    flag = ' <span style="color:#854F0B">(location needs a check)</span>' if row["location_unclear"] else ""
    emp = f" &middot; <b>{e(row['employment_type'])}</b>" if row["employment_type"] else ""
    via = f" &middot; via {e(row['publisher'])}" if row["publisher"] else ""
    return f"""
<div style="border:1px solid #D3D1C7;border-radius:8px;padding:12px 14px;margin:0 0 12px">
  <div style="font-size:16px"><b>{row['rank']:.1f}/10</b> &nbsp;
    <a href="{e(row['url'])}" style="color:#185FA5;text-decoration:none"><b>{e(row['title'])}</b></a></div>
  <div style="color:#444441;margin:2px 0 6px">{e(row['company'])} &middot; {e(row['location_label'] or row['location'] or '')}{flag}
    &middot; {e(row['salary_note'] or 'salary not listed')}{emp}{via}</div>
  <div style="margin:4px 0"><span style="color:{color};font-weight:bold;text-transform:uppercase">{e(verdict)}</span>
    &nbsp;Fit {row['fit_pct']:.0f}% &middot; Keywords {row['ats_pct']:.0f}%</div>
  <p style="margin:4px 0">{e(row['summary'] or '')}</p>
  {skills_html}
  <p style="margin:4px 0"><b>Lead with:</b> {e(row['pitch'] or '')}</p>
  {hard_html}
  <div style="margin:4px 0"><b>Gaps:</b><ul style="margin:2px 0 0 18px;padding:0">{gap_html}</ul></div>
</div>"""


def _text_line(row) -> str:
    return (f"{row['rank']:.1f}/10  {row['title']} | {row['company']} | {row['location_label'] or row['location']} | "
            f"{row['salary_note'] or 'salary not listed'} | {row['employment_type'] or 'type not stated'} | {row['verdict']}\n"
            f"    {row['url']}")


def alert_email(rows) -> tuple[str, str, str]:
    top = rows[0]
    n = len(rows)
    subject = f"Job Radar: {n} strong match{'es' if n > 1 else ''}, top {top['rank']:.1f} {top['title']} at {top['company']}"
    body = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#2C2C2A;max-width:680px">'
            + "".join(_card(r) for r in rows)
            + '<p style="color:#888780;font-size:12px">Mark jobs in feedback.yaml (applied / not_interested) to stop repeats.</p></div>')
    text = "\n\n".join(_text_line(r) for r in rows)
    return subject, body, text


def digest_email(rows, stats: dict, reasons: list[tuple[str, int]], errors: list[str], date_label: str,
                 strong: float = 8.0, near: list | None = None) -> tuple[str, str, str]:
    e = html.escape
    seen = sum(stats.values())
    passed = seen - stats.get("filtered", 0)
    top = [r for r in rows if r["rank"] >= strong]
    rest = [r for r in rows if r["rank"] < strong]
    subject = (f"Job Radar {date_label}: {len(rows)} ranked job{'s' if len(rows) != 1 else ''}"
               + (f", {len(top)} strong match{'es' if len(top) != 1 else ''}" if top else ""))
    reason_html = "".join(f"<li>{e(r or 'other')}: {n}</li>" for r, n in reasons)
    err_html = ("<p style='color:#A32D2D'><b>Source problems in the last 24 hours (Eastern time):</b><br>"
                + "<br>".join(e(x) for x in errors[:10]) + "</p>") if errors else ""
    h = lambda t: f'<h3 style="font-size:15px;margin:16px 0 8px">{t}</h3>'
    cards = ""
    if top:
        cards += h(f"Strong matches ({strong:.0f}+)") + "".join(_card(r) for r in top)
    if rest:
        cards += h("Worth a look") + "".join(_card(r) for r in rest)
    if not rows:
        cards = "<p>No new jobs at or above the ranking threshold since the last email.</p>"
    near_html = ""
    if near:   # scored just under the cut by the quick pass and never fully assessed: listed so a miss is visible
        items = "".join(
            f'<li><a href="{e(r["url"])}" style="color:#185FA5">{e(r["title"])}</a>, {e(r["company"])} '
            f'({r["triage_score"]:.0f}/10: {e(r["triage_reason"] or "")})</li>' for r in near)
        near_html = (f'<details><summary style="color:#5F5E5A">Just below the cut ({len(near)}), not fully assessed</summary>'
                     f'<ul>{items}</ul></details>')
    body = f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#2C2C2A;max-width:680px">
<p>Since the last email: <b>{seen}</b> new postings seen, <b>{passed}</b> passed the filters, <b>{len(rows)}</b> ranked here.</p>
{cards}
{near_html}
<details><summary style="color:#5F5E5A">Why the rest were filtered</summary><ul>{reason_html}</ul></details>
{err_html}</div>"""
    text = f"{seen} seen, {passed} passed filters, {len(rows)} ranked.\n\n" + "\n\n".join(_text_line(r) for r in rows)
    return subject, body, text
