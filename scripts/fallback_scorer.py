"""
fallback_scorer.py — Rule-based call scorer for CS/AM Call QA Agent.
=====================================================================
Used automatically when Claude API is unavailable (e.g. low credits).
Scores each call based on keyword analysis of the summary, action items,
and call metadata. No external API calls — fully offline.

Scoring is conservative by design: if evidence is absent, points are deducted.
Claude grading is always preferred when available.
"""

import re
from rubric import (
    HYGIENE_CRITERIA, CS_AM_CRITERIA, OPS_CRITERIA,
    AUTO_FLAGS, score_to_grade,
)

# ─── Keyword signal banks ──────────────────────────────────────────────────────

# Positive signals — indicate good rep behaviour
NEXT_STEP_SIGNALS = [
    "next call", "follow up", "follow-up", "scheduled", "booked", "calendar",
    "meeting invite", "next week", "next month", "action item", "by eod",
    "by friday", "by monday", "by end of", "will send", "will share",
    "will schedule", "will book", "confirmed for", "next touchpoint",
]

AGENDA_SIGNALS = [
    "agenda", "today we", "purpose of", "goal for", "objective", "plan to cover",
    "wanted to discuss", "on today's call", "let's cover", "walk through",
    "three things", "two things", "few things",
]

EXPANSION_SIGNALS = [
    "new sku", "new channel", "new market", "additional volume", "expand",
    "add on", "addon", "freight", "cold storage", "middle mile", "fba",
    "dtc", "uk", "canada", "new geo", "b2b", "wholesale", "new service",
    "additional service", "new product",
]

PROACTIVE_SIGNALS = [
    "benchmark", "insight", "data show", "we noticed", "we see", "trend",
    "recommend", "suggestion", "opportunity", "industry", "best practice",
    "update from", "new feature", "new capability", "wanted to share",
    "thought you'd", "heads up",
]

RISK_SIGNALS = [
    "concern", "issue", "problem", "complaint", "unhappy", "frustrated",
    "delay", "late", "missed", "error", "wrong", "damaged", "lost",
    "escalate", "urgent", "critical", "asap", "not happy", "disappointed",
]

HEALTH_CHECK_SIGNALS = [
    "volume", "orders", "shipments", "launch", "season", "q4", "q1", "q2", "q3",
    "peak", "forecast", "plan", "inventory level", "stock", "growth",
    "how are things", "how's business", "update on", "performance",
]

ISSUE_RESOLUTION_SIGNALS = [
    "will fix", "resolved", "resolution", "by", "owner", "responsible",
    "eta", "deadline", "committed", "we will", "i will", "team will",
    "follow up with", "escalated to",
]

HUBSPOT_SIGNALS = [
    "hubspot", "crm", "logged", "noted", "updated", "task created",
    "deal updated", "record updated", "notes added",
]

# Ops-specific signals
OPS_ISSUE_DEFINED_SIGNALS = [
    "so to confirm", "just to clarify", "what i'm hearing", "the issue is",
    "understand correctly", "problem is", "restate", "to summarize the issue",
]

OPS_ROOT_CAUSE_SIGNALS = [
    "root cause", "reason for", "happened because", "caused by", "identified",
    "traced to", "found that", "investigation", "will find out", "look into",
]

OPS_RESOLUTION_SIGNALS = [
    "reship", "refund", "credit", "replacement", "escalated", "sop fix",
    "process change", "will reship", "will refund", "will credit",
    "next steps are", "action is",
]

OPS_SLA_SIGNALS = [
    "new timeline", "revised eta", "by tomorrow", "by end of week",
    "within 24", "within 48", "within 72", "new commitment", "expect by",
]

OPS_DOCS_SIGNALS = [
    "shipment id", "tracking", "asin", "fc location", "order id",
    "reference number", "po number", "bol", "invoice", "label",
]

OPS_HANDOFF_SIGNALS = [
    "warehouse team", "finance team", "tech team", "operations will",
    "i'll loop in", "cc'ing", "assigned to", "handed off to", "owner is",
]

# Auto-flag churn keywords
CHURN_KEYWORDS = [
    "cancel", "canceling", "cancelling", "cancellation",
    "leaving", "switching", "switch to", "going with",
    "pause", "pausing", "downgrade", "downgrading",
    "terminate", "terminating", "termination",
    "not renewing", "ending contract",
]

COMPETITOR_KEYWORDS = [
    "shipbob", "deliverr", "stord", "ware2go", "saddle creek",
    "flexport", "whiplash", "shipmonk", "red stag", "3pl central",
    "efulfillment", "fulfillment by amazon", "amazon logistics",
]

PRICING_PUSHBACK_KEYWORDS = [
    "too expensive", "price is high", "cost is too", "cheaper option",
    "better rate", "negotiate", "discount", "reduce the price",
    "lower the cost", "price concern", "budget issue",
]

NEGATIVE_END_KEYWORDS = [
    "not satisfied", "still not resolved", "this is unacceptable",
    "very frustrated", "extremely disappointed", "will escalate",
    "speaking to management", "legal action", "end the contract",
]


