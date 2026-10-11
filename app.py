"""Flask backend for workout-weaver.

Endpoints:
  GET  /                       — UI
  POST /api/analyze            — body: {brief: str}; returns profile + week plan + score + flags
  GET  /api/sessions           — list recent sessions
  GET  /api/sessions/<id>      — get one session
  PATCH /api/sessions/<id>     — body: {rating?: 1-5, notes?: str}
  GET  /api/export/<id>?fmt=md|json — export a session
  GET  /api/health
"""

import os

from flask import Flask, jsonify, render_template, request, Response
from engine import (
    init_db, save_session, update_session, list_sessions, get_session,
    build_plan_from_brief, parse_brief, to_markdown, DB_PATH_DEFAULT,
)


app = Flask(__name__)
DB_PATH = os.environ.get("WORKOUT_WEAVER_DB", DB_PATH_DEFAULT)
conn = init_db(DB_PATH)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "tool": "workout-weaver"})


@app.route("/api/analyze", methods=["POST"])
def analyze():
    body = request.get_json(force=True, silent=True) or {}
    brief = (body.get("brief") or "").strip()
    if not brief:
        return jsonify({"error": "Missing brief"}), 400

    # safety: cap brief length
    if len(brief) > 8000:
        brief = brief[:8000]

    out = build_plan_from_brief(brief)
    sid = save_session(conn, brief, parse_brief(brief), out["result"])
    out["session_id"] = sid
    return jsonify(out)


@app.route("/api/sessions")
def sessions():
    return jsonify({"sessions": list_sessions(conn, limit=int(request.args.get("limit", 20)))})


@app.route("/api/sessions/<int:sid>", methods=["GET"])
def session_detail(sid):
    s = get_session(conn, sid)
    if not s:
        return jsonify({"error": "Not found"}), 404
    return jsonify(s)


@app.route("/api/sessions/<int:sid>", methods=["PATCH"])
def session_update(sid):
    body = request.get_json(force=True, silent=True) or {}
    rating = body.get("rating")
    notes = body.get("notes")
    if rating is not None and (rating < 1 or rating > 5):
        return jsonify({"error": "rating must be 1-5"}), 400
    s = get_session(conn, sid)
    if not s:
        return jsonify({"error": "Not found"}), 404
    update_session(conn, sid, rating=rating, notes=notes)
    return jsonify({"ok": True})


@app.route("/api/export/<int:sid>")
def export(sid):
    s = get_session(conn, sid)
    if not s:
        return jsonify({"error": "Not found"}), 404
    fmt = request.args.get("fmt", "md").lower()
    profile = parse_brief(s["brief"])
    if fmt == "md":
        md = to_markdown(profile, s["result"])
        return Response(md, mimetype="text/markdown",
                        headers={"Content-Disposition": f"attachment; filename=workout-weaver-{sid}.md"})
    if fmt == "json":
        import json
        return Response(json.dumps({"profile": s["profile"], "result": s["result"]}, indent=2),
                        mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=workout-weaver-{sid}.json"})
    return jsonify({"error": "fmt must be md or json"}), 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=False)
