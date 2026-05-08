"""
CS/AM Call QA Agent — Phase 2: Grade Calls
==========================================
Reads Phase 1 output JSON, sends each gradeable call to Claude API
for scoring. If Claude is unavailable (low credits, 400/529 errors),
automatically falls back to the rule-based fallback_scorer.py.

Grading priority:
  1. Claude API (claude-sonnet-4-5)  — preferred, richer coaching notes
  2. Fallback scorer (fallback_scorer.py) — keyword-based, no API needed

GitHub Actions env vars required:
  ANTHROPIC_API_KEY  — Claude API key (optional if fallback is acceptable)

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
from fallback_scorer import score_call as fallback_score_call

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
# API key is optional — if missing, all calls go straight to fallback scorer
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = "claude-sonnet-4-5"
MAX_TOKENS        = 1000
RATE_LIMIT_DELAY  = 1.5   # seconds between Claude API calls

# Errors that trigger fallback (billing/credit issues — no point retrying)
BILLING_ERROR_CODES = {"credit_balance_too_low", "insufficient_quota"}
BILLING_HTTP_CODES  = {400, 402, 429}

# Track if Claude billing failed this run — once confirmed down, skip all remaining Claude calls
_claude_billing_failed = False

# Always resolve output/ relative to repo root (one level up from scripts/)
OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


# ─── Claude API call ──────────────────────────────────────────────────────────
def grade_call_with_claude(call: dict) -> tuple[dict | None, bool]:
    """
    Sends a single call to Claude for grading.
    Returns: (result_dict | None, billing_failed: bool)
      - result_dict: parsed JSON on success
      - None: on any failure
      - billing_failed=True: signals caller to switch to fallback for all remaining calls
    """
    global _claude_billing_failed

    if not ANTHROPIC_API_KEY:
        return None, False   # no key configured — let caller use fallback

    prompt = build_grading_prompt(call)

    try:
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
    except requests.RequestException as e:
        log.error(f"Claude API request failed: {e}")
        return None, False

    if not resp.ok:
        body = resp.text[:400]
        log.error(f"Claude API error {resp.status_code}: {body}")

        # Detect billing/credit errors — flag so we stop calling Claude
        is_billing_error = (
            resp.status_code in BILLING_HTTP_CODES and
            any(code in body for code in BILLING_ERROR_CODES)
        ) or "credit balance is too low" in body or "insufficient" in body.lower()

        if is_billing_error:
            log.warning("Claude billing/credit error detected — switching ALL remaining calls to fallback scorer")
            _claude_billing_failed = True
            return None, True   # signal billing failure

        return None, False

    data = resp.json()
    raw_text = data["content"][0]["text"].strip()

    # Strip markdown fences if Claude wrapped response in ```json
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        result = json.loads(raw_text)
        result["graded_by"] = "claude"
        return result, False
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse Claude response as JSON: {e}")
        log.error(f"Raw response: {raw_text[:300]}")
        return None, False


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
    global _claude_billing_failed

    log.info("=== CS/AM Call QA Agent — Phase 2: Grade Calls ===")

    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY not set — all calls will use fallback scorer")
        _claude_billing_failed = True
    else:
        log.info("Claude API configured — will fall back to rule-based scorer if billing fails")

    phase1_data, _ = load_latest_calls()
    run_date = phase1_data["run_date"]

    existing = load_existing_graded(run_date)
    already_graded = {c["id"]: c for c in existing.get("calls", [])} if existing else {}

    calls     = phase1_data["calls"]
    gradeable = [c for c in calls if c["call_type"] in ("CS_AM", "OPS")]
    log.info(f"Total calls: {len(calls)} | Gradeable: {len(gradeable)}")

    graded_calls = []
    claude_success = claude_failed = fallback_used = skipped_count = resumed = 0

    for i, call in enumerate(calls):
        # Non-gradeable calls pass through unchanged
        if call["call_type"] not in ("CS_AM", "OPS"):
            graded_calls.append(call)
            skipped_count += 1
            continue

        # Resume: already graded in a previous run
        if call["id"] in already_graded:
            graded_calls.append(already_graded[call["id"]])
            resumed += 1
            log.info(f"  [{i+1}/{len(calls)}] RESUMED   [{call['call_type']}] {call['title'][:50]}")
            continue

        label = call['call_type']
        title = call['title'][:50]

        # ── Try Claude first (unless billing already failed this run) ──────────
        result = None
        used_fallback = False

        if not _claude_billing_failed:
            log.info(f"  [{i+1}/{len(calls)}] Claude    [{label}] {title}")
            result, billing_failed = grade_call_with_claude(call)

            if billing_failed:
                # Billing confirmed down — immediately fall back for this call too
                log.warning(f"  [{i+1}/{len(calls)}] Fallback  [{label}] {title} (billing error)")
                result = fallback_score_call(call)
                used_fallback = True
                fallback_used += 1
            elif result is None:
                # Non-billing failure (timeout, parse error) — use fallback for this call only
                log.warning(f"  [{i+1}/{len(calls)}] Fallback  [{label}] {title} (Claude failed, non-billing)")
                result = fallback_score_call(call)
                used_fallback = True
                fallback_used += 1
            else:
                claude_success += 1

        else:
            # Billing already known to be down — go straight to fallback
            log.info(f"  [{i+1}/{len(calls)}] Fallback  [{label}] {title}")
            result = fallback_score_call(call)
            used_fallback = True
            fallback_used += 1

        # ── Validate and merge result ──────────────────────────────────────────
        if result:
            validated   = validate_grade(result, call)
            graded_call = {**call, **validated, "graded_by": result.get("graded_by", "unknown")}
            graded_calls.append(graded_call)
            source = "fallback" if used_fallback else "claude"
            log.info(
                f"    [{source}] Score: {validated['score_total']}/100  "
                f"Grade: {validated['grade']}  "
                f"Flags: {validated['auto_flags'] or 'none'}"
            )
        else:
            # Should never reach here — fallback always returns a result
            graded_calls.append(call)
            claude_failed += 1
            log.error(f"    Both Claude and fallback failed for call {call['id']}")

        save_progress(graded_calls, phase1_data, run_date)

        # Only delay if we actually called Claude
        if not used_fallback:
            time.sleep(RATE_LIMIT_DELAY)

    log.info(
        f"Grading complete — "
        f"Claude: {claude_success} | Fallback: {fallback_used} | "
        f"Resumed: {resumed} | Failed: {claude_failed} | Skipped: {skipped_count}"
    )
    save_progress(graded_calls, phase1_data, run_date)
    log.info(f"Output written → output/graded_{run_date}.json")
    log.info("=== Phase 2 complete ===")

    if claude_failed > 0:
        log.error(f"{claude_failed} calls could not be graded by either method")
    if fallback_used > 0 and not _claude_billing_failed:
        log.warning(f"{fallback_used} calls used fallback scorer — check Claude API health")


if __name__ == "__main__":
    main()
