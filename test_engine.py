"""Smoke tests for workout-weaver engine.

Run: python3 test_engine.py
"""

from engine import (
    build_plan_from_brief, parse_brief, generate_week, Profile,
    detect_equipment, detect_goal, detect_experience, detect_injuries,
    detect_age, detect_days_per_week, detect_session_minutes,
    to_markdown, save_session, list_sessions, get_session, update_session, init_db,
)


def test_basic_extraction():
    p = parse_brief("I'm 30, intermediate, 4 days/week, 60 min at a gym. Squat, bench, deadlift. Goal is strength. Full gym. No injuries.")
    assert p.experience == "intermediate", p
    assert p.goal == "strength", p
    assert p.days_per_week == 4
    assert p.session_minutes == 60
    assert "full_gym" in p.equipment
    assert not p.injuries
    print("✓ basic_extraction")


def test_injury_substitution():
    p = parse_brief("I'm 60, intermediate, 3 days/week, dumbbells at home. Strength. Lower back pain and shoulder impingement.")
    out = build_plan_from_brief(p.raw_text)
    # Confirm no bench press / back squat / deadlift in main lifts
    main_lift_names = []
    for day in out["result"]["week_plan"]:
        for blk in day["blocks"]:
            if blk["name"].startswith("Main Lift"):
                for ex in blk["exercises"]:
                    main_lift_names.append(ex["name"])
    forbidden = ["Bench Press", "Back Squat", "Conventional Deadlift", "Standing Overhead Press", "Pull-up"]
    for name in main_lift_names:
        assert name not in forbidden, f"Found injury-contraindicated lift: {name}"
    # Should have injury flags
    flag_ids = [f["id"] for f in out["result"]["risk_flags"]]
    assert any("injury_" in fid for fid in flag_ids), flag_ids
    print("✓ injury_substitution")


def test_age_detection():
    assert detect_age("I'm 63, intermediate") == 63
    assert detect_age("I am 28 years old") == 28
    assert detect_age("age 35") == 35
    assert detect_age("45 yo") == 45
    assert detect_age("nothing useful") is None
    print("✓ age_detection")


def test_equipment_detection():
    assert "full_gym" in detect_equipment("I have a full gym, all equipment, commercial")
    assert detect_equipment("at home with dumbbells + bands") == ["dumbbells", "bands"]
    assert detect_equipment("nothing, no equipment at home") == ["bodyweight"]
    # exercise mentions should NOT trigger equipment detection
    assert "barbell" not in detect_equipment("I avoid barbell squats and overhead work")
    print("✓ equipment_detection")


def test_days_per_week():
    assert detect_days_per_week("3 days/week") == 3
    assert detect_days_per_week("four days a week") == 4
    assert detect_days_per_week("5x per week") == 5
    assert detect_days_per_week("every day") == 7
    print("✓ days_per_week")


def test_session_minutes():
    assert detect_session_minutes("45 min sessions") == 45
    assert detect_session_minutes("60 minute") == 60
    assert detect_session_minutes("1.5 hours") == 90
    print("✓ session_minutes")


def test_score_bands():
    out = build_plan_from_brief("Beginner, 3 days/week, 30 min, bodyweight, general health, no injuries.")
    s = out["result"]["score"]
    assert 0 <= s["overall"] <= 100
    assert s["verdict"] in {"ready_to_train", "mostly_ready", "needs_review", "tight_but_usable", "plan_revise"}
    print("✓ score_bands")


def test_split_selection():
    out = build_plan_from_brief("Advanced, 5 days/week, 75 min, hypertrophy PPL, full gym.")
    assert out["result"]["split"] in {"ppl_5x", "upper_lower_4x"}
    out = build_plan_from_brief("Beginner, 5 days/week, 30 min, treadmill, run a 5k.")
    assert "cardio" in out["result"]["split"]
    print("✓ split_selection")


def test_export_markdown():
    out = build_plan_from_brief("Intermediate, 4 days/week, 60 min, gym, strength. Full gym.")
    profile = parse_brief(out["profile"]["raw_text"] if "raw_text" in out["profile"] else "placeholder")
    # Use the profile directly from the result
    from engine import Profile
    from dataclasses import asdict
    prof_dict = out["profile"]
    profile2 = Profile(**{k: v for k, v in prof_dict.items()})
    md = to_markdown(profile2, out["result"])
    assert "# workout-weaver — Weekly Plan" in md
    assert "## Score" in md
    assert "## Weekly Plan" in md
    assert "## Progressive Overload" in md
    print("✓ export_markdown")


def test_db_roundtrip(tmp_db="test_workout_weaver.db"):
    import os
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    conn = init_db(tmp_db)
    prof = parse_brief("Beginner, 3 days/week, 30 min, general health, no equipment.")
    out = build_plan_from_brief(prof.raw_text)
    sid = save_session(conn, prof.raw_text, prof, out["result"], rating=4, notes="nice")
    s = get_session(conn, sid)
    assert s["rating"] == 4
    assert s["notes"] == "nice"
    update_session(conn, sid, notes="updated")
    s2 = get_session(conn, sid)
    assert s2["notes"] == "updated"
    sessions = list_sessions(conn)
    assert len(sessions) >= 1
    os.remove(tmp_db)
    print("✓ db_roundtrip")


if __name__ == "__main__":
    test_basic_extraction()
    test_injury_substitution()
    test_age_detection()
    test_equipment_detection()
    test_days_per_week()
    test_session_minutes()
    test_score_bands()
    test_split_selection()
    test_export_markdown()
    test_db_roundtrip()
    print("\nAll tests passed.")
