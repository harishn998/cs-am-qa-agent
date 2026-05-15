"""
CS/AM Call QA Agent — Phase 4: Deliver
=======================================
Reads the digest JSON and delivers the weekly report via:
  1. Resend email → Lakshita + Thomas (CC: Ari)
  2. Slack DM     → Lakshita + Thomas (separate group DMs with Ari)

Bot name: Zeno (AMZ Prep's call QA agent)

GitHub Actions env vars required:
  RESEND_API_KEY    — Resend transactional email API key
  SLACK_BOT_TOKEN   — Slack bot token (needs chat:write, im:write, mpim:write)

Input:  output/digest_<YYYY-MM-DD>.json
"""

import os
import json
import logging
import requests
from pathlib import Path

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
RESEND_API_KEY  = os.environ["RESEND_API_KEY"]
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")   # optional — Slack disabled

OUTPUT_DIR      = Path(__file__).parent.parent / "output"
TEMPLATES_DIR   = Path(__file__).parent / "templates"

# Email recipients
LAKSHITA_EMAIL  = "lakshita@amzprep.com"
THOMAS_EMAIL    = "thomas@amzprep.com"
ARI_EMAIL       = "ari@amzprep.com"
CC_EMAILS       = [
    "ari@amzprep.com",
    "harishnath@amzprep.com",
    "blair@amzprep.com",
]

FROM_EMAIL      = "reports@amzprep.com"
FROM_NAME       = "Zeno · AMZ Prep QA"

# Slack disabled — set to False to re-enable when needed
SLACK_ENABLED   = False


# ─── Load latest digest ───────────────────────────────────────────────────────
def load_latest_digest() -> tuple[dict, Path]:
    files = sorted(OUTPUT_DIR.glob("digest_*.json"))
    if not files:
        raise FileNotFoundError("No digest found. Run aggregate.py first.")
    path = files[-1]
    log.info(f"Loading digest from {path}")
    return json.loads(path.read_text()), path


# ─── HTML email builder ───────────────────────────────────────────────────────
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from call_report import (
    detect_rag, rag_label, get_rep_names, extract_bullets,
    extract_action_items, TEAM_NAMES, RAG_COLORS,
    _section_header, _stat_cell, _call_card_html,
    MAX_CLIENT_FULL, MAX_INTERNAL_FULL, MAX_MISSING_FF,
)

MAX_FLAG_TYPES = 5
MAX_FLAG_CALLS = 3


