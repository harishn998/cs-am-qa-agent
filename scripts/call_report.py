"""
CS/AM Call QA Agent — Call Visibility Report
=============================================
Generates a COO-level weekly visibility report for Thomas and Lakshita.
Runs after Phase 1 (fetch_and_classify.py) using the calls JSON directly.

NO external API required — all summarization is rule-based using
Fireflies summary + action items data already captured in Phase 1.

Report contains:
  1. High-level snapshot (external / ops / internal call counts)
  2. CSM breakdown (who spoke to how many clients)
  3. Per-call report (summary, RAG status, action items) for all external calls
  4. Fireflies coverage (who is missing recordings)

GitHub Actions env vars required:
  RESEND_API_KEY

Email:
  To:  lakshita@amzprep.com, thomas@amzprep.com
  CC:  ari@amzprep.com, harishnath@amzprep.com, blair@amzprep.com

Input:  output/calls_<YYYY-MM-DD>.json
Output: output/call_report_<YYYY-MM-DD>.html (saved for reference)
"""

import os
import json
import logging
import requests
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
FROM_EMAIL     = "reports@amzprep.com"
FROM_NAME      = "Zeno · AMZ Prep"

TO_RECIPIENTS  = [
    ("Lakshita Dang",   "lakshita@amzprep.com"),
    ("Thomas Gewarges", "thomas@amzprep.com"),
]
CC_RECIPIENTS  = [
    "ari@amzprep.com",
    "harishnath@amzprep.com",
    "blair@amzprep.com",
]

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── Team name map ────────────────────────────────────────────────────────────
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

# ─── RAG signal keywords ──────────────────────────────────────────────────────
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
    "Chrome extension may not be installed or active",
    "Meeting was not created through Google Calendar (no bot invite sent)",
    "Call was too short for Fireflies to process",
    "Bot was manually removed from the call",
]


# ─── Load Phase 1 output ──────────────────────────────────────────────────────
def load_latest_calls() -> dict:
    files = sorted(OUTPUT_DIR.glob("calls_*.json"))
    if not files:
        raise FileNotFoundError("No Phase 1 output found. Run fetch_and_classify.py first.")
    path = files[-1]
    log.info(f"Loading calls from {path}")
    return json.loads(path.read_text())


# ─── RAG status detection ─────────────────────────────────────────────────────
def detect_rag(call: dict) -> str:
    """
    Returns RED, YELLOW, or GREEN based on keyword analysis
    of the summary + action items + title.
    RED takes priority over YELLOW over GREEN.
    """
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

    # Default — has a summary but no clear signals
    return "YELLOW" if text.strip() else "YELLOW"


def rag_reason(call: dict, rag: str) -> str:
    """Returns a short plain-English reason for the RAG status."""
    text = " ".join([
        call.get("short_summary") or "",
        call.get("action_items") or "",
    ]).lower()

    if rag == "RED":
        for kw in RED_KEYWORDS:
            if kw in text:
                if any(c in kw for c in ["cancel", "leav", "switch", "terminat"]):
                    return "Customer expressed intent to leave or cancel"
                if any(c in kw for c in ["frustrat", "upset", "angry", "unacceptable"]):
                    return "Customer expressed clear frustration or dissatisfaction"
                if any(c in kw for c in ["shipbob", "deliverr", "stord"]):
                    return "Competitor mentioned by name"
                if "escalat" in kw:
                    return "Escalation raised on the call"
                return f"High-risk signal detected: '{kw}'"
        return "Multiple high-risk signals detected"

    if rag == "YELLOW":
        for kw in YELLOW_KEYWORDS:
            if kw in text:
                if "delay" in kw or "late" in kw or "sla" in kw:
                    return "SLA or delivery concern raised"
                if "pric" in kw or "expensive" in kw:
                    return "Pricing pushback or cost concern"
                if "error" in kw or "mislabel" in kw or "wrong" in kw:
                    return "Operational error or product issue discussed"
                if "unresolved" in kw or "not resolved" in kw or "pending" in kw:
                    return "Issue raised but not fully resolved"
                return "Concern or open issue on the call"
        return "No major issues but some items need follow-up"

    return "Call went smoothly — no concerns flagged"


