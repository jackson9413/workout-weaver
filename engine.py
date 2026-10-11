"""
workout-weaver: local-first weekly workout program generator.

Takes a free-form learner brief and returns:
- detected profile (experience, goal, days/week, session length, equipment, injuries, location)
- 7-day plan with day-by-day workout blocks (warmup / main / accessory / cooldown)
- exercise library matched to equipment + goal
- progressive overload scheme across the week
- risk-flag engine (overtraining, undertraining, injury mismatches, recovery gaps, etc.)
- 0-100 readiness score
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


# ---------- DETECTION ----------

EXPERIENCE_KEYWORDS = {
    "beginner": ["beginner", "new to", "just starting", "never lifted", "first time", "no experience", "novice"],
    "intermediate": ["intermediate", "some experience", "been training", "year or two", "1-2 years", "couple years", "know the basics"],
    "advanced": ["advanced", "experienced", "years of lifting", "5+ years", "long time", "comp prep", "powerlifting", "bodybuilding"],
}

GOAL_KEYWORDS = {
    "strength": ["strength", "strong", "get stronger", "squat", "deadlift", "bench", "1rm", "pr", "powerlifting"],
    "hypertrophy": ["muscle", "hypertrophy", "bigger", "grow", "size", "aesthetic", "shredded", "toned", "lean muscle"],
    "fat_loss": ["lose weight", "lose fat", "lose a", "fat loss", "cut", "shred", "lean", "slim down", "burn fat", "calorie deficit", "drop pounds", "weight loss"],
    "endurance": ["endurance", "cardio", "run", "5k", "10k", "marathon", "cycling", "rowing", "aerobic", "stamina"],
    "general_health": ["health", "general fitness", "stay healthy", "wellness", "longevity", "feel better", "energy"],
    "mobility": ["mobility", "flexibility", "stretch", "yoga", "range of motion", "tight", "stiff"],
    "athletic": ["athletic", "sport", "soccer", "basketball", "football", "tennis", "combat", "mma", "boxing", "performance"],
}

EQUIPMENT_KEYWORDS = {
    "full_gym": ["full gym", "commercial gym", "all equipment", "everything", "barbell + dumbbells + machines + cables"],
    "barbell": ["barbell", "squat rack", "power rack", "bench press", "deadlift platform"],
    "dumbbells": ["dumbbells", "dumbbell", "db only", "pair of dumbbells"],
    "kettlebells": ["kettlebell", "kb", "kettlebells"],
    "machines": ["machines", "machine", "selectorized", "cable machine", "smith"],
    "bands": ["bands", "resistance bands", "band", "loop bands"],
    "bodyweight": ["bodyweight", "body weight", "no equipment", "no gear", "no weights", "nothing at home", "calisthenics", "pull-up bar", "no dumbbells", "no barbell", "no rack"],
    "cardio_machines": ["treadmill", "bike", "rowing machine", "elliptical", "stairmaster"],
    "outdoor": ["outdoor", "park", "track", "trail", "running outside"],
}

INJURY_KEYWORDS = [
    "lower back", "back pain", "herniated disc", "sciatica",
    "shoulder", "rotator cuff", "shoulder pain", "shoulder impingement",
    "knee", "acl", "meniscus", "patellar", "knee pain",
    "wrist", "elbow", "tennis elbow", "carpal tunnel",
    "hip", "hip pain", "labrum",
    "ankle", "achilles",
    "neck", "cervical",
]

LOCATION_KEYWORDS = {
    "home": ["at home", "home gym", "garage gym", "in my apartment", "basement", "living room"],
    "gym": ["at the gym", "at gym", "commercial", "equinox", "planet fitness", "la fitness", "ymca"],
    "outdoor": ["outside", "outdoor", "park", "track"],
}


def detect_experience(text: str) -> tuple[str, float]:
    t = text.lower()
    for kw in EXPERIENCE_KEYWORDS["advanced"]:
        if kw in t:
            return "advanced", 0.9
    for kw in EXPERIENCE_KEYWORDS["beginner"]:
        if kw in t:
            return "beginner", 0.9
    for kw in EXPERIENCE_KEYWORDS["intermediate"]:
        if kw in t:
            return "intermediate", 0.85
    return "beginner", 0.4  # default safe


def detect_goal(text: str) -> tuple[str, float]:
    t = text.lower()
    # priority order matches importance
    for goal in ["fat_loss", "strength", "hypertrophy", "endurance", "general_health", "mobility", "athletic"]:
        for kw in GOAL_KEYWORDS[goal]:
            if kw in t:
                return goal, 0.85
    return "general_health", 0.4


def detect_days_per_week(text: str) -> int:
    t = text.lower()
    # explicit numbers
    m = re.search(r"(\d)\s*(?:days?|x|times?)\s*(?:a|per)\s*week", t)
    if m:
        return max(1, min(7, int(m.group(1))))
    m = re.search(r"(\d)[\s-]*day(?:s)?(?:\s+(?:a|per)\s+week)?", t)
    if m:
        return max(1, min(7, int(m.group(1))))
    # word-of-mouth
    for n, word in [(7, "every day"), (6, "six days"), (5, "five days"), (4, "four days"), (3, "three days"), (2, "twice"), (1, "one day")]:
        if word in t:
            return n
    return 4  # sane default for an adult


def detect_session_minutes(text: str) -> int:
    t = text.lower()
    m = re.search(r"(\d{1,3})\s*(?:min(?:ute)?s?|mins?)", t)
    if m:
        return max(15, min(180, int(m.group(1))))
    # hours: prefer decimal-style if available
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", t)
    if m:
        return max(15, min(180, int(round(float(m.group(1)) * 60))))
    # buckets
    if "short" in t or "quick" in t:
        return 30
    if "long" in t:
        return 75
    return 45


def detect_equipment(text: str) -> list[str]:
    t = text.lower()
    # full_gym is a superset
    if any(kw in t for kw in EQUIPMENT_KEYWORDS["full_gym"]):
        return ["full_gym"]
    # Detection: match keyword as a whole word OR in immediate "I have/use/with + keyword" context.
    # This avoids false positives like "barbell squats" (mentioning the exercise).
    found = []
    for eq, kws in EQUIPMENT_KEYWORDS.items():
        if eq == "full_gym":
            continue
        matched = False
        for kw in kws:
            # as standalone word
            if re.search(rf"\b{re.escape(kw)}\b", t):
                # exclude false positives where keyword is followed by an exercise name
                tail = re.search(rf"\b{re.escape(kw)}\b\s+(\w+)", t)
                if tail and tail.group(1) in {"squat", "squat", "squats", "bench", "row", "press", "deadlift", "deadlifts", "rack", "overhead", "hip", "thrust", "lunge", "lifts"}:
                    continue
                matched = True
                break
            # in "I have/with/use + keyword" context
            if re.search(rf"(?:have|with|use|using|and|\+)\s+(?:a\s+|an\s+|some\s+|pair\s+of\s+)?{re.escape(kw)}", t):
                matched = True
                break
            if re.search(rf"{re.escape(kw)}\s+(?:and|set|rack|station|rack|bar|pairs?)", t):
                matched = True
                break
        if matched:
            found.append(eq)
    if "no equipment" in t or "no dumbbells" in t or "no weights" in t or "nothing at home" in t:
        return ["bodyweight"]
    if not found:
        return ["bodyweight"]
    return found


def detect_injuries(text: str) -> list[str]:
    t = text.lower()
    found = []
    for inj in INJURY_KEYWORDS:
        if inj in t:
            found.append(inj)
    # dedupe: if both "lower back" and "back pain" present, keep one canonical
    canonicals = {
        "back": "lower back",
        "back pain": "lower back",
        "lower back": "lower back",
        "shoulder": "shoulder",
        "shoulder impingement": "shoulder",
        "shoulder pain": "shoulder",
        "rotator cuff": "shoulder",
        "knee": "knee",
        "knee pain": "knee",
        "acl": "knee",
        "meniscus": "knee",
        "patellar": "knee",
        "wrist": "wrist",
        "elbow": "elbow",
        "tennis elbow": "elbow",
        "carpal tunnel": "wrist",
        "hip": "hip",
        "hip pain": "hip",
        "labrum": "hip",
        "ankle": "ankle",
        "achilles": "ankle",
        "neck": "neck",
        "cervical": "neck",
        "sciatica": "lower back",
        "herniated disc": "lower back",
    }
    canon_set = set()
    for f in found:
        c = canonicals.get(f, f)
        canon_set.add(c)
    return sorted(canon_set)


def detect_location(text: str) -> str:
    t = text.lower()
    for loc, kws in LOCATION_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return loc
    return "gym"  # default assumption


def detect_age(text: str) -> Optional[int]:
    t = text.lower()
    # explicit forms: "I'm 63", "I am 63", "age 63", "63 yo", "63-year-old"
    m = re.search(r"(?:i'?m|i am|age[d]?)\s*(\d{2})\b", t)
    if m:
        a = int(m.group(1))
        if 14 < a < 90:
            return a
    m = re.search(r"(\d{2})[\s-]*(?:years?\s*old|y\.o\.?|yo)\b", t)
    if m:
        a = int(m.group(1))
        if 14 < a < 90:
            return a
    m = re.search(r"\b(\d{2})\s*(?:year[- ]old|y\.o\.)\b", t)
    if m:
        a = int(m.group(1))
        if 14 < a < 90:
            return a
    return None


def detect_sex(text: str) -> Optional[str]:
    t = text.lower()
    if re.search(r"\b(?:female|woman|wife|girl|mom|mother|she|her)\b", t):
        return "female"
    if re.search(r"\b(?:male|man|husband|guy|dad|father|he|him)\b", t):
        return "male"
    return None


# ---------- EXERCISE LIBRARY ----------

# Tags:
#   equipment: required (all must be available)
#   avoid: injury keywords to flag against (any match => skip suggestion for that goal)
#   primary_muscle, movement: squat / hinge / push / pull / carry / core / cardio
#   intensity: 1=light, 2=moderate, 3=hard, 4=very hard
#   experience_floor: minimum experience level

EXERCISES = [
    # ===== Strength / Barbell =====
    {"id": "back_squat", "name": "Back Squat", "equipment": ["barbell"], "movement": "squat",
     "primary_muscle": "quads+glutes", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["knee", "lower back", "hip"], "swap_when_avoid": ["goblet_squat", "leg_press", "split_squat"]},
    {"id": "front_squat", "name": "Front Squat", "equipment": ["barbell"], "movement": "squat",
     "primary_muscle": "quads", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["wrist", "lower back"], "swap_when_avoid": ["goblet_squat"]},
    {"id": "goblet_squat", "name": "Goblet Squat", "equipment": ["dumbbells", "kettlebells"], "movement": "squat",
     "primary_muscle": "quads+glutes", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["knee"], "swap_when_avoid": ["box_squat_bodyweight", "leg_press"]},
    {"id": "box_squat_bodyweight", "name": "Box Squat (Bodyweight)", "equipment": ["bodyweight"], "movement": "squat",
     "primary_muscle": "quads+glutes", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["knee"], "swap_when_avoid": ["split_squat_bodyweight"]},
    {"id": "split_squat", "name": "Dumbbell Split Squat", "equipment": ["dumbbells"], "movement": "squat",
     "primary_muscle": "quads+glutes", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["knee", "hip"], "swap_when_avoid": ["leg_press"]},
    {"id": "split_squat_bodyweight", "name": "Bodyweight Split Squat", "equipment": ["bodyweight"], "movement": "squat",
     "primary_muscle": "quads+glutes", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["knee"], "swap_when_avoid": []},
    {"id": "leg_press", "name": "Leg Press", "equipment": ["machines", "full_gym"], "movement": "squat",
     "primary_muscle": "quads+glutes", "intensity": 3, "experience_floor": "beginner",
     "avoid": ["knee", "lower back"], "swap_when_avoid": ["leg_extension"]},
    {"id": "leg_extension", "name": "Leg Extension", "equipment": ["machines", "full_gym"], "movement": "squat",
     "primary_muscle": "quads", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["knee"], "swap_when_avoid": []},

    {"id": "deadlift", "name": "Conventional Deadlift", "equipment": ["barbell"], "movement": "hinge",
     "primary_muscle": "posterior chain", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["lower back", "hip"], "swap_when_avoid": ["trap_bar_deadlift", "rdl_dumbbell"]},
    {"id": "trap_bar_deadlift", "name": "Trap-bar Deadlift", "equipment": ["barbell", "full_gym"], "movement": "hinge",
     "primary_muscle": "posterior chain", "intensity": 3, "experience_floor": "intermediate",
     "avoid": ["lower back", "hip"], "swap_when_avoid": ["rdl_dumbbell"]},
    {"id": "rdl_barbell", "name": "Barbell RDL", "equipment": ["barbell"], "movement": "hinge",
     "primary_muscle": "hamstrings+glutes", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["lower back", "hamstring"], "swap_when_avoid": ["rdl_dumbbell"]},
    {"id": "rdl_dumbbell", "name": "Dumbbell RDL", "equipment": ["dumbbells"], "movement": "hinge",
     "primary_muscle": "hamstrings+glutes", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["lower back"], "swap_when_avoid": ["glute_bridge"]},
    {"id": "glute_bridge", "name": "Glute Bridge", "equipment": ["bodyweight", "machines", "full_gym"], "movement": "hinge",
     "primary_muscle": "glutes", "intensity": 1, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": []},
    {"id": "hip_thrust", "name": "Barbell Hip Thrust", "equipment": ["barbell", "full_gym"], "movement": "hinge",
     "primary_muscle": "glutes", "intensity": 3, "experience_floor": "intermediate",
     "avoid": ["lower back", "hip"], "swap_when_avoid": ["glute_bridge"]},
    {"id": "leg_curl", "name": "Lying Leg Curl", "equipment": ["machines", "full_gym"], "movement": "hinge",
     "primary_muscle": "hamstrings", "intensity": 2, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": ["nordic_curl"]},
    {"id": "nordic_curl", "name": "Nordic Hamstring Curl", "equipment": ["bands", "bodyweight"], "movement": "hinge",
     "primary_muscle": "hamstrings", "intensity": 4, "experience_floor": "advanced",
     "avoid": ["knee", "hamstring"], "swap_when_avoid": []},

    {"id": "bench_press", "name": "Bench Press", "equipment": ["barbell"], "movement": "push",
     "primary_muscle": "chest+triceps", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["shoulder", "wrist", "elbow"], "swap_when_avoid": ["db_bench", "pushup"]},
    {"id": "db_bench", "name": "Dumbbell Bench Press", "equipment": ["dumbbells"], "movement": "push",
     "primary_muscle": "chest+triceps", "intensity": 3, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": ["pushup", "machine_chest_press"]},
    {"id": "machine_chest_press", "name": "Machine Chest Press", "equipment": ["machines", "full_gym"], "movement": "push",
     "primary_muscle": "chest+triceps", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": ["pushup"]},
    {"id": "pushup", "name": "Push-up", "equipment": ["bodyweight"], "movement": "push",
     "primary_muscle": "chest+triceps", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["wrist", "shoulder"], "swap_when_avoid": ["knee_pushup"]},
    {"id": "knee_pushup", "name": "Knee Push-up", "equipment": ["bodyweight"], "movement": "push",
     "primary_muscle": "chest+triceps", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["wrist"], "swap_when_avoid": []},
    {"id": "incline_db_press", "name": "Incline DB Press", "equipment": ["dumbbells"], "movement": "push",
     "primary_muscle": "upper chest", "intensity": 3, "experience_floor": "intermediate",
     "avoid": ["shoulder"], "swap_when_avoid": ["machine_chest_press"]},
    {"id": "ohp_barbell", "name": "Standing Overhead Press", "equipment": ["barbell"], "movement": "push",
     "primary_muscle": "shoulders+triceps", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["shoulder", "lower back", "neck"], "swap_when_avoid": ["db_shoulder_press", "machine_shoulder_press"]},
    {"id": "db_shoulder_press", "name": "DB Shoulder Press", "equipment": ["dumbbells"], "movement": "push",
     "primary_muscle": "shoulders+triceps", "intensity": 3, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": ["machine_shoulder_press"]},
    {"id": "machine_shoulder_press", "name": "Machine Shoulder Press", "equipment": ["machines", "full_gym"], "movement": "push",
     "primary_muscle": "shoulders+triceps", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": []},
    {"id": "lateral_raise", "name": "DB Lateral Raise", "equipment": ["dumbbells", "bands"], "movement": "push",
     "primary_muscle": "side delts", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": []},
    {"id": "tricep_pushdown", "name": "Cable Tricep Pushdown", "equipment": ["machines", "full_gym", "bands"], "movement": "push",
     "primary_muscle": "triceps", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["elbow"], "swap_when_avoid": ["db_kickback"]},

    {"id": "pullup", "name": "Pull-up", "equipment": ["bodyweight", "bands"], "movement": "pull",
     "primary_muscle": "back+biceps", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["shoulder", "elbow", "wrist"], "swap_when_avoid": ["lat_pulldown", "band_pull_apart"]},
    {"id": "chinup", "name": "Chin-up", "equipment": ["bodyweight", "bands"], "movement": "pull",
     "primary_muscle": "back+biceps", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["shoulder", "elbow"], "swap_when_avoid": ["lat_pulldown"]},
    {"id": "lat_pulldown", "name": "Lat Pulldown", "equipment": ["machines", "full_gym", "bands"], "movement": "pull",
     "primary_muscle": "back", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": ["band_row"]},
    {"id": "barbell_row", "name": "Bent-over Barbell Row", "equipment": ["barbell"], "movement": "pull",
     "primary_muscle": "back", "intensity": 4, "experience_floor": "intermediate",
     "avoid": ["lower back"], "swap_when_avoid": ["db_row", "chest_supported_row"]},
    {"id": "db_row", "name": "Single-arm DB Row", "equipment": ["dumbbells"], "movement": "pull",
     "primary_muscle": "back", "intensity": 2, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": []},
    {"id": "chest_supported_row", "name": "Chest-supported Row", "equipment": ["machines", "full_gym", "dumbbells"], "movement": "pull",
     "primary_muscle": "back", "intensity": 2, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": ["db_row"]},
    {"id": "seated_row", "name": "Seated Cable Row", "equipment": ["machines", "full_gym", "bands"], "movement": "pull",
     "primary_muscle": "back", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["lower back"], "swap_when_avoid": ["db_row"]},
    {"id": "face_pull", "name": "Cable Face Pull", "equipment": ["machines", "full_gym", "bands"], "movement": "pull",
     "primary_muscle": "rear delts", "intensity": 1, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": []},
    {"id": "band_pull_apart", "name": "Band Pull-apart", "equipment": ["bands"], "movement": "pull",
     "primary_muscle": "rear delts", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": []},
    {"id": "barbell_curl", "name": "Barbell Curl", "equipment": ["barbell"], "movement": "pull",
     "primary_muscle": "biceps", "intensity": 3, "experience_floor": "beginner",
     "avoid": ["elbow"], "swap_when_avoid": ["db_curl"]},
    {"id": "db_curl", "name": "Dumbbell Curl", "equipment": ["dumbbells"], "movement": "pull",
     "primary_muscle": "biceps", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["elbow"], "swap_when_avoid": []},

    # ===== Core =====
    {"id": "plank", "name": "Front Plank", "equipment": ["bodyweight"], "movement": "core",
     "primary_muscle": "core", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["shoulder", "lower back"], "swap_when_avoid": ["dead_bug"]},
    {"id": "side_plank", "name": "Side Plank", "equipment": ["bodyweight"], "movement": "core",
     "primary_muscle": "obliques", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": ["dead_bug"]},
    {"id": "dead_bug", "name": "Dead Bug", "equipment": ["bodyweight"], "movement": "core",
     "primary_muscle": "deep core", "intensity": 1, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": []},
    {"id": "hanging_leg_raise", "name": "Hanging Leg Raise", "equipment": ["bodyweight"], "movement": "core",
     "primary_muscle": "abs", "intensity": 3, "experience_floor": "intermediate",
     "avoid": ["shoulder", "lower back"], "swap_when_avoid": ["plank", "dead_bug"]},
    {"id": "pallof_press", "name": "Pallof Press", "equipment": ["bands", "machines", "full_gym"], "movement": "core",
     "primary_muscle": "anti-rotation", "intensity": 1, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": []},

    # ===== Cardio =====
    {"id": "treadmill_run", "name": "Treadmill Run (Z2)", "equipment": ["cardio_machines", "full_gym"], "movement": "cardio",
     "primary_muscle": "cardio", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["knee", "ankle"], "swap_when_avoid": ["bike_easy"]},
    {"id": "bike_easy", "name": "Stationary Bike (Easy)", "equipment": ["cardio_machines", "full_gym"], "movement": "cardio",
     "primary_muscle": "cardio", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["knee"], "swap_when_avoid": []},
    {"id": "row_erg", "name": "Rower (Steady State)", "equipment": ["cardio_machines", "full_gym"], "movement": "cardio",
     "primary_muscle": "cardio+back", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["lower back"], "swap_when_avoid": ["bike_easy"]},
    {"id": "outdoor_run", "name": "Outdoor Run", "equipment": ["outdoor", "bodyweight"], "movement": "cardio",
     "primary_muscle": "cardio", "intensity": 2, "experience_floor": "beginner",
     "avoid": ["knee", "ankle"], "swap_when_avoid": ["bike_easy"]},
    {"id": "walk_z2", "name": "Brisk Walk (Zone 2)", "equipment": ["bodyweight", "cardio_machines", "outdoor"], "movement": "cardio",
     "primary_muscle": "cardio", "intensity": 1, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": []},

    # ===== Mobility / Recovery =====
    {"id": "hip_90_90", "name": "Hip 90/90 Mobility", "equipment": ["bodyweight"], "movement": "mobility",
     "primary_muscle": "hips", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["hip"], "swap_when_avoid": []},
    {"id": "world_greatest_stretch", "name": "World's Greatest Stretch", "equipment": ["bodyweight"], "movement": "mobility",
     "primary_muscle": "full body", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["knee"], "swap_when_avoid": ["hip_90_90"]},
    {"id": "cat_cow", "name": "Cat-Cow", "equipment": ["bodyweight"], "movement": "mobility",
     "primary_muscle": "spine", "intensity": 0, "experience_floor": "beginner",
     "avoid": [], "swap_when_avoid": []},
    {"id": "thoracic_open", "name": "Thoracic Open Book", "equipment": ["bodyweight"], "movement": "mobility",
     "primary_muscle": "t-spine", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": []},
    {"id": "downward_dog", "name": "Downward Dog", "equipment": ["bodyweight"], "movement": "mobility",
     "primary_muscle": "posterior chain", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["wrist", "shoulder"], "swap_when_avoid": ["cat_cow"]},
    {"id": "dead_hang", "name": "Dead Hang", "equipment": ["bodyweight"], "movement": "mobility",
     "primary_muscle": "shoulders+spine", "intensity": 1, "experience_floor": "beginner",
     "avoid": ["shoulder"], "swap_when_avoid": []},
]


# ---------- SPLIT TEMPLATES ----------

# Each split is keyed by goal + (optional) experience level.
# Each entry is a list of day buckets, where each bucket includes the muscles/movements to hit that day.
# The actual exercise selection happens later.

SPLITS = {
    # ----- Strength (compound focus) -----
    ("strength", "beginner"): "full_body_3x",
    ("strength", "intermediate"): "upper_lower_4x",
    ("strength", "advanced"): "ppl_5x",

    # ----- Hypertrophy -----
    ("hypertrophy", "beginner"): "full_body_3x",
    ("hypertrophy", "intermediate"): "upper_lower_4x",
    ("hypertrophy", "advanced"): "ppl_5x",

    # ----- Fat loss -----
    ("fat_loss", "beginner"): "full_body_3x",
    ("fat_loss", "intermediate"): "upper_lower_4x",
    ("fat_loss", "advanced"): "upper_lower_4x",

    # ----- Endurance -----
    ("endurance", "beginner"): "cardio_3x",
    ("endurance", "intermediate"): "cardio_4x",
    ("endurance", "advanced"): "cardio_5x",

    # ----- General health -----
    ("general_health", "beginner"): "full_body_3x",
    ("general_health", "intermediate"): "full_body_3x",
    ("general_health", "advanced"): "upper_lower_4x",

    # ----- Mobility -----
    ("mobility", "beginner"): "mobility_5x",
    ("mobility", "intermediate"): "mobility_5x",
    ("mobility", "advanced"): "mobility_5x",

    # ----- Athletic -----
    ("athletic", "beginner"): "full_body_3x",
    ("athletic", "intermediate"): "upper_lower_4x",
    ("athletic", "advanced"): "upper_lower_4x",
}

SPLIT_TEMPLATES = {
    # Each day lists movement groups to cover; main = primary compound, accessory = secondary, finisher = short burnout
    "full_body_3x": [
        {"label": "Full Body A", "focus": ["squat", "push", "pull", "core"]},
        {"label": "Active Recovery", "focus": ["mobility", "cardio"]},
        {"label": "Full Body B", "focus": ["hinge", "push", "pull", "core"]},
        {"label": "Rest", "focus": []},
        {"label": "Optional Cardio", "focus": ["cardio"]},
        {"label": "Rest", "focus": []},
        {"label": "Rest", "focus": []},
    ],
    "upper_lower_4x": [
        {"label": "Upper Push", "focus": ["push", "pull"]},
        {"label": "Lower", "focus": ["squat", "hinge"]},
        {"label": "Active Recovery", "focus": ["mobility", "cardio"]},
        {"label": "Upper Pull", "focus": ["pull", "push"]},
        {"label": "Lower Hinge", "focus": ["hinge", "squat", "core"]},
        {"label": "Optional Cardio", "focus": ["cardio"]},
        {"label": "Rest", "focus": []},
    ],
    "ppl_5x": [
        {"label": "Push", "focus": ["push"]},
        {"label": "Pull", "focus": ["pull"]},
        {"label": "Legs", "focus": ["squat", "hinge"]},
        {"label": "Active Recovery", "focus": ["mobility", "cardio"]},
        {"label": "Upper", "focus": ["push", "pull"]},
        {"label": "Lower Hinge", "focus": ["hinge", "core"]},
        {"label": "Rest", "focus": []},
    ],
    "cardio_3x": [
        {"label": "Easy Run (Z2)", "focus": ["cardio"]},
        {"label": "Strength Primer", "focus": ["squat", "push"]},
        {"label": "Long Run", "focus": ["cardio"]},
        {"label": "Rest", "focus": []},
        {"label": "Tempo Run", "focus": ["cardio"]},
        {"label": "Strength Primer", "focus": ["hinge", "pull"]},
        {"label": "Rest", "focus": []},
    ],
    "cardio_4x": [
        {"label": "Easy Run (Z2)", "focus": ["cardio"]},
        {"label": "Strength Primer", "focus": ["squat", "push"]},
        {"label": "Tempo / Intervals", "focus": ["cardio"]},
        {"label": "Rest", "focus": []},
        {"label": "Long Run", "focus": ["cardio"]},
        {"label": "Strength Primer", "focus": ["hinge", "pull"]},
        {"label": "Rest", "focus": []},
    ],
    "cardio_5x": [
        {"label": "Easy Run (Z2)", "focus": ["cardio"]},
        {"label": "Strength Primer", "focus": ["squat", "push"]},
        {"label": "Tempo Run", "focus": ["cardio"]},
        {"label": "Easy Run (Z2)", "focus": ["cardio"]},
        {"label": "Strength Primer", "focus": ["hinge", "pull", "core"]},
        {"label": "Long Run", "focus": ["cardio"]},
        {"label": "Rest", "focus": []},
    ],
    "mobility_5x": [
        {"label": "Hip + T-spine Flow", "focus": ["mobility"]},
        {"label": "Strength Primer", "focus": ["squat", "core"]},
        {"label": "Full Body Flow", "focus": ["mobility"]},
        {"label": "Strength Primer", "focus": ["hinge", "push"]},
        {"label": "Posterior Chain Flow", "focus": ["mobility"]},
        {"label": "Strength Primer", "focus": ["pull", "core"]},
        {"label": "Rest", "focus": []},
    ],
}


# ---------- VOLUME / INTENSITY PRESETS ----------

# Default set/rep scheme per experience level
SCHEMES = {
    "beginner": {
        "main_sets": 3, "main_reps": "8-12", "main_intensity": "RPE 7",
        "accessory_sets": 2, "accessory_reps": "10-15", "accessory_intensity": "RPE 6-7",
    },
    "intermediate": {
        "main_sets": 4, "main_reps": "5-8", "main_intensity": "RPE 7-8",
        "accessory_sets": 3, "accessory_reps": "8-12", "accessory_intensity": "RPE 7",
    },
    "advanced": {
        "main_sets": 5, "main_reps": "3-6", "main_intensity": "RPE 8-9",
        "accessory_sets": 4, "accessory_reps": "6-10", "accessory_intensity": "RPE 8",
    },
}

CARDIO_SCHEMES = {
    "beginner": {"z2_minutes": 20, "tempo_minutes": 10, "long_minutes": 30},
    "intermediate": {"z2_minutes": 35, "tempo_minutes": 20, "long_minutes": 60},
    "advanced": {"z2_minutes": 50, "tempo_minutes": 30, "long_minutes": 90},
}

# ---------- INJURY SUBSTITUTION RULES ----------

# Exercises absolutely contraindicated for specific injuries (auto-skip + flag)
HARD_INJURY_AVOID = {
    "lower back": ["deadlift", "ohp_barbell", "good_morning", "barbell_row"],
    "shoulder": ["bench_press", "ohp_barbell", "pullup", "chinup", "lateral_raise"],
    "knee": ["back_squat", "leg_extension", "pistol"],
    "wrist": ["pushup", "knee_pushup", "front_squat"],
    "elbow": ["barbell_curl", "db_curl", "pullup", "chinup"],
    "hip": ["hip_thrust", "deadlift", "split_squat"],
    "ankle": ["outdoor_run", "box_jump"],
    "neck": ["ohp_barbell", "back_squat"],
}


# ---------- PROFILE ----------

@dataclass
class Profile:
    experience: str
    experience_conf: float
    goal: str
    goal_conf: float
    days_per_week: int
    session_minutes: int
    equipment: list[str]
    injuries: list[str]
    location: str
    age: Optional[int] = None
    sex: Optional[str] = None
    raw_text: str = ""

    def as_dict(self):
        return asdict(self)


# ---------- GENERATION ----------

def generate_week(profile: Profile) -> dict:
    """Return the full structured week plan + scorecard + risk flags."""
    equipment_set = set(profile.equipment)
    if "full_gym" in equipment_set:
        # superset
        equipment_set.update(["barbell", "dumbbells", "machines", "bands", "cardio_machines", "kettlebells"])
    # bodyweight is always implicit (you have your own body)
    equipment_set.add("bodyweight")

    split_key = (profile.goal, profile.experience)
    split_name = SPLITS.get(split_key, SPLITS[("general_health", profile.experience)])

    # If days_per_week exceeds the template's training-day count, upgrade to a denser split
    template_training_count = sum(1 for d in SPLIT_TEMPLATES[split_name] if d["focus"])
    if profile.days_per_week > template_training_count:
        denser = {
            "cardio_3x": "cardio_5x",
            "cardio_4x": "cardio_5x",
            "full_body_3x": "upper_lower_4x",
            "upper_lower_4x": "ppl_5x",
            "mobility_5x": "mobility_5x",
            "ppl_5x": "ppl_5x",
        }
        candidate = denser.get(split_name)
        if candidate and profile.days_per_week <= sum(1 for d in SPLIT_TEMPLATES[candidate] if d["focus"]):
            split_name = candidate

    day_templates = SPLIT_TEMPLATES[split_name]

    # Adjust to actual days
    days = build_days(profile, day_templates)

    # Cap cardio finishers across the week
    cardio_finishers_used = 0
    cardio_finisher_cap = 3 if profile.goal in ("fat_loss", "general_health") else 0

    # Pick exercises per day
    week_plan = []
    for i, day in enumerate(days):
        blocks = build_blocks(profile, equipment_set, day["focus"], day["label"],
                              cardio_finisher_cap=cardio_finisher_cap - cardio_finishers_used)
        # Count any cardio finisher blocks we just added
        for b in blocks:
            if b["name"] == "Cardio Finisher":
                cardio_finishers_used += 1
        week_plan.append({
            "day_index": i,
            "day_label": day["label"],
            "focus": day["focus"],
            "is_rest": not day["focus"],
            "blocks": blocks,
        })

    # Trim week to requested days_per_week (keep rest day at end if there are extras)
    # Actually, build_days already handles this — verify it produced the right number of training days.

    # Progressive overload: a 1-week plan doesn't show progression, but we provide RPE-ramp guidance.
    overload = build_overload(profile.experience)

    # Risk flags
    flags = build_flags(profile, week_plan)

    # Score card
    score = compute_score(profile, week_plan, flags)

    return {
        "profile": profile.as_dict(),
        "split": split_name,
        "week_plan": week_plan,
        "overload": overload,
        "risk_flags": flags,
        "score": score,
    }


def build_days(profile: Profile, day_templates: list[dict]) -> list[dict]:
    """Pick the first N training days from the template, appending rest as needed."""
    training = [d for d in day_templates if d["focus"]]
    rest = [d for d in day_templates if not d["focus"]]

    needed_training = profile.days_per_week
    # If user wants more training days than template provides, we cycle.
    if needed_training > len(training):
        cycles = (needed_training // len(training)) + 1
        training = (training * cycles)[:max(needed_training, len(training))]

    chosen_training = training[:needed_training]

    # Re-assemble into Mon-Sun layout: cluster training on weekdays, leave Sat/Sun as recovery
    days = []
    training_idx = 0
    weekday_pattern = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i, dow in enumerate(weekday_pattern):
        if training_idx < len(chosen_training):
            days.append(chosen_training[training_idx])
            training_idx += 1
        else:
            days.append({"label": "Rest", "focus": []})

    # If there are still training slots left after Mon-Sun, append.
    while training_idx < len(chosen_training):
        days.append(chosen_training[training_idx])
        training_idx += 1

    return days


def available_exercises(profile: Profile, equipment_set: set[str], movement: str = None) -> list[dict]:
    """Return exercises that match (a) equipment available, (b) experience floor, (c) injury contraindications."""
    exp_order = ["beginner", "intermediate", "advanced"]
    min_idx = exp_order.index(profile.experience)

    out = []
    for ex in EXERCISES:
        # equipment check
        if not any(eq in equipment_set for eq in ex["equipment"]):
            continue
        # experience floor
        if exp_order.index(ex["experience_floor"]) > min_idx:
            continue
        # movement filter
        if movement and ex["movement"] != movement:
            continue
        # injury check: skip if exercise's avoid matches one of profile injuries
        if any(inj in ex["avoid"] for inj in profile.injuries):
            continue
        out.append(ex)
    return out


def safe_swappable(profile: Profile, equipment_set: set[str], movement: str, primary_slot: bool = True) -> list[dict]:
    """Same as available_exercises but excluding the exercises that have a HARD_INJURY_AVOID match."""
    candidates = available_exercises(profile, equipment_set, movement)
    # additionally filter HARD_INJURY_AVOID
    safe = []
    for ex in candidates:
        blocked = False
        for inj in profile.injuries:
            if inj in HARD_INJURY_AVOID and ex["id"] in HARD_INJURY_AVOID[inj]:
                blocked = True
                break
        if not blocked:
            safe.append(ex)
    return safe


def pick_exercise(profile: Profile, equipment_set: set[str], movement: str, used_ids: set[str], primary: bool = False) -> Optional[dict]:
    """Pick one exercise from a movement pattern. Prefers lower-intensity for warmup, higher for main."""
    pool = safe_swappable(profile, equipment_set, movement)
    pool = [e for e in pool if e["id"] not in used_ids]
    if not pool:
        pool = available_exercises(profile, equipment_set, movement)
        pool = [e for e in pool if e["id"] not in used_ids]
    if not pool:
        return None
    pool.sort(key=lambda e: (-e["intensity"] if primary else -e["intensity"]))
    # for accessories, prefer lower intensity + smaller muscles
    if not primary:
        pool.sort(key=lambda e: e["intensity"])
    return pool[0]


def build_blocks(profile: Profile, equipment_set: set[str], focus: list[str], day_label: str,
                 cardio_finisher_cap: int = 0) -> list[dict]:
    """Build the actual exercise blocks for a single day.

    Structure:
        - Warm-up (mobility + activation)
        - Main lift(s)
        - Accessory / hypertrophy
        - Core (if not already covered)
        - Cooldown / mobility
        - Cardio finisher (optional)
    """
    blocks = []
    scheme = SCHEMES[profile.experience]

    # Rest days have no blocks at all
    if not focus:
        return blocks

    used_ids = set()

    # ----- Warm-up -----
    # Warm-up should be on every training day, even push/pull focused ones.
    # Pick a mobility drill relevant to the focus (squat → hip mobility, push → t-spine, pull → face-pull-style, etc.)
    warmup_picks = []
    primary_focus = focus[0] if focus else "mobility"
    # Always try one mobility warmup appropriate to the focus
    warmup_mv_priority = ["mobility"]  # at least mobility
    pick = pick_exercise(profile, equipment_set, "mobility", used_ids, primary=False)
    if pick:
        warmup_picks.append(pick)
        used_ids.add(pick["id"])
    # Activation: pull-day → band pull-apart / face-pull; push-day → lateral raise / core; squat/hinge → glute activation
    if "pull" in primary_focus or primary_focus in ("upper", "push", "pull"):
        activation = pick_exercise(profile, equipment_set, "core", used_ids, primary=False)
        if activation:
            warmup_picks.append(activation)
            used_ids.add(activation["id"])
    if warmup_picks:
        warmup_lines = []
        for p in warmup_picks:
            warmup_lines.append(f"{p['name']} — 2 sets × 30-45s")
        blocks.append({
            "name": "Warm-up / Activation",
            "intensity_band": "light",
            "est_minutes": max(5, min(10, len(warmup_picks) * 3)),
            "items": warmup_lines,
            "exercises": [{"id": p["id"], "name": p["name"], "sets": 2, "reps": "30-45s", "intensity": "RPE 5"} for p in warmup_picks],
        })

    # ----- Main lift(s) -----
    main_picks = []
    # For strength/hypertrophy: pick one big compound per requested movement
    main_movements = [m for m in focus if m in ["squat", "hinge", "push", "pull"]]
    if main_movements:
        for mv in main_movements[:2]:  # at most 2 main lifts in a session to fit time
            pick = pick_exercise(profile, equipment_set, mv, used_ids, primary=True)
            if pick:
                main_picks.append(pick)
                used_ids.add(pick["id"])
    if main_picks:
        main_lines = []
        main_reps = scheme["main_reps"]
        main_intensity = scheme["main_intensity"]
        for p in main_picks:
            main_lines.append(f"{p['name']} — {scheme['main_sets']} sets × {main_reps} @ {main_intensity}")
        blocks.append({
            "name": "Main Lift" + ("s" if len(main_picks) > 1 else ""),
            "intensity_band": "heavy",
            "est_minutes": len(main_picks) * (8 if profile.experience == "beginner" else 12),
            "items": main_lines,
            "exercises": [{"id": p["id"], "name": p["name"], "sets": scheme["main_sets"], "reps": main_reps, "intensity": main_intensity} for p in main_picks],
        })

    # ----- Accessory / hypertrophy -----
    accessory_picks = []
    # try the remaining movement patterns
    for mv in ["squat", "hinge", "push", "pull"]:
        if mv not in main_movements and mv in focus:
            pick = pick_exercise(profile, equipment_set, mv, used_ids, primary=False)
            if pick:
                accessory_picks.append(pick)
                used_ids.add(pick["id"])
    # If still short on volume, add another movement pass
    if profile.experience != "beginner":
        for mv in ["push", "pull", "squat", "hinge"]:
            if len(accessory_picks) >= 3:
                break
            if mv in focus:
                pick = pick_exercise(profile, equipment_set, mv, used_ids, primary=False)
                if pick:
                    accessory_picks.append(pick)
                    used_ids.add(pick["id"])
    if accessory_picks:
        acc_lines = []
        for p in accessory_picks:
            acc_lines.append(f"{p['name']} — {scheme['accessory_sets']} sets × {scheme['accessory_reps']} @ {scheme['accessory_intensity']}")
        blocks.append({
            "name": "Accessory",
            "intensity_band": "moderate",
            "est_minutes": len(accessory_picks) * 6,
            "items": acc_lines,
            "exercises": [{"id": p["id"], "name": p["name"], "sets": scheme["accessory_sets"], "reps": scheme["accessory_reps"], "intensity": scheme["accessory_intensity"]} for p in accessory_picks],
        })

    # ----- Core (if strength/hypertrophy + core not in focus) -----
    if "core" not in focus and profile.goal in ("strength", "hypertrophy", "general_health", "athletic"):
        core_pick = pick_exercise(profile, equipment_set, "core", used_ids, primary=False)
        if core_pick:
            blocks.append({
                "name": "Core",
                "intensity_band": "light",
                "est_minutes": 5,
                "items": [f"{core_pick['name']} — 3 sets × 30-45s"],
                "exercises": [{"id": core_pick["id"], "name": core_pick["name"], "sets": 3, "reps": "30-45s", "intensity": "RPE 6"}],
            })
            used_ids.add(core_pick["id"])

    # ----- Cooldown / mobility -----
    cooldown_pick = pick_exercise(profile, equipment_set, "mobility", used_ids, primary=False)
    if cooldown_pick:
        blocks.append({
            "name": "Cooldown / Mobility",
            "intensity_band": "light",
            "est_minutes": 5,
            "items": [f"{cooldown_pick['name']} — 2 sets × 30-60s"],
            "exercises": [{"id": cooldown_pick["id"], "name": cooldown_pick["name"], "sets": 2, "reps": "30-60s", "intensity": "stretch"}],
        })

    # ----- Cardio finisher (only on training days where it makes sense) -----
    cardio_finisher_eligible = (
        cardio_finisher_cap > 0
        and profile.goal in ("fat_loss", "general_health")
        and "cardio" not in focus
        and any(mv in focus for mv in ["squat", "hinge", "push", "pull"])
    )
    if cardio_finisher_eligible:
        cardio_pick = pick_exercise(profile, equipment_set, "cardio", used_ids, primary=False)
        if cardio_pick:
            blocks.append({
                "name": "Cardio Finisher",
                "intensity_band": "moderate",
                "est_minutes": 15,
                "items": [f"{cardio_pick['name']} — 15 min steady pace"],
                "exercises": [{"id": cardio_pick["id"], "name": cardio_pick["name"], "sets": 1, "reps": "15 min", "intensity": "RPE 6-7"}],
            })
    elif profile.goal == "endurance" and "cardio" in focus:
        # cardio day: full cardio block instead of strength
        cardio_sch = CARDIO_SCHEMES[profile.experience]
        c_minutes = cardio_sch["z2_minutes"] if "Easy" in day_label or "Recovery" in day_label else \
                   cardio_sch["tempo_minutes"] if "Tempo" in day_label or "Intervals" in day_label else \
                   cardio_sch["long_minutes"]
        cardio_pick = pick_exercise(profile, equipment_set, "cardio", used_ids, primary=False)
        if cardio_pick:
            blocks.append({
                "name": "Cardio Block",
                "intensity_band": "moderate" if c_minutes > 20 else "light",
                "est_minutes": c_minutes,
                "items": [f"{cardio_pick['name']} — {c_minutes} min"],
                "exercises": [{"id": cardio_pick["id"], "name": cardio_pick["name"], "sets": 1, "reps": f"{c_minutes} min", "intensity": "Z2-Z3"}],
            })

    return blocks


def build_overlap_warning(week_plan: list[dict]) -> list[str]:
    """Check for overuse flags (same exercise multiple times in a week at high volume)."""
    return []


def build_overload(experience: str) -> dict:
    """Progressive overload guidance."""
    if experience == "beginner":
        return {
            "phase": "Linear progression",
            "rule": "Add 1 rep per set, then 5 lb (2.5 kg) next session. Stay in 8-12 rep range.",
            "weekly_target": "+5 lb / +1 rep",
            "checkpoint": "Re-test every 4 weeks; expect 10-20 lb / 4-6 reps gain on main lifts each month.",
            "deload": "Every 4 weeks, take 1 easy week (50% volume).",
        }
    if experience == "intermediate":
        return {
            "phase": "Double progression",
            "rule": "Push each main lift to top of rep range, then add 5 lb reset.",
            "weekly_target": "+1 set volume OR +5 lb load",
            "checkpoint": "Re-test every 6-8 weeks.",
            "deload": "Every 6 weeks, take 1 week at 60% volume.",
        }
    return {
        "phase": "Block periodization",
        "rule": "4-week blocks: accumulation (volume) → intensification (load) → realization (peaking) → deload.",
        "weekly_target": "+5-10 lb on main lifts at peak",
        "checkpoint": "Re-test every 8-12 weeks.",
        "deload": "Built into every block (week 4).",
    }


# ---------- RISK FLAGS ----------

def build_flags(profile: Profile, week_plan: list[dict]) -> list[dict]:
    flags = []

    # overtraining
    if profile.days_per_week >= 6 and profile.experience == "beginner":
        flags.append({
            "id": "beginner_overtraining",
            "severity": "high",
            "title": "Beginner with 6+ days/week",
            "description": "Beginners need recovery; 3-4 days is plenty. More days risks joint overuse and stalled progress.",
            "suggestion": "Cut to 3-4 days and use other days for walks, mobility, or rest.",
        })
    if profile.days_per_week == 7 and profile.experience != "advanced":
        flags.append({
            "id": "seven_day_training",
            "severity": "medium",
            "title": "Training every day with sub-advanced experience",
            "description": "Without an advanced recovery setup, daily training typically backfires.",
            "suggestion": "Try 5 training + 2 active recovery days, or 4 training + 3 walks.",
        })

    # session too short for plan
    if profile.session_minutes < 30 and profile.days_per_week <= 3:
        flags.append({
            "id": "session_too_short",
            "severity": "medium",
            "title": "Short sessions limit stimulus",
            "description": f"{profile.session_minutes}-min sessions can only fit so much; consider adding a day or trimming accessory volume.",
            "suggestion": "Either add a 4th day OR keep sessions at the warmup + 1 main lift + 1 accessory level.",
        })

    # session too long (recovery cost)
    if profile.session_minutes > 90 and profile.experience == "beginner":
        flags.append({
            "id": "session_too_long",
            "severity": "medium",
            "title": "Beginner with 90+ min sessions",
            "description": "Beginners don't need long sessions; long sessions increase dropout.",
            "suggestion": "Cap at 45-60 min; do more days instead.",
        })

    # injury + compound exercises
    for inj in profile.injuries:
        if inj in HARD_INJURY_AVOID:
            flags.append({
                "id": f"injury_{inj.replace(' ', '_')}",
                "severity": "high",
                "title": f"{inj.title()} flag — exercises auto-substituted",
                "description": f"Standard compound lifts may aggravate {inj}; we swapped to safer alternatives. Confirm with a clinician if the pain is acute.",
                "suggestion": "Use the substitution, but if pain is new, sharp, or radiating, see a physio before training.",
            })

    # endurance undertraining volume
    if profile.goal == "endurance" and profile.days_per_week < 3:
        flags.append({
            "id": "endurance_low_volume",
            "severity": "high",
            "title": "Endurance goal with fewer than 3 days/week",
            "description": "Endurance adaptations require repeat stimulus; below 3 sessions/week rarely yields improvements.",
            "suggestion": "Aim for 4+ cardio sessions; even 25-min easy runs count.",
        })

    # strength undertraining frequency
    if profile.goal in ("strength", "hypertrophy") and profile.days_per_week < 2:
        flags.append({
            "id": "strength_low_frequency",
            "severity": "high",
            "title": "Strength goal with 1 day/week or less",
            "description": "Strength gains require hitting each muscle 2+ times per week, even at low volume.",
            "suggestion": "Move to 3+ days/week minimum, even if shorter.",
        })

    # age considerations
    if profile.age is not None and profile.age >= 60 and profile.experience != "beginner":
        flags.append({
            "id": "older_athlete_volume",
            "severity": "low",
            "title": "60+ training with non-beginner load",
            "description": "Recovery is slower past 60; high-intensity work should still include longer warmups and at least one recovery day.",
            "suggestion": "Add a 5-10 min extra warmup; cap working sets at RPE 8 unless cleared.",
        })
    if profile.age is not None and profile.age < 18:
        flags.append({
            "id": "underage_athlete",
            "severity": "high",
            "title": "Under-18 trainee — needs supervision",
            "description": "Under-18 lifters should be coached on form, not max out, and avoid 1RM work until growth plates close.",
            "suggestion": "Stick to 3-5 sets per muscle, no max attempts. Get a strength coach if competing.",
        })

    # sex considerations (rough — defaults to supplements/hormonal caveats only)
    if profile.sex == "female":
        flags.append({
            "id": "iron_aware",
            "severity": "low",
            "title": "Iron + protein floors matter more for female athletes",
            "description": "Especially during heavy menstrual bleeding or in endurance phases; iron and protein targets are common blind spots.",
            "suggestion": "Hit at least 1.6 g/kg protein/day; consider a ferritin panel if energy is low.",
        })

    # no declared equipment
    if profile.equipment == ["bodyweight"]:
        flags.append({
            "id": "no_equipment_note",
            "severity": "low",
            "title": "Bodyweight-only plan",
            "description": "Progressions are limited; pull-ups, push-ups, and pistol progressions are the main stimuli.",
            "suggestion": "Add a pair of adjustable dumbbells or a band set to expand range.",
        })

    # location consistency
    if profile.location == "home" and "outdoor" in profile.equipment:
        flags.append({
            "id": "home_outdoor_mix",
            "severity": "low",
            "title": "Mixed home + outdoor days",
            "description": "Plan mixes home and outdoor sessions — this is fine, just be weather-aware.",
            "suggestion": "Use a treadmill or rower as backup on weather days.",
        })

    # repetitive stress — same main lift appearing 4+ times in week
    main_lift_counts: dict[str, int] = {}
    for day in week_plan:
        for block in day["blocks"]:
            if block["name"].startswith("Main Lift"):
                for ex in block.get("exercises", []):
                    main_lift_counts[ex["id"]] = main_lift_counts.get(ex["id"], 0) + 1
    for ex_id, count in main_lift_counts.items():
        if count >= 4:
            ex_name = next(e["name"] for e in EXERCISES if e["id"] == ex_id)
            flags.append({
                "id": "repetitive_main_lift",
                "severity": "medium",
                "title": f"{ex_name} appears {count}× this week as a main lift",
                "description": "Same heavy lift >3×/week can fatigue joints and stall progress.",
                "suggestion": "Rotate in a variation (front squat → back squat, bench → DB bench) to spread stress.",
            })

    # session-minute vs total volume feasibility
    total_main_sets = 0
    for day in week_plan:
        for block in day["blocks"]:
            if block["name"].startswith("Main Lift"):
                for ex in block.get("exercises", []):
                    total_main_sets += ex["sets"]
    avg_main_sets_per_session = total_main_sets / max(1, sum(1 for d in week_plan if d["focus"]))
    if avg_main_sets_per_session > 12:
        flags.append({
            "id": "high_volume_session",
            "severity": "medium",
            "title": "High main-lift volume per session",
            "description": f"Averaging {avg_main_sets_per_session:.1f} main sets/session can exceed recovery capacity.",
            "suggestion": "Trim accessory volume or split sessions (e.g. squat + push on one day, hinge + pull on another).",
        })

    # Sort: high > medium > low, then by id
    severity_order = {"high": 0, "medium": 1, "low": 2}
    flags.sort(key=lambda f: (severity_order[f["severity"]], f["id"]))
    return flags


# ---------- SCORE ----------

def compute_score(profile: Profile, week_plan: list[dict], flags: list[dict]) -> dict:
    axes = {}

    # Profile completeness (10 axes)
    completeness_factors = [
        bool(profile.experience and profile.experience != "beginner" or profile.experience_conf > 0.5),
        profile.goal_conf > 0.5,
        profile.days_per_week > 0,
        profile.session_minutes > 0,
        bool(profile.equipment and profile.equipment != ["bodyweight"]),
        True,  # injuries is optional but having it explicit is good
        bool(profile.location),
        True,  # raw_text is always present
        True,
        True,
    ]
    axes["profile_completeness"] = round(100 * sum(completeness_factors) / 10)

    # Plan density: avg items per training day (warmup + main + accessory + core + cooldown + cardio)
    training_days = [d for d in week_plan if d["focus"]]
    items_per_day = []
    for d in training_days:
        cnt = 0
        for b in d["blocks"]:
            cnt += len(b.get("items", []))
        items_per_day.append(cnt)
    avg_items = sum(items_per_day) / max(1, len(items_per_day))
    axes["plan_density"] = round(min(100, max(0, avg_items * 18)))  # ~5 items -> 90; 7 items -> 100 (capped)

    # Experience-appropriate (uses correct scheme + split + RPE band)
    exp_match_score = 0
    # days_per_week sweet spot per experience
    sweet = {"beginner": [2, 3, 4], "intermediate": [3, 4, 5], "advanced": [4, 5, 6]}.get(profile.experience, [3, 4])
    if profile.days_per_week in sweet:
        exp_match_score += 50
    elif profile.days_per_week in [sweet[0]-1, sweet[-1]+1]:
        exp_match_score += 30
    # session length sanity
    sl_sweet = {"beginner": [30, 45, 60], "intermediate": [45, 60, 75], "advanced": [60, 75, 90]}.get(profile.experience, [45, 60])
    if sl_sweet[0] <= profile.session_minutes <= sl_sweet[-1]:
        exp_match_score += 30
    elif profile.session_minutes > 0:
        exp_match_score += 15
    # equipment match
    if profile.equipment and set(profile.equipment) != {"bodyweight"}:
        exp_match_score += 20
    elif profile.experience == "beginner":
        exp_match_score += 10  # bodyweight is OK for beginners
    axes["experience_fit"] = min(100, exp_match_score)

    # Goal alignment: does the split match the goal?
    split = SPLITS.get((profile.goal, profile.experience), "full_body_3x")
    goal_split_score = {
        "strength": {"full_body_3x": 60, "upper_lower_4x": 80, "ppl_5x": 100},
        "hypertrophy": {"full_body_3x": 70, "upper_lower_4x": 90, "ppl_5x": 100},
        "fat_loss": {"full_body_3x": 80, "upper_lower_4x": 90},
        "endurance": {"cardio_3x": 80, "cardio_4x": 90, "cardio_5x": 100},
        "general_health": {"full_body_3x": 90, "upper_lower_4x": 80, "mobility_5x": 75},
        "mobility": {"mobility_5x": 100, "full_body_3x": 60},
        "athletic": {"upper_lower_4x": 90, "full_body_3x": 70, "ppl_5x": 80},
    }
    axes["goal_alignment"] = goal_split_score.get(profile.goal, {}).get(split, 70)

    # Recovery / rest day ratio
    rest_days = 7 - len(training_days)
    if profile.experience == "beginner" and rest_days >= 3:
        axes["recovery_balance"] = 100
    elif profile.experience == "beginner" and rest_days >= 2:
        axes["recovery_balance"] = 80
    elif rest_days >= 1:
        axes["recovery_balance"] = 90
    else:
        axes["recovery_balance"] = 50

    # Risk hygiene: deduct for high/medium flags
    high = sum(1 for f in flags if f["severity"] == "high")
    medium = sum(1 for f in flags if f["severity"] == "medium")
    low = sum(1 for f in flags if f["severity"] == "low")
    axes["risk_hygiene"] = max(0, 100 - high * 18 - medium * 8 - low * 3)

    weights = {
        "profile_completeness": 15,
        "plan_density": 15,
        "experience_fit": 18,
        "goal_alignment": 22,
        "recovery_balance": 12,
        "risk_hygiene": 18,
    }
    total = sum(axes[k] * weights[k] for k in weights) / 100
    overall = round(total)

    if overall >= 85:
        verdict = "ready_to_train"
    elif overall >= 70:
        verdict = "mostly_ready"
    elif overall >= 55:
        verdict = "needs_review"
    elif overall >= 40:
        verdict = "tight_but_usable"
    else:
        verdict = "plan_revise"

    return {
        "overall": overall,
        "verdict": verdict,
        "axes": axes,
        "weights": weights,
        "band": verdict_label(verdict),
    }


def verdict_label(v: str) -> str:
    return {
        "ready_to_train": "Ready to train",
        "mostly_ready": "Mostly ready",
        "needs_review": "Needs review",
        "tight_but_usable": "Tight but usable",
        "plan_revise": "Plan needs revision",
    }.get(v, v)


# ---------- DB ----------

DB_PATH_DEFAULT = "workout_weaver.db"

def init_db(path: str = DB_PATH_DEFAULT) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            brief TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            rating INTEGER,
            notes TEXT
        )
    """)
    conn.commit()
    return conn