def build_email_html(digest: dict, recipient_name: str) -> str:
    from collections import defaultdict

    template   = (TEMPLATES_DIR / "email_digest.html").read_text()
    date_range = digest["date_range"]
    week_range = f"{date_range['from']} to {date_range['to']}"
    all_calls  = digest.get("calls") or []

    client_calls   = [c for c in all_calls if c.get("call_type") in ("CS_AM","OPS")]
    internal_calls = [c for c in all_calls if c.get("call_type") == "INTERNAL"]
    missing_ff     = digest.get("missing_fireflies") or []

    rag_counts = {"RED":0,"YELLOW":0,"GREEN":0}
    for c in client_calls:
        rag_counts[detect_rag(c)] += 1

    csm_counts = defaultdict(int)
    for c in client_calls:
        for e in (c.get("team_members_on_call") or []):
            if e in TEAM_NAMES: csm_counts[e] += 1
    max_count = max(csm_counts.values()) if csm_counts else 1

    template = template.replace("{{recipient_name}}", recipient_name)
    template = template.replace("{{week_range}}", week_range)

    # ── Snapshot ──────────────────────────────────────────────────────────────
    snap  = _section_header("Overview", "Week at a Glance")
    snap += '<tr><td style="padding:0 40px 10px;">'
    snap += '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
    snap += _stat_cell(len(client_calls),   "Customer Calls")
    snap += _stat_cell(len(internal_calls), "Internal Calls")
    snap += '</tr></table></td></tr>'
    snap += '<tr><td style="padding:10px 40px 28px;">'
    snap += '<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
    snap += _stat_cell(rag_counts["RED"],    "Needs Attention",    "#C53030")
    snap += _stat_cell(rag_counts["YELLOW"], "Monitor Closely",    "#92600A")
    snap += _stat_cell(rag_counts["GREEN"],  "On Track",           "#22543D")
    snap += _stat_cell(len(missing_ff),      "Missing Recordings", "#718096")
    snap += '</tr></table></td></tr>'
    snap += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'
    template = template.replace("{{snapshot_section}}", snap)

    # ── CSM breakdown ─────────────────────────────────────────────────────────
    csm  = _section_header("Team", "Customer Calls by CSM")
    csm += '<tr><td style="padding:0 40px 28px;">'
    csm += '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-radius:6px;overflow:hidden;">'
    csm += '<tr style="background:#EBF4FF;"><td style="padding:10px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;letter-spacing:0.7px;font-family:Arial,sans-serif;">CSM</td>'
    csm += '<td style="padding:10px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;letter-spacing:0.7px;font-family:Arial,sans-serif;width:80px;">Calls</td>'
    csm += '<td style="padding:10px 14px;font-size:11px;font-weight:700;color:#2C5282;text-transform:uppercase;letter-spacing:0.7px;font-family:Arial,sans-serif;width:200px;">Volume</td></tr>'
    for email, count in sorted(csm_counts.items(), key=lambda x: -x[1]):
        name = TEAM_NAMES.get(email, email.split("@")[0].title())
        pct  = int((count / max_count) * 100)
        csm += f'<tr style="border-bottom:1px solid #F0F4F8;">'
        csm += f'<td style="padding:11px 14px;font-size:14px;font-weight:600;color:#2D3748;font-family:Arial,sans-serif;">{name}</td>'
        csm += f'<td style="padding:11px 14px;font-size:14px;font-weight:700;color:#2B6CB0;font-family:Arial,sans-serif;">{count}</td>'
        csm += f'<td style="padding:11px 14px;"><table width="{pct}%" cellpadding="0" cellspacing="0" border="0"><tr><td style="background:#3182CE;height:8px;border-radius:4px;min-width:4px;">&nbsp;</td></tr></table></td></tr>'
    csm += '</table></td></tr>'
    csm += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'
    template = template.replace("{{csm_section}}", csm)

    # ── Flagged calls ─────────────────────────────────────────────────────────
    flagged = digest.get("flagged_calls") or {}
    priority_order = [
        "churn_language","competitor_mentioned","sla_miss_no_resolution",
        "pricing_pushback","negative_sentiment_end","no_next_step",
        "short_call","fireflies_missing","repeat_issue",
    ]
    sorted_flags = sorted(flagged.items(),
        key=lambda x: priority_order.index(x[0]) if x[0] in priority_order else 99)

    if sorted_flags:
        fl  = _section_header("Action Required", "Flagged Calls")
        fl += '<tr><td style="padding:0 40px 28px;">'
        for flag_key, flag_calls in sorted_flags[:MAX_FLAG_TYPES]:
            flag_label_txt = digest.get("flag_definitions", {}).get(flag_key, flag_key)
            fl += f'<div style="font-size:13px;font-weight:700;color:#C53030;font-family:Arial,sans-serif;margin-bottom:8px;">{flag_label_txt}</div>'
            for fc in flag_calls[:MAX_FLAG_CALLS]:
                date_str = (fc.get("date") or "")[:10]
                rep_str  = (fc.get("organizer") or "").split("@")[0]
                fl += f'<div style="font-size:13px;font-family:Arial,sans-serif;padding:7px 0;border-bottom:1px solid #F0F4F8;color:#4A5568;line-height:1.5;">'
                fl += f'<a href="{fc["fireflies_url"]}" style="color:#2B6CB0;text-decoration:none;font-weight:600;">{fc["title"][:60]}</a>'
                fl += f' &nbsp;·&nbsp; {date_str} &nbsp;·&nbsp; {rep_str}</div>'
            fl += '<div style="height:14px;"></div>'
        fl += '</td></tr>'
        fl += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'
        template = template.replace("{{flagged_section_html}}", fl)
    else:
        template = template.replace("{{flagged_section_html}}", "")

    # ── Client calls ──────────────────────────────────────────────────────────
    sorted_client = sorted(client_calls, key=lambda c: c.get("date") or "", reverse=True)
    full_client   = sorted_client[:MAX_CLIENT_FULL]
    rest_client   = sorted_client[MAX_CLIENT_FULL:]

    cc  = _section_header("External", f'Client Calls <span style="font-size:14px;font-weight:500;color:#A0AEC0;">({len(client_calls)} calls)</span>')
    cc += '<tr><td style="padding:0 40px 28px;">'
    if not client_calls:
        cc += '<p style="font-size:14px;color:#A0AEC0;font-style:italic;font-family:Arial,sans-serif;margin:0;">No client calls this week.</p>'
    else:
        for c in full_client:
            r = {
                "id": c.get("id",""), "title": c.get("title") or "Untitled",
                "call_type": c.get("call_type",""), "date": (c.get("date") or "")[:10],
                "duration": f"{int(c.get('duration_minutes') or 0)} min",
                "reps": get_rep_names(c), "rag": detect_rag(c),
                "bullets": extract_bullets(c), "action_items": extract_action_items(c),
                "has_summary": bool(c.get("short_summary")),
                "fireflies_url": f"https://app.fireflies.ai/view/{c.get('id','')}",
            }
            cc += _call_card_html(r, show_rag=True)
        if rest_client:
            cc += f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F7FAFC;border:1px solid #E2E8F0;border-radius:8px;margin-top:8px;"><tr><td style="padding:14px 18px;">'
            cc += f'<div style="font-size:13px;font-weight:700;color:#4A5568;font-family:Arial,sans-serif;margin-bottom:10px;">{len(rest_client)} additional client calls — see full list in Fireflies</div>'
            for c in rest_client:
                reps = ", ".join(get_rep_names(c)) or "—"
                ff   = f"https://app.fireflies.ai/view/{c.get('id','')}"
                cc  += f'<div style="font-size:13px;color:#4A5568;font-family:Arial,sans-serif;padding:5px 0;border-bottom:1px solid #EDF2F7;">'
                cc  += f'<a href="{ff}" style="color:#2B6CB0;text-decoration:none;font-weight:600;">{(c.get("title") or "")[:60]}</a>'
                cc  += f' &nbsp;·&nbsp; {(c.get("date") or "")[:10]} &nbsp;·&nbsp; {reps}</div>'
            cc += '</td></tr></table>'
    cc += '</td></tr>'
    cc += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'
    template = template.replace("{{client_calls_section}}", cc)

    # ── Internal calls ────────────────────────────────────────────────────────
    sorted_internal = sorted(internal_calls, key=lambda c: c.get("date") or "", reverse=True)
    full_internal   = sorted_internal[:MAX_INTERNAL_FULL]
    rest_internal   = sorted_internal[MAX_INTERNAL_FULL:]

    ic  = _section_header("Internal", f'Internal Calls <span style="font-size:14px;font-weight:500;color:#A0AEC0;">({len(internal_calls)} calls)</span>')
    ic += '<tr><td style="padding:0 40px 28px;">'
    if not internal_calls:
        ic += '<p style="font-size:14px;color:#A0AEC0;font-style:italic;font-family:Arial,sans-serif;margin:0;">No internal calls this week.</p>'
    else:
        for c in full_internal:
            r = {
                "id": c.get("id",""), "title": c.get("title") or "Untitled",
                "call_type": c.get("call_type",""), "date": (c.get("date") or "")[:10],
                "duration": f"{int(c.get('duration_minutes') or 0)} min",
                "reps": get_rep_names(c), "rag": detect_rag(c),
                "bullets": extract_bullets(c), "action_items": extract_action_items(c),
                "has_summary": bool(c.get("short_summary")),
                "fireflies_url": f"https://app.fireflies.ai/view/{c.get('id','')}",
            }
            ic += _call_card_html(r, show_rag=False)
        if rest_internal:
            ic += f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F7FAFC;border:1px solid #E2E8F0;border-radius:8px;margin-top:8px;"><tr><td style="padding:14px 18px;">'
            ic += f'<div style="font-size:13px;font-weight:700;color:#4A5568;font-family:Arial,sans-serif;margin-bottom:10px;">{len(rest_internal)} additional internal calls not shown</div>'
            for c in rest_internal:
                reps = ", ".join(get_rep_names(c)) or "—"
                ff   = f"https://app.fireflies.ai/view/{c.get('id','')}"
                ic  += f'<div style="font-size:13px;color:#4A5568;font-family:Arial,sans-serif;padding:5px 0;border-bottom:1px solid #EDF2F7;">'
                ic  += f'<a href="{ff}" style="color:#2B6CB0;text-decoration:none;font-weight:600;">{(c.get("title") or "")[:60]}</a>'
                ic  += f' &nbsp;·&nbsp; {(c.get("date") or "")[:10]} &nbsp;·&nbsp; {reps}</div>'
            ic += '</td></tr></table>'
    ic += '</td></tr>'
    ic += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'
    template = template.replace("{{internal_calls_section}}", ic)

    # ── Missing Fireflies ─────────────────────────────────────────────────────
    if missing_ff:
        mf  = _section_header("Coverage", "Missing Fireflies Recordings")
        mf += '<tr><td style="padding:0 40px 28px;">'
        mf += f'<div style="background:#FFFBEB;border:1px solid #F6AD55;border-radius:8px;padding:12px 16px;font-size:13px;color:#92600A;font-family:Arial,sans-serif;margin-bottom:14px;line-height:1.6;">'
        mf += f'{len(missing_ff)} call(s) not recorded this week. Ensure the Fireflies Chrome extension is installed and active on all reps browsers.</div>'
        for m in missing_ff[:MAX_MISSING_FF]:
            org  = (m.get("organizer") or "").split("@")[0]
            date = (m.get("date") or "")[:10]
            mf  += f'<div style="font-size:13px;color:#4A5568;font-family:Arial,sans-serif;padding:8px 0;border-bottom:1px solid #F0F4F8;line-height:1.5;">'
            mf  += f'<b>{m.get("title","")[:60]}</b> &nbsp;·&nbsp; {org} &nbsp;·&nbsp; {date}</div>'
        if len(missing_ff) > MAX_MISSING_FF:
            mf += f'<p style="font-size:12px;color:#A0AEC0;font-style:italic;font-family:Arial,sans-serif;padding-top:6px;margin:0;">... and {len(missing_ff)-MAX_MISSING_FF} more.</p>'
        mf += '</td></tr>'
    else:
        mf  = _section_header("Coverage", "Fireflies Coverage")
        mf += '<tr><td style="padding:0 40px 28px;"><p style="font-size:14px;color:#22543D;font-weight:600;font-family:Arial,sans-serif;margin:0;">All calls recorded this week.</p></td></tr>'
    mf += '<tr><td style="padding:0 40px;"><div style="height:1px;background:#EDF2F7;"></div></td></tr>'
    template = template.replace("{{missing_ff_section_html}}", mf)

    # ── Repeat issues ─────────────────────────────────────────────────────────
    repeats = digest.get("repeat_issues") or []
    if repeats:
        ri  = _section_header("Watch List", "Repeat Issue Accounts")
        ri += '<tr><td style="padding:0 40px 28px;">'
        for r in repeats:
            ri += f'<div style="background:#FFFBEB;border:1px solid #F6AD55;border-radius:8px;padding:14px 16px;margin-bottom:10px;font-size:13px;color:#4A5568;font-family:Arial,sans-serif;line-height:1.6;">'
            ri += f'<b>{r["client_domain"]}</b> &nbsp;·&nbsp; {r["rep_name"]}<br/>'
            ri += f'<span style="font-size:12px;color:#718096;">{r["note"]}</span></div>'
        ri += '</td></tr>'
        template = template.replace("{{repeat_issues_html}}", ri)
    else:
        template = template.replace("{{repeat_issues_html}}", "")

    return template


