"""
Zeno — Weekly Call Visibility Report
=====================================
Generates a clean weekly call report for Thomas and Lakshita.
No grading. Focus on visibility: what happened, who was on the call,
what was discussed, what's next — split into Client Calls and Internal Calls.

Sections:
  1. Week at a Glance (high-level counts)
  2. CSM Call Breakdown (who handled how many client calls)
  3. Client Calls — full summaries, RAG status, action items
  4. Internal Calls — brief summaries
  5. Fireflies Coverage

Input:  output/calls_<YYYY-MM-DD>.json
Output: email via Resend + output/call_report_<YYYY-MM-DD>.html
"""

import os, json, logging, requests, re
from pathlib import Path
from collections import defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
FROM_EMAIL     = "reports@amzprep.com"
FROM_NAME      = "Zeno · AMZ Prep"

TO_RECIPIENTS  = [
    ("Lakshita Dang",   "harishnath@amzprep.com"),
    ("Thomas Gewarges", "jerun@amzprep.com"),
]
CC_RECIPIENTS  = [
    "ari@amzprep.com",
]

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

TEAM_NAMES = {
    "navesh@amzprep.com":    "Navesh Khedu",
    "jacob@amzprep.com":     "Jacob Penney",
    "furqan@amzprep.com":    "Furqan Ali",
    "omer@amzprep.com":      "Omer Muhammad",
    "deepakshi@amzprep.com": "Deepakshi Sharma",
    "prakash@amzprep.com":   "Prakash Thakkar",
    "dan@amzprep.com":       "Danny Prabudial",
    "lakshita@amzprep.com":  "Lakshita Dang",
    "thomas@amzprep.com":    "Thomas Gewarges",
    "trini@amzprep.com":     "Trini Baldon",
    "roshni@amzprep.com":    "Roshni Nair",
}

# ─── RAG keywords ─────────────────────────────────────────────────────────────
RED_KEYWORDS = [
    "cancel", "canceling", "cancelling", "leaving", "switching",
    "frustrated", "frustration", "extremely disappointed", "very unhappy",
    "unacceptable", "legal", "escalate to management", "very upset",
    "not happy", "terminate", "chargeback", "lawsuit", "angry",
    "outraged", "shipbob", "deliverr", "stord", "going with another",
    "this is unacceptable", "serious issue", "critical issue",
]
YELLOW_KEYWORDS = [
    "concern", "issue", "problem", "delay", "late", "missed sla",
    "not resolved", "still waiting", "unclear", "confused", "pushback",
    "pricing concern", "too expensive", "no next step", "unresolved",
    "need to follow up", "needs clarification", "investigate",
    "error", "mislabeled", "damaged", "lost shipment", "wrong sku",
    "placement fee", "chargeback risk", "not sure", "pending",
]
GREEN_KEYWORDS = [
    "resolved", "great call", "happy", "satisfied", "confirmed",
    "booked", "scheduled next", "positive", "going well", "on track",
    "no issues", "good relationship", "expanding", "growing",
    "new sku", "new channel", "excellent", "smooth", "working well",
    "thank", "appreciate", "pleased",
]

FIREFLIES_MISSING_REASONS = [
    "Chrome extension may not be installed or active on the rep's browser",
    "Call was not created via Google Calendar — Fireflies needs a calendar event to auto-join",
    "Call duration was too short for Fireflies to process a transcript",
    "Fireflies bot was manually removed from the call",
]

RAG_COLORS = {
    "RED":    {"bg": "#FFF5F5", "border": "#E53E3E", "text": "#C53030", "pill_bg": "#FED7D7"},
    "YELLOW": {"bg": "#FFFBEB", "border": "#D69E2E", "text": "#92600A", "pill_bg": "#FEFCBF"},
    "GREEN":  {"bg": "#F0FFF4", "border": "#38A169", "text": "#22543D", "pill_bg": "#C6F6D5"},
}


# ─── Helpers ──────────────────────────────────────────────────────────────────
def load_latest_calls() -> dict:
    files = sorted(OUTPUT_DIR.glob("calls_*.json"))
    if not files:
        raise FileNotFoundError("No Phase 1 output found.")
    path = files[-1]
    log.info(f"Loading calls from {path}")
    return json.loads(path.read_text())


