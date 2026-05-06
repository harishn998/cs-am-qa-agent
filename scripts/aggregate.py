"""
CS/AM Call QA Agent — Phase 3: Aggregate
=========================================
Reads graded JSON, builds the full weekly digest data structure:
- Team scorecard per rep (call count, avg score, grade, trend)
- Top 3 calls (best examples)
- Bottom 3 calls (with coaching notes)
- All auto-flagged calls
- Missing Fireflies list
- Repeat issue accounts (same client + rep within 30 days)

Input:  output/graded_<YYYY-MM-DD>.json
Output: output/digest_<YYYY-MM-DD>.json
"""

import json
import logging
from pathlib import Path
from collections import defaultdict

from rubric import score_to_grade, grade_action, AUTO_FLAGS

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


# ─── Load graded file ─────────────────────────────────────────────────────────
def load_latest_graded() -> tuple[dict, Path]:
    files = sorted(OUTPUT_DIR.glob("graded_*.json"))
    if not files:
        raise FileNotFoundError("No graded output found. Run grade_calls.py first.")
    path = files[-1]
    log.info(f"Loading graded data from {path}")
    return json.loads(path.read_text()), path


# ─── Load previous week's graded file for trend comparison ────────────────────
def load_previous_graded(current_run_date: str) -> dict | None:
    files = sorted(OUTPUT_DIR.glob("graded_*.json"))
    previous = [f for f in files if f.stem.replace("graded_", "") < current_run_date]
    if not previous:
        return None
    path = previous[-1]
    log.info(f"Loading previous week data from {path} for trend comparison")
    return json.loads(path.read_text())


# ─── Build per-rep scorecard ──────────────────────────────────────────────────
def build_rep_scorecard(graded_data: dict, prev_data: dict | None) -> list[dict]:
    """
    For each rep: call count, avg score, grade distribution, trend vs last week.
    """
    team_emails = graded_data["team_emails"]
    calls = [c for c in graded_data["calls"] if c["call_type"] in ("CS_AM", "OPS") and c.get("score_total") is not None]

    # Map rep email → their calls
    rep_calls = defaultdict(list)
    for call in calls:
        for email in call.get("team_members_on_call", []):
            if email in team_emails:
                rep_calls[email].append(call)

    # Previous week avg scores per rep
    prev_avgs = {}
    if prev_data:
        prev_calls = [c for c in prev_data["calls"] if c["call_type"] in ("CS_AM", "OPS") and c.get("score_total") is not None]
        prev_rep_calls = defaultdict(list)
        for call in prev_calls:
            for email in call.get("team_members_on_call", []):
                if email in team_emails:
                    prev_rep_calls[email].append(call)
        for email, pcalls in prev_rep_calls.items():
            if pcalls:
                prev_avgs[email] = round(sum(c["score_total"] for c in pcalls) / len(pcalls), 1)

    scorecard = []
    for email, name in team_emails.items():
        rcalls = rep_calls.get(email, [])
        if not rcalls:
            scorecard.append({
                "email": email,
                "name": name,
                "call_count": 0,
                "avg_score": None,
                "grade": None,
                "grade_distribution": {"A": 0, "B": 0, "C": 0, "D": 0},
                "prev_avg_score": prev_avgs.get(email),
                "trend": "no_calls",
                "flag_count": 0,
            })
            continue

        scores   = [c["score_total"] for c in rcalls]
        avg      = round(sum(scores) / len(scores), 1)
        grade    = score_to_grade(int(avg))
        prev_avg = prev_avgs.get(email)

        if prev_avg is None:
            trend = "new"
        elif avg > prev_avg + 2:
            trend = "up"
        elif avg < prev_avg - 2:
            trend = "down"
        else:
            trend = "stable"

        grade_dist = {"A": 0, "B": 0, "C": 0, "D": 0}
        for c in rcalls:
            g = c.get("grade") or score_to_grade(c["score_total"])
            if g in grade_dist:
                grade_dist[g] += 1

        flag_count = sum(len(c.get("auto_flags") or []) for c in rcalls)

        scorecard.append({
            "email":              email,
            "name":               name,
            "call_count":         len(rcalls),
            "avg_score":          avg,
            "grade":              grade,
            "grade_distribution": grade_dist,
            "prev_avg_score":     prev_avg,
            "trend":              trend,
            "flag_count":         flag_count,
        })

    # Sort by avg_score descending (reps with no calls go to bottom)
    scorecard.sort(key=lambda r: r["avg_score"] or -1, reverse=True)
    return scorecard


# ─── Top / Bottom calls ───────────────────────────────────────────────────────
def get_top_bottom_calls(graded_data: dict, n: int = 3) -> tuple[list, list]:
    calls = [
        c for c in graded_data["calls"]
        if c["call_type"] in ("CS_AM", "OPS") and c.get("score_total") is not None
    ]
    sorted_calls = sorted(calls, key=lambda c: c["score_total"], reverse=True)

    def summarise(call):
        return {
            "id":                  call["id"],
            "title":               call["title"],
            "date":                call.get("date", ""),
            "duration_minutes":    call.get("duration_minutes"),
            "call_type":           call["call_type"],
            "organizer_email":     call.get("organizer_email", ""),
            "team_members":        call.get("team_members_on_call", []),
            "score_total":         call["score_total"],
            "grade":               call.get("grade"),
            "auto_flags":          call.get("auto_flags") or [],
            "coaching_note":       call.get("coaching_note"),
            "short_summary":       (call.get("short_summary") or "")[:300],
            "fireflies_url":       f"https://app.fireflies.ai/view/{call['id']}",
        }

    top    = [summarise(c) for c in sorted_calls[:n]]
    bottom = [summarise(c) for c in sorted_calls[-n:] if c["score_total"] < 80]
    bottom.reverse()   # worst first
    return top, bottom


