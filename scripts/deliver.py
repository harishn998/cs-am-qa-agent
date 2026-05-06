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
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

OUTPUT_DIR      = Path("output")
TEMPLATES_DIR   = Path(__file__).parent / "templates"

# Recipients
LAKSHITA_EMAIL  = "harishnath@amzprep.com"
THOMAS_EMAIL    = "jerun@amzprep.com"
ARI_EMAIL       = "ari@amzprep.com"

# Slack User IDs
LAKSHITA_SLACK  = "U07HW2GFSG4"   # ← replace with real Slack user ID
THOMAS_SLACK    = "U0ACYKH849J"     # ← replace with real Slack user ID
ARI_SLACK       = "U06CP1PJN3Y"         # Ari's confirmed Slack ID

FROM_EMAIL      = "reports@amzprep.com"
FROM_NAME       = "Zeno · AMZ Prep QA"


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
        flagged_html = '<div class="section"><div class="section-title">🚩 Auto-Flagged Calls — Manager Review</div>'
        for flag_key, flag_calls in flagged.items():
            flag_label = digest.get("flag_definitions", {}).get(flag_key, flag_key)
            flagged_html += f'<div class="flag-title">⚠ {flag_label}</div>'
            for fc in flag_calls:
                score_txt = f" · Score: {fc['score_total']}" if fc.get("score_total") is not None else ""
                flagged_html += f'<div style="font-size:13px;padding:6px 0;border-bottom:1px solid #f0f4f8;"><a href="{fc["fireflies_url"]}" class="call-link">{fc["title"]}</a> <span style="color:#6b7a8d;">— {fc.get("date","")[:10]}{score_txt}</span></div>'
            flagged_html += '<div class="divider"></div>'
        flagged_html += "</div>"
    else:
        flagged_html = '<div class="section"><div class="section-title">🚩 Auto-Flagged Calls</div><p style="color:#6b7a8d;font-size:13px;">No flags raised this week. ✅</p></div>'
    template = template.replace("{{flagged_section_html}}", flagged_html)

    # ── Missing Fireflies ──────────────────────────────────────────────────────
    missing = digest.get("missing_fireflies") or []
    if missing:
        mff_html = '<div class="section"><div class="section-title">🎙 Missing Fireflies Recordings</div><div class="alert">These calls were not recorded. Check Chrome extension on affected machines.</div>'
        for mf in missing:
            mff_html += f'<div style="font-size:13px;padding:6px 0;border-bottom:1px solid #f0f4f8;">{mf["title"]} <span style="color:#6b7a8d;">— {mf.get("organizer","")} · {mf.get("date","")[:10]}</span></div>'
        mff_html += "</div>"
    else:
        mff_html = '<div class="section"><div class="section-title">🎙 Fireflies Coverage</div><p style="color:#2e7d32;font-size:13px;">✅ All calls recorded this week.</p></div>'
    template = template.replace("{{missing_ff_section_html}}", mff_html)

    # ── Repeat issues ──────────────────────────────────────────────────────────
    repeats = digest.get("repeat_issues") or []
    if repeats:
        rep_html = '<div class="section"><div class="section-title">🔁 Repeat Issue Accounts</div>'
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
    """Builds Slack Block Kit blocks for the weekly digest DM."""
    overall    = digest["overall"]
    date_range = digest["date_range"]
    week_range = f"{date_range['from']} → {date_range['to']}"

    avg        = overall.get("avg_score", "—")
    graded     = overall.get("total_graded", 0)
    flags      = overall.get("total_flags", 0)
    missing_ff = len(digest.get("missing_fireflies") or [])

    # Grade distribution summary
    dist = overall.get("grade_distribution") or {}
    dist_text = " · ".join(f"*{g}:* {dist.get(g,0)}" for g in ["A","B","C","D"])

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "⚡ Zeno — Weekly Call QA Digest"}
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Week: *{week_range}*  |  Hi {recipient_name} 👋"}]
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*📞 Calls Graded*\n{graded}"},
                {"type": "mrkdwn", "text": f"*📊 Avg Team Score*\n{avg}/100"},
                {"type": "mrkdwn", "text": f"*🚩 Flags Raised*\n{flags}"},
                {"type": "mrkdwn", "text": f"*🎙 Missing Fireflies*\n{missing_ff}"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Grade Breakdown:*  {dist_text}"}
        },
        {"type": "divider"},
    ]

    # Rep scorecard
    scorecard_lines = ["*👥 Team Scorecard*"]
    trend_icons = {"up": "↑", "down": "↓", "stable": "→", "new": "★", "no_calls": "—"}
    for rep in digest["rep_scorecard"]:
        if rep["call_count"] == 0:
            continue
        trend_icon = trend_icons.get(rep.get("trend", ""), "")
        avg_score  = rep.get("avg_score", "—")
        scorecard_lines.append(
            f"• *{rep['name']}*  —  {rep['call_count']} calls  ·  Score: *{avg_score}*  ·  Grade: *{rep.get('grade','—')}*  {trend_icon}"
        )
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(scorecard_lines)}})
    blocks.append({"type": "divider"})

    # Top calls
    if digest.get("top_calls"):
        top_lines = ["*🏆 Top Calls — Share in Huddle*"]
        for tc in digest["top_calls"]:
            top_lines.append(f"• <{tc['fireflies_url']}|{tc['title']}> — Score: *{tc['score_total']}* (Grade {tc.get('grade','')})")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(top_lines)}})
        blocks.append({"type": "divider"})

    # Bottom calls
    if digest.get("bottom_calls"):
        bottom_lines = ["*🎯 Calls Needing Coaching*"]
        for bc in digest["bottom_calls"]:
            note = f"\n  _{bc['coaching_note']}_" if bc.get("coaching_note") else ""
            bottom_lines.append(f"• <{bc['fireflies_url']}|{bc['title']}> — Score: *{bc['score_total']}* (Grade {bc.get('grade','')}){note}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(bottom_lines)}})
        blocks.append({"type": "divider"})

    # Flags
    flagged = digest.get("flagged_calls") or {}
    if flagged:
        flag_lines = ["*🚩 Auto-Flagged Calls*"]
        for flag_key, flag_calls in flagged.items():
            flag_label = digest.get("flag_definitions", {}).get(flag_key, flag_key)
            for fc in flag_calls[:3]:   # max 3 per flag type in Slack
                flag_lines.append(f"• `{flag_key}` → <{fc['fireflies_url']}|{fc['title']}>")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(flag_lines)}})
        blocks.append({"type": "divider"})

    # Missing Fireflies
    if missing_ff > 0:
        missing = digest.get("missing_fireflies") or []
        mf_lines = [f"*🎙 {missing_ff} Call(s) Without Fireflies Recording*"]
        for mf in missing[:5]:
            mf_lines.append(f"• {mf['title']} ({mf.get('organizer','')}) — {mf.get('date','')[:10]}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(mf_lines)}})

    # Footer
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "Sent by *Zeno* · AMZ Prep Call QA Agent · Questions? Reach <mailto:ari@amzprep.com|Ari>"}]
    })

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
    run_date  = digest["run_date"]
    date_range = digest["date_range"]
    week_range = f"{date_range['from']} → {date_range['to']}"
    subject    = f"⚡ Zeno Weekly QA Digest — {week_range}"

    # ── Email delivery ─────────────────────────────────────────────────────────
    log.info("Sending emails via Resend...")

    for to_email, to_name in [(LAKSHITA_EMAIL, "Lakshita"), (THOMAS_EMAIL, "Thomas")]:
        html = build_email_html(digest, to_name)
        send_email(to_email, to_name, subject, html)

    # ── Slack delivery ─────────────────────────────────────────────────────────
    log.info("Sending Slack DMs via Zeno...")
    fallback = f"⚡ Zeno Weekly QA Digest — {week_range}"

    # DM to Lakshita + Ari
    ch_lakshita = open_slack_dm([LAKSHITA_SLACK, ARI_SLACK])
    if ch_lakshita:
        blocks = build_slack_message(digest, "Lakshita")
        send_slack_dm(ch_lakshita, blocks, fallback)

    # DM to Thomas + Ari
    ch_thomas = open_slack_dm([THOMAS_SLACK, ARI_SLACK])
    if ch_thomas:
        blocks = build_slack_message(digest, "Thomas")
        send_slack_dm(ch_thomas, blocks, fallback)

    log.info(f"Output digest: output/digest_{run_date}.json")
    log.info("=== Phase 4 complete ===")


if __name__ == "__main__":
    main()
