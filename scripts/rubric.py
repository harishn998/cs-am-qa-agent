"""
rubric.py — Shared rubric definitions for the CS/AM Call QA Agent.
Imported by grade_calls.py and aggregate.py.
"""

# ─── Scoring Sections ─────────────────────────────────────────────────────────

HYGIENE_CRITERIA = {
    "fireflies_attached":   {"max": 5,  "label": "Fireflies Attached"},
    "agenda_set":           {"max": 5,  "label": "Agenda Set Early"},
    "talk_listen_ratio":    {"max": 5,  "label": "Talk-to-Listen Ratio"},
    "clear_next_steps":     {"max": 5,  "label": "Clear Next Steps"},
}

CS_AM_CRITERIA = {
    "account_health_check":     {"max": 10, "label": "Account Health Check"},
    "issue_resolution":         {"max": 15, "label": "Issue Resolution Clarity"},
    "proactive_value":          {"max": 10, "label": "Proactive Value Delivery"},
    "expansion_signals":        {"max": 10, "label": "Expansion Signals Captured"},
    "risk_signals":             {"max": 15, "label": "Risk Signals Captured"},
    "hubspot_updated":          {"max": 10, "label": "HubSpot Updated"},
    "followup_scheduled":       {"max": 10, "label": "Follow-up Scheduled"},
}

OPS_CRITERIA = {
    "issue_defined":            {"max": 15, "label": "Issue Clearly Defined"},
    "root_cause":               {"max": 15, "label": "Root Cause Identified"},
    "resolution_path":          {"max": 15, "label": "Resolution Path Stated"},
    "sla_reset":                {"max": 10, "label": "SLA / Expectation Reset"},
    "documentation_referenced": {"max": 10, "label": "Documentation Referenced"},
    "sentiment_recovery":       {"max": 10, "label": "Customer Sentiment Recovery"},
    "internal_handoff":         {"max": 5,  "label": "Internal Handoff Clear"},
}

HYGIENE_MAX   = sum(v["max"] for v in HYGIENE_CRITERIA.values())   # 20
CS_AM_MAX     = sum(v["max"] for v in CS_AM_CRITERIA.values())     # 80
OPS_MAX       = sum(v["max"] for v in OPS_CRITERIA.values())       # 80
TOTAL_MAX     = 100

# ─── Grading Bands ────────────────────────────────────────────────────────────

GRADE_BANDS = [
    {"grade": "A", "min": 90, "max": 100, "label": "Exemplary",        "action": "Share as best practice in team huddle"},
    {"grade": "B", "min": 75, "max": 89,  "label": "Solid",            "action": "No action needed"},
    {"grade": "C", "min": 60, "max": 74,  "label": "Needs Coaching",   "action": "Coaching note attached"},
    {"grade": "D", "min": 0,  "max": 59,  "label": "Needs Review",     "action": "Manager 1:1 review required"},
]

def score_to_grade(score: int) -> str:
    for band in GRADE_BANDS:
        if band["min"] <= score <= band["max"]:
            return band["grade"]
    return "D"

def grade_action(grade: str) -> str:
    for band in GRADE_BANDS:
        if band["grade"] == grade:
            return band["action"]
    return ""

# ─── Auto-Flag Conditions ─────────────────────────────────────────────────────

AUTO_FLAGS = {
    "churn_language":           "Customer used churn language (cancel / leaving / switching / pause / downgrade / terminate)",
    "competitor_mentioned":     "Competitor named (ShipBob, Deliverr, Stord, Ware2Go, Saddle Creek, etc.)",
    "pricing_pushback":         "Pricing pushback raised and left unresolved",
    "sla_miss_no_resolution":   "SLA miss discussed without a clear resolution path",
    "short_call":               "Call under 10 min (QBR / onboarding / escalation context)",
    "no_next_step":             "No next step captured before call ended",
    "fireflies_missing":        "Fireflies bot did not join — transcript not captured",
    "negative_sentiment_end":   "Negative sentiment shift in the last 5 minutes of the call",
    "repeat_issue":             "Repeat issue: same client, same problem within 30 days",
}

# ─── Prompt Templates ─────────────────────────────────────────────────────────

GRADING_SYSTEM_PROMPT = """
You are Zeno, AMZ Prep's internal call quality analyst.
Your job is to grade customer-facing calls made by the CS/AM and Ops teams
at AMZ Prep, a 3PL/eCommerce fulfillment company.

You will be given a call summary, action items, keywords, metadata, and the
rubric to grade against. You must return ONLY a valid JSON object — no
preamble, no markdown, no explanation outside the JSON.

Scoring rules:
- Be honest and strict. A score of 5/5 should be rare and earned.
- If information is missing from the summary (e.g. no mention of next steps),
  deduct points — do not assume it happened.
- Coaching notes must be specific: name the exact gap and what the rep should
  have done differently. Never write generic feedback.
- Keep coaching notes under 60 words.
- For auto_flags, only set true if you have clear evidence in the summary.
""".strip()


