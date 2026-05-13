"""
Zeno — Weekly Call Visibility Report
=====================================
Gmail-safe email. All layout via HTML tables and inline styles.
No flexbox, no grid, no pseudo-elements, no complex CSS selectors.
Target output size: under 90KB (Gmail clips at ~102KB).

Caps enforced:
  - Client calls with full summary: max 30
  - Internal calls with full summary: max 15
  - Action items per call: max 5
  - Bullet points per call: max 4
  - Missing FF list: max 20
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

MAX_CLIENT_FULL   = 15   # full summary cards
MAX_INTERNAL_FULL = 10   # full summary cards
MAX_BULLETS       = 3
MAX_ACTIONS       = 4
MAX_MISSING_FF    = 15

RED_KEYWORDS = [
    "cancel","canceling","cancelling","leaving","switching","frustrated",
    "frustration","extremely disappointed","very unhappy","unacceptable",
    "legal","very upset","not happy","terminate","chargeback","lawsuit",
    "angry","outraged","shipbob","deliverr","stord","going with another",
    "serious issue","critical issue",
]
YELLOW_KEYWORDS = [
    "concern","issue","problem","delay","late","missed sla","not resolved",
    "still waiting","unclear","confused","pushback","pricing concern",
    "too expensive","no next step","unresolved","needs clarification",
    "investigate","error","mislabeled","damaged","lost shipment","wrong sku",
    "placement fee","pending",
]
GREEN_KEYWORDS = [
    "resolved","great call","happy","satisfied","confirmed","booked",
    "scheduled next","positive","going well","on track","no issues",
    "good relationship","expanding","growing","excellent","smooth",
    "working well","appreciate","pleased",
]

RAG_COLORS = {
    "RED":    {"bg":"#FFF5F5","border":"#E53E3E","text":"#C53030","pill_bg":"#FED7D7"},
    "YELLOW": {"bg":"#FFFBEB","border":"#D69E2E","text":"#92600A","pill_bg":"#FEFCBF"},
    "GREEN":  {"bg":"#F0FFF4","border":"#38A169","text":"#22543D","pill_bg":"#C6F6D5"},
}


# ─── Data helpers ─────────────────────────────────────────────────────────────
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
        if kw in text: return "RED"
    for kw in YELLOW_KEYWORDS:
        if kw in text: return "YELLOW"
    if any(kw in text for kw in GREEN_KEYWORDS): return "GREEN"
    return "YELLOW"


def rag_label(rag: str) -> str:
    return {"RED":"Needs Attention","YELLOW":"Monitor Closely","GREEN":"On Track"}.get(rag,"Monitor Closely")


def get_rep_names(call: dict) -> list:
    return [TEAM_NAMES.get(e, e.split("@")[0].title())
            for e in (call.get("team_members_on_call") or [])]


def get_client_label(call: dict) -> str:
    title = call.get("title") or ""
    for sep in [" - "," - "," – ",": "," / "," | "]:
        if sep in title:
            for part in title.split(sep):
                p = part.strip()
                if p and "amz" not in p.lower() and len(p) > 2:
                    return p[:60]
    return title[:60] or "Unknown"


def extract_bullets(call: dict) -> list:
    summary = (call.get("short_summary") or "").strip()
    if not summary: return []
    sentences = re.split(r'(?<=[.!?])\s+', summary)
    bullets = []
    for s in sentences:
        s = s.strip().rstrip(".")
        if len(s) > 25:
            bullets.append((s[:180] + "...") if len(s) > 180 else s)
        if len(bullets) == MAX_BULLETS: break
    if not bullets and call.get("keywords"):
        bullets.append("Topics: " + ", ".join((call.get("keywords") or [])[:5]))
    return bullets


def extract_action_items(call: dict) -> list:
    raw = (call.get("action_items") or "").strip()
    if not raw: return []
    items, owner = [], "Team"
    for line in raw.split("\n"):
        line = line.strip()
        if not line: continue
        if line.startswith("**") and line.endswith("**"):
            owner = line.strip("*").strip(); continue
        if line.startswith(("-","•")):
            action = re.sub(r'\s*\(\d+:\d+\)\s*$', '', line.lstrip("-•").strip())
            if action and len(action) > 5:
                items.append({"owner": owner, "action": action[:120]})
    return items[:MAX_ACTIONS]


def build_snapshot(calls: list) -> dict:
    client  = [c for c in calls if c["call_type"] in ("CS_AM","OPS")]
    intern_ = [c for c in calls if c["call_type"] == "INTERNAL"]
    csm     = defaultdict(int)
    for c in client:
        for e in (c.get("team_members_on_call") or []):
            if e in TEAM_NAMES: csm[e] += 1
    rag = {"RED":0,"YELLOW":0,"GREEN":0}
    for c in client: rag[detect_rag(c)] += 1
    return {
        "total_client":   len(client),
        "total_cs_am":    len([c for c in client if c["call_type"]=="CS_AM"]),
        "total_ops":      len([c for c in client if c["call_type"]=="OPS"]),
        "total_internal": len(intern_),
        "total_all":      len(calls),
        "csm_counts":     dict(csm),
        "rag_counts":     rag,
    }


def build_client_reports(calls: list) -> list:
    ext = sorted(
        [c for c in calls if c["call_type"] in ("CS_AM","OPS")],
        key=lambda c: c.get("date") or "", reverse=True
    )
    return [{
        "id":           c["id"],
        "title":        c.get("title") or "Untitled",
        "call_type":    c["call_type"],
        "date":         (c.get("date") or "")[:10],
        "duration":     f"{int(c.get('duration_minutes') or 0)} min",
        "reps":         get_rep_names(c),
        "rag":          detect_rag(c),
        "bullets":      extract_bullets(c),
        "action_items": extract_action_items(c),
        "has_summary":  bool(c.get("short_summary")),
        "fireflies_url": f"https://app.fireflies.ai/view/{c['id']}",
    } for c in ext]


def build_internal_reports(calls: list) -> list:
    intern_ = sorted(
        [c for c in calls if c["call_type"] == "INTERNAL"],
        key=lambda c: c.get("date") or "", reverse=True
    )
    return [{
        "title":        c.get("title") or "Untitled",
        "date":         (c.get("date") or "")[:10],
        "duration":     f"{int(c.get('duration_minutes') or 0)} min",
        "reps":         get_rep_names(c),
        "bullets":      extract_bullets(c),
        "action_items": extract_action_items(c),
        "has_summary":  bool(c.get("short_summary")),
        "fireflies_url": f"https://app.fireflies.ai/view/{c['id']}",
    } for c in intern_]


def build_missing_ff(calls: list) -> list:
    return [
        {
            "title":  c.get("title") or "Untitled",
            "date":   (c.get("date") or "")[:10],
            "reps":   get_rep_names(c),
            "type":   c["call_type"],
        }
        for c in calls
        if not c.get("fireflies_joined") and c["call_type"] != "SKIP"
    ]


# ─── Gmail-safe HTML primitives ───────────────────────────────────────────────
def _td(content, style=""):
    return f'<td style="font-family:Arial,sans-serif;{style}">{content}</td>'


def _stat_cell(num, label, num_color="#1A365D"):
    return f"""<td style="width:25%;padding:0 6px 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="background:#F7FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:18px 10px;text-align:center;">