# ─── Utility ──────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    return (text or "").lower()


def has_signal(text: str, signals: list[str]) -> bool:
    t = normalize(text)
    return any(s in t for s in signals)


def count_signals(text: str, signals: list[str]) -> int:
    t = normalize(text)
    return sum(1 for s in signals if s in t)


def combined_text(call: dict) -> str:
    """Combine all scoreable text fields into one string."""
    parts = [
        call.get("short_summary") or "",
        call.get("action_items") or "",
        " ".join(call.get("keywords") or []),
        call.get("title") or "",
    ]
    return " ".join(parts)


# ─── Hygiene Scoring ──────────────────────────────────────────────────────────

def score_hygiene(call: dict) -> dict:
    text = combined_text(call)
    duration = call.get("duration_minutes") or 0

    # fireflies_attached: binary — bot joined or not
    fireflies_attached = 5 if call.get("fireflies_joined") else 0

    # agenda_set: look for agenda signals in summary
    agenda_set = 4 if has_signal(text, AGENDA_SIGNALS) else 2

    # talk_listen_ratio: infer from duration + action items
    # Longer calls with many client action items suggest good balance
    action_items = call.get("action_items") or ""
    client_actions = len([l for l in action_items.split("\n") if l.strip() and
                          not any(e in l.lower() for e in ["roshni", "thomas", "lakshita",
                                  "trini", "deepakshi", "navesh", "jacob", "furqan",
                                  "omer", "prakash", "danny", "dan"])])
    if client_actions >= 3:
        talk_listen = 4
    elif client_actions >= 1:
        talk_listen = 3
    else:
        talk_listen = 2

    # clear_next_steps: look for next step signals
    if has_signal(text, NEXT_STEP_SIGNALS):
        next_step_count = count_signals(text, NEXT_STEP_SIGNALS)
        clear_next_steps = 5 if next_step_count >= 2 else 3
    else:
        clear_next_steps = 1

    return {
        "fireflies_attached": fireflies_attached,
        "agenda_set":         agenda_set,
        "talk_listen_ratio":  talk_listen,
        "clear_next_steps":   clear_next_steps,
    }


# ─── CS/AM Rubric Scoring ─────────────────────────────────────────────────────

def score_cs_am(call: dict) -> dict:
    text = combined_text(call)

    # account_health_check (0-10)
    health_hits = count_signals(text, HEALTH_CHECK_SIGNALS)
    account_health_check = min(10, health_hits * 2) if health_hits else 3

    # issue_resolution (0-15)
    if has_signal(text, RISK_SIGNALS):
        issue_resolution = 12 if has_signal(text, ISSUE_RESOLUTION_SIGNALS) else 5
    else:
        issue_resolution = 10   # no issues raised = partial credit

    # proactive_value (0-10)
    proactive_hits = count_signals(text, PROACTIVE_SIGNALS)
    proactive_value = min(10, proactive_hits * 3) if proactive_hits else 3

    # expansion_signals (0-10)
    expansion_hits = count_signals(text, EXPANSION_SIGNALS)
    expansion_signals = min(10, expansion_hits * 3) if expansion_hits else 3

    # risk_signals (0-15)
    risk_hits = count_signals(text, RISK_SIGNALS)
    if risk_hits >= 2:
        risk_signals = 12   # multiple risks flagged = good awareness
    elif risk_hits == 1:
        risk_signals = 8
    else:
        risk_signals = 10   # no risks = stable account, partial credit

    # hubspot_updated (0-10)
    hubspot_updated = 8 if has_signal(text, HUBSPOT_SIGNALS) else 3

    # followup_scheduled (0-10)
    followup_hits = count_signals(text, NEXT_STEP_SIGNALS)
    if followup_hits >= 2:
        followup_scheduled = 9
    elif followup_hits == 1:
        followup_scheduled = 6
    else:
        followup_scheduled = 2

    return {
        "account_health_check": account_health_check,
        "issue_resolution":     issue_resolution,
        "proactive_value":      proactive_value,
        "expansion_signals":    expansion_signals,
        "risk_signals":         risk_signals,
        "hubspot_updated":      hubspot_updated,
        "followup_scheduled":   followup_scheduled,
    }


# ─── OPS Rubric Scoring ───────────────────────────────────────────────────────