def save_session(conn: sqlite3.Connection, brief: str, profile: Profile, result: dict,
                 rating: Optional[int] = None, notes: Optional[str] = None) -> int:
    cur = conn.execute(
        "INSERT INTO sessions (created_at, brief, profile_json, result_json, rating, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), brief, json.dumps(profile.as_dict()), json.dumps(result), rating, notes)
    )
    conn.commit()
    return cur.lastrowid


def update_session(conn: sqlite3.Connection, sid: int, rating: Optional[int] = None, notes: Optional[str] = None):
    sets, vals = [], []
    if rating is not None:
        sets.append("rating = ?")
        vals.append(rating)
    if notes is not None:
        sets.append("notes = ?")
        vals.append(notes)
    if not sets:
        return
    vals.append(sid)
    conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()


def list_sessions(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    cur = conn.execute("SELECT id, created_at, brief, rating, notes FROM sessions ORDER BY id DESC LIMIT ?", (limit,))
    return [{"id": r[0], "created_at": r[1], "brief": r[2], "rating": r[3], "notes": r[4]} for r in cur.fetchall()]


def get_session(conn: sqlite3.Connection, sid: int) -> Optional[dict]:
    cur = conn.execute("SELECT brief, profile_json, result_json, rating, notes, created_at FROM sessions WHERE id = ?", (sid,))
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": sid,
        "brief": row[0],
        "profile": json.loads(row[1]),
        "result": json.loads(row[2]),
        "rating": row[3],
        "notes": row[4],
        "created_at": row[5],
    }