<div style="font-size:30px;font-weight:800;color:{num_color};line-height:1;font-family:Arial,sans-serif;">{num}</div>
<div style="font-size:11px;color:#718096;margin-top:7px;text-transform:uppercase;letter-spacing:0.7px;font-family:Arial,sans-serif;line-height:1.4;">{label}</div>
</td></tr></table></td>"""


def _section_header(eyebrow, title):
    return f"""<tr><td style="padding:32px 40px 0;">
<div style="font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#A0AEC0;font-family:Arial,sans-serif;margin-bottom:5px;">{eyebrow}</div>
<div style="font-size:18px;font-weight:700;color:#1A365D;font-family:Arial,sans-serif;padding-bottom:12px;border-bottom:2px solid #EBF4FF;margin-bottom:20px;line-height:1.3;">{title}</div>
</td></tr>"""


def _card_wrap(content, pad="0 40px 28px"):
    return f'<tr><td style="padding:{pad};">{content}</td></tr>'


# ─── Call card — compact Gmail-safe table ─────────────────────────────────────
def _call_card_html(r: dict, show_rag: bool = True) -> str:
    cfg      = RAG_COLORS.get(r.get("rag","YELLOW"), RAG_COLORS["YELLOW"])
    rag      = r.get("rag","YELLOW")
    reps_str = ", ".join(r.get("reps") or []) or "—"
    type_lbl = "Merchant" if r.get("call_type")=="CS_AM" else "Ops" if r.get("call_type")=="OPS" else "Internal"
    type_col = "#2B6CB0" if r.get("call_type")=="CS_AM" else "#6B46C1"
    type_bg  = "#EBF4FF" if r.get("call_type")=="CS_AM" else "#FAF5FF"
    ff_url   = r.get("fireflies_url","#")

    s  = f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:12px;border-left:3px solid {cfg["border"]};background:{cfg["bg"]};border-radius:6px;">'
    s += f'<tr><td style="padding:14px 16px;">'

    # Title + meta in one line
    s += f'<a href="{ff_url}" style="font-size:14px;font-weight:700;color:#1A202C;text-decoration:none;font-family:Arial,sans-serif;line-height:1.4;">{r["title"]}</a>'
    s += f' <span style="font-size:10px;font-weight:700;background:{type_bg};color:{type_col};padding:2px 7px;border-radius:8px;">{type_lbl}</span>'
    if show_rag:
        s += f' <span style="font-size:10px;font-weight:700;background:{cfg["pill_bg"]};color:{cfg["text"]};padding:2px 8px;border-radius:8px;">{rag_label(rag)}</span>'
    s += f'<br/><span style="font-size:12px;color:#718096;font-family:Arial,sans-serif;">{r["date"]} &nbsp;·&nbsp; {r["duration"]} &nbsp;·&nbsp; {reps_str}</span>'

    # Bullets
    bullets = r.get("bullets") or []
    if not r.get("has_summary"):
        s += '<br/><span style="font-size:12px;color:#A0AEC0;font-style:italic;font-family:Arial,sans-serif;">No Fireflies summary captured.</span>'
    elif bullets:
        s += '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;">'
        for b in bullets:
            s += f'<tr><td style="font-size:13px;color:#2D3748;font-family:Arial,sans-serif;padding:3px 0 3px 12px;border-bottom:1px solid #EDF2F7;line-height:1.6;">&#8211; {b}</td></tr>'
        s += '</table>'

    # Action items
    actions = r.get("action_items") or []
    if actions:
        s += '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px;background:#FFFFFF;border:1px solid #E2E8F0;border-radius:4px;">'
        s += '<tr><td colspan="2" style="padding:8px 10px 4px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#4A5568;font-family:Arial,sans-serif;">Takeaways</td></tr>'
        for item in actions:
            s += f'<tr><td style="padding:4px 10px;font-size:12px;font-weight:700;color:#2B6CB0;font-family:Arial,sans-serif;width:110px;vertical-align:top;border-top:1px solid #F0F4F8;">{item["owner"]}</td>'
            s += f'<td style="padding:4px 10px 4px 0;font-size:12px;color:#2D3748;font-family:Arial,sans-serif;line-height:1.5;border-top:1px solid #F0F4F8;">{item["action"]}</td></tr>'
        s += '</table>'

    s += f'<br/><a href="{ff_url}" style="font-size:11px;font-weight:600;color:#3182CE;text-decoration:none;font-family:Arial,sans-serif;">View in Fireflies &rarr;</a>'
    s += '</td></tr></table>'
    return s


# ─── Main HTML builder ────────────────────────────────────────────────────────
def build_html(snapshot, client_reports, internal_reports, missing_ff, date_range, run_date) -> str:
    week_range = f"{date_range['from']} to {date_range['to']}"
    rag        = snapshot["rag_counts"]
    csm_counts = snapshot["csm_counts"]
    max_count  = max(csm_counts.values()) if csm_counts else 1

    html  = '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/>'
    html += '<meta name="viewport" content="width=device-width,initial-scale=1.0"/>'
    html += '<title>Zeno Weekly Call Report</title></head>'
    html += '<body style="margin:0;padding:0;background:#EAEEF3;">'
    html += '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#EAEEF3;">'
    html += '<tr><td align="center" style="padding:28px 16px 48px;">'
    html += '<table width="680" cellpadding="0" cellspacing="0" border="0" style="max-width:680px;width:100%;">'

    # ── Header ────────────────────────────────────────────────────────────────
    html += '<tr><td style="background:#1A365D;border-radius:12px 12px 0 0;padding:34px 40px;">'
    html += '<div style="font-size:11px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:#90CDF4;font-family:Arial,sans-serif;margin-bottom:10px;">Zeno · AMZ Prep</div>'
    html += '<div style="font-size:24px;font-weight:800;color:#FFFFFF;font-family:Arial,sans-serif;line-height:1.25;margin-bottom:8px;">Weekly Call Visibility Report</div>'
    html += f'<div style="font-size:13px;color:#BEE3F8;font-family:Arial,sans-serif;line-height:1.6;">Week: {week_range} &nbsp;·&nbsp; Generated {run_date}</div>'
    html += '</td></tr>'

    # ── White card wrapper starts ──────────────────────────────────────────────
    html += '<tr><td style="background:#FFFFFF;border-left:1px solid #DDE3EC;border-right:1px solid #DDE3EC;">'
    html += '<table width="100%" cellpadding="0" cellspacing="0" border="0">'

    # ── Snapshot ──────────────────────────────────────────────────────────────
    html += _section_header("Overview", "Week at a Glance")
    html += '<tr><td style="padding:0 40px 10px;">'
    html += '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
    html += _stat_cell(snapshot["total_cs_am"], "Merchant Calls")
    html += _stat_cell(snapshot["total_ops"], "Warehouse / Ops")
    html += _stat_cell(snapshot["total_internal"], "Internal Calls")
    html += _stat_cell(snapshot["total_all"], "Total Calls")
    html += '</tr></table></td></tr>'
    html += '<tr><td style="padding:10px 40px 28px;">'
    html += '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
    html += _stat_cell(rag["RED"],    "Needs Attention",  "#C53030")
    html += _stat_cell(rag["YELLOW"], "Monitor Closely",  "#92600A")
    html += _stat_cell(rag["GREEN"],  "On Track",         "#22543D")
    html += _stat_cell(len(missing_ff), "Missing Recordings", "#718096")
    html += '</tr></table></td></tr>'

    # ── Divider ───────────────────────────────────────────────────────────────
    html += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'

    # ── CSM Breakdown ─────────────────────────────────────────────────────────
    html += _section_header("Team", "CSM Client Call Breakdown")
    html += '<tr><td style="padding:0 40px 28px;">'
    html += '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-radius:6px;overflow:hidden;">'
    html += '<tr style="background:#EBF4FF;"><td style="padding:10px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;letter-spacing:0.7px;font-family:Arial,sans-serif;">CSM</td>'
    html += '<td style="padding:10px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;letter-spacing:0.7px;font-family:Arial,sans-serif;width:80px;">Calls</td>'
    html += '<td style="padding:10px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;letter-spacing:0.7px;font-family:Arial,sans-serif;width:200px;">Volume</td></tr>'
    for email, count in sorted(csm_counts.items(), key=lambda x: -x[1]):
        name = TEAM_NAMES.get(email, email.split("@")[0].title())
        pct  = int((count / max_count) * 100)
        html += f'<tr style="border-bottom:1px solid #F0F4F8;">'
        html += f'<td style="padding:11px 14px;font-size:14px;font-weight:600;color:#2D3748;font-family:Arial,sans-serif;">{name}</td>'
        html += f'<td style="padding:11px 14px;font-size:14px;font-weight:700;color:#2B6CB0;font-family:Arial,sans-serif;">{count}</td>'
        html += f'<td style="padding:11px 14px;"><table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        html += f'<td style="background:#EBF4FF;border-radius:4px;height:8px;"><table height="8" cellpadding="0" cellspacing="0" border="0"><tr><td style="background:#3182CE;border-radius:4px;width:{pct}%;min-width:4px;">&nbsp;</td></tr></table></td>'
        html += f'</tr></table></td></tr>'
    html += '</table></td></tr>'

    html += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'

    # ── Client Calls ──────────────────────────────────────────────────────────
    total_client = len(client_reports)
    full_client  = client_reports[:MAX_CLIENT_FULL]
    rest_client  = client_reports[MAX_CLIENT_FULL:]

    html += _section_header("External", f'Client Calls <span style="font-size:14px;font-weight:500;color:#A0AEC0;">({total_client} calls)</span>')
    html += '<tr><td style="padding:0 40px 28px;">'
    if not client_reports:
        html += '<p style="font-size:14px;color:#A0AEC0;font-style:italic;font-family:Arial,sans-serif;margin:0;">No client calls this week.</p>'
    else:
        for r in full_client:
            html += _call_card_html(r, show_rag=True)
        if rest_client:
            html += f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F7FAFC;border:1px solid #E2E8F0;border-radius:8px;margin-top:8px;">'
            html += f'<tr><td style="padding:14px 18px;">'
            html += f'<div style="font-size:13px;font-weight:700;color:#4A5568;font-family:Arial,sans-serif;margin-bottom:10px;">{len(rest_client)} additional client calls — see full list in Fireflies</div>'
            for r in rest_client:
                reps = ", ".join(r.get("reps") or []) or "—"
                html += f'<div style="font-size:13px;color:#4A5568;font-family:Arial,sans-serif;padding:5px 0;border-bottom:1px solid #EDF2F7;">'
                html += f'<a href="{r["fireflies_url"]}" style="color:#2B6CB0;text-decoration:none;font-weight:600;">{r["title"][:60]}</a>'
                html += f' &nbsp;·&nbsp; {r["date"]} &nbsp;·&nbsp; {reps}</div>'
            html += '</td></tr></table>'
    html += '</td></tr>'

    html += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'

    # ── Internal Calls — compact table ──────────────────────────────────────
    total_internal = len(internal_reports)
    html += _section_header("Internal", f'Internal Calls <span style="font-size:14px;font-weight:500;color:#A0AEC0;">({total_internal} calls)</span>')
    html += '<tr><td style="padding:0 40px 28px;">'
    if not internal_reports:
        html += '<p style="font-size:14px;color:#A0AEC0;font-style:italic;font-family:Arial,sans-serif;margin:0;">No internal calls this week.</p>'
    else:
        html += '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid #E2E8F0;border-radius:6px;">'
        html += '<tr style="background:#EBF4FF;"><td style="padding:8px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;letter-spacing:0.7px;font-family:Arial,sans-serif;">Title</td><td style="padding:8px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;width:90px;font-family:Arial,sans-serif;">Date</td><td style="padding:8px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;width:55px;font-family:Arial,sans-serif;">Dur</td><td style="padding:8px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;width:120px;font-family:Arial,sans-serif;">Rep</td></tr>'
        for r in internal_reports[:30]:
            reps   = ", ".join(r.get("reps") or []) or "—"
            ff_url = r["fireflies_url"]
            title  = r["title"][:55]
            date   = r["date"]
            dur    = r["duration"]
            html += f'<tr style="border-top:1px solid #F0F4F8;"><td style="padding:8px 14px;font-size:13px;font-family:Arial,sans-serif;"><a href="{ff_url}" style="color:#2B6CB0;text-decoration:none;font-weight:600;">{title}</a></td><td style="padding:8px 14px;font-size:12px;color:#718096;font-family:Arial,sans-serif;">{date}</td><td style="padding:8px 14px;font-size:12px;color:#718096;font-family:Arial,sans-serif;">{dur}</td><td style="padding:8px 14px;font-size:12px;color:#718096;font-family:Arial,sans-serif;">{reps[:22]}</td></tr>'
        if total_internal > 30:
            html += f'<tr><td colspan="4" style="padding:8px 14px;font-size:12px;color:#A0AEC0;font-style:italic;font-family:Arial,sans-serif;border-top:1px solid #F0F4F8;">... and {total_internal-30} more internal calls this week</td></tr>'
        html += '</table>'
    html += '</td></tr>'
    html += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'

    # ── Missing Fireflies ─────────────────────────────────────────────────────
    html += _section_header("Coverage", "Fireflies Recording Coverage")
    html += '<tr><td style="padding:0 40px 28px;">'
    if not missing_ff:
        html += '<p style="font-size:14px;color:#22543D;font-weight:600;font-family:Arial,sans-serif;margin:0;">All calls recorded this week. Coverage is complete.</p>'
    else:
        ext_m = [m for m in missing_ff if m["type"] in ("CS_AM","OPS")]
        int_m = [m for m in missing_ff if m["type"] == "INTERNAL"]
        html += f'<p style="font-size:14px;color:#C53030;font-weight:600;font-family:Arial,sans-serif;margin:0 0 6px 0;">{len(missing_ff)} call(s) not recorded &nbsp;·&nbsp; {len(ext_m)} external &nbsp;·&nbsp; {len(int_m)} internal</p>'
        html += '<p style="font-size:13px;color:#718096;font-family:Arial,sans-serif;margin:0 0 14px 0;line-height:1.6;">Every call should appear in Fireflies automatically once the Chrome extension is installed and active. No manual bot invite is needed.</p>'
        if ext_m:
            html += '<div style="font-size:12px;font-weight:700;text-transform:uppercase;color:#C53030;font-family:Arial,sans-serif;margin-bottom:8px;letter-spacing:0.8px;">External — Priority to Fix</div>'
            for m in ext_m:
                reps = ", ".join(m.get("reps") or []) or "—"
                html += f'<div style="font-size:13px;color:#4A5568;font-family:Arial,sans-serif;padding:8px 0;border-bottom:1px solid #F0F4F8;line-height:1.5;"><b>{m["title"][:60]}</b> &nbsp;·&nbsp; {m["date"]} &nbsp;·&nbsp; {reps}</div>'
        if int_m:
            html += '<div style="font-size:12px;font-weight:700;text-transform:uppercase;color:#92600A;font-family:Arial,sans-serif;margin:16px 0 8px 0;letter-spacing:0.8px;">Internal</div>'
            for m in int_m[:MAX_MISSING_FF]:
                reps = ", ".join(m.get("reps") or []) or "—"
                html += f'<div style="font-size:13px;color:#4A5568;font-family:Arial,sans-serif;padding:8px 0;border-bottom:1px solid #F0F4F8;line-height:1.5;">{m["title"][:60]} &nbsp;·&nbsp; {m["date"]} &nbsp;·&nbsp; {reps}</div>'
            if len(int_m) > MAX_MISSING_FF:
                html += f'<p style="font-size:12px;color:#A0AEC0;font-style:italic;font-family:Arial,sans-serif;padding-top:6px;margin:0;">... and {len(int_m)-MAX_MISSING_FF} more internal calls not recorded.</p>'
    html += '</td></tr>'

    # ── Close white card, footer ───────────────────────────────────────────────
    html += '</table></td></tr>'
    html += '<tr><td style="background:#FFFFFF;border-radius:0 0 12px 12px;border:1px solid #DDE3EC;border-top:none;padding:20px 40px;text-align:center;">'
    html += f'<p style="font-size:12px;color:#A0AEC0;font-family:Arial,sans-serif;line-height:2;margin:0;">Sent by <b style="color:#4A5568;">Zeno</b> &nbsp;·&nbsp; AMZ Prep Call QA Agent &nbsp;·&nbsp; Report generated {run_date}<br/>'
    html += 'Questions: <a href="mailto:ari@amzprep.com" style="color:#3182CE;text-decoration:none;">ari@amzprep.com</a></p>'
    html += '</td></tr>'
    html += '</table></td></tr></table></body></html>'

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
        log.info(f"Report sent to: {', '.join(to_list)}")
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

    html     = build_html(snapshot, client_reports, internal_reports, missing, date_range, run_date)
    size_kb  = len(html.encode("utf-8")) // 1024
    log.info(f"HTML size: {size_kb}KB (Gmail limit: ~100KB)")
    if size_kb > 90:
        log.warning(f"HTML size {size_kb}KB may be clipped by Gmail")

    out_path = OUTPUT_DIR / f"call_report_{run_date}.html"
    out_path.write_text(html)
    log.info(f"Saved to {out_path}")

    week_range = f"{date_range['from']} to {date_range['to']}"
    send_email(f"Zeno — Weekly Call Report | {week_range}", html)
    log.info("=== Call Visibility Report complete ===")


if __name__ == "__main__":
    main()
