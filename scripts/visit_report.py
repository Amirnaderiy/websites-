#!/usr/bin/env python3
"""Email a visit report each time the website passes another N visits (default 10).

Reads visit numbers from GoatCounter's API and sends the email through Gmail SMTP.
Run by .github/workflows/visit-report.yml. Needs these repository secrets:

    GOATCOUNTER_TOKEN  API token from GoatCounter (permission: read statistics)
    MAIL_USERNAME      Gmail address that sends the report
    MAIL_PASSWORD      Gmail app password (not the normal password)
    MAIL_TO            optional; defaults to MAIL_USERNAME

State (how many visits were already reported, and when) is kept in
.github/analytics-state.json so each email covers only the new visits.
Only the standard library is used.
"""

import datetime as dt
import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_FILE = ROOT / "data" / "site.json"
STATE_FILE = ROOT / ".github" / "analytics-state.json"
EVERY = int(os.environ.get("REPORT_EVERY", "10"))
START = "2026-01-01T00:00:00Z"  # before the counter was added, so "total" is all-time


def notice(kind, msg):
    """Show a message on the Actions run (yellow/blue box) and in the log."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{kind} title=Visit report::{msg}")
    else:
        print(f"{kind}: {msg}")


def api(code, token, path, **params):
    url = f"https://{code}.goatcounter.com/api/v0/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def total_visits(code, token, start, end):
    data = api(code, token, "stats/total", start=start, end=end)
    return int(data.get("total") or 0)


def countries(code, token, start, end):
    data = api(code, token, "stats/locations", start=start, end=end, limit=100)
    rows = [(r.get("name") or r.get("id") or "Unknown", int(r.get("count") or 0)) for r in data.get("stats", [])]
    return sorted([r for r in rows if r[1] > 0], key=lambda r: -r[1])


def send_mail(subject, body):
    user, password = os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"]
    to = os.environ.get("MAIL_TO") or user
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, f"amirnaderi.com <{user}>", to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, password)
        s.send_message(msg)


def build_report(total, new, since, now, recent, alltime, code):
    def table(rows):
        if not rows:
            return "  (no country data yet)"
        width = max(len(n) for n, _ in rows)
        return "\n".join(f"  {name.ljust(width)}  {count}" for name, count in rows)

    since_txt = since.strftime("%d %b %Y, %H:%M UTC")
    return (
        f"Your website amirnaderi.com has now had {total} visits.\n\n"
        f"New since the last report ({since_txt}): {new} visits\n\n"
        f"Where the new visitors came from:\n{table(recent)}\n\n"
        f"All visitors so far, by country:\n{table(alltime[:15])}\n\n"
        f"Full dashboard (pages, referrers, devices): https://{code}.goatcounter.com\n\n"
        f"Sent automatically every {EVERY} visits. Countries are estimated from the visitor's internet connection.\n"
    )


def main():
    code = (json.loads(SITE_FILE.read_text(encoding="utf-8")).get("goatcounter") or "").strip()
    token = os.environ.get("GOATCOUNTER_TOKEN")
    missing = [n for n, v in [("GOATCOUNTER_TOKEN", token), ("MAIL_USERNAME", os.environ.get("MAIL_USERNAME")),
                              ("MAIL_PASSWORD", os.environ.get("MAIL_PASSWORD"))] if not v]
    if not code or missing:
        notice("notice", "Visit reports are not set up yet (missing: " + ", ".join(missing or ["goatcounter code"]) + "). Nothing to do.")
        return

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    try:
        total = total_visits(code, token, START, iso(now))
    except Exception as exc:
        notice("warning", f"Could not read visits from GoatCounter ({exc}).")
        return

    reported = state.get("reported_total")
    if reported is None:
        # First run: start counting from here without sending an email for old visits.
        state = {"reported_total": total, "reported_at": iso(now)}
        notice("notice", f"Visit reports started at {total} visits; first email after {EVERY - total % EVERY or EVERY} more.")
    elif total // EVERY > reported // EVERY:
        since = dt.datetime.strptime(state["reported_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        try:
            recent = countries(code, token, iso(since), iso(now))
            alltime = countries(code, token, START, iso(now))
        except Exception as exc:
            notice("warning", f"Could not read countries from GoatCounter ({exc}).")
            return
        milestone = total // EVERY * EVERY
        body = build_report(total, total - reported, since, now, recent, alltime, code)
        try:
            send_mail(f"Website report: {milestone} visits reached (amirnaderi.com)", body)
        except Exception as exc:
            notice("warning", f"Could not send the email ({exc}). Check MAIL_USERNAME and MAIL_PASSWORD.")
            return
        notice("notice", f"Report sent: {total} visits ({total - reported} new).")
        state = {"reported_total": total, "reported_at": iso(now)}
    else:
        print(f"{total} visits; next report at {(reported // EVERY + 1) * EVERY}.")
        return

    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