# ─── Send email via Resend ────────────────────────────────────────────────────
def send_email(to_email: str, to_name: str, subject: str, html: str) -> bool:
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type":  "application/json",
        },
        json={
            "from":    f"{FROM_NAME} <{FROM_EMAIL}>",
            "to":      [f"{to_name} <{to_email}>"],
            "cc":      [f"Ari <{ARI_EMAIL}>"],
            "subject": subject,
            "html":    html,
        },
        timeout=20,
    )
    if resp.ok:
        log.info(f"  ✅ Email sent to {to_email}")
        return True
    else:
        log.error(f"  ❌ Email failed to {to_email}: {resp.status_code} {resp.text[:200]}")
        return False


# ─── Build Slack message ──────────────────────────────────────────────────────
def build_slack_message(digest: dict, recipient_name: str) -> list[dict]:
    """
    Slack Block Kit message. Hard limits enforced:
      Max 50 blocks | Max 3000 chars/field | Max 150 chars for header
    Priority: Summary -> Flags -> Coaching -> Top Calls -> Scorecard -> Missing FF
    """
    overall    = digest["overall"]
    date_range = digest["date_range"]
    week_range = f"{date_range['from']} to {date_range['to']}"

    avg         = overall.get("avg_score", "—")
    graded      = overall.get("total_graded", 0)
    total_flags = overall.get("total_flags", 0)
    missing_ff  = len(digest.get("missing_fireflies") or [])
    highest     = overall.get("highest_score", "—")
    lowest      = overall.get("lowest_score", "—")
    dist        = overall.get("grade_distribution") or {}
    trend_map   = {"up": "(+)", "down": "(-)", "stable": "(=)", "new": "new", "no_calls": "—"}

    def T(text: str, limit: int = 2900) -> str:
        return text if len(text) <= limit else text[:limit] + "..."

    def div() -> dict:
        return {"type": "divider"}

    def hdr(text: str) -> dict:
        return {"type": "header", "text": {"type": "plain_text", "text": text[:149], "emoji": False}}

    def sec(text: str) -> dict:
        return {"type": "section", "text": {"type": "mrkdwn", "text": T(text)}}

    def two(left: str, right: str) -> dict:
        return {"type": "section", "fields": [
            {"type": "mrkdwn", "text": T(left)},
            {"type": "mrkdwn", "text": T(right)},
        ]}

    def four(a: str, b: str, c: str, d: str) -> dict:
        return {"type": "section", "fields": [
            {"type": "mrkdwn", "text": T(a)},
            {"type": "mrkdwn", "text": T(b)},
            {"type": "mrkdwn", "text": T(c)},
            {"type": "mrkdwn", "text": T(d)},
        ]}

    def ctx(text: str) -> dict:
        return {"type": "context", "elements": [{"type": "mrkdwn", "text": T(text)}]}

    blocks = []

    # 1. HEADER
    blocks.append(hdr("Zeno — Weekly Call QA Digest"))
    blocks.append(ctx(f"Week: {week_range}   |   Hi {recipient_name}"))
    blocks.append(div())

    # 2. SUMMARY — 2 blocks
    blocks.append(four(
        f"*Calls Graded*\n`{graded}`",
        f"*Avg Team Score*\n`{avg} / 100`",
        f"*Flags Raised*\n`{total_flags}`",
        f"*Missing Recordings*\n`{missing_ff}`",
    ))
    grade_line = "  ".join(f"*{g}:* {dist.get(g, 0)}" for g in ["A", "B", "C", "D"])
    blocks.append(sec(f"Score range: *{lowest}* — *{highest}*     Grades:  {grade_line}"))
    blocks.append(div())

    # 3. FLAGS — priority sorted, max 4 types x 2 calls each
    flagged = digest.get("flagged_calls") or {}
    priority_order = [
        "churn_language", "competitor_mentioned", "sla_miss_no_resolution",
        "pricing_pushback", "negative_sentiment_end", "no_next_step",
        "short_call", "fireflies_missing", "repeat_issue",
    ]
    sorted_flags = sorted(
        flagged.items(),
        key=lambda x: priority_order.index(x[0]) if x[0] in priority_order else 99
    )
    if sorted_flags:
        blocks.append(sec("*FLAGGED CALLS — Requires Attention*"))
        for flag_key, flag_calls in sorted_flags[:4]:
            flag_label = digest.get("flag_definitions", {}).get(flag_key, flag_key)
            lines = [f"_{flag_label}_"]
            for fc in flag_calls[:2]:
                score = f"  Score: `{fc['score_total']}`" if fc.get("score_total") is not None else ""
                date  = (fc.get("date") or "")[:10]
                rep   = fc.get("organizer", "").split("@")[0]
                lines.append(f">  <{fc['fireflies_url']}|{fc['title'][:50]}>   {date}{score}   rep: {rep}")
            if len(flag_calls) > 2:
                lines.append(f">  _... and {len(flag_calls) - 2} more in email digest_")
            blocks.append(sec("\n".join(lines)))
        if len(sorted_flags) > 4:
            extra = [digest.get("flag_definitions", {}).get(k, k) for k, _ in sorted_flags[4:]]
            blocks.append(ctx(f"Additional flags in email: {chr(44).join(extra)}"))
        blocks.append(div())

    # 4. CALLS NEEDING COACHING
    bottom_calls = digest.get("bottom_calls") or []
    if bottom_calls:
        blocks.append(sec("*CALLS NEEDING COACHING*"))
        for bc in bottom_calls:
            rep   = ", ".join(e.split("@")[0] for e in (bc.get("team_members") or []))
            date  = (bc.get("date") or "")[:10]
            dur   = bc.get("duration_minutes") or "?"
            note  = T((bc.get("coaching_note") or "Review call for coaching opportunities."), 180)
            score = bc.get("score_total", "—")
            grade = bc.get("grade", "—")
            flag_str = ", ".join(bc.get("auto_flags") or []) or "—"
            blocks.append(two(
                f"*<{bc['fireflies_url']}|{bc['title'][:48]}>*\n"
                f"Score: `{score} / 100`   Grade: `{grade}`\n"
                f"Rep: {rep}   {date}   {dur} min",
                f"*Flags:*\n`{flag_str}`\n\n*Note:*\n_{note}_",
            ))
        blocks.append(div())

    # 5. TOP CALLS
    top_calls = digest.get("top_calls") or []
    if top_calls:
        blocks.append(sec("*TOP CALLS — Share in Huddle*"))
        for tc in top_calls:
            rep  = ", ".join(e.split("@")[0] for e in (tc.get("team_members") or []))
            date = (tc.get("date") or "")[:10]
            dur  = tc.get("duration_minutes") or "?"
            blocks.append(two(
                f"*<{tc['fireflies_url']}|{tc['title'][:48]}>*\n"
                f"Score: `{tc['score_total']} / 100`   Grade: `{tc.get('grade','—')}`",
                f"Rep: {rep}\n{date}   {dur} min",
            ))
        blocks.append(div())

    # 6. TEAM SCORECARD — all reps in one monospaced block
    blocks.append(sec("*TEAM SCORECARD*"))
    reps = [r for r in digest["rep_scorecard"] if r["call_count"] > 0]
    lines = ["```"]
    lines.append(f"{'Rep':<22} {'Calls':>5}  {'Score':>6}  {'Grade':>5}  {'Trend':>6}  {'Flags':>5}")
    lines.append("─" * 58)
    for rep in reps:
        name   = rep["name"][:21]
        calls  = rep["call_count"]
        avg_sc = rep.get("avg_score") or "—"
        grade  = rep.get("grade") or "—"
        trend  = trend_map.get(rep.get("trend", ""), "—")
        flags_n= rep.get("flag_count", 0)
        lines.append(f"{name:<22} {calls:>5}  {str(avg_sc):>6}  {grade:>5}  {trend:>6}  {flags_n:>5}")
    lines.append("```")
    blocks.append(sec("\n".join(lines)))
    blocks.append(div())

    # 7. MISSING FIREFLIES — summary + top 4
    if missing_ff > 0:
        missing = digest.get("missing_fireflies") or []
        rep_counts: dict = {}
        for mf in missing:
            org = mf.get("organizer", "unknown").split("@")[0]
            rep_counts[org] = rep_counts.get(org, 0) + 1
        rep_summary = "   ".join(
            f"{r}: {c}" for r, c in sorted(rep_counts.items(), key=lambda x: -x[1])
        )
        blocks.append(sec(
            f"*MISSING FIREFLIES — {missing_ff} call(s) not recorded*\n"
            f"By rep:  {rep_summary}\n"
            f"_Ensure Fireflies Chrome extension is active and connected._"
        ))
        shown_lines = []
        for mf in missing[:4]:
            org  = mf.get("organizer", "").split("@")[0]
            date = (mf.get("date") or "")[:10]
            shown_lines.append(f">  {mf['title'][:50]}   rep: {org}   {date}")
        if missing_ff > 4:
            shown_lines.append(f">  _... and {missing_ff - 4} more. Full list in email digest._")
        blocks.append(sec("\n".join(shown_lines)))
        blocks.append(div())

    # 8. REPEAT ISSUES
    repeat_issues = digest.get("repeat_issues") or []
    if repeat_issues:
        lines = ["*REPEAT ISSUES — Same Client, Second Week*"]
        for r in repeat_issues[:3]:
            lines.append(f">  *{r['client_domain']}*   rep: {r['rep_name']}")
        blocks.append(sec("\n".join(lines)))
        blocks.append(div())

    # 9. FOOTER
    blocks.append(ctx(
        f"Sent by *Zeno* — AMZ Prep Call QA Agent   |   "
        f"Full report with all details sent via email   |   "
        f"Questions: <mailto:ari@amzprep.com|Ari>"
    ))

    # Enforce 50-block hard cap
    if len(blocks) > 50:
        log.warning(f"Block count {len(blocks)} exceeds Slack limit — truncating")
        blocks = blocks[:49]
        blocks.append(ctx("Message truncated. Full digest available in email."))

    log.info(f"Slack message built: {len(blocks)} blocks")
    return blocks