# ─── Executive bullet point builder ──────────────────────────────────────────
def build_exec_bullets(call: dict) -> list[str]:
    """
    Extracts 4-5 exec bullet points from the Fireflies summary.
    Uses sentence splitting — no LLM needed.
    """
    summary = call.get("short_summary") or ""
    action_items = call.get("action_items") or ""

    bullets = []

    if not summary:
        bullets.append("No call summary available — Fireflies may not have captured this call.")
        return bullets

    # Split summary into sentences and pick the most meaningful ones
    sentences = re.split(r'(?<=[.!?])\s+', summary.strip())
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30]

    # Take up to 4 key sentences
    for sentence in sentences[:4]:
        # Clean up and format as exec bullet
        bullet = sentence.rstrip(".")
        if len(bullet) > 160:
            bullet = bullet[:157] + "..."
        bullets.append(bullet)

    # If summary was too short, pad with keywords context
    if len(bullets) < 2:
        keywords = call.get("keywords") or []
        if keywords:
            bullets.append(f"Key topics: {', '.join(keywords[:5])}")

    return bullets if bullets else ["No structured summary available for this call."]


# ─── Action items extractor ───────────────────────────────────────────────────
def extract_action_items(call: dict) -> list[dict]:
    """
    Parses the Fireflies action_items field into structured items.
    Format from Fireflies:
      **Person Name**
      - Action description (timestamp)
    Returns list of {"owner": str, "action": str}
    """
    raw = call.get("action_items") or ""
    if not raw.strip():
        return []

    items = []
    current_owner = "Team"

    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Owner header: **Name** or bold markdown
        if line.startswith("**") and line.endswith("**"):
            current_owner = line.strip("*").strip()
            continue
        # Action item line
        if line.startswith("-") or line.startswith("•"):
            action = line.lstrip("-•").strip()
            # Remove timestamp like (00:02) at end
            action = re.sub(r'\s*\(\d+:\d+\)\s*$', '', action).strip()
            if action and len(action) > 5:
                items.append({"owner": current_owner, "action": action})

    return items[:8]   # cap at 8 action items per call


# ─── External client name extractor ──────────────────────────────────────────
def get_client_name(call: dict) -> str:
    """Extract the client/company name from call title or external participants."""
    title = call.get("title") or ""

    # Common title patterns: "ClientName: Meeting Type" or "Rep / ClientName"
    for sep in [" - ", " – ", " : ", " / ", " | "]:
        if sep in title:
            parts = title.split(sep)
            # Return the part that doesn't look like an AMZ Prep internal name
            for part in parts:
                part = part.strip()
                if part and "amz" not in part.lower() and len(part) > 2:
                    return part
    return title[:50] if title else "Unknown Client"


# ─── Build high-level snapshot ────────────────────────────────────────────────
def build_snapshot(calls: list[dict]) -> dict:
    external_calls  = [c for c in calls if c["call_type"] in ("CS_AM", "OPS")]
    internal_calls  = [c for c in calls if c["call_type"] == "INTERNAL"]
    cs_am_calls     = [c for c in calls if c["call_type"] == "CS_AM"]
    ops_calls       = [c for c in calls if c["call_type"] == "OPS"]

    # CSM external call counts
    csm_counts = defaultdict(int)
    for call in external_calls:
        for email in call.get("team_members_on_call") or []:
            if email in TEAM_NAMES:
                csm_counts[email] += 1

    # RAG distribution
    rag_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    for call in external_calls:
        rag = detect_rag(call)
        rag_counts[rag] += 1

    return {
        "total_external":   len(external_calls),
        "total_cs_am":      len(cs_am_calls),
        "total_ops":        len(ops_calls),
        "total_internal":   len(internal_calls),
        "total_all":        len(calls),
        "csm_counts":       dict(csm_counts),
        "rag_counts":       rag_counts,
    }


# ─── Build per-call report data ───────────────────────────────────────────────
def build_call_reports(calls: list[dict]) -> list[dict]:
    """Build structured report for every external call."""
    external = [c for c in calls if c["call_type"] in ("CS_AM", "OPS")]

    # Sort by date descending
    external.sort(key=lambda c: c.get("date") or "", reverse=True)

    reports = []
    for call in external:
        rag    = detect_rag(call)
        reason = rag_reason(call, rag)
        reps   = [TEAM_NAMES.get(e, e.split("@")[0]) for e in (call.get("team_members_on_call") or [])]
        client = get_client_name(call)
        date   = (call.get("date") or "")[:10]
        dur    = call.get("duration_minutes") or 0
        dur_str = f"{int(dur)} min" if dur else "Unknown duration"

        reports.append({
            "id":           call["id"],
            "title":        call.get("title") or "Untitled",
            "client":       client,
            "call_type":    call["call_type"],
            "date":         date,
            "duration":     dur_str,
            "reps":         reps,
            "rag":          rag,
            "rag_reason":   reason,
            "bullets":      build_exec_bullets(call),
            "action_items": extract_action_items(call),
            "fireflies_url": f"https://app.fireflies.ai/view/{call['id']}",
            "has_summary":  bool(call.get("short_summary")),
            "ff_joined":    call.get("fireflies_joined", False),
        })

    return reports