# ─── Flagged calls ────────────────────────────────────────────────────────────
def get_flagged_calls(graded_data: dict) -> dict:
    """
    Returns dict of flag_key → list of call summaries that triggered that flag.
    """
    flagged = defaultdict(list)
    for call in graded_data["calls"]:
        for flag in call.get("auto_flags") or []:
            flagged[flag].append({
                "id":            call["id"],
                "title":         call["title"],
                "date":          call.get("date", ""),
                "organizer":     call.get("organizer_email", ""),
                "score_total":   call.get("score_total"),
                "grade":         call.get("grade"),
                "fireflies_url": f"https://app.fireflies.ai/view/{call['id']}",
            })
    return dict(flagged)


# ─── Missing Fireflies list ───────────────────────────────────────────────────
def get_missing_fireflies(graded_data: dict) -> list:
    return [
        {
            "id":          c["id"],
            "title":       c["title"],
            "date":        c.get("date", ""),
            "organizer":   c.get("organizer_email", ""),
            "call_type":   c["call_type"],
            "fireflies_url": f"https://app.fireflies.ai/view/{c['id']}",
        }
        for c in graded_data["calls"]
        if not c.get("fireflies_joined") and c["call_type"] in ("CS_AM", "OPS")
    ]


# ─── Repeat issue detection ───────────────────────────────────────────────────
def get_repeat_issues(graded_data: dict, prev_data: dict | None) -> list:
    """
    Finds same external client appearing in flagged calls in both this week and last week.
    Signals same problem not resolved.
    """
    if not prev_data:
        return []

    def flagged_client_pairs(data):
        pairs = set()
        for call in data["calls"]:
            flags = call.get("auto_flags") or []
            if not flags:
                continue
            for ext in call.get("external_participants") or []:
                domain = ext.split("@")[-1] if "@" in ext else ext
                for member in call.get("team_members_on_call") or []:
                    pairs.add((domain, member))
        return pairs

    this_week = flagged_client_pairs(graded_data)
    last_week = flagged_client_pairs(prev_data)
    repeat_pairs = this_week & last_week

    if not repeat_pairs:
        return []

    repeats = []
    for (domain, rep_email) in repeat_pairs:
        repeats.append({
            "client_domain": domain,
            "rep_email":     rep_email,
            "rep_name":      graded_data["team_emails"].get(rep_email, rep_email),
            "note":          f"Same client ({domain}) had flagged issues with {rep_email} in both this week and last week",
        })
    return repeats


# ─── Overall summary stats ────────────────────────────────────────────────────
def build_overall_stats(graded_data: dict) -> dict:
    calls = [c for c in graded_data["calls"] if c["call_type"] in ("CS_AM", "OPS") and c.get("score_total") is not None]
    if not calls:
        return {"total_graded": 0}

    scores = [c["score_total"] for c in calls]
    grade_dist = {"A": 0, "B": 0, "C": 0, "D": 0}
    for c in calls:
        g = c.get("grade") or score_to_grade(c["score_total"])
        if g in grade_dist:
            grade_dist[g] += 1

    return {
        "total_graded":       len(calls),
        "avg_score":          round(sum(scores) / len(scores), 1),
        "highest_score":      max(scores),
        "lowest_score":       min(scores),
        "grade_distribution": grade_dist,
        "total_flags":        sum(len(c.get("auto_flags") or []) for c in calls),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=== CS/AM Call QA Agent — Phase 3: Aggregate ===")

    graded_data, graded_path = load_latest_graded()
    run_date = graded_data["run_date"]
    prev_data = load_previous_graded(run_date)

    log.info("Building rep scorecard...")
    rep_scorecard = build_rep_scorecard(graded_data, prev_data)

    log.info("Finding top/bottom calls...")
    top_calls, bottom_calls = get_top_bottom_calls(graded_data, n=3)

    log.info("Collecting flagged calls...")
    flagged_calls = get_flagged_calls(graded_data)

    log.info("Checking Fireflies coverage...")
    missing_fireflies = get_missing_fireflies(graded_data)

    log.info("Detecting repeat issues...")
    repeat_issues = get_repeat_issues(graded_data, prev_data)

    log.info("Building overall stats...")
    overall = build_overall_stats(graded_data)

    digest = {
        "run_date":         run_date,
        "date_range":       graded_data["date_range"],
        "overall":          overall,
        "rep_scorecard":    rep_scorecard,
        "top_calls":        top_calls,
        "bottom_calls":     bottom_calls,
        "flagged_calls":    flagged_calls,
        "missing_fireflies": missing_fireflies,
        "repeat_issues":    repeat_issues,
        "flag_definitions": AUTO_FLAGS,
    }

    out_path = OUTPUT_DIR / f"digest_{run_date}.json"
    out_path.write_text(json.dumps(digest, indent=2, default=str))
    log.info(f"Output written → {out_path}")

    # Log summary to Actions
    log.info(f"Overall avg score:    {overall.get('avg_score')}")
    log.info(f"Total flags raised:   {overall.get('total_flags')}")
    log.info(f"Missing Fireflies:    {len(missing_fireflies)}")
    log.info(f"Repeat issues:        {len(repeat_issues)}")
    log.info(f"Top call score:       {top_calls[0]['score_total'] if top_calls else 'N/A'}")
    log.info(f"Bottom call score:    {bottom_calls[0]['score_total'] if bottom_calls else 'N/A'}")
    log.info("=== Phase 3 complete ===")


if __name__ == "__main__":
    main()
