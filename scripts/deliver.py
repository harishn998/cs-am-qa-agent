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
    "ari@amzprep.com",
    "harishnath@amzprep.com",
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
def build_email_html(digest: dict, recipient_name: str) -> str:
    template = (TEMPLATES_DIR / "email_digest.html").read_text()

    overall     = digest["overall"]
    date_range  = digest["date_range"]
    week_range  = f"{date_range['from']} → {date_range['to']}"

    # ── Overall stats ──────────────────────────────────────────────────────────
    template = template.replace("{{week_range}}",   week_range)
    template = template.replace("{{total_graded}}", str(overall.get("total_graded", 0)))
    template = template.replace("{{avg_score}}",    str(overall.get("avg_score", "—")))
    template = template.replace("{{total_flags}}",  str(overall.get("total_flags", 0)))
    template = template.replace("{{missing_ff}}",   str(len(digest.get("missing_fireflies") or [])))

    # ── Rep scorecard rows ─────────────────────────────────────────────────────
    trend_map = {
        "up":       '<span class="trend-up">↑ Up</span>',
        "down":     '<span class="trend-down">↓ Down</span>',
        "stable":   '<span class="trend-stable">→ Stable</span>',
        "new":      '<span class="trend-new">★ New</span>',
        "no_calls": '<span class="trend-stable">—</span>',
    }
    rows_html = ""
    for rep in digest["rep_scorecard"]:
        grade     = rep.get("grade") or "—"
        avg       = rep.get("avg_score")
        trend     = trend_map.get(rep.get("trend", ""), "")
        badge     = f'<span class="badge grade-{grade}">{grade}</span>' if grade != "—" else "—"
        rows_html += f"""
        <tr>
          <td><strong>{rep['name']}</strong></td>
          <td>{rep['call_count']}</td>
          <td><strong>{avg if avg is not None else '—'}</strong></td>
          <td>{badge}</td>
          <td>{trend}</td>
          <td>{rep['flag_count']}</td>
        </tr>"""
    template = template.replace("{{rep_scorecard_rows}}", rows_html)

    # ── Top calls ──────────────────────────────────────────────────────────────
    top_html = ""
    for call in digest.get("top_calls") or []:
        grade  = call.get("grade", "")
        score  = call.get("score_total", 0)
        members = ", ".join(call.get("team_members", []))
        top_html += f"""
        <div class="call-card">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <div class="call-title">{call['title']}</div>
              <div class="call-meta">{call.get('date','')[:10]} &nbsp;·&nbsp; {call.get('duration_minutes','?')} min &nbsp;·&nbsp; {members}</div>
            </div>
            <div style="text-align:right;">
              <div class="call-score" style="color:#2e7d32;">{score}</div>
              <span class="badge grade-{grade}">{grade}</span>
            </div>
          </div>
          <a href="{call['fireflies_url']}" class="call-link">▶ View in Fireflies</a>
        </div>"""
    template = template.replace("{{top_calls_html}}", top_html or "<p style='color:#6b7a8d;font-size:13px;'>No top calls this week.</p>")

    # ── Bottom calls ───────────────────────────────────────────────────────────
    bottom_html = ""
    for call in digest.get("bottom_calls") or []:
        grade   = call.get("grade", "")
        score   = call.get("score_total", 0)
        members = ", ".join(call.get("team_members", []))
        coaching = f'<div class="call-coaching">💡 {call["coaching_note"]}</div>' if call.get("coaching_note") else ""
        flags_html = " ".join(f'<span class="flag-pill">{f}</span>' for f in (call.get("auto_flags") or []))
        bottom_html += f"""
        <div class="call-card">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <div class="call-title">{call['title']}</div>
              <div class="call-meta">{call.get('date','')[:10]} &nbsp;·&nbsp; {call.get('duration_minutes','?')} min &nbsp;·&nbsp; {members}</div>
              {f'<div style="margin-top:6px;">{flags_html}</div>' if flags_html else ''}
            </div>
            <div style="text-align:right;">
              <div class="call-score" style="color:#c62828;">{score}</div>
              <span class="badge grade-{grade}">{grade}</span>
            </div>
          </div>
          {coaching}
          <div style="margin-top:10px;"><a href="{call['fireflies_url']}" class="call-link">▶ View in Fireflies</a></div>
        </div>"""
    template = template.replace("{{bottom_calls_html}}", bottom_html or "<p style='color:#6b7a8d;font-size:13px;'>No coaching calls this week — great job!</p>")

    # ── Flagged calls section ──────────────────────────────────────────────────
    flagged = digest.get("flagged_calls") or {}
    if flagged:
        flagged_html = '<div class="section"><div class="section-title">Auto-Flagged Calls — Manager Review</div>'
        for flag_key, flag_calls in flagged.items():
            flag_label = digest.get("flag_definitions", {}).get(flag_key, flag_key)
            flagged_html += f'<div class="flag-title">{flag_label}</div>'
            for fc in flag_calls:
                score_txt = f" · Score: {fc['score_total']}" if fc.get("score_total") is not None else ""
                flagged_html += f'<div style="font-size:13px;padding:6px 0;border-bottom:1px solid #f0f4f8;"><a href="{fc["fireflies_url"]}" class="call-link">{fc["title"]}</a> <span style="color:#6b7a8d;">— {fc.get("date","")[:10]}{score_txt}</span></div>'
            flagged_html += '<div class="divider"></div>'
        flagged_html += "</div>"
    else:
        flagged_html = '<div class="section"><div class="section-title">Auto-Flagged Calls</div><p style="color:#6b7a8d;font-size:13px;">No flags raised this week.</p></div>'
    template = template.replace("{{flagged_section_html}}", flagged_html)

    # ── Missing Fireflies ──────────────────────────────────────────────────────
    missing = digest.get("missing_fireflies") or []
    if missing:
        mff_html = '<div class="section"><div class="section-title">Missing Fireflies Recordings</div><div class="alert">These calls were not recorded. Check Chrome extension on affected machines.</div>'
        for mf in missing:
            mff_html += f'<div style="font-size:13px;padding:6px 0;border-bottom:1px solid #f0f4f8;">{mf["title"]} <span style="color:#6b7a8d;">— {mf.get("organizer","")} · {mf.get("date","")[:10]}</span></div>'
        mff_html += "</div>"
    else:
        mff_html = '<div class="section"><div class="section-title">Fireflies Coverage</div><p style="color:#2e7d32;font-size:13px;">All calls recorded this week.</p></div>'
    template = template.replace("{{missing_ff_section_html}}", mff_html)

    # ── Repeat issues ──────────────────────────────────────────────────────────
    repeats = digest.get("repeat_issues") or []
    if repeats:
        rep_html = '<div class="section"><div class="section-title">Repeat Issue Accounts</div>'
        for r in repeats:
            rep_html += f'<div class="call-card"><strong>{r["client_domain"]}</strong> · {r["rep_name"]}<div style="font-size:12px;color:#6b7a8d;margin-top:4px;">{r["note"]}</div></div>'
        rep_html += "</div>"
        template = template.replace("{{repeat_issues_html}}", rep_html)
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
