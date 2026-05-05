"""
CS/AM Call QA Agent — Fetch & Classify
=======================================
Pulls all Fireflies calls from the past 7 days for the CS/AM/Ops team,
classifies each call as CS_AM, OPS, INTERNAL, or SKIP, and outputs a
structured JSON file ready for Claude grading.

GitHub Actions env vars required:
  FIREFLIES_API_KEY   — Neha's Fireflies API key (admin access)

Output:
  output/calls_<YYYY-MM-DD>.json
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
FIREFLIES_API_URL = "https://api.fireflies.ai/graphql"
FIREFLIES_API_KEY = os.environ["FIREFLIES_API_KEY"]

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

# All 11 monitored team members
TEAM_EMAILS = {
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

# All known internal @amzprep.com domains — used to detect external participants
INTERNAL_DOMAINS = {"amzprep.com", "amzprepcanada.ca", "eshipper.com"}

# Keywords that strongly indicate an ops/fulfillment call vs CS/AM call
OPS_TITLE_KEYWORDS = [
    "onboarding", "inbound", "shipment", "sop", "warehouse", "freight",
    "logistics", "fulfillment", "tracking", "inventory", "receiving",
    "go-live", "golive", "escalation", "ops", "operations", "wms",
    "implementation", "integration setup", "prep huddle",
    "sync",          # "Rough Country Fitness: Sync" → ops coordination
    "h&s",           # health & safety / ops reviews
]

CS_AM_TITLE_KEYWORDS = [
    "mbr", "monthly business review", "qbr", "quarterly business review",
    "weekly check-in", "weekly touch", "account review",
    "check-in", "checkin", "client success", "csm", "account manager",
    "bi-weekly", "biweekly",
    # "weekly check" and "weekly meeting" are intentionally removed as they
    # appear in both ops (Spreetail Weekly Check in) and CS contexts.
    # Title ambiguity is resolved by OPS keywords taking priority first.
]

# Titles that are purely internal — skip grading
INTERNAL_TITLE_KEYWORDS = [
    "eos", "l10", "leadership", "all hands", "team huddle", "prep huddle",
    "deal recap", "internal", "1:1", "one on one", "scorecard", "rocks",
    "h&s with thomas", "check and balance",
]


# ─── Fireflies GraphQL Query ──────────────────────────────────────────────────
TRANSCRIPTS_QUERY = """
query GetTranscripts($fromDate: String!, $toDate: String!, $limit: Int!, $skip: Int!) {
  transcripts(
    fromDate: $fromDate
    toDate: $toDate
    limit: $limit
    skip: $skip
  ) {
    id
    title
    date
    duration
    organizer_email
    meeting_link
    participants
    summary {
      short_summary
      action_items
      keywords
    }
    meeting_attendees {
      displayName
      email
    }
    meeting_info {
      fred_joined
      silent_meeting
      summary_status
    }
  }
}
"""


def gql(query: str, variables: dict) -> dict:
    """Execute a Fireflies GraphQL query."""
    resp = requests.post(
        FIREFLIES_API_URL,
        json={"query": query, "variables": variables},
        headers={
            "Authorization": f"Bearer {FIREFLIES_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


# ─── Fetch all calls from the past 7 days ─────────────────────────────────────
def fetch_all_calls(from_date: str, to_date: str) -> list[dict]:
    """
    Pages through Fireflies transcripts for the date range.
    Returns raw list of all transcript objects.
    """
    all_calls = []
    skip = 0
    limit = 50

    while True:
        log.info(f"Fetching transcripts — skip={skip}, limit={limit}")
        data = gql(TRANSCRIPTS_QUERY, {
            "fromDate": from_date,
            "toDate": to_date,
            "limit": limit,
            "skip": skip,
        })
        batch = data.get("transcripts") or []
        log.info(f"  → Got {len(batch)} transcripts")
        all_calls.extend(batch)

        if len(batch) < limit:
            break   # last page
        skip += limit

    log.info(f"Total raw transcripts fetched: {len(all_calls)}")
    return all_calls


# ─── Filter to calls involving our team ───────────────────────────────────────
def involves_team_member(call: dict) -> tuple[bool, list[str]]:
    """
    Returns (True, [matched_emails]) if any team member is organizer or participant.
    """
    matched = set()

    organizer = (call.get("organizer_email") or "").lower()
    if organizer in TEAM_EMAILS:
        matched.add(organizer)

    for p in call.get("participants") or []:
        if isinstance(p, str) and p.lower() in TEAM_EMAILS:
            matched.add(p.lower())

    for att in call.get("meeting_attendees") or []:
        email = (att.get("email") or "").lower()
        if email in TEAM_EMAILS:
            matched.add(email)

    return bool(matched), list(matched)


def get_external_participants(call: dict) -> list[str]:
    """Return list of external (non-internal-domain) participant emails."""
    external = []
    all_emails = set()

    organizer = (call.get("organizer_email") or "").lower()
    if organizer:
        all_emails.add(organizer)

    for p in call.get("participants") or []:
        if isinstance(p, str):
            all_emails.add(p.lower())

    for att in call.get("meeting_attendees") or []:
        email = (att.get("email") or "").lower()
        if email:
            all_emails.add(email)

    for email in all_emails:
        domain = email.split("@")[-1] if "@" in email else ""
        if domain and domain not in INTERNAL_DOMAINS:
            external.append(email)

    return external


# ─── Call type classifier ─────────────────────────────────────────────────────
def classify_call(call: dict, external_participants: list[str]) -> str:
    """
    Returns one of: CS_AM | OPS | INTERNAL | SKIP

    Logic:
    1. No external participants → INTERNAL (skip grading)
    2. Duration < 5 min → SKIP (too short to grade meaningfully)
    3. summary_status != processed → SKIP (no transcript to grade)
    4. Title/keyword match → OPS or CS_AM
    5. Fallback → CS_AM (assume CS call if can't determine)
    """
    title = (call.get("title") or "").lower()
    duration = call.get("duration") or 0
    summary_status = (call.get("meeting_info") or {}).get("summary_status") or ""

    # No external participants = internal only meeting
    if not external_participants:
        log.debug(f"  INTERNAL (no external participants): {call['title']}")
        return "INTERNAL"

    # Too short or not processed
    if duration < 5:
        log.debug(f"  SKIP (duration {duration:.1f} min < 5): {call['title']}")
        return "SKIP"

    if summary_status not in ("processed", ""):
        log.debug(f"  SKIP (summary_status={summary_status}): {call['title']}")
        return "SKIP"

    # Internal title keywords override (e.g. L10 with a guest)
    for kw in INTERNAL_TITLE_KEYWORDS:
        if kw in title:
            log.debug(f"  INTERNAL (title keyword '{kw}'): {call['title']}")
            return "INTERNAL"

    # OPS keywords
    for kw in OPS_TITLE_KEYWORDS:
        if kw in title:
            log.debug(f"  OPS (title keyword '{kw}'): {call['title']}")
            return "OPS"

    # CS/AM keywords
    for kw in CS_AM_TITLE_KEYWORDS:
        if kw in title:
            log.debug(f"  CS_AM (title keyword '{kw}'): {call['title']}")
            return "CS_AM"

    # Fallback — has external participant, can't classify from title → CS_AM
    log.debug(f"  CS_AM (fallback, has external): {call['title']}")
    return "CS_AM"


# ─── Build rep → calls map ────────────────────────────────────────────────────
def build_rep_call_map(classified_calls: list[dict]) -> dict:
    """
    Groups graded calls by rep email for easy per-rep reporting in Phase 3.
    A call is attributed to every team member who appeared in it.
    """
    rep_map = {email: [] for email in TEAM_EMAILS}

    for call in classified_calls:
        for email in call["team_members_on_call"]:
            if email in rep_map:
                rep_map[email].append(call["id"])

    return rep_map


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    today = datetime.now(timezone.utc)
    from_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    log.info(f"=== CS/AM Call QA Agent — Phase 1 ===")
    log.info(f"Date range: {from_date} → {to_date}")
    log.info(f"Monitoring {len(TEAM_EMAILS)} team members")

    # 1. Fetch all calls
    raw_calls = fetch_all_calls(from_date, to_date)

    # 2. Filter to team-involved calls only
    team_calls = []
    skipped_no_team = 0

    for call in raw_calls:
        has_team, matched_emails = involves_team_member(call)
        if not has_team:
            skipped_no_team += 1
            continue
        call["_team_emails"] = matched_emails
        team_calls.append(call)

    log.info(f"Calls involving team: {len(team_calls)} (skipped {skipped_no_team} with no team member)")

    # 3. Classify each call
    classified = []
    counts = {"CS_AM": 0, "OPS": 0, "INTERNAL": 0, "SKIP": 0}

    for call in team_calls:
        external = get_external_participants(call)
        call_type = classify_call(call, external)
        counts[call_type] += 1

        # Build the structured object for Phase 2
        structured = {
            "id": call["id"],
            "title": call.get("title") or "Untitled",
            "date": call.get("date") or "",
            "duration_minutes": round(call.get("duration") or 0, 1),
            "organizer_email": (call.get("organizer_email") or "").lower(),
            "call_type": call_type,       # CS_AM | OPS | INTERNAL | SKIP
            "team_members_on_call": call["_team_emails"],
            "external_participants": external,
            "fireflies_joined": (call.get("meeting_info") or {}).get("fred_joined") or False,
            "summary_status": (call.get("meeting_info") or {}).get("summary_status") or "",
            "silent_meeting": (call.get("meeting_info") or {}).get("silent_meeting") or False,
            "short_summary": (call.get("summary") or {}).get("short_summary") or "",
            "action_items": (call.get("summary") or {}).get("action_items") or "",
            "keywords": (call.get("summary") or {}).get("keywords") or [],
            # Phase 2 will fill these in:
            "grade": None,
            "score_total": None,
            "score_hygiene": None,
            "score_rubric": None,
            "score_breakdown": None,
            "auto_flags": [],
            "coaching_note": None,
        }
        classified.append(structured)

    log.info(f"Classification results: {counts}")

    # 4. Build rep map
    gradeable = [c for c in classified if c["call_type"] in ("CS_AM", "OPS")]
    rep_map = build_rep_call_map(gradeable)

    log.info(f"Gradeable calls: {len(gradeable)} (CS_AM={counts['CS_AM']}, OPS={counts['OPS']})")
    log.info("Rep call counts:")
    for email, call_ids in rep_map.items():
        if call_ids:
            log.info(f"  {TEAM_EMAILS[email]:25s}  {len(call_ids)} calls")

    # 5. Write output JSON
    run_date = today.strftime("%Y-%m-%d")
    output = {
        "run_date": run_date,
        "date_range": {"from": from_date, "to": to_date},
        "team_emails": TEAM_EMAILS,
        "summary": {
            "total_fetched": len(raw_calls),
            "team_involved": len(team_calls),
            "gradeable": len(gradeable),
            "classification_counts": counts,
        },
        "rep_call_map": rep_map,
        "calls": classified,
    }

    out_path = OUTPUT_DIR / f"calls_{run_date}.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    log.info(f"Output written → {out_path}")
    log.info("=== Phase 1 complete ===")


if __name__ == "__main__":
    main()