# ─── Missing Fireflies report ─────────────────────────────────────────────────
def build_missing_ff_report(calls: list[dict]) -> list[dict]:
    """All team calls (external + internal) where Fireflies didn't join."""
    missing = []
    for call in calls:
        if call["call_type"] == "SKIP":
            continue
        if not call.get("fireflies_joined"):
            reps = [TEAM_NAMES.get(e, e.split("@")[0]) for e in (call.get("team_members_on_call") or [])]
            missing.append({
                "title":    call.get("title") or "Untitled",
                "date":     (call.get("date") or "")[:10],
                "reps":     reps,
                "type":     call["call_type"],
                "duration": call.get("duration_minutes") or 0,
            })
    return missing


# ─── HTML email builder ───────────────────────────────────────────────────────
RAG_COLORS = {
    "RED":    {"bg": "#FFF0F0", "border": "#E53E3E", "text": "#C53030", "label": "RED"},
    "YELLOW": {"bg": "#FFFBEB", "border": "#D69E2E", "text": "#B7791F", "label": "YELLOW"},
    "GREEN":  {"bg": "#F0FFF4", "border": "#38A169", "text": "#276749", "label": "GREEN"},
}

def build_email_html(
    snapshot: dict,
    call_reports: list[dict],
    missing_ff: list[dict],
    date_range: dict,
    run_date: str,
) -> str:
    week_range = f"{date_range['from']} to {date_range['to']}"
    rag = snapshot["rag_counts"]

    # ── CSS ───────────────────────────────────────────────────────────────────
    css = """
    body { margin:0; padding:0; background:#f0f2f5; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif; color:#1a202c; }
    .wrap { max-width:700px; margin:0 auto; padding:28px 16px; }

    /* Header */
    .hdr { background:linear-gradient(135deg,#1E3A5F,#2B6CB0); border-radius:12px 12px 0 0; padding:32px 36px; }
    .hdr-title { color:#fff; font-size:22px; font-weight:800; letter-spacing:.5px; line-height:1.3; }
    .hdr-sub { color:#90CDF4; font-size:13px; margin-top:6px; }
    .hdr-week { color:#BEE3F8; font-size:13px; margin-top:14px; font-weight:500; }

    /* Cards */
    .card { background:#fff; border-left:1px solid #E2E8F0; border-right:1px solid #E2E8F0; padding:28px 36px; }
    .card + .card { border-top:1px solid #EDF2F7; }
    .card-last { border-radius:0 0 12px 12px; border-bottom:1px solid #E2E8F0; }
    .sec-title { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:1.4px; color:#2B6CB0; margin:0 0 20px 0; padding-bottom:10px; border-bottom:2px solid #EBF4FF; }

    /* Stat grid */
    .stat-row { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:14px; }
    .stat { flex:1; min-width:130px; background:#F7FAFC; border:1px solid #E2E8F0; border-radius:10px; padding:18px 16px 16px; text-align:center; }
    .stat .val { font-size:30px; font-weight:800; color:#1E3A5F; line-height:1.1; }
    .stat .lbl { font-size:11px; color:#718096; margin-top:6px; text-transform:uppercase; letter-spacing:.6px; line-height:1.4; }

    /* CSM table */
    .csm-table { width:100%; border-collapse:collapse; font-size:13px; margin-top:4px; }
    .csm-table th { background:#EBF4FF; color:#2C5282; font-weight:700; text-align:left; padding:10px 14px; font-size:11px; text-transform:uppercase; letter-spacing:.6px; }
    .csm-table th:last-child { width:200px; }
    .csm-table td { padding:11px 14px; border-bottom:1px solid #F0F4F8; vertical-align:middle; line-height:1.4; }
    .csm-table tr:last-child td { border-bottom:none; }
    .bar { height:8px; background:#EBF4FF; border-radius:4px; overflow:hidden; margin-top:5px; }
    .bar-fill { height:100%; border-radius:4px; background:#3182CE; }

    /* Call cards */
    .call-card { border-radius:8px; padding:20px 22px; margin-bottom:16px; border-left:4px solid #ccc; }
    .call-card:last-child { margin-bottom:0; }
    .call-card h3 { margin:0 0 8px 0; font-size:15px; font-weight:700; color:#1A202C; line-height:1.4; }
    .call-meta { font-size:12px; color:#718096; margin-bottom:12px; line-height:1.6; }
    .rag-badge { display:inline-block; padding:4px 12px; border-radius:12px; font-size:11px; font-weight:700; margin-bottom:4px; letter-spacing:.5px; }
    .rag-reason { display:block; font-size:12px; font-style:italic; margin:4px 0 14px 0; line-height:1.5; }
    .bullet-list { margin:0 0 14px 0; padding:0; list-style:none; }
    .bullet-list li { font-size:13px; color:#2D3748; padding:5px 0 5px 16px; position:relative; line-height:1.6; border-bottom:1px solid #F7FAFC; }
    .bullet-list li:last-child { border-bottom:none; }
    .bullet-list li:before { content:"–"; position:absolute; left:0; color:#A0AEC0; }
    .action-block { background:#F7FAFC; border-radius:8px; padding:14px 16px; margin-top:14px; }
    .action-title { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.8px; color:#4A5568; margin-bottom:10px; }
    .action-item { font-size:12px; color:#2D3748; padding:6px 0; border-bottom:1px solid #EDF2F7; line-height:1.5; }
    .action-item:last-child { border-bottom:none; padding-bottom:0; }
    .action-owner { font-weight:700; color:#2B6CB0; }
    .ff-link { font-size:12px; color:#3182CE; text-decoration:none; font-weight:600; display:inline-block; margin-top:12px; }
    .ff-row { font-size:12px; padding:8px 0; border-bottom:1px solid #F7FAFC; color:#4A5568; line-height:1.5; }
    .ff-row:last-child { border-bottom:none; }
    .no-summary { background:#FFF5F5; border:1px dashed #FC8181; border-radius:6px; padding:12px 14px; font-size:12px; color:#C53030; margin-bottom:12px; line-height:1.5; }
    .type-badge { font-size:10px; font-weight:700; padding:3px 8px; border-radius:10px; margin-left:8px; vertical-align:middle; }
    .type-cs { background:#EBF4FF; color:#2B6CB0; }
    .type-ops { background:#FAF5FF; color:#6B46C1; }
    .footer { text-align:center; padding:20px 16px; font-size:11px; color:#A0AEC0; line-height:1.7; }
    """

    # ── Header ────────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"/>
    <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
    <style>{css}</style></head><body><div class="wrap">
    <div class="hdr">
      <div class="hdr-title">Zeno — Weekly Call Visibility Report</div>
      <div class="hdr-sub">AMZ Prep CS/AM · COO Briefing</div>
      <div class="hdr-week">Week: {week_range}</div>
    </div>"""

    # ── Snapshot ──────────────────────────────────────────────────────────────
    rag_red    = rag.get("RED", 0)
    rag_yellow = rag.get("YELLOW", 0)
    rag_green  = rag.get("GREEN", 0)

    html += f"""<div class="card">
    <div class="sec-title">Week at a Glance</div>
    <div class="stat-row">
      <div class="stat"><div class="val">{snapshot['total_cs_am']}</div><div class="lbl">Merchant Calls</div></div>
      <div class="stat"><div class="val">{snapshot['total_ops']}</div><div class="lbl">Warehouse / Ops Calls</div></div>
      <div class="stat"><div class="val">{snapshot['total_internal']}</div><div class="lbl">Internal Calls</div></div>
      <div class="stat"><div class="val">{snapshot['total_all']}</div><div class="lbl">Total Calls</div></div>
    </div>
    <div class="stat-row">
      <div class="stat" style="border-color:#FC8181;"><div class="val" style="color:#C53030;">{rag_red}</div><div class="lbl">Red Accounts</div></div>
      <div class="stat" style="border-color:#F6AD55;"><div class="val" style="color:#C05621;">{rag_yellow}</div><div class="lbl">Yellow Accounts</div></div>
      <div class="stat" style="border-color:#68D391;"><div class="val" style="color:#276749;">{rag_green}</div><div class="lbl">Green Accounts</div></div>
    </div>
    </div>"""

    # ── CSM Breakdown ─────────────────────────────────────────────────────────
    csm_counts = snapshot["csm_counts"]
    max_calls  = max(csm_counts.values()) if csm_counts else 1
    # Sort by call count desc, only reps with external calls
    sorted_csm = sorted(csm_counts.items(), key=lambda x: -x[1])

    html += """<div class="card"><div class="sec-title">CSM External Call Breakdown</div>
    <table class="csm-table"><thead><tr>
      <th>CSM</th><th>External Calls</th><th>Volume</th>
    </tr></thead><tbody>"""

    for email, count in sorted_csm:
        name     = TEAM_NAMES.get(email, email.split("@")[0])
        pct      = int((count / max_calls) * 100)
        html += f"""<tr>
          <td><strong>{name}</strong></td>
          <td>{count}</td>
          <td style="width:180px;">
            <div class="bar"><div class="bar-fill" style="width:{pct}%;"></div></div>
          </td>
        </tr>"""

    html += "</tbody></table></div>"

    # ── Per-Call Reports ──────────────────────────────────────────────────────
    html += """<div class="card"><div class="sec-title">Call-by-Call Report — All External Calls</div>"""

    if not call_reports:
        html += "<p style='color:#718096;font-size:13px;'>No external calls recorded this week.</p>"
    else:
        for report in call_reports:
            rag_cfg  = RAG_COLORS.get(report["rag"], RAG_COLORS["YELLOW"])
            type_cls = "type-cs" if report["call_type"] == "CS_AM" else "type-ops"
            type_lbl = "Merchant" if report["call_type"] == "CS_AM" else "Warehouse/Ops"
            reps_str = ", ".join(report["reps"]) or "Unknown"

            html += f"""<div class="call-card" style="background:{rag_cfg['bg']};border-left-color:{rag_cfg['border']};">"""

            # Title + type badge
            html += f"""<h3><a href="{report['fireflies_url']}" style="color:#1A202C;text-decoration:none;">
              {report['title']}</a>
              <span class="type-badge {type_cls}">{type_lbl}</span>
            </h3>"""

            # Meta
            html += f"""<div class="call-meta">
              {report['date']} &nbsp;·&nbsp; {report['duration']} &nbsp;·&nbsp; Rep: {reps_str}
            </div>"""

            # RAG badge
            html += f"""<div>
              <span class="rag-badge" style="background:{rag_cfg['bg']};color:{rag_cfg['text']};border:1px solid {rag_cfg['border']};">
                {report['rag']}
              </span>
              <span class="rag-reason" style="color:{rag_cfg['text']};">&nbsp; {report['rag_reason']}</span>
            </div>"""

            # No summary warning
            if not report["has_summary"]:
                html += """<div class="no-summary">
                  No Fireflies summary available — transcript was not captured for this call.
                  Check that the Chrome extension is active and connected.
                </div>"""
            else:
                # Exec bullets
                html += "<ul class='bullet-list'>"
                for bullet in report["bullets"]:
                    html += f"<li>{bullet}</li>"
                html += "</ul>"

            # Action items
            if report["action_items"]:
                html += """<div class="action-block">
                <div class="action-title">Action Items / Takeaways</div>"""
                for item in report["action_items"]:
                    html += f"""<div class="action-item">
                      <span class="action-owner">{item['owner']}:</span> {item['action']}
                    </div>"""
                html += "</div>"
            else:
                html += """<div class="action-block">
                  <div class="action-title">Action Items / Takeaways</div>
                  <div style="font-size:12px;color:#A0AEC0;font-style:italic;">No action items captured.</div>
                </div>"""

            # Fireflies link
            html += f"""<div style="margin-top:10px;">
              <a href="{report['fireflies_url']}" class="ff-link">View full transcript in Fireflies</a>
            </div>"""
            html += "</div>"   # call-card

    html += "</div>"   # card

    # ── Fireflies Coverage ────────────────────────────────────────────────────
    html += """<div class="card card-last">
    <div class="sec-title">Fireflies Coverage — Missing Recordings</div>"""

    if not missing_ff:
        html += """<p style="color:#276749;font-size:13px;font-weight:600;">
          All calls recorded this week. Fireflies coverage is complete.
        </p>"""
    else:
        external_missing = [m for m in missing_ff if m["type"] in ("CS_AM", "OPS")]
        internal_missing = [m for m in missing_ff if m["type"] == "INTERNAL"]

        html += f"""<p style="font-size:13px;color:#C53030;font-weight:600;margin-bottom:4px;">
          {len(missing_ff)} call(s) not recorded
          ({len(external_missing)} external, {len(internal_missing)} internal)
        </p>
        <p style="font-size:12px;color:#718096;margin-bottom:14px;">
          Every call — internal and external — should appear in Fireflies automatically
          when the Chrome extension is installed and active. No manual bot invite is needed.
          If calls are missing, the most likely reasons are:
        </p>
        <ul style="font-size:12px;color:#4A5568;margin:0 0 14px 0;">"""

        for reason in FIREFLIES_MISSING_REASONS:
            html += f"<li style='padding:3px 0;'>{reason}</li>"
        html += "</ul>"

        if external_missing:
            html += f"""<div style="font-weight:700;font-size:12px;color:#C53030;margin-bottom:6px;">
              External calls missing ({len(external_missing)}) — Priority to fix:
            </div>"""
            for m in external_missing:
                reps = ", ".join(m["reps"]) or "Unknown"
                html += f"""<div class="ff-row">
                  <strong>{m['title']}</strong> &nbsp;·&nbsp; {m['date']} &nbsp;·&nbsp; Rep: {reps}
                </div>"""

        if internal_missing:
            html += f"""<div style="font-weight:700;font-size:12px;color:#B7791F;margin:12px 0 6px 0;">
              Internal calls missing ({len(internal_missing)}):
            </div>"""
            for m in internal_missing[:10]:
                reps = ", ".join(m["reps"]) or "Unknown"
                html += f"""<div class="ff-row">
                  {m['title']} &nbsp;·&nbsp; {m['date']} &nbsp;·&nbsp; Rep: {reps}
                </div>"""
            if len(internal_missing) > 10:
                html += f"""<div style="font-size:12px;color:#A0AEC0;font-style:italic;padding:6px 0;">
                  ... and {len(internal_missing) - 10} more internal calls not recorded.
                </div>"""

    html += "</div>"   # card-last

    # ── Footer ────────────────────────────────────────────────────────────────
    html += f"""<div class="footer">
      Sent by <strong>Zeno</strong> · AMZ Prep Call QA Agent &nbsp;·&nbsp;
      Report generated {run_date} &nbsp;·&nbsp;
      Questions: <a href="mailto:ari@amzprep.com" style="color:#3182CE;">ari@amzprep.com</a>
    </div>
    </div></body></html>"""

    return html


# ─── Send email via Resend ────────────────────────────────────────────────────
def send_email(subject: str, html: str) -> bool:
    to_list  = [f"{name} <{email}>" for name, email in TO_RECIPIENTS]
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
        log.info(f"Call visibility report sent to {', '.join(to_list)}")
        log.info(f"CC: {', '.join(CC_RECIPIENTS)}")
        return True
    else:
        log.error(f"Email failed: {resp.status_code} {resp.text[:300]}")
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=== Zeno — Call Visibility Report ===")

    data      = load_latest_calls()
    run_date  = data["run_date"]
    calls     = data["calls"]
    date_range= data["date_range"]

    log.info(f"Processing {len(calls)} calls for {date_range['from']} to {date_range['to']}")

    # Build all sections
    snapshot     = build_snapshot(calls)
    call_reports = build_call_reports(calls)
    missing_ff   = build_missing_ff_report(calls)

    log.info(f"Snapshot: {snapshot['total_cs_am']} merchant | {snapshot['total_ops']} ops | {snapshot['total_internal']} internal")
    log.info(f"RAG: RED={snapshot['rag_counts']['RED']} YELLOW={snapshot['rag_counts']['YELLOW']} GREEN={snapshot['rag_counts']['GREEN']}")
    log.info(f"Missing Fireflies: {len(missing_ff)} calls")

    # Build HTML
    html = build_email_html(snapshot, call_reports, missing_ff, date_range, run_date)

    # Save for reference
    out_path = OUTPUT_DIR / f"call_report_{run_date}.html"
    out_path.write_text(html)
    log.info(f"Report saved to {out_path}")

    # Send email
    week_range = f"{date_range['from']} to {date_range['to']}"
    subject    = f"Zeno — Weekly Call Report | {week_range}"
    send_email(subject, html)

    log.info("=== Call Visibility Report complete ===")


if __name__ == "__main__":
    main()