def build_grading_prompt(call: dict) -> str:
    call_type = call["call_type"]  # CS_AM or OPS

    if call_type == "CS_AM":
        rubric_text = """
RUBRIC — CS/AM CALL (80 pts):
- account_health_check (0-10): Did rep ask about volume trends, upcoming launches, seasonality, pain points?
- issue_resolution (0-15): If a problem was raised, was a fix committed with owner and ETA?
- proactive_value (0-10): Did rep bring something new — data insight, AMZ Prep update, optimization idea?
- expansion_signals (0-10): Were new SKUs, channels, geos, FBA+DTC, cold storage, freight needs surfaced?
- risk_signals (0-15): Were churn language, complaints, competitor mentions, escalations flagged?
- hubspot_updated (0-10): Were call notes logged, tasks created, deal/company record updated?
- followup_scheduled (0-10): Was next touchpoint booked on calendar before call ended (not vague "let's reconnect")?
""".strip()
        rubric_keys = list(CS_AM_CRITERIA.keys())
    else:
        rubric_text = """
RUBRIC — FULFILLMENT/OPS CALL (80 pts):
- issue_defined (0-15): Did rep restate the problem in their own words to confirm understanding?
- root_cause (0-15): Was the root cause explained, or did rep commit to find out with a deadline?
- resolution_path (0-15): Was a concrete action stated — refund, reship, credit, SOP fix, escalation?
- sla_reset (0-10): If a commitment was missed, was a new realistic timeline given?
- documentation_referenced (0-10): Did rep pull shipment ID, ASIN, FC location, tracking — showing prep?
- sentiment_recovery (0-10): If call started hot, did rep de-escalate by the end?
- internal_handoff (0-5): If warehouse/tech/finance needs to act, was owner named?
""".strip()
        rubric_keys = list(OPS_CRITERIA.keys())

    fireflies_joined = call.get("fireflies_joined", False)

    prompt = f"""
CALL METADATA:
- Title: {call['title']}
- Date: {call['date']}
- Duration: {call['duration_minutes']} minutes
- Organizer: {call['organizer_email']}
- Team members on call: {', '.join(call['team_members_on_call'])}
- External participants: {', '.join(call['external_participants']) or 'Unknown'}
- Fireflies bot joined: {fireflies_joined}
- Call type: {call_type}

CALL SUMMARY:
{call.get('short_summary') or 'No summary available.'}

ACTION ITEMS:
{call.get('action_items') or 'None captured.'}

KEYWORDS:
{', '.join(call.get('keywords') or []) or 'None'}

---

SHARED HYGIENE RUBRIC (20 pts):
- fireflies_attached (0-5): Bot joined and transcript captured. Score 5 if fireflies_joined=true, 0 if false.
- agenda_set (0-5): Rep stated purpose and desired outcome in first 2 minutes.
- talk_listen_ratio (0-5): Customer talked 50%+. Penalize rep monologuing.
- clear_next_steps (0-5): Who does what, by when — stated before call ended.

{rubric_text}

---

AUTO-FLAG CONDITIONS (set true only if clearly evidenced):
- churn_language: Customer used: cancel, leaving, switching, pause, downgrade, terminate
- competitor_mentioned: ShipBob, Deliverr, Stord, Ware2Go, Saddle Creek, or similar named
- pricing_pushback: Pricing objection raised and NOT resolved
- sla_miss_no_resolution: SLA miss discussed with no resolution path given
- short_call: Call under 10 min AND it was a QBR, onboarding, or escalation
- no_next_step: No clear next step committed before call ended
- fireflies_missing: Fireflies did not join (fireflies_joined = false)
- negative_sentiment_end: Sentiment clearly deteriorated in final minutes

---

Respond with ONLY this JSON and nothing else:
{{
  "score_hygiene": <int 0-20>,
  "score_rubric": <int 0-80>,
  "score_total": <int 0-100>,
  "grade": <"A"|"B"|"C"|"D">,
  "score_breakdown": {{
    "fireflies_attached": <int>,
    "agenda_set": <int>,
    "talk_listen_ratio": <int>,
    "clear_next_steps": <int>,
    {', '.join(f'"{k}": <int>' for k in rubric_keys)}
  }},
  "auto_flags": [<list of flag keys that are true>],
  "coaching_note": <string, null if grade A or B>
}}
""".strip()

    return prompt
