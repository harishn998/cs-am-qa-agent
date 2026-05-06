"""
CS/AM Call QA Agent — Phase 2: Grade Calls
==========================================
Reads Phase 1 output JSON, sends each gradeable call to Claude API
for scoring against the rubric, and writes a graded JSON output.

GitHub Actions env vars required:
  ANTHROPIC_API_KEY  — Claude API key

Input:  output/calls_<YYYY-MM-DD>.json
Output: output/graded_<YYYY-MM-DD>.json
"""

import os
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone

from rubric import (
    build_grading_prompt,
    GRADING_SYSTEM_PROMPT,
    score_to_grade,
    AUTO_FLAGS,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = "claude-sonnet-4-5"
MAX_TOKENS        = 1000
RATE_LIMIT_DELAY  = 1.5   # seconds between API calls

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


# ─── Claude API call ──────────────────────────────────────────────────────────
def grade_call_with_claude(call: dict) -> dict | None:
    """
    Sends a single call to Claude for grading.
    Returns parsed JSON score dict, or None if grading failed.
    """
    prompt = build_grading_prompt(call)

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": MAX_TOKENS,
            "system": GRADING_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )

    if not resp.ok:
        log.error(f"Claude API error {resp.status_code}: {resp.text[:300]}")
        return None

    data = resp.json()
    raw_text = data["content"][0]["text"].strip()

    # Strip markdown fences if Claude wrapped in ```json
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse Claude response as JSON: {e}")
        log.error(f"Raw response: {raw_text[:300]}")
        return None

    return result


# ─── Validate and normalise the graded result ─────────────────────────────────
def validate_grade(result: dict, call: dict) -> dict:
    """
    Ensures all required fields are present and types are correct.
    Recalculates total from breakdown if needed.
    """
    score_hygiene = int(result.get("score_hygiene") or 0)
    score_rubric  = int(result.get("score_rubric")  or 0)
    score_total   = score_hygiene + score_rubric   # always recalculate

    # Clamp to valid ranges
    score_hygiene = max(0, min(20, score_hygiene))
    score_rubric  = max(0, min(80, score_rubric))
    score_total   = max(0, min(100, score_total))

    grade = score_to_grade(score_total)

    # Validate auto_flags — only allow known flag keys
    raw_flags = result.get("auto_flags") or []
    valid_flags = [f for f in raw_flags if f in AUTO_FLAGS]

    # Force fireflies_missing flag if bot didn't join
    if not call.get("fireflies_joined") and "fireflies_missing" not in valid_flags:
        valid_flags.append("fireflies_missing")

    coaching_note = result.get("coaching_note")
    if grade in ("A", "B") and coaching_note:
        coaching_note = None   # No coaching notes for good calls

    return {
        "score_hygiene":    score_hygiene,
        "score_rubric":     score_rubric,
        "score_total":      score_total,
        "grade":            grade,
        "score_breakdown":  result.get("score_breakdown") or {},
        "auto_flags":       valid_flags,
        "coaching_note":    coaching_note,
    }


# ─── Load most recent Phase 1 output ─────────────────────────────────────────
def load_latest_calls() -> tuple[dict, Path]:
    files = sorted(OUTPUT_DIR.glob("calls_*.json"))
    if not files:
        raise FileNotFoundError("No Phase 1 output found in output/. Run fetch_and_classify.py first.")
    path = files[-1]
    log.info(f"Loading Phase 1 data from {path}")
    return json.loads(path.read_text()), path


# ─── Load existing graded file for resume support ─────────────────────────────
def load_existing_graded(run_date: str) -> dict:
    path = OUTPUT_DIR / f"graded_{run_date}.json"
    if path.exists():
        log.info(f"Found existing graded file — resuming from {path}")
        return json.loads(path.read_text())
    return {}


# ─── Save progress incrementally ──────────────────────────────────────────────
def save_progress(graded_calls: list, phase1_data: dict, run_date: str):
    out = {
        "run_date":     run_date,
        "date_range":   phase1_data["date_range"],
        "team_emails":  phase1_data["team_emails"],
        "summary":      phase1_data["summary"],
        "rep_call_map": phase1_data["rep_call_map"],
        "calls":        graded_calls,
    }
    path = OUTPUT_DIR / f"graded_{run_date}.json"
    path.write_text(json.dumps(out, indent=2, default=str))


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=== CS/AM Call QA Agent — Phase 2: Grade Calls ===")

    phase1_data, _ = load_latest_calls()
    run_date = phase1_data["run_date"]

    # Load existing graded calls for resume support
    existing = load_existing_graded(run_date)
    already_graded = {c["id"]: c for c in existing.get("calls", [])} if existing else {}

    calls     = phase1_data["calls"]
    gradeable = [c for c in calls if c["call_type"] in ("CS_AM", "OPS")]
    log.info(f"Total calls: {len(calls)} | Gradeable: {len(gradeable)}")

    graded_calls  = []
    success = failed = skipped_count = resumed = 0

    for i, call in enumerate(calls):
        # Non-gradeable calls pass through unchanged
        if call["call_type"] not in ("CS_AM", "OPS"):
            graded_calls.append(call)
            skipped_count += 1
            continue

        # Resume: if already graded in a previous run, skip re-grading
        if call["id"] in already_graded:
            graded_calls.append(already_graded[call["id"]])
            resumed += 1
            log.info(f"  [{i+1}/{len(calls)}] RESUMED  [{call['call_type']}] {call['title'][:55]}")
            continue

        log.info(f"  [{i+1}/{len(calls)}] Grading  [{call['call_type']}] {call['title'][:55]}")

        result = grade_call_with_claude(call)

        if result:
            validated   = validate_grade(result, call)
            graded_call = {**call, **validated}
            graded_calls.append(graded_call)
            success += 1
            log.info(
                f"    → Score: {validated['score_total']}/100  "
                f"Grade: {validated['grade']}  "
                f"Flags: {validated['auto_flags'] or 'none'}"
            )
        else:
            graded_calls.append(call)
            failed += 1
            log.warning(f"    → Grading FAILED for call {call['id']}")

        # Save after every call so we can resume on interruption
        save_progress(graded_calls, phase1_data, run_date)
        time.sleep(RATE_LIMIT_DELAY)

    log.info(f"Grading complete — Graded: {success} | Resumed: {resumed} | Failed: {failed} | Skipped: {skipped_count}")
    save_progress(graded_calls, phase1_data, run_date)
    log.info(f"Output written → output/graded_{run_date}.json")
    log.info("=== Phase 2 complete ===")

    if failed > 0:
        log.warning(f"{failed} calls failed grading — check logs above")


if __name__ == "__main__":
    main()