def detect_rag(call: dict) -> str:
    text = " ".join([
        call.get("short_summary") or "",
        call.get("action_items") or "",
        call.get("title") or "",
    ]).lower()
    for kw in RED_KEYWORDS:
        if kw in text:
            return "RED"
    for kw in YELLOW_KEYWORDS:
        if kw in text:
            return "YELLOW"
    if any(kw in text for kw in GREEN_KEYWORDS):
        return "GREEN"
    return "YELLOW"


def rag_label(rag: str) -> str:
    return {
        "RED":    "Needs Attention",
        "YELLOW": "Monitor Closely",
        "GREEN":  "On Track",
    }.get(rag, "Monitor Closely")


def get_rep_names(call: dict) -> list[str]:
    return [TEAM_NAMES.get(e, e.split("@")[0].title())
            for e in (call.get("team_members_on_call") or [])]


def get_client_label(call: dict) -> str:
    title = call.get("title") or ""
    for sep in [" - ", " – ", ": ", " / ", " | "]:
        if sep in title:
            for part in title.split(sep):
                part = part.strip()
                if part and "amz" not in part.lower() and len(part) > 2:
                    return part
    return title[:60] if title else "Unknown"


def extract_bullets(call: dict) -> list[str]:
    summary = (call.get("short_summary") or "").strip()
    if not summary:
        return []
    sentences = re.split(r'(?<=[.!?])\s+', summary)
    bullets = []
    for s in sentences:
        s = s.strip().rstrip(".")
        if len(s) > 25:
            bullets.append(s[:200] + "..." if len(s) > 200 else s)
        if len(bullets) == 5:
            break
    if not bullets and call.get("keywords"):
        bullets.append(f"Topics covered: {', '.join((call.get('keywords') or [])[:6])}")
    return bullets


def extract_action_items(call: dict) -> list[dict]:
    raw = (call.get("action_items") or "").strip()
    if not raw:
        return []
    items = []
    current_owner = "Team"
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("**") and line.endswith("**"):
            current_owner = line.strip("*").strip()
            continue
        if line.startswith(("-", "•")):
            action = re.sub(r'\s*\(\d+:\d+\)\s*$', '', line.lstrip("-•").strip())
            if action and len(action) > 5:
                items.append({"owner": current_owner, "action": action})
    return items[:8]


# ─── Data builders ────────────────────────────────────────────────────────────
def build_snapshot(calls: list[dict]) -> dict:
    client_calls   = [c for c in calls if c["call_type"] in ("CS_AM", "OPS")]
    internal_calls = [c for c in calls if c["call_type"] == "INTERNAL"]
    cs_am          = [c for c in calls if c["call_type"] == "CS_AM"]
    ops            = [c for c in calls if c["call_type"] == "OPS"]

    csm_counts = defaultdict(int)
    for call in client_calls:
        for email in (call.get("team_members_on_call") or []):
            if email in TEAM_NAMES:
                csm_counts[email] += 1

    rag_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    for call in client_calls:
        rag_counts[detect_rag(call)] += 1

    return {
        "total_client":   len(client_calls),
        "total_cs_am":    len(cs_am),
        "total_ops":      len(ops),
        "total_internal": len(internal_calls),
        "total_all":      len(calls),
        "csm_counts":     dict(csm_counts),
        "rag_counts":     rag_counts,
    }


def build_client_reports(calls: list[dict]) -> list[dict]:
    external = sorted(
        [c for c in calls if c["call_type"] in ("CS_AM", "OPS")],
        key=lambda c: c.get("date") or "",
        reverse=True,
    )
    reports = []
    for call in external:
        rag = detect_rag(call)
        reports.append({
            "id":           call["id"],
            "title":        call.get("title") or "Untitled",
            "client":       get_client_label(call),
            "call_type":    call["call_type"],
            "date":         (call.get("date") or "")[:10],
            "duration":     f"{int(call.get('duration_minutes') or 0)} min",
            "reps":         get_rep_names(call),
            "rag":          rag,
            "rag_label":    rag_label(rag),
            "bullets":      extract_bullets(call),
            "action_items": extract_action_items(call),
            "has_summary":  bool(call.get("short_summary")),
            "ff_joined":    call.get("fireflies_joined", False),
            "fireflies_url": f"https://app.fireflies.ai/view/{call['id']}",
        })
    return reports