# ─── Send Slack DM ────────────────────────────────────────────────────────────
def open_slack_dm(user_ids: list[str]) -> str | None:
    """Opens a group DM (mpim) with given user IDs. Returns channel ID."""
    resp = requests.post(
        "https://slack.com/api/conversations.open",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json={"users": ",".join(user_ids)},
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        log.error(f"Failed to open Slack DM: {data.get('error')}")
        return None
    return data["channel"]["id"]


def send_slack_dm(channel_id: str, blocks: list[dict], fallback_text: str) -> bool:
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json={
            "channel":  channel_id,
            "text":     fallback_text,
            "blocks":   blocks,
            "username": "Zeno",
            "icon_emoji": ":zap:",
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("ok"):
        log.info(f"  ✅ Slack DM sent to channel {channel_id}")
        return True
    else:
        log.error(f"  ❌ Slack DM failed: {data.get('error')}")
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=== CS/AM Call QA Agent — Phase 4: Deliver ===")

    digest, _ = load_latest_digest()
    run_date   = digest["run_date"]
    date_range = digest["date_range"]
    week_range = f"{date_range['from']} to {date_range['to']}"
    subject    = f"Zeno Weekly QA Digest — {week_range}"

    # ── Email delivery ─────────────────────────────────────────────────────────
    log.info("Sending grading digest emails via Resend...")
    for to_email, to_name in [(LAKSHITA_EMAIL, "Lakshita"), (THOMAS_EMAIL, "Thomas")]:
        html = build_email_html(digest, to_name)
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from":    f"{FROM_NAME} <{FROM_EMAIL}>",
                "to":      [f"{to_name} <{to_email}>"],
                "cc":      CC_EMAILS,
                "subject": subject,
                "html":    html,
            },
            timeout=20,
        )
        if resp.ok:
            log.info(f"  Email sent to {to_email}  (CC: {', '.join(CC_EMAILS)})")
        else:
            log.error(f"  Email failed to {to_email}: {resp.status_code} {resp.text[:200]}")

    # ── Slack — disabled ───────────────────────────────────────────────────────
    if SLACK_ENABLED:
        log.info("Sending Slack DMs via Zeno...")
        # Re-enable by setting SLACK_ENABLED = True in config
    else:
        log.info("Slack delivery disabled — email only mode")

    log.info(f"Output digest: output/digest_{run_date}.json")
    log.info("=== Phase 4 complete ===")


if __name__ == "__main__":
    main()
