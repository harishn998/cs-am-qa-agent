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
LAKSHITA_EMAIL  = "harishnath@amzprep.com"
THOMAS_EMAIL    = "jerun@amzprep.com"
ARI_EMAIL       = "ari@amzprep.com"
CC_EMAILS       = [
    "jerun@amzprep.com",
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

# Import helpers from call_report (same logic, no duplication)
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from call_report import (
    detect_rag, rag_label, get_rep_names, extract_bullets,
    extract_action_items, TEAM_NAMES, RAG_COLORS,
)

RAG_PILL_CLASS = {"RED": "rag-red", "YELLOW": "rag-yellow", "GREEN": "rag-green"}


def _stat_box(num, label):
    return f"""<div class="stat-box"><div class="num">{num}</div><div class="lbl">{label}</div></div>"""


def _call_card(call: dict, show_rag: bool = True) -> str:
    """Render a single call card with summary bullets and action items."""
    rag      = detect_rag(call)
    cfg      = RAG_COLORS.get(rag, RAG_COLORS["YELLOW"])
    pill_cls = RAG_PILL_CLASS.get(rag, "rag-yellow")
    reps     = ", ".join(get_rep_names(call)) or "—"
    date     = (call.get("date") or "")[:10]
    dur      = f"{int(call.get('duration_minutes') or 0)} min"
    title    = call.get("title") or "Untitled"
    ff_url   = f"https://app.fireflies.ai/view/{call.get('id','')}"
    bullets  = extract_bullets(call)
    actions  = extract_action_items(call)

    ctype    = call.get("call_type", "")
    type_lbl = "Merchant" if ctype == "CS_AM" else "Warehouse / Ops" if ctype == "OPS" else "Internal"
    type_cls = "badge-merchant" if ctype == "CS_AM" else "badge-ops"

    html = f"""
<div class="call-card" style="background:{cfg['bg']};border-left-color:{cfg['border']};">
  <div class="call-card-header">
    <div class="call-title">
      <a href="{ff_url}">{title}</a>
      <span class="type-badge {type_cls}">{type_lbl}</span>
    </div>"""

    if show_rag:
        html += f"""<span class="rag-pill {pill_cls}" style="flex-shrink:0;">{rag_label(rag)}</span>"""

    html += f"""
  </div>
  <div class="call-meta">
    <strong>Date:</strong> {date} &nbsp;·&nbsp;
    <strong>Duration:</strong> {dur} &nbsp;·&nbsp;
    <strong>Rep:</strong> {reps}
  </div>"""

    if not call.get("short_summary"):
        html += """<p style="font-size:13px;color:#A0AEC0;font-style:italic;margin-bottom:12px;">
    No Fireflies summary available for this call.</p>"""
    else:
        html += """<div class="summary-label">Call Summary</div><ul class="bullets">"""
        for b in bullets:
            html += f"<li>{b}</li>"
        html += "</ul>"

    html += """<div class="action-block"><div class="action-block-title">Action Items &amp; Takeaways</div>"""
    if actions:
        for item in actions:
            html += f"""<div class="action-row">
      <span class="action-owner">{item['owner']}</span>
      <span class="action-text">{item['action']}</span>
    </div>"""
    else:
        html += """<p class="no-action">No action items captured for this call.</p>"""
    html += "</div>"

    html += f"""<a href="{ff_url}" class="ff-link">View full transcript in Fireflies &rarr;</a>
</div>"""
    return html


def build_email_html(digest: dict, recipient_name: str) -> str:
    template   = (TEMPLATES_DIR / "email_digest.html").read_text()
    date_range = digest["date_range"]
    week_range = f"{date_range['from']} to {date_range['to']}"

    # All calls from digest — graded calls JSON has full call data
    all_calls     = digest.get("calls") or []
    client_calls  = [c for c in all_calls if c.get("call_type") in ("CS_AM", "OPS")]
    internal_calls= [c for c in all_calls if c.get("call_type") == "INTERNAL"]
    missing_ff    = digest.get("missing_fireflies") or []

    # RAG counts across client calls
    rag_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    for c in client_calls:
        rag_counts[detect_rag(c)] += 1

    # CSM client call counts
    from collections import defaultdict
    csm_counts = defaultdict(int)
    for c in client_calls:
        for email in (c.get("team_members_on_call") or []):
            if email in TEAM_NAMES:
                csm_counts[email] += 1
    max_count = max(csm_counts.values()) if csm_counts else 1

    # ── Header tokens ─────────────────────────────────────────────────────────
    template = template.replace("{{recipient_name}}", recipient_name)
    template = template.replace("{{week_range}}", week_range)

    # ── Snapshot section ──────────────────────────────────────────────────────
    snap = f"""<div class="card">
  <div class="sec-eyebrow">Overview</div>
  <div class="sec-title">Week at a Glance</div>
  <div class="stats-grid">
    {_stat_box(len(client_calls), "Client Calls")}
    {_stat_box(len([c for c in client_calls if c.get("call_type")=="CS_AM"]), "Merchant Calls")}
    {_stat_box(len([c for c in client_calls if c.get("call_type")=="OPS"]), "Warehouse / Ops")}
    {_stat_box(len(internal_calls), "Internal Calls")}
  </div>
  <div class="stats-grid" style="margin-top:12px;">
    <div class="stat-box" style="border-color:#FC8181;"><div class="num" style="color:#C53030;">{rag_counts['RED']}</div><div class="lbl">Needs Attention</div></div>
    <div class="stat-box" style="border-color:#F6AD55;"><div class="num" style="color:#92600A;">{rag_counts['YELLOW']}</div><div class="lbl">Monitor Closely</div></div>
    <div class="stat-box" style="border-color:#68D391;"><div class="num" style="color:#22543D;">{rag_counts['GREEN']}</div><div class="lbl">On Track</div></div>
    {_stat_box(len(missing_ff), "Missing Recordings")}
  </div>
</div>"""
    template = template.replace("{{snapshot_section}}", snap)

    # ── CSM breakdown ─────────────────────────────────────────────────────────
    csm_rows = ""
    for email, count in sorted(csm_counts.items(), key=lambda x: -x[1]):
        name = TEAM_NAMES.get(email, email.split("@")[0].title())
        pct  = int((count / max_count) * 100)
        csm_rows += f"""<tr>
      <td class="rep-name">{name}</td>
      <td class="rep-count">{count}</td>
      <td><div class="bar-wrap"><div class="bar-fill" style="width:{pct}%;"></div></div></td>
    </tr>"""

    csm_sec = f"""<div class="card">
  <div class="sec-eyebrow">Team</div>
  <div class="sec-title">CSM Client Call Breakdown</div>
  <table class="csm-table">
    <thead><tr>
      <th>CSM</th><th>Client Calls</th><th style="width:180px;">Volume</th>
    </tr></thead>
    <tbody>{csm_rows}</tbody>
  </table>
</div>"""
    template = template.replace("{{csm_section}}", csm_sec)

    # ── Flagged calls ─────────────────────────────────────────────────────────
    flagged = digest.get("flagged_calls") or {}
    if flagged:
        flag_inner = ""
        priority_order = [
            "churn_language", "competitor_mentioned", "sla_miss_no_resolution",
            "pricing_pushback", "negative_sentiment_end", "no_next_step",
            "short_call", "fireflies_missing", "repeat_issue",
        ]
        sorted_flags = sorted(
            flagged.items(),
            key=lambda x: priority_order.index(x[0]) if x[0] in priority_order else 99
        )
        for flag_key, flag_calls in sorted_flags[:5]:
            flag_label = digest.get("flag_definitions", {}).get(flag_key, flag_key)
            flag_inner += f'<div class="flag-section-title">{flag_label}</div>'
            for fc in flag_calls[:3]:
                date_str = (fc.get("date") or "")[:10]
                rep_str  = (fc.get("organizer") or "").split("@")[0]
                flag_inner += f"""<div class="flag-call-row">
          <a href="{fc['fireflies_url']}">{fc['title']}</a>
          &nbsp;·&nbsp; {date_str} &nbsp;·&nbsp; {rep_str}
        </div>"""
            flag_inner += '<div style="height:14px;"></div>'
        flag_sec = f"""<div class="card">
  <div class="sec-eyebrow">Action Required</div>
  <div class="sec-title">Flagged Calls</div>
  {flag_inner}
</div>"""
    else:
        flag_sec = ""
    template = template.replace("{{flagged_section_html}}", flag_sec)

    # ── Client calls ──────────────────────────────────────────────────────────
    if client_calls:
        client_inner = "".join(_call_card(c, show_rag=True) for c in client_calls)
    else:
        client_inner = "<p style='font-size:14px;color:#A0AEC0;font-style:italic;'>No client calls this week.</p>"

    client_sec = f"""<div class="card">
  <div class="sec-eyebrow">External</div>
  <div class="sec-title">Client Calls <span style="font-size:14px;font-weight:500;color:#A0AEC0;">({len(client_calls)} calls)</span></div>
  {client_inner}
</div>"""
    template = template.replace("{{client_calls_section}}", client_sec)

    # ── Internal calls — compact, no RAG ─────────────────────────────────────
    if internal_calls:
        internal_inner = "".join(_call_card(c, show_rag=False) for c in internal_calls)
    else:
        internal_inner = "<p style='font-size:14px;color:#A0AEC0;font-style:italic;'>No internal calls this week.</p>"

    internal_sec = f"""<div class="card">
  <div class="sec-eyebrow">Internal</div>
  <div class="sec-title">Internal Calls <span style="font-size:14px;font-weight:500;color:#A0AEC0;">({len(internal_calls)} calls)</span></div>
  {internal_inner}
</div>"""
    template = template.replace("{{internal_calls_section}}", internal_sec)

    # ── Missing Fireflies ─────────────────────────────────────────────────────
    if missing_ff:
        ff_inner = f"""<div class="alert-box">
    {len(missing_ff)} call(s) not recorded this week. Every call should appear in Fireflies
    automatically once the Chrome extension is installed and active.
  </div>"""
        for mf in missing_ff[:20]:
            org  = (mf.get("organizer") or "").split("@")[0]
            date = (mf.get("date") or "")[:10]
            ff_inner += f"""<div class="ff-row">
      <strong>{mf['title']}</strong> &nbsp;·&nbsp; {org} &nbsp;·&nbsp; {date}
    </div>"""
        if len(missing_ff) > 20:
            ff_inner += f"<p style='font-size:12px;color:#A0AEC0;font-style:italic;padding-top:8px;'>... and {len(missing_ff)-20} more.</p>"
        ff_sec = f"""<div class="card">
  <div class="sec-eyebrow">Coverage</div>
  <div class="sec-title">Missing Fireflies Recordings</div>
  {ff_inner}
</div>"""
    else:
        ff_sec = """<div class="card">
  <div class="sec-eyebrow">Coverage</div>
  <div class="sec-title">Fireflies Coverage</div>
  <p style="font-size:14px;color:#22543D;font-weight:600;">All calls recorded this week.</p>
</div>"""
    template = template.replace("{{missing_ff_section_html}}", ff_sec)

    # ── Repeat issues ─────────────────────────────────────────────────────────
    repeats = digest.get("repeat_issues") or []
    if repeats:
        rep_inner = "".join(
            f"""<div class="repeat-card">
        <strong>{r['client_domain']}</strong> &nbsp;·&nbsp; {r['rep_name']}<br/>
        <span style="font-size:12px;color:#718096;">{r['note']}</span>
      </div>"""
            for r in repeats
        )
        rep_sec = f"""<div class="card">
  <div class="sec-eyebrow">Watch List</div>
  <div class="sec-title">Repeat Issue Accounts</div>
  {rep_inner}
</div>"""
        template = template.replace("{{repeat_issues_html}}", rep_sec)
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