def build_internal_reports(calls: list[dict]) -> list[dict]:
    internal = sorted(
        [c for c in calls if c["call_type"] == "INTERNAL"],
        key=lambda c: c.get("date") or "",
        reverse=True,
    )
    reports = []
    for call in internal:
        reports.append({
            "title":        call.get("title") or "Untitled",
            "date":         (call.get("date") or "")[:10],
            "duration":     f"{int(call.get('duration_minutes') or 0)} min",
            "reps":         get_rep_names(call),
            "bullets":      extract_bullets(call),
            "action_items": extract_action_items(call),
            "has_summary":  bool(call.get("short_summary")),
            "ff_joined":    call.get("fireflies_joined", False),
            "fireflies_url": f"https://app.fireflies.ai/view/{call['id']}",
        })
    return reports


def build_missing_ff(calls: list[dict]) -> list[dict]:
    return [
        {
            "title":    call.get("title") or "Untitled",
            "date":     (call.get("date") or "")[:10],
            "reps":     get_rep_names(call),
            "type":     call["call_type"],
        }
        for call in calls
        if not call.get("fireflies_joined") and call["call_type"] != "SKIP"
    ]


# ─── HTML builder ─────────────────────────────────────────────────────────────
def build_html(snapshot, client_reports, internal_reports, missing_ff, date_range, run_date) -> str:
    week_range = f"{date_range['from']} to {date_range['to']}"
    rag = snapshot["rag_counts"]

    css = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #EAEEF3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; color: #1A202C; -webkit-font-smoothing: antialiased; }
.outer { max-width: 680px; margin: 0 auto; padding: 32px 16px 48px; }