def score_ops(call: dict) -> dict:
    text = combined_text(call)

    # issue_defined (0-15)
    issue_defined = 12 if has_signal(text, OPS_ISSUE_DEFINED_SIGNALS) else 7

    # root_cause (0-15)
    root_cause = 12 if has_signal(text, OPS_ROOT_CAUSE_SIGNALS) else 6

    # resolution_path (0-15)
    resolution_path = 13 if has_signal(text, OPS_RESOLUTION_SIGNALS) else 5

    # sla_reset (0-10)
    sla_reset = 8 if has_signal(text, OPS_SLA_SIGNALS) else 4

    # documentation_referenced (0-10)
    documentation_referenced = 8 if has_signal(text, OPS_DOCS_SIGNALS) else 4

    # sentiment_recovery (0-10)
    # Positive sentiment at end inferred from resolution signals
    if has_signal(text, OPS_RESOLUTION_SIGNALS) and not has_signal(text, NEGATIVE_END_KEYWORDS):
        sentiment_recovery = 8
    elif has_signal(text, NEGATIVE_END_KEYWORDS):
        sentiment_recovery = 3
    else:
        sentiment_recovery = 6

    # internal_handoff (0-5)
    internal_handoff = 4 if has_signal(text, OPS_HANDOFF_SIGNALS) else 2

    return {
        "issue_defined":            issue_defined,
        "root_cause":               root_cause,
        "resolution_path":          resolution_path,
        "sla_reset":                sla_reset,
        "documentation_referenced": documentation_referenced,
        "sentiment_recovery":       sentiment_recovery,
        "internal_handoff":         internal_handoff,
    }


# ─── Auto-Flag Detection ──────────────────────────────────────────────────────

def detect_flags(call: dict) -> list[str]:
    text = combined_text(call)
    duration = call.get("duration_minutes") or 0
    flags = []

    if not call.get("fireflies_joined"):
        flags.append("fireflies_missing")

    if has_signal(text, CHURN_KEYWORDS):
        flags.append("churn_language")

    if has_signal(text, COMPETITOR_KEYWORDS):
        flags.append("competitor_mentioned")

    if has_signal(text, PRICING_PUSHBACK_KEYWORDS):
        flags.append("pricing_pushback")

    if has_signal(text, NEGATIVE_END_KEYWORDS):
        flags.append("negative_sentiment_end")

    # Short call flag — only for QBR/onboarding/escalation
    title_lower = (call.get("title") or "").lower()
    if duration < 10 and any(kw in title_lower for kw in ["qbr", "onboarding", "escalation"]):
        flags.append("short_call")

    # No next step
    if not has_signal(text, NEXT_STEP_SIGNALS):
        flags.append("no_next_step")

    # SLA miss with no resolution
    sla_miss_words = ["sla miss", "missed sla", "sla breach", "late delivery", "commitment missed"]
    if has_signal(text, sla_miss_words) and not has_signal(text, OPS_RESOLUTION_SIGNALS):
        flags.append("sla_miss_no_resolution")

    return flags


# ─── Coaching Note Generator ──────────────────────────────────────────────────

def generate_coaching_note(call: dict, breakdown: dict, flags: list[str], grade: str) -> str | None:
    if grade in ("A", "B"):
        return None

    notes = []

    if breakdown.get("clear_next_steps", 5) < 3:
        notes.append("No clear next step was committed before the call ended — rep should confirm who does what and by when.")

    if breakdown.get("followup_scheduled", 10) < 4:
        notes.append("Next touchpoint was not booked on the call — rep should lock in the next meeting before hanging up.")

    if breakdown.get("proactive_value", 10) < 4:
        notes.append("Rep did not bring new value to the client — share a data insight, benchmark, or AMZ Prep update next time.")

    if breakdown.get("hubspot_updated", 10) < 5:
        notes.append("No evidence of HubSpot update — notes, tasks, and deal records must be logged same day.")

    if "no_next_step" in flags:
        notes.append("Call ended without a committed next action.")

    if "churn_language" in flags:
        notes.append("Customer used churn language — this call needs manager review and a follow-up plan.")

    if not notes:
        notes.append("Review call recording for specific coaching opportunities across rubric sections.")

    # Return the most impactful note (first one), capped at 60 words
    note = notes[0]
    words = note.split()
    if len(words) > 60:
        note = " ".join(words[:60]) + "..."

    return f"[Rule-based] {note}"


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def score_call(call: dict) -> dict:
    """
    Score a single call using rule-based logic.
    Returns the same structure as Claude's grading response.
    """
    call_type = call["call_type"]

    # Score hygiene
    hygiene_scores = score_hygiene(call)
    score_hygiene_total = sum(hygiene_scores.values())

    # Score rubric section
    if call_type == "CS_AM":
        rubric_scores = score_cs_am(call)
    else:
        rubric_scores = score_ops(call)

    score_rubric_total = sum(rubric_scores.values())

    # Clamp
    score_hygiene_total = max(0, min(20, score_hygiene_total))
    score_rubric_total  = max(0, min(80, score_rubric_total))
    score_total         = score_hygiene_total + score_rubric_total

    grade = score_to_grade(score_total)

    # Full breakdown
    breakdown = {**hygiene_scores, **rubric_scores}

    # Detect flags
    flags = detect_flags(call)

    # Coaching note
    coaching_note = generate_coaching_note(call, breakdown, flags, grade)

    return {
        "score_hygiene":   score_hygiene_total,
        "score_rubric":    score_rubric_total,
        "score_total":     score_total,
        "grade":           grade,
        "score_breakdown": breakdown,
        "auto_flags":      flags,
        "coaching_note":   coaching_note,
        "graded_by":       "fallback_scorer",   # tag so we know source
    }
