# CS/AM Call QA Agent

Automated weekly call quality grading system for Lakshita's CS team and Thomas's AM/Ops team.

## Architecture

```
Phase 1 — Fetch & Classify   (Monday 8 AM ET)
  ↓  output/calls_YYYY-MM-DD.json
Phase 2 — Claude Grader      (Monday 8:30 AM ET)  ← coming next
  ↓  output/graded_YYYY-MM-DD.json
Phase 3 — Aggregator         (Monday 8:45 AM ET)  ← coming next
  ↓  output/digest_YYYY-MM-DD.json
Phase 4 — Delivery           (Monday 9 AM ET)     ← coming next
  → SendGrid email to Lakshita + Thomas
  → Slack message to Lakshita + Thomas
Phase 5 — GitHub Actions     cron orchestration   ← coming next
```

## Team Members Monitored

| Name | Email |
|---|---|
| Navesh Khedu | navesh@amzprep.com |
| Jacob Penney | jacob@amzprep.com |
| Furqan Ali | furqan@amzprep.com |
| Omer Muhammad | omer@amzprep.com |
| Deepakshi Sharma | deepakshi@amzprep.com |
| Prakash Thakkar | prakash@amzprep.com |
| Danny Prabudial | dan@amzprep.com |
| Lakshita Dang | lakshita@amzprep.com |
| Thomas Gewarges | thomas@amzprep.com |
| Trini Baldon | trini@amzprep.com |
| Roshni Nair | roshni@amzprep.com |

## Call Classification

| Type | Rubric Applied | Examples |
|---|---|---|
| `CS_AM` | CS/AM 80pt rubric | MBRs, Weekly Check-ins, Account Syncs |
| `OPS` | Ops/Fulfillment 80pt rubric | Onboarding calls, Inbound/Shipment syncs, SOP reviews |
| `INTERNAL` | Skipped | EOS L10, Team huddles, Deal recaps (no external participants) |
| `SKIP` | Skipped | Calls < 5 min, unprocessed transcripts |

## Scoring

- **Shared Call Hygiene**: 20 pts (all calls)
- **CS/AM Rubric**: 80 pts
- **Ops/Fulfillment Rubric**: 80 pts
- **Total**: 100 pts per call

| Grade | Score | Action |
|---|---|---|
| A | 90–100 | Share as best practice |
| B | 75–89 | No action |
| C | 60–74 | Coaching note |
| D | < 60 | Manager 1:1 required |

## GitHub Secrets Required

| Secret | Value |
|---|---|
| `FIREFLIES_API_KEY` | Fireflies API key (admin access) |
| `ANTHROPIC_API_KEY` | For Phase 2 Claude grading |
| `SENDGRID_API_KEY` | For Phase 4 email delivery |
| `SLACK_BOT_TOKEN` | For Phase 4 Slack delivery |

## Setup

1. Add all secrets to GitHub repo → Settings → Secrets → Actions
2. Push this repo to GitHub
3. Phase 1 runs automatically every Monday at 8 AM ET
4. Trigger manually: Actions → Phase 1 → Run workflow

## Output Format (Phase 1)

```json
{
  "run_date": "2026-05-05",
  "date_range": { "from": "2026-04-28", "to": "2026-05-05" },
  "summary": {
    "total_fetched": 45,
    "team_involved": 32,
    "gradeable": 18,
    "classification_counts": { "CS_AM": 10, "OPS": 8, "INTERNAL": 9, "SKIP": 5 }
  },
  "rep_call_map": {
    "roshni@amzprep.com": ["call_id_1", "call_id_2"],
    ...
  },
  "calls": [
    {
      "id": "01KQTB6D...",
      "title": "Mid Day Squares: MBR",
      "date": "2026-05-04T18:00:00Z",
      "duration_minutes": 32.0,
      "organizer_email": "lakshita@amzprep.com",
      "call_type": "CS_AM",
      "team_members_on_call": ["lakshita@amzprep.com", "trini@amzprep.com"],
      "external_participants": ["antoine@middaysquares.com"],
      "fireflies_joined": true,
      "short_summary": "...",
      "action_items": "...",
      "keywords": ["SLA", "inventory"],
      "grade": null,          // filled by Phase 2
      "score_total": null,    // filled by Phase 2
      "auto_flags": [],       // filled by Phase 2
      "coaching_note": null   // filled by Phase 2
    }
  ]
}
```
