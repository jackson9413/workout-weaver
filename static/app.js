const SAMPLES = {
  "beginner-home": "I'm 35, beginner, no equipment at home, 3 days/week, sessions around 30 min. Goal is general health and to lose a bit of weight. No injuries. I'd like easy runs or walks too.",
  "intermediate-gym": "Intermediate lifter, 4 days/week, ~60 min at a commercial gym. Squat, bench, deadlift. Goal is strength. I have a barbell, dumbbells, machines, cables. No injuries.",
  "hypertrophy-5day": "Advanced, 5 days/week, 75 min, want hypertrophy (ppl). Full gym access. No injuries, age 28.",
  "endurance": "Beginner runner, 5 days/week, 30-45 min. Goal is to run a 5k in 8 weeks. I have a treadmill and outdoor access. No injuries.",
  "injured-older": "I'm 63, intermediate, 3 days/week, 60 min at home gym with dumbbells. Goal is general health. I have lower back pain and shoulder impingement — I avoid barbell squats and overhead work.",
  "fatloss-home": "Female, 38, intermediate, 4 days/week, 45 min, fat loss goal. Dumbbells + bands at home, plus a treadmill. No injuries.",
};

function pillStatus(msg, cls = "") {
  const s = document.getElementById("status");
  s.textContent = msg;
  s.className = "pill" + (cls ? " " + cls : "");
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function severityClass(sev) {
  return "flag " + sev;
}

function verdictClass(v) {
  return "verdict " + v;
}

function bandColor(o) {
  if (o >= 85) return "ready_to_train";
  if (o >= 70) return "mostly_ready";
  if (o >= 55) return "needs_review";
  if (o >= 40) return "tight_but_usable";
  return "plan_revise";
}

function renderAxes(score) {
  const wrap = el("div");
  for (const [k, v] of Object.entries(score.axes)) {
    const row = el("div", "axis");
    row.appendChild(el("span", "name", k.replace(/_/g, " ")));
    const bar = el("div", "bar");
    bar.appendChild(el("div")).style.width = v + "%";
    const inner = bar.firstChild;
    inner.style.background = v >= 75 ? "var(--green)" : v >= 50 ? "var(--accent)" : "var(--amber)";
    row.appendChild(bar);
    row.appendChild(el("span", "val", v + "%"));
    wrap.appendChild(row);
  }
  return wrap;
}

function renderProfile(profile) {
  const grid = el("div", "profile-grid");
  function row(k, v) {
    grid.appendChild(el("div", "k", k));
    grid.appendChild(el("div", "", v));
  }
  row("Experience", `${profile.experience} (${(profile.experience_conf * 100).toFixed(0)}% conf)`);
  row("Goal", `${profile.goal} (${(profile.goal_conf * 100).toFixed(0)}% conf)`);
  row("Days/week", profile.days_per_week);
  row("Session length", profile.session_minutes + " min");
  row("Equipment", profile.equipment.join(", "));
  row("Location", profile.location);
  row("Injuries", profile.injuries.length ? profile.injuries.join(", ") : "none");
  if (profile.age != null) row("Age", profile.age);
  if (profile.sex) row("Sex", profile.sex);
  return grid;
}

function renderDay(day) {
  const card = el("div", "day" + (day.is_rest ? " rest" : ""));
  const head = el("div", "day-head");
  head.appendChild(el("h4", "", day.day_label));
  if (!day.is_rest) head.appendChild(el("span", "day-focus", day.focus.join(" · ")));
  card.appendChild(head);
  if (day.is_rest) {
    card.appendChild(el("p", "", "Rest day. Sleep, walk, mobility if you want."));
  } else {
    for (const block of day.blocks) {
      const b = el("div", "block");
      b.appendChild(el("div", "block-name", block.name + " · " + block.est_minutes + " min · " + block.intensity_band));
      b.appendChild(el("div", "block-meta", `Intensity band: ${block.intensity_band}`));
      const ul = el("ul");
      for (const item of block.items) ul.appendChild(el("li", "", item));
      b.appendChild(ul);
      card.appendChild(b);
    }
  }
  return card;
}

function renderResult(data) {
  const out = document.getElementById("result");
  out.innerHTML = "";
  const profile = data.profile;
  const result = data.result;
  const score = result.score;
  const split = result.split;
  const sessions = result.week_plan;

  // Score header
  const scoreHeader = el("div");
  scoreHeader.style.display = "flex";
  scoreHeader.style.alignItems = "center";
  scoreHeader.style.gap = "12px";
  scoreHeader.style.flexWrap = "wrap";
  scoreHeader.appendChild(el("span", "score-band", score.overall + " / 100"));
  const verdict = el("span", verdictClass(score.verdict), score.band);
  scoreHeader.appendChild(verdict);
  scoreHeader.appendChild(el("span", "pill", split));
  scoreHeader.appendChild(el("a", "", "export .md").setAttribute === undefined ? null : null);
  if (data.session_id) {
    const aMd = el("a");
    aMd.href = `/api/export/${data.session_id}?fmt=md`;
    aMd.textContent = "↓ export .md";
    aMd.target = "_blank";
    scoreHeader.appendChild(aMd);
    const aJ = el("a");
    aJ.href = `/api/export/${data.session_id}?fmt=json`;
    aJ.textContent = "↓ export .json";
    aJ.target = "_blank";
    scoreHeader.appendChild(aJ);
  }
  out.appendChild(scoreHeader);

  // Axis bars
  out.appendChild(el("h3", "", "Score Breakdown"));
  out.appendChild(renderAxes(score));

  // Profile
  out.appendChild(el("h3", "", "Profile"));
  out.appendChild(renderProfile(profile));

  // Overload
  out.appendChild(el("h3", "", "Progressive Overload"));
  const ov = result.overload;
  const ovWrap = el("div", "profile-grid");
  for (const [k, v] of Object.entries(ov)) {
    ovWrap.appendChild(el("div", "k", k.replace(/_/g, " ")));
    ovWrap.appendChild(el("div", "", v));
  }
  out.appendChild(ovWrap);

  // Week plan
  out.appendChild(el("h3", "", "Weekly Plan"));
  for (const day of sessions) {
    out.appendChild(renderDay(day));
  }

  // Risk flags
  out.appendChild(el("h3", "", `Risk Flags (${result.risk_flags.length})`));
  if (result.risk_flags.length === 0) {
    out.appendChild(el("p", "muted", "No flags — plan looks balanced for the brief."));
  } else {
    for (const f of result.risk_flags) {
      const card = el("div", severityClass(f.severity));
      card.appendChild(el("div", "flag-title", `[${f.severity.toUpperCase()}] ${f.title}`));
      card.appendChild(el("div", "flag-desc", f.description));
      card.appendChild(el("div", "flag-sug", "→ " + f.suggestion));
      out.appendChild(card);
    }
  }

  // Rate + note
  if (data.session_id) {
    out.appendChild(el("h3", "", "Reflect on this plan"));
    const wrap = el("div");
    wrap.style.display = "flex";
    wrap.style.gap = "10px";
    wrap.style.alignItems = "center";
    wrap.style.flexWrap = "wrap";
    const starLabel = el("label", "", "Stars:");
    wrap.appendChild(starLabel);
    const sel = document.createElement("select");
    sel.id = "rating";
    sel.style.background = "var(--pill-bg)";
    sel.style.color = "var(--text)";
    sel.style.border = "1px solid var(--border)";
    sel.style.borderRadius = "6px";
    sel.style.padding = "4px 6px";
    [null, 1, 2, 3, 4, 5].forEach(n => {
      const opt = document.createElement("option");
      opt.value = n === null ? "" : String(n);
      opt.textContent = n === null ? "—" : "★".repeat(n);
      sel.appendChild(opt);
    });
    wrap.appendChild(sel);
    const note = document.createElement("input");
    note.id = "notes";
    note.placeholder = "notes (optional)";
    note.style.flex = "1";
    note.style.minWidth = "200px";
    note.style.background = "var(--pill-bg)";
    note.style.color = "var(--text)";
    note.style.border = "1px solid var(--border)";
    note.style.borderRadius = "6px";
    note.style.padding = "5px 8px";
    wrap.appendChild(note);
    const saveBtn = el("button", "secondary", "Save");
    saveBtn.onclick = async () => {
      const rating = sel.value ? Number(sel.value) : null;
      const notes = note.value || null;
      const res = await fetch(`/api/sessions/${data.session_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating, notes }),
      });
      if (res.ok) {
        pillStatus("Saved ★ " + (rating || "—") + (notes ? " · noted" : ""));
        loadSessions();
      } else {
        pillStatus("Save failed");
      }
    };
    wrap.appendChild(saveBtn);
    out.appendChild(wrap);
  }
}

async function loadSessions() {
  const res = await fetch("/api/sessions?limit=10");
  const data = await res.json();
  const wrap = document.getElementById("sessions");
  wrap.innerHTML = "";
  if (!data.sessions || data.sessions.length === 0) {
    wrap.appendChild(el("p", "muted", "None yet."));
    return;
  }
  for (const s of data.sessions) {
    const card = el("div", "day");
    const head = el("div", "day-head");
    head.appendChild(el("h4", "", `#${s.id}`));
    head.appendChild(el("span", "day-focus", new Date(s.created_at).toLocaleString()));
    card.appendChild(head);
    const briefShort = s.brief.split("\n")[0].slice(0, 80) + (s.brief.length > 80 ? "…" : "");
    card.appendChild(el("p", "", briefShort));
    const meta = el("div", "session-meta");
    if (s.rating) meta.appendChild(el("span", "pill", "★".repeat(s.rating)));
    if (s.notes) meta.appendChild(el("span", "pill", s.notes.slice(0, 40)));
    const viewBtn = el("button", "secondary", "View");
    viewBtn.style.padding = "4px 8px";
    viewBtn.style.fontSize = "11px";
    viewBtn.onclick = async () => {
      const r = await fetch(`/api/sessions/${s.id}`);
      const data = await r.json();
      renderResult({ session_id: data.id, profile: data.profile, result: data.result });
      document.getElementById("brief").value = data.brief;
      pillStatus("Loaded session #" + data.id);
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
    card.appendChild(viewBtn);
    card.appendChild(meta);
    wrap.appendChild(card);
  }
}

async function runPlan() {
  const brief = document.getElementById("brief").value.trim();
  if (!brief) { pillStatus("Add a brief first"); return; }
  pillStatus("Generating…");
  const go = document.getElementById("go");
  go.disabled = true;
  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brief }),
    });
    if (!res.ok) {
      pillStatus("Error: " + res.status);
      return;
    }
    const data = await res.json();
    renderResult(data);
    pillStatus("Plan ready (#" + data.session_id + ")");
    loadSessions();
  } finally {
    go.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("go").onclick = runPlan;
  document.getElementById("clear").onclick = () => {
    document.getElementById("brief").value = "";
    pillStatus("No plan yet");
    document.getElementById("result").innerHTML = '<div class="empty">Run a brief on the left to see your weekly plan here.</div>';
  };
  document.querySelectorAll("[data-sample]").forEach(b => {
    b.onclick = () => {
      document.getElementById("brief").value = SAMPLES[b.getAttribute("data-sample")] || "";
      pillStatus("Sample loaded — click Generate");
    };
  });
  loadSessions();
});
