#!/usr/bin/env python3
"""
Parking Monitor - strefaprogress.pl investment 106
Checks daily availability of parking spots and storage rooms.
Sends Gmail notification when status changes.
"""

import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.request import urlopen, Request

API_URL = "https://backend.quptos.sensevr.pl/api/0/investment/106/supplements?show_hidden=false"
STATE_FILE = Path("state.json")
HTML_FILE = Path("index.html")

TYPE_LABELS = {
    "parking_outdoor": "Parking zewnętrzny",
    "storage": "Komórka lokatorska",
}


def fetch_data() -> list:
    req = Request(API_URL, headers={"User-Agent": "parking-monitor/1.0"})
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def extract_items(data: list) -> dict:
    """Return dict keyed by display_name with relevant fields."""
    return {
        item["display_name"]: {
            "display_name": item["display_name"],
            "type": item["type"],
            "sales_status": item["sales_status"],
            "cost": item["cost"],
            "currency": item["currency"],
        }
        for item in data
        if item["type"] in TYPE_LABELS
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # Support both old flat format and new format with metadata
        if "items" in raw:
            return raw
        return {"items": raw, "last_check": None}
    return {"items": {}, "last_check": None}


def save_state(items: dict, last_check: str) -> None:
    STATE_FILE.write_text(
        json.dumps({"items": items, "last_check": last_check}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def compute_diff(old: dict, new: dict) -> list:
    """Return list of change tuples: (event, item, old_status?)"""
    changes = []
    for name, item in new.items():
        if name not in old:
            changes.append(("new", item, None))
        elif old[name]["sales_status"] != item["sales_status"]:
            changes.append(("changed", item, old[name]["sales_status"]))
    for name, item in old.items():
        if name not in new:
            changes.append(("removed", item, None))
    return changes


STATUS_PL = {"free": "Wolne", "sold": "Sprzedane"}
STATUS_COLOR = {"free": "#22c55e", "sold": "#ef4444"}

CHANGE_ICONS = {
    "new": "[NEW]",
    "changed": "[ZMIANA]",
    "removed": "[USUNIETO]",
}
CHANGE_ICONS_HTML = {
    "new": "&#x1F195;",
    "changed": "&#x1F504;",
    "removed": "&#x274C;",
}


def _change_description(event: str, item: dict, old_status: str | None) -> str:
    type_label = TYPE_LABELS.get(item["type"], item["type"])
    name = item["display_name"]
    status = STATUS_PL.get(item["sales_status"], item["sales_status"])
    if event == "new":
        return f"{type_label} #{name} — pojawił się ({status})"
    if event == "removed":
        return f"{type_label} #{name} — zniknął z oferty"
    old = STATUS_PL.get(old_status, old_status)
    return f"{type_label} #{name} — {old} → {status}"


def send_email(changes: list, current: dict, last_check: str) -> None:
    gmail_from = os.environ["GMAIL_FROM"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    notify_email = os.environ["NOTIFY_EMAIL"]

    free_count = sum(1 for i in current.values() if i["sales_status"] == "free")
    subject = f"[Strefa Progress] Zmiany parkingów — {len(changes)} zmian, {free_count} wolnych"

    # Plain text
    lines = [f"Sprawdzono: {last_check}", "", "ZMIANY:", ""]
    for event, item, old_status in changes:
        icon = CHANGE_ICONS.get(event, "•")
        lines.append(f"  {icon} {_change_description(event, item, old_status)}")
    lines += ["", "AKTUALNIE WOLNE:", ""]
    free_items = [i for i in current.values() if i["sales_status"] == "free"]
    if free_items:
        def _free_sort(x):
            try:
                return (x["type"], 0, int(x["display_name"]))
            except ValueError:
                return (x["type"], 1, x["display_name"])
        for item in sorted(free_items, key=_free_sort):
            label = TYPE_LABELS.get(item["type"], item["type"])
            lines.append(f"  • {label} #{item['display_name']} — {item['cost']:,.0f} {item['currency']}")
    else:
        lines.append("  (brak wolnych)")
    body_text = "\n".join(lines)

    # HTML
    changes_html = "".join(
        f'<li>{CHANGE_ICONS_HTML.get(ev,"&bull;")} {_change_description(ev, it, old)}</li>'
        for ev, it, old in changes
    )
    free_rows = "".join(
        f'<tr><td>{TYPE_LABELS.get(i["type"], i["type"])}</td>'
        f'<td>#{i["display_name"]}</td>'
        f'<td style="color:{STATUS_COLOR["free"]};font-weight:bold">Wolne</td>'
        f'<td>{i["cost"]:,.0f} {i["currency"]}</td></tr>'
        for i in sorted(free_items, key=_free_sort)
    ) or '<tr><td colspan="4" style="text-align:center;color:#888">Brak wolnych miejsc</td></tr>'

    body_html = f"""
    <html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:20px">
    <h2 style="color:#1d4ed8">Strefa Progress — Aktualizacja parkingów</h2>
    <p style="color:#666">Sprawdzono: {last_check}</p>
    <h3>Zmiany ({len(changes)})</h3>
    <ul>{''.join(f'<li>{CHANGE_ICONS.get(ev,"•")} {_change_description(ev, it, old)}</li>' for ev, it, old in changes)}</ul>
    <h3>Aktualnie wolne ({len(free_items)})</h3>
    <table border="0" cellspacing="0" cellpadding="6"
           style="border-collapse:collapse;width:100%;border:1px solid #e5e7eb">
      <tr style="background:#f3f4f6;font-weight:bold">
        <th>Typ</th><th>Nr</th><th>Status</th><th>Cena</th>
      </tr>
      {free_rows}
    </table>
    <p style="margin-top:20px;font-size:12px;color:#9ca3af">
      <a href="https://strefaprogress.pl/mieszkania/">strefaprogress.pl/mieszkania/</a>
    </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_from
    msg["To"] = notify_email
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_from, gmail_password)
        server.sendmail(gmail_from, notify_email, msg.as_string())
    print(f"Email sent to {notify_email}")


def generate_html(current: dict, changes: list, last_check: str) -> None:
    by_type: dict[str, list] = {}
    for item in current.values():
        by_type.setdefault(item["type"], []).append(item)

    sections = ""
    for type_key, label in TYPE_LABELS.items():
        def sort_key(x):
            try:
                return (0, int(x["display_name"]))
            except ValueError:
                return (1, x["display_name"])
        items = sorted(by_type.get(type_key, []), key=sort_key)
        if not items:
            continue
        free = [i for i in items if i["sales_status"] == "free"]
        rows = "".join(
            f'<tr>'
            f'<td>#{i["display_name"]}</td>'
            f'<td style="color:{STATUS_COLOR[i["sales_status"]]};font-weight:600">'
            f'{STATUS_PL.get(i["sales_status"], i["sales_status"])}</td>'
            f'<td>{i["cost"]:,.0f} {i["currency"]}</td>'
            f'</tr>'
            for i in items
        )
        sections += f"""
        <section>
          <h2>{label} <span class="badge">{len(free)}/{len(items)} wolnych</span></h2>
          <table>
            <tr><th>Numer</th><th>Status</th><th>Cena</th></tr>
            {rows}
          </table>
        </section>
        """

    changes_html = ""
    if changes:
        change_items = "".join(
            f'<li class="change-{ev}">'
            f'{CHANGE_ICONS_HTML.get(ev,"&bull;")} {_change_description(ev, it, old)}'
            f'</li>'
            for ev, it, old in changes
        )
        changes_html = f'<section><h2>Ostatnie zmiany</h2><ul class="changes">{change_items}</ul></section>'

    total_free = sum(1 for i in current.values() if i["sales_status"] == "free")
    total = len(current)

    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Strefa Progress — Parkingi</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #f8fafc;
            color: #1e293b; margin: 0; padding: 20px; }}
    .container {{ max-width: 700px; margin: auto; }}
    header {{ background: #1d4ed8; color: white; padding: 24px 28px; border-radius: 12px;
              margin-bottom: 24px; }}
    header h1 {{ margin: 0 0 4px; font-size: 1.5rem; }}
    header p {{ margin: 0; opacity: .75; font-size: .9rem; }}
    .summary {{ display: flex; gap: 12px; margin-bottom: 24px; }}
    .card {{ background: white; border-radius: 10px; padding: 16px 20px; flex: 1;
             box-shadow: 0 1px 3px rgba(0,0,0,.08); text-align: center; }}
    .card .num {{ font-size: 2rem; font-weight: 700; color: #1d4ed8; }}
    .card .lbl {{ font-size: .8rem; color: #64748b; margin-top: 2px; }}
    section {{ background: white; border-radius: 10px; padding: 20px 24px;
               margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
    section h2 {{ margin: 0 0 16px; font-size: 1.1rem; display: flex;
                  align-items: center; gap: 10px; }}
    .badge {{ background: #dbeafe; color: #1d4ed8; font-size: .75rem; font-weight: 600;
              padding: 2px 8px; border-radius: 20px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
    th {{ text-align: left; padding: 6px 10px; background: #f1f5f9;
          color: #64748b; font-size: .78rem; text-transform: uppercase; }}
    td {{ padding: 7px 10px; border-bottom: 1px solid #f1f5f9; }}
    tr:last-child td {{ border-bottom: none; }}
    ul.changes {{ margin: 0; padding: 0 0 0 20px; }}
    ul.changes li {{ margin-bottom: 6px; font-size: .9rem; }}
    .change-changed {{ color: #d97706; }}
    .change-new {{ color: #2563eb; }}
    .change-removed {{ color: #dc2626; }}
    footer {{ text-align: center; font-size: .8rem; color: #94a3b8; margin-top: 24px; }}
    footer a {{ color: #94a3b8; }}
  </style>
</head>
<body>
<div class="container">
  <header>
    <h1>Strefa Progress — Parkingi i komórki</h1>
    <p>Ostatnie sprawdzenie: {last_check}</p>
  </header>
  <div class="summary">
    <div class="card"><div class="num">{total_free}</div><div class="lbl">Wolnych miejsc</div></div>
    <div class="card"><div class="num">{total - total_free}</div><div class="lbl">Sprzedanych</div></div>
    <div class="card"><div class="num">{total}</div><div class="lbl">Łącznie</div></div>
  </div>
  {changes_html}
  {sections}
  <footer>
    Dane: <a href="https://strefaprogress.pl/mieszkania/" target="_blank">strefaprogress.pl/mieszkania/</a>
    · Aktualizacja automatyczna raz dziennie
  </footer>
</div>
</body>
</html>
"""
    HTML_FILE.write_text(html, encoding="utf-8")
    print(f"HTML generated: {HTML_FILE}")


def main() -> None:
    print(f"Fetching {API_URL} ...")
    try:
        data = fetch_data()
    except Exception as exc:
        print(f"ERROR: Failed to fetch data: {exc}", file=sys.stderr)
        sys.exit(1)

    current = extract_items(data)
    state = load_state()
    old_items = state["items"]

    changes = compute_diff(old_items, current)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    free = [i for i in current.values() if i["sales_status"] == "free"]
    print(f"Items fetched: {len(current)} | Free: {len(free)} | Changes: {len(changes)}")
    for event, item, old_status in changes:
        print(f"  {CHANGE_ICONS.get(event,'?')} {_change_description(event, item, old_status)}")

    generate_html(current, changes, now)
    save_state(current, now)

    if changes and all(k in os.environ for k in ("GMAIL_FROM", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL")):
        try:
            send_email(changes, current, now)
        except Exception as exc:
            print(f"ERROR: Failed to send email: {exc}", file=sys.stderr)
    elif changes:
        print("Email env vars not set — skipping email.")
    else:
        print("No changes — no email sent.")


if __name__ == "__main__":
    main()
