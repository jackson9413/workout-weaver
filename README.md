# workout-weaver

> Local-first weekly workout generator. Paste a brief, get a 7-day plan with progressive overload, exercise swaps, recovery blocks, and a 0–100 readiness score.

![split](https://img.shields.io/badge/split-full_body-+upper_lower-+ppl-blue)
![stack](https://img.shields.io/badge/stack-Flask+SQLite+vanilla_JS-lightgrey)
![deps](https://img.shields.io/badge/deps-stdlib_only-brightgreen)
![llm](https://img.shields.io/badge/llm-none-darkgreen)

## What it does

workout-weaver turns a one-paragraph brief — "I'm 63, intermediate, 3 days/week, 60 min at home with dumbbells. Goal is general health. Lower back pain and shoulder impingement" — into a structured **weekly training program** with:

- detected **profile** (experience, goal, days/week, session length, equipment, injuries, age, sex)
- a chosen **split** (full-body 3×, upper/lower 4×, PPL 5×, cardio 3-5×, mobility 5×) auto-bumped if you ask for more days than the template supports
- per-day **exercise blocks** — warm-up → main lifts → accessories → core → cooldown → optional cardio finisher
- a progressive-overload guide tuned to your experience (linear / double progression / block periodization)
- **risk-flag engine** with severity + ready-to-act suggestions (over-training, undertraining, injury substitutions, age-aware volume, equipment reality-check)
- a 0–100 **readiness score** across 6 weighted axes (profile completeness, plan density, experience fit, goal alignment, recovery balance, risk hygiene)

## Why it matters

Most "AI workout planners" are either (a) generic template fill-ins or (b) vague LLM chat with no consistency. workout-weaver is deterministic, citation-backed, and **knows the constraints that usually get missed**:

- it won't put a back squat on your calendar if you said "lower back pain"
- it caps cardio finishers across the week instead of stacking them on every day
- it downgrades volume for 60+ lifters and flags unsupervised max attempts for under-18s
- it picks a denser split automatically when you bump days-per-week above the template's capacity

It runs **locally with zero external dependencies** — no API keys, no LLM, no surprise data egress.

## Tech stack

- **Python 3** stdlib only (sqlite3, dataclasses, re, json)
- **Flask** (single endpoint), **vanilla JS** (no React, no build step)
- **SQLite** for session log + ratings + notes
- Plain HTML/CSS for the UI (dark, dense, server-rendered shell)

## Domain

`health / personal / workout-generator` — the first consumer/health tool built in this slot. Future runs should rotate away from this domain (try recruiting, real estate rentals, niche B2B, etc.).

## Run it

```bash
# 1. install (only flask)
pip install -r requirements.txt

# 2. run
python3 app.py
# → http://127.0.0.1:5050

# 3. tests
python3 test_engine.py
```

No env vars. No database setup. The first request creates `workout_weaver.db`.

## REST API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET  | `/` | — | UI |
| GET  | `/api/health` | — | `{"status":"ok"}` |
| POST | `/api/analyze` | `{brief: str}` | full result + saved session id |
| GET  | `/api/sessions?limit=20` | — | list of recent sessions |
| GET  | `/api/sessions/<id>` | — | full session (brief + profile + result) |
| PATCH | `/api/sessions/<id>` | `{rating?: 1-5, notes?: str}` | update |
| GET  | `/api/export/<id>?fmt=md\|json` | — | downloadable plan |

## Project layout

```
workout-weaver/
├── app.py                # Flask endpoints
├── engine.py             # detection + generation + scoring + DB + export
├── test_engine.py        # 10 smoke tests
├── requirements.txt      # just Flask
├── samples/              # 6 ready-to-paste briefs
└── templates/index.html  # UI
└── static/app.js
```

## Sample briefs (click any in the UI)

- **Beginner · home · 3×** — bodyweight, health + weight loss, walks
- **Intermediate · gym · 4× strength** — barbell + machines + cables
- **Advanced · hypertrophy · 5-day PPL** — full gym, 75 min
- **Endurance · 5× cardio** — beginner, treadmill + outdoor, 5k goal
- **Injured + 60+** — lower back + shoulder impingement, dumbbells only
- **Fat loss · home · 4×** — female, dumbbells + bands + treadmill

## Sample output (one training day, from sample-intermediate-gym)

```
### Upper Push
- Focus: push, pull
- Warm-up / Activation (~6 min, light)
  - Cat-Cow — 2 sets × 30-45s
  - Front Plank — 2 sets × 30-45s
- Main Lifts (~24 min, heavy)
  - Bench Press — 4 sets × 5-8 @ RPE 7-8
  - Pull-up — 4 sets × 5-8 @ RPE 7-8
- Accessory (~12 min, moderate)
  - Push-up — 3 sets × 8-12 @ RPE 7
  - Cable Face Pull — 3 sets × 8-12 @ RPE 7
- Core (~5 min, light)
  - Side Plank — 3 sets × 30-45s
- Cooldown / Mobility (~5 min, light)
  - Hip 90/90 Mobility — 2 sets × 30-60s
```

## Risk-flag examples

| Flag | Severity | Why it fires |
|---|---|---|
| `beginner_overtraining` | high | 6+ days/week at beginner experience |
| `injury_lower_back` | high | brief contains "lower back" + bench/squat/deadlift auto-substituted |
| `seven_day_training` | medium | training every day without advanced recovery setup |
| `endurance_low_volume` | high | cardio goal with <3 sessions/week |
| `older_athlete_volume` | low | 60+ lifting non-beginner weights — add longer warmup |
| `underage_athlete` | high | under-18 brief — requires coaching + no 1RM |
| `iron_aware` | low | female athlete — protein + ferritin floors |
| `repetitive_main_lift` | medium | same main lift ≥4×/week — joints fatigue |

## What it does NOT do

- This is **not medical advice**. It's a planning heuristic; adapt to how your body responds and consult a clinician for acute pain.
- It doesn't prescribe **nutrition** (use meal-weaver for that) or **recovery protocols** (use hydration-forge + breathing-forge).
- It doesn't generate workout **videos** or technique analysis — pick a coach for that.

## License

MIT — build on it, ship a similar one, attribute kindly.