/* Header */
.hdr { background: linear-gradient(135deg, #1A365D 0%, #2B6CB0 100%); border-radius: 12px 12px 0 0; padding: 36px 40px; }
.hdr-brand { font-size: 13px; font-weight: 700; color: #90CDF4; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 10px; }
.hdr-title { font-size: 24px; font-weight: 800; color: #FFFFFF; line-height: 1.3; }
.hdr-sub { font-size: 13px; color: #BEE3F8; margin-top: 8px; line-height: 1.5; }

/* Cards */
.card { background: #FFFFFF; border-left: 1px solid #DDE3EC; border-right: 1px solid #DDE3EC; padding: 32px 40px; }
.card + .card { border-top: 1px solid #EDF2F7; }
.card-last { border-radius: 0 0 12px 12px; border-bottom: 1px solid #DDE3EC; }

/* Section headings */
.sec-label { font-size: 10px; font-weight: 700; letter-spacing: 1.8px; text-transform: uppercase; color: #718096; margin-bottom: 6px; }
.sec-title { font-size: 18px; font-weight: 700; color: #1A365D; margin-bottom: 20px; padding-bottom: 14px; border-bottom: 2px solid #EBF4FF; line-height: 1.3; }

/* Stats grid */
.stats-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.stat-box { flex: 1; min-width: 120px; background: #F7FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 20px 16px 18px; text-align: center; }
.stat-box .num { font-size: 32px; font-weight: 800; color: #1A365D; line-height: 1; }
.stat-box .lbl { font-size: 11px; color: #718096; margin-top: 8px; text-transform: uppercase; letter-spacing: 0.8px; line-height: 1.4; }
.stat-box.red   { border-color: #FC8181; }
.stat-box.yellow{ border-color: #F6AD55; }
.stat-box.green { border-color: #68D391; }
.stat-box.red   .num { color: #C53030; }
.stat-box.yellow .num { color: #92600A; }
.stat-box.green .num { color: #22543D; }

/* CSM table */
.csm-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.csm-table thead tr { background: #EBF4FF; }
.csm-table th { padding: 11px 14px; text-align: left; font-size: 11px; font-weight: 700; color: #2C5282; text-transform: uppercase; letter-spacing: 0.7px; }
.csm-table td { padding: 12px 14px; border-bottom: 1px solid #F0F4F8; vertical-align: middle; }
.csm-table tr:last-child td { border-bottom: none; }
.csm-table .name { font-weight: 600; color: #2D3748; font-size: 14px; }
.csm-table .count { font-size: 14px; font-weight: 700; color: #2B6CB0; }
.bar-wrap { height: 8px; background: #EBF4FF; border-radius: 4px; overflow: hidden; margin-top: 6px; min-width: 80px; }
.bar-fill { height: 100%; border-radius: 4px; background: linear-gradient(90deg, #3182CE, #63B3ED); }

/* Call cards */
.call-card { border-radius: 10px; padding: 22px 24px; margin-bottom: 16px; border-left: 4px solid #CBD5E0; }
.call-card:last-child { margin-bottom: 0; }
.call-card-title { font-size: 15px; font-weight: 700; color: #1A202C; line-height: 1.4; margin-bottom: 6px; }
.call-card-title a { color: #1A202C; text-decoration: none; }
.call-card-title a:hover { text-decoration: underline; }
.call-type-badge { display: inline-block; font-size: 10px; font-weight: 700; padding: 3px 9px; border-radius: 10px; margin-left: 8px; vertical-align: middle; letter-spacing: 0.5px; text-transform: uppercase; }
.badge-merchant { background: #EBF4FF; color: #2B6CB0; }
.badge-ops { background: #FAF5FF; color: #6B46C1; }
.call-meta { font-size: 13px; color: #718096; margin-bottom: 14px; line-height: 1.6; }
.call-meta strong { color: #4A5568; font-weight: 600; }

/* RAG pill */
.rag-row { margin-bottom: 16px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rag-pill { display: inline-block; font-size: 11px; font-weight: 700; padding: 5px 14px; border-radius: 20px; letter-spacing: 0.5px; text-transform: uppercase; }
.rag-reason-txt { font-size: 13px; font-style: italic; color: #4A5568; line-height: 1.5; }

/* Summary bullets */
.summary-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #4A5568; margin-bottom: 10px; }
.bullets { list-style: none; padding: 0; margin: 0 0 16px 0; }
.bullets li { font-size: 14px; color: #2D3748; line-height: 1.7; padding: 5px 0 5px 18px; position: relative; border-bottom: 1px solid #F7FAFC; }
.bullets li:last-child { border-bottom: none; }
.bullets li::before { content: "–"; position: absolute; left: 0; color: #A0AEC0; font-weight: 700; }
.no-summary-note { font-size: 13px; color: #A0AEC0; font-style: italic; padding: 12px 0; }

/* Action items */
.action-block { background: #F7FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 16px 18px; margin-top: 14px; }
.action-block-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #4A5568; margin-bottom: 12px; }
.action-row { display: flex; gap: 10px; padding: 7px 0; border-bottom: 1px solid #EDF2F7; font-size: 13px; line-height: 1.5; }
.action-row:last-child { border-bottom: none; padding-bottom: 0; }
.action-owner { font-weight: 700; color: #2B6CB0; min-width: 110px; flex-shrink: 0; }
.action-text { color: #2D3748; }

/* Fireflies link */
.ff-link { display: inline-block; margin-top: 14px; font-size: 12px; font-weight: 600; color: #3182CE; text-decoration: none; }

/* Internal calls - compact */
.internal-card { background: #FAFBFD; border: 1px solid #E2E8F0; border-radius: 8px; padding: 18px 20px; margin-bottom: 12px; }
.internal-card:last-child { margin-bottom: 0; }
.internal-title { font-size: 14px; font-weight: 700; color: #2D3748; margin-bottom: 5px; line-height: 1.4; }
.internal-title a { color: #2D3748; text-decoration: none; }
.internal-meta { font-size: 12px; color: #A0AEC0; margin-bottom: 10px; line-height: 1.5; }
.internal-bullets { list-style: none; padding: 0; margin: 0; }
.internal-bullets li { font-size: 13px; color: #4A5568; padding: 4px 0 4px 16px; position: relative; line-height: 1.6; }
.internal-bullets li::before { content: "–"; position: absolute; left: 0; color: #CBD5E0; }

/* Missing FF */
.ff-row { padding: 9px 0; border-bottom: 1px solid #F0F4F8; font-size: 13px; color: #4A5568; line-height: 1.5; }
.ff-row:last-child { border-bottom: none; }
.ff-reasons { list-style: none; padding: 0; margin: 0 0 16px 0; }
.ff-reasons li { font-size: 13px; color: #4A5568; padding: 5px 0 5px 16px; position: relative; line-height: 1.5; }
.ff-reasons li::before { content: "·"; position: absolute; left: 0; color: #A0AEC0; font-weight: 700; }

/* Footer */
.footer { text-align: center; padding: 24px 20px; font-size: 12px; color: #A0AEC0; line-height: 2; }
.footer a { color: #3182CE; text-decoration: none; }
"""

    # ── HTML open ─────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Zeno — Weekly Call Report</title>
<style>{css}</style>
</head>
<body>
<div class="outer">

<!-- HEADER -->
<div class="hdr">
  <div class="hdr-brand">Zeno · AMZ Prep</div>
  <div class="hdr-title">Weekly Call Visibility Report</div>
  <div class="hdr-sub">Week: {week_range} &nbsp;·&nbsp; Generated {run_date}</div>
</div>"""

    # ── Snapshot ──────────────────────────────────────────────────────────────
    html += f"""
<!-- SNAPSHOT -->
<div class="card">
  <div class="sec-label">Overview</div>
  <div class="sec-title">Week at a Glance</div>
  <div class="stats-grid">
    <div class="stat-box">
      <div class="num">{snapshot['total_cs_am']}</div>
      <div class="lbl">Merchant Calls</div>
    </div>
    <div class="stat-box">
      <div class="num">{snapshot['total_ops']}</div>
      <div class="lbl">Warehouse / Ops Calls</div>
    </div>
    <div class="stat-box">
      <div class="num">{snapshot['total_internal']}</div>
      <div class="lbl">Internal Calls</div>
    </div>
    <div class="stat-box">
      <div class="num">{snapshot['total_all']}</div>
      <div class="lbl">Total Calls</div>
    </div>
  </div>
  <div class="stats-grid">
    <div class="stat-box red">
      <div class="num">{rag['RED']}</div>
      <div class="lbl">Needs Attention</div>
    </div>
    <div class="stat-box yellow">
      <div class="num">{rag['YELLOW']}</div>
      <div class="lbl">Monitor Closely</div>
    </div>
    <div class="stat-box green">
      <div class="num">{rag['GREEN']}</div>
      <div class="lbl">On Track</div>
    </div>
  </div>
</div>"""

    # ── CSM Breakdown ─────────────────────────────────────────────────────────
    csm_counts = snapshot["csm_counts"]
    max_calls  = max(csm_counts.values()) if csm_counts else 1
    sorted_csm = sorted(csm_counts.items(), key=lambda x: -x[1])

    html += """
<!-- CSM BREAKDOWN -->
<div class="card">
  <div class="sec-label">Team</div>
  <div class="sec-title">CSM Client Call Breakdown</div>
  <table class="csm-table">
    <thead><tr>
      <th>CSM</th>
      <th>Client Calls</th>
      <th style="width:200px;">Volume</th>
    </tr></thead>
    <tbody>"""

    for email, count in sorted_csm:
        name = TEAM_NAMES.get(email, email.split("@")[0].title())
        pct  = int((count / max_calls) * 100)
        html += f"""
      <tr>
        <td class="name">{name}</td>
        <td class="count">{count}</td>
        <td>
          <div class="bar-wrap">
            <div class="bar-fill" style="width:{pct}%;"></div>
          </div>
        </td>
      </tr>"""

    html += """
    </tbody>
  </table>
</div>"""

    # ── Client Calls ──────────────────────────────────────────────────────────
    html += f"""
<!-- CLIENT CALLS -->
<div class="card">
  <div class="sec-label">External</div>
  <div class="sec-title">Client Calls &nbsp;<span style="font-size:14px;font-weight:500;color:#718096;">({len(client_reports)} calls)</span></div>"""

    if not client_reports:
        html += """<p style="font-size:14px;color:#A0AEC0;font-style:italic;">No external calls recorded this week.</p>"""
    else:
        for r in client_reports:
            cfg      = RAG_COLORS.get(r["rag"], RAG_COLORS["YELLOW"])
            type_cls = "badge-merchant" if r["call_type"] == "CS_AM" else "badge-ops"
            type_lbl = "Merchant" if r["call_type"] == "CS_AM" else "Warehouse / Ops"
            reps_str = ", ".join(r["reps"]) or "—"

            html += f"""
  <div class="call-card" style="background:{cfg['bg']};border-left-color:{cfg['border']};">

    <div class="call-card-title">
      <a href="{r['fireflies_url']}">{r['title']}</a>
      <span class="call-type-badge {type_cls}">{type_lbl}</span>
    </div>

    <div class="call-meta">
      <strong>Date:</strong> {r['date']} &nbsp;·&nbsp;
      <strong>Duration:</strong> {r['duration']} &nbsp;·&nbsp;
      <strong>Rep:</strong> {reps_str}
    </div>

    <div class="rag-row">
      <span class="rag-pill" style="background:{cfg['pill_bg']};color:{cfg['text']};">{r['rag_label']}</span>
      <span class="rag-reason-txt">{r['rag_label'] == 'On Track' and 'No concerns raised on this call.' or ''}</span>
    </div>"""

            if not r["has_summary"]:
                html += """<p class="no-summary-note">Fireflies did not capture a summary for this call. Check that the Chrome extension is active.</p>"""
            else:
                html += """<div class="summary-label">Call Summary</div>
    <ul class="bullets">"""
                for b in r["bullets"]:
                    html += f"<li>{b}</li>"
                html += "</ul>"

            if r["action_items"]:
                html += """<div class="action-block">
      <div class="action-block-title">Action Items &amp; Takeaways</div>"""
                for item in r["action_items"]:
                    html += f"""
      <div class="action-row">
        <span class="action-owner">{item['owner']}</span>
        <span class="action-text">{item['action']}</span>
      </div>"""
                html += "</div>"
            else:
                html += """<div class="action-block">
      <div class="action-block-title">Action Items &amp; Takeaways</div>
      <p style="font-size:13px;color:#A0AEC0;font-style:italic;margin:0;">No action items captured for this call.</p>
    </div>"""

            html += f"""
    <a href="{r['fireflies_url']}" class="ff-link">View full transcript in Fireflies &rarr;</a>
  </div>"""

    html += "\n</div>"

    # ── Internal Calls ────────────────────────────────────────────────────────
    html += f"""
<!-- INTERNAL CALLS -->
<div class="card">
  <div class="sec-label">Internal</div>
  <div class="sec-title">Internal Calls &nbsp;<span style="font-size:14px;font-weight:500;color:#718096;">({len(internal_reports)} calls)</span></div>"""

    if not internal_reports:
        html += """<p style="font-size:14px;color:#A0AEC0;font-style:italic;">No internal calls recorded this week.</p>"""
    else:
        for r in internal_reports:
            reps_str = ", ".join(r["reps"]) or "—"
            html += f"""
  <div class="internal-card">
    <div class="internal-title">
      <a href="{r['fireflies_url']}">{r['title']}</a>
    </div>
    <div class="internal-meta">
      {r['date']} &nbsp;·&nbsp; {r['duration']} &nbsp;·&nbsp; {reps_str}
    </div>"""

            if r["bullets"]:
                html += """<ul class="internal-bullets">"""
                for b in r["bullets"]:
                    html += f"<li>{b}</li>"
                html += "</ul>"
            else:
                html += """<p style="font-size:13px;color:#A0AEC0;font-style:italic;margin:0;">No summary available.</p>"""

            if r["action_items"]:
                html += """<div style="margin-top:10px;padding-top:10px;border-top:1px solid #EDF2F7;">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#718096;margin-bottom:8px;">Takeaways</div>"""
                for item in r["action_items"]:
                    html += f"""<div style="font-size:13px;color:#4A5568;padding:4px 0;line-height:1.5;">
        <span style="font-weight:700;color:#4A5568;">{item['owner']}:</span> {item['action']}
      </div>"""
                html += "</div>"

            html += "\n  </div>"

    html += "\n</div>"

    # ── Missing Fireflies ─────────────────────────────────────────────────────
    ext_missing = [m for m in missing_ff if m["type"] in ("CS_AM", "OPS")]
    int_missing = [m for m in missing_ff if m["type"] == "INTERNAL"]

    html += f"""
<!-- FIREFLIES COVERAGE -->
<div class="card card-last">
  <div class="sec-label">Coverage</div>
  <div class="sec-title">Fireflies Recording Coverage</div>"""

    if not missing_ff:
        html += """<p style="font-size:14px;color:#22543D;font-weight:600;">
    All calls were recorded this week. Coverage is complete.
  </p>"""
    else:
        html += f"""<p style="font-size:14px;color:#C53030;font-weight:600;margin-bottom:6px;">
    {len(missing_ff)} call(s) not recorded &nbsp;·&nbsp;
    {len(ext_missing)} external &nbsp;·&nbsp; {len(int_missing)} internal
  </p>
  <p style="font-size:13px;color:#718096;margin-bottom:16px;line-height:1.6;">
    Every call — internal and external — should appear in Fireflies automatically
    once the Chrome extension is installed and active. No manual bot invite is needed.
    Common reasons calls go missing:
  </p>
  <ul class="ff-reasons">"""
        for reason in FIREFLIES_MISSING_REASONS:
            html += f"<li>{reason}</li>"
        html += "</ul>"

        if ext_missing:
            html += f"""<div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#C53030;margin-bottom:10px;">
    External — Priority to Fix ({len(ext_missing)})
  </div>"""
            for m in ext_missing:
                reps = ", ".join(m["reps"]) or "—"
                html += f"""<div class="ff-row">
    <strong>{m['title']}</strong> &nbsp;·&nbsp; {m['date']} &nbsp;·&nbsp; {reps}
  </div>"""

        if int_missing:
            html += f"""<div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#92600A;margin:18px 0 10px;">
    Internal ({len(int_missing)})
  </div>"""
            for m in int_missing[:12]:
                reps = ", ".join(m["reps"]) or "—"
                html += f"""<div class="ff-row">
    {m['title']} &nbsp;·&nbsp; {m['date']} &nbsp;·&nbsp; {reps}
  </div>"""
            if len(int_missing) > 12:
                html += f"""<p style="font-size:12px;color:#A0AEC0;font-style:italic;padding-top:8px;">
    ... and {len(int_missing) - 12} more internal calls not recorded.
  </p>"""

    html += "</div>"

    # ── Footer ────────────────────────────────────────────────────────────────
    html += f"""
<!-- FOOTER -->
<div class="footer">
  Sent by <strong>Zeno</strong> &nbsp;·&nbsp; AMZ Prep Call QA Agent<br/>
  Report generated {run_date} &nbsp;·&nbsp;
  Questions: <a href="mailto:ari@amzprep.com">ari@amzprep.com</a>
</div>

</div><!-- outer -->
</body>
</html>"""

    return html


# ─── Send + main ──────────────────────────────────────────────────────────────
def send_email(subject: str, html: str) -> bool:
    to_list = [f"{name} <{email}>" for name, email in TO_RECIPIENTS]
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from":    f"{FROM_NAME} <{FROM_EMAIL}>",
            "to":      to_list,
            "cc":      CC_RECIPIENTS,
            "subject": subject,
            "html":    html,
        },
        timeout=30,
    )
    if resp.ok:
        log.info(f"Report sent to {', '.join(to_list)}")
        log.info(f"CC: {', '.join(CC_RECIPIENTS)}")
        return True
    log.error(f"Email failed: {resp.status_code} {resp.text[:300]}")
    return False


def main():
    log.info("=== Zeno — Call Visibility Report ===")
    data       = load_latest_calls()
    run_date   = data["run_date"]
    calls      = data["calls"]
    date_range = data["date_range"]

    log.info(f"Processing {len(calls)} calls | {date_range['from']} to {date_range['to']}")

    snapshot         = build_snapshot(calls)
    client_reports   = build_client_reports(calls)
    internal_reports = build_internal_reports(calls)
    missing          = build_missing_ff(calls)

    log.info(f"Client: {snapshot['total_client']} | Internal: {snapshot['total_internal']} | Missing FF: {len(missing)}")
    log.info(f"RAG — RED: {snapshot['rag_counts']['RED']} | YELLOW: {snapshot['rag_counts']['YELLOW']} | GREEN: {snapshot['rag_counts']['GREEN']}")

    html     = build_html(snapshot, client_reports, internal_reports, missing, date_range, run_date)
    out_path = OUTPUT_DIR / f"call_report_{run_date}.html"
    out_path.write_text(html)
    log.info(f"Saved to {out_path}")

    week_range = f"{date_range['from']} to {date_range['to']}"
    send_email(f"Zeno — Weekly Call Report | {week_range}", html)
    log.info("=== Call Visibility Report complete ===")


if __name__ == "__main__":
    main()