# ---------- EXPORT ----------

def to_markdown(profile: Profile, result: dict) -> str:
    plan = result["week_plan"]
    flags = result["risk_flags"]
    score = result["score"]
    overload = result["overload"]

    lines = []
    lines.append(f"# workout-weaver — Weekly Plan")
    lines.append("")
    lines.append(f"**Generated:** {datetime.utcnow().isoformat()} UTC")
    lines.append(f"**Split:** `{result['split']}` ({plan[0]['day_label']} … {plan[-1]['day_label']})")
    lines.append("")
    lines.append("## Profile")
    lines.append("")
    p = profile
    lines.append(f"- **Experience:** {p.experience} (confidence {p.experience_conf:.0%})")
    lines.append(f"- **Goal:** {p.goal} (confidence {p.goal_conf:.0%})")
    lines.append(f"- **Days/week:** {p.days_per_week}")
    lines.append(f"- **Session length:** {p.session_minutes} min")
    lines.append(f"- **Equipment:** {', '.join(p.equipment)}")
    lines.append(f"- **Location:** {p.location}")
    if p.injuries:
        lines.append(f"- **Injuries/cautions:** {', '.join(p.injuries)}")
    else:
        lines.append(f"- **Injuries/cautions:** none flagged")
    if p.age is not None:
        lines.append(f"- **Age:** {p.age}")
    lines.append("")

    lines.append("## Score")
    lines.append("")
    lines.append(f"- **Overall:** {score['overall']} / 100 — {score['band']}")
    for axis, val in score["axes"].items():
        lines.append(f"- **{axis.replace('_', ' ').title()}** ({score['weights'][axis]}%): {val}/100")
    lines.append("")

    lines.append("## Weekly Plan")
    lines.append("")
    for day in plan:
        lines.append(f"### {day['day_label']}")
        if day["is_rest"]:
            lines.append(f"- Rest day. Sleep, walk, mobility if you want.")
        else:
            lines.append(f"- Focus: {', '.join(day['focus'])}")
            for blk in day["blocks"]:
                lines.append(f"- **{blk['name']}** (~{blk['est_minutes']} min, {blk['intensity_band']})")
                for item in blk["items"]:
                    lines.append(f"  - {item}")
        lines.append("")

    lines.append("## Progressive Overload")
    lines.append("")
    lines.append(f"- **Phase:** {overload['phase']}")
    lines.append(f"- **Rule:** {overload['rule']}")
    lines.append(f"- **Weekly target:** {overload['weekly_target']}")
    lines.append(f"- **Re-test:** {overload['checkpoint']}")
    lines.append(f"- **Deload:** {overload['deload']}")
    lines.append("")

    if flags:
        lines.append("## Risk Flags")
        lines.append("")
        for f in flags:
            lines.append(f"### [{f['severity'].upper()}] {f['title']}")
            lines.append(f"- {f['description']}")
            lines.append(f"- **Action:** {f['suggestion']}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Generated by workout-weaver. Not medical advice — adapt to how your body responds._")
    return "\n".join(lines)


# ---------- PARSE / ENTRYPOINT ----------

def parse_brief(text: str) -> Profile:
    """Convert free-form text into a structured Profile using keyword + regex detection."""
    experience, exp_conf = detect_experience(text)
    goal, goal_conf = detect_goal(text)
    days = detect_days_per_week(text)
    minutes = detect_session_minutes(text)
    equipment = detect_equipment(text)
    injuries = detect_injuries(text)
    location = detect_location(text)
    age = detect_age(text)
    sex = detect_sex(text)

    return Profile(
        experience=experience, experience_conf=exp_conf,
        goal=goal, goal_conf=goal_conf,
        days_per_week=days, session_minutes=minutes,
        equipment=equipment, injuries=injuries, location=location,
        age=age, sex=sex, raw_text=text,
    )


def build_plan_from_brief(text: str) -> dict:
    """Public entrypoint: parse + generate."""
    profile = parse_brief(text)
    result = generate_week(profile)
    return {"profile": profile.as_dict(), "result": result}
