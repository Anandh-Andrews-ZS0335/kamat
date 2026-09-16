// Onboard a bank: start the Onboarding Agent, watch it reason, review its proposal, approve, and test it.
const O = { id: null, poll: null, s: null, events: [], feed: "scores" };
const STATUS_PILL = { running: "pri", proposed: "good", needs_review: "warn", failed: "bad", approved: "good", rejected: "bad" };
const STATUS_TEXT = { running: "agent working", proposed: "ready to approve", needs_review: "needs a person", failed: "failed",
  approved: "approved", rejected: "rejected" };
const ENDPOINT_TEXT = { health_endpoint: "Status and business date", catalogue_endpoint: "Feed catalogue",
  release_endpoint: "Receives approved actions", simulation_endpoint: "Closes the business day (simulation)" };
const FEED_TEXT = { scores: "Risk scores", accounts: "Accounts", members: "Customers", consents: "Contact permissions",
  queue_history: "Collections history", outcomes: "Outcomes" };

async function loadSessions() {
  const list = await api("/api/onboarding");
  $("#sessions").innerHTML = list.length ? list.map((s) => `<button data-id="${esc(s.session_id)}" class="${s.session_id === O.id ? "on" : ""}">
      <b>${esc(s.institution_name || s.base_url)}</b><span class="pill ${STATUS_PILL[s.status] || ""}">${esc(STATUS_TEXT[s.status] || s.status)}</span>
      <small>${esc(new Date(s.created_at).toLocaleString())} · ${esc(s.created_by || "")}</small></button>`).join("")
    : `<div class="muted">No banks onboarded yet.</div>`;
  $$("#sessions button").forEach((b) => b.onclick = () => open(b.dataset.id));
  return list;
}

async function start() {
  const base_url = $("#baseUrl").value.trim();
  $("#startBtn").disabled = true;
  try {
    const r = await api("/api/onboarding", { method: "POST", body: JSON.stringify({ base_url }) });
    toast("The Onboarding Agent is reading the bank's API");
    await loadSessions(); open(r.session_id);
  } catch (e) { toast(e.message, "bad"); }
  finally { $("#startBtn").disabled = false; }
}

async function open(id) {
  clearInterval(O.poll); O.poll = null;
  O.id = id; O.events = [];
  history.replaceState(null, "", `/admin/onboarding?session=${encodeURIComponent(id)}`);
  $$("#sessions button").forEach((b) => b.classList.toggle("on", b.dataset.id === id));
  await refresh();
  if (O.s && O.s.status === "running") O.poll = setInterval(refresh, 1500);
}

async function refresh() {
  const [s, ev] = await Promise.all([api(`/api/onboarding/${O.id}`), api(`/api/onboarding/${O.id}/trace`)]);
  O.s = s; O.events = ev;
  render();
  if (s.status !== "running" && O.poll) { clearInterval(O.poll); O.poll = null; loadSessions(); }
}

function kindText(col) {
  if (!col) return "";
  if (col.kind === "number" || col.kind === "integer") return `${col.kind} ${col.min} – ${col.max}`;
  if (col.kind === "date") return `date ${col.min} – ${col.max}`;
  if (col.values) return `code: ${col.values.slice(0, 5).join(", ")}${col.values.length > 5 ? "…" : ""}`;
  return `text, shapes ${(col.shapes || []).join(" ")}`;
}

function render() {
  const s = O.s;
  Shell.setContext(`${s.institution_name || s.base_url} · ${STATUS_TEXT[s.status] || s.status}`);
  const prop = s.proposal || {}, plan = s.plan || {}, score = prop.score || {};
  const reasons = plan.reasons || {};
  const isAdmin = Shell.user && Shell.user.role === "admin";

  const actions = [
    s.status === "proposed" && isAdmin ? `<button class="btn good" id="approveBtn">Approve and save</button>` : "",
    ["proposed", "needs_review", "failed"].includes(s.status) && isAdmin ? `<button class="btn bad" id="rejectBtn">Reject</button>` : "",
    s.status === "approved" && isAdmin ? `<button class="btn primary" id="testBtn">Run a test day for this bank</button>` : "",
    s.test_run_id ? `<a class="btn" href="/admin?run=${encodeURIComponent(s.test_run_id)}">Open test run →</a>` : "",
  ].join("");

  const thinking = O.events.map((e) => {
    const t = new Date(e.ts).toLocaleTimeString(undefined, { hour12: false });
    const label = e.kind === "llm" ? `<span class="brain">LLM decides</span>` : e.kind === "decision" ? `<span class="pill violet">reasoning</span>`
      : e.kind === "handoff" ? `<span class="pill">handoff</span>` : `<span class="rules">${esc(e.kind === "api" ? "api" : "tool")}</span>`;
    return `<div class="ev ${esc(e.kind)}"><span class="num muted">${t}</span><div>${label} ${e.tool ? `<span class="kbd muted">${esc(e.tool)}</span>` : ""}
      <div class="msg">${esc(e.message || "")}</div></div></div>`;
  }).join("") || `<div class="muted">Waiting for the agent…</div>`;

  const running = s.status === "running";
  let body = `<div class="panel"><div class="panel-b head">
      <div><span class="eyebrow">${esc(s.session_id)}</span><h2>${esc(s.institution_name || "Reading the bank's API…")}</h2>
        <span class="muted mono">${esc(s.base_url)}</span></div>
      <span class="pill ${STATUS_PILL[s.status] || ""}">${esc(STATUS_TEXT[s.status] || s.status)}</span>
      ${s.attempts ? `<span class="chip">${s.attempts === 1 ? "mapped in one attempt" : "mapped after one rewrite"}</span>` : ""}
      <div class="spacer"></div><div style="display:flex;gap:8px;flex-wrap:wrap">${actions}</div>
    </div>
    ${s.error ? `<div class="panel-b" style="padding-top:0"><div class="callout bad">${esc(s.error)}</div></div>` : ""}
    ${s.decided_by ? `<div class="panel-b" style="padding-top:0"><div class="callout ${s.status === "approved" ? "good" : ""}">
      ${esc(s.status)} by ${esc(s.decided_by)} on ${esc(new Date(s.decided_at).toLocaleString())}${s.decision_note ? ` — “${esc(s.decision_note)}”` : ""}</div></div>` : ""}
  </div>

  <div class="grid2" style="margin-top:var(--s4)">
    <div class="panel"><div class="panel-h"><h2>How the agent reasoned</h2>${running ? '<span class="chip"><i class="dot run"></i>thinking</span>' : ""}</div>
      <div class="panel-b think" id="think">${thinking}</div></div>
    <div class="panel"><div class="panel-h"><h2>What the bank's risk score is</h2></div><div class="panel-b">
      ${score.input_type ? `<div class="scorecard">
          <span class="brain">LLM decision</span>
          <div class="big">${score.input_type === "probability" ? `Default probability over ${esc(score.source_horizon_days)} days`
            : `Grade letters, read as ${esc(score.source_horizon_days)}-day probabilities`}</div>
          <div>${esc(score.reasoning || "")}</div>
          <div class="note" style="margin:0">Last Mile turns it into a 12-month probability (for expected loss) and a 30-day one
            (for the decision window), so the rest of the engine is the same for every bank${score.input_type === "probability"
            ? " — whether the bank runs logistic regression, a random forest or gradient boosting" : ""}.</div>
        </div>` : `<div class="muted">${running ? "Not decided yet." : "No decision."}</div>`}
      ${(prop.pii_fields || []).length ? `<div class="section-title">Personal data kept in the identity vault</div>
        <div class="chips-row">${prop.pii_fields.map((f) => `<span class="chip">${esc(f)}</span>`).join("")}</div>` : ""}
      ${prop.customer_noun ? `<p class="note">Calls its customers “${esc(prop.customer_noun)}”${prop.institution_type ? ` · ${esc(prop.institution_type)}` : ""}.</p>` : ""}
    </div></div>
  </div>`;

  if (plan.endpoints || plan.feeds) {
    const rows = [
      ...Object.entries(plan.endpoints || {}).map(([k, v]) => ({ what: ENDPOINT_TEXT[k] || k, path: v, why: reasons[k] })),
      ...Object.entries(plan.feeds || {}).map(([k, v]) => ({ what: `Feed: ${FEED_TEXT[k] || k}`, path: v, why: reasons[k] })),
    ];
    body += `<div class="panel" style="margin-top:var(--s4)"><div class="panel-h"><h2>Endpoints it chose</h2><span class="brain">LLM decision</span></div>
      <div class="panel-b">${tableHTML(rows, { maxHeight: 360, render: { path: (v) => `<span class="mono">${esc(v || "—")}</span>`,
        why: (v) => v ? esc(v) : '<span class="miss">no reason given</span>' } })}</div></div>`;
  }

  if (prop.field_map) {
    const feeds = Object.keys(s.canonical);
    const fm = prop.field_map[O.feed] || {}, why = (prop.field_reasons || {})[O.feed] || {};
    const cols = ((s.profiles || {})[O.feed] || {}).columns || {};
    const used = new Set(Object.values(fm).filter(Boolean));
    const rows = Object.entries(s.canonical[O.feed]).map(([f, spec]) => ({ field: f, meaning: spec.meaning, required: spec.required,
      bank_column: fm[f] || null, what_it_holds: kindText(cols[fm[f]]), reason: why[f] || "" }));
    const unused = Object.keys(cols).filter((c) => !used.has(c));
    body += `<div class="panel" style="margin-top:var(--s4)"><div class="panel-h"><h2>Column mapping</h2><span class="brain">LLM decision</span>
        <span class="muted">profiled values only — no customer data was sent</span></div>
      <div class="tabs">${feeds.map((f) => `<button class="tab ${f === O.feed ? "on" : ""}" data-feed="${f}">${esc(FEED_TEXT[f] || f)}
        <span class="count">${Object.values(prop.field_map[f] || {}).filter(Boolean).length}</span></button>`).join("")}</div>
      <div class="panel-b">${tableHTML(rows, { maxHeight: 520, render: {
          field: (v) => `<span class="mono">${esc(v)}</span>`,
          required: (v) => v ? '<span class="pill">required</span>' : '<span class="muted">optional</span>',
          bank_column: (v) => v ? `<b class="mono">${esc(v)}</b>` : '<span class="miss">not mapped</span>',
          what_it_holds: (v) => `<span class="muted">${esc(v)}</span>`, reason: (v) => esc(v) } })}
        ${unused.length ? `<p class="note">Bank columns not used: <span class="mono">${unused.map(esc).join(", ")}</span></p>` : ""}</div></div>`;
  }

  if (!running && (s.problems || s.status !== "failed")) {
    const problems = s.problems || [];
    body += `<div class="grid2" style="margin-top:var(--s4)">
      <div class="panel"><div class="panel-h"><h2>Checks against the real data</h2><span class="rules">rules</span></div><div class="panel-b">
        ${problems.length ? `<div class="callout bad">${problems.length} problem(s) remain after ${s.attempts} attempt(s). This proposal cannot be approved.</div>
          <ul>${problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`
          : `<div class="callout good">Every required field is mapped to a column that exists, types and ranges fit, flags are 0/1,
             the score's range and horizon match the data, and the Institution Pack loads with the same schema a run uses.</div>`}
        ${(prop.open_questions || []).length ? `<div class="section-title">The agent's questions for the bank</div>
          <ul>${prop.open_questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
      </div></div>
      <div class="panel"><div class="panel-h"><h2>Confirm with the bank</h2><span class="rules">not in the data</span></div><div class="panel-b">
        <p class="note" style="margin-top:0">An API shows what data looks like, not the bank's business rules. These were copied from the reference
          institution and should be confirmed before live use (Configuration → the new file):</p>
        ${tableHTML(Object.entries(s.reference_sections).map(([k, v]) => ({ section: k, what: v })), { maxHeight: 320,
          render: { section: (v) => `<span class="mono">${esc(v)}</span>` } })}
      </div></div></div>`;
  }

  if (s.pack_yaml) {
    body += `<div class="panel" style="margin-top:var(--s4)"><div class="panel-b"><details><summary>Institution Pack that will be saved (${esc(s.institution_id)}.yaml)</summary>
      <pre class="yaml" style="margin-top:var(--s3)">${esc(s.pack_yaml)}</pre></details></div></div>`;
  }
  body += `<div class="panel" style="margin-top:var(--s4)"><div class="panel-h"><h2>LLM calls</h2><span class="muted">every prompt and response, stored in full</span></div>
    <div class="panel-b" id="llmCalls"><div class="muted">Loading…</div></div></div>`;

  $("#detail").innerHTML = body;
  const think = $("#think"); if (think && running) think.scrollTop = think.scrollHeight;
  $$("#detail .tab[data-feed]").forEach((b) => b.onclick = () => { O.feed = b.dataset.feed; render(); });
  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
  on("approveBtn", approve); on("rejectBtn", reject); on("testBtn", testRun);
  loadCalls();
}

async function loadCalls() {
  const calls = await api(`/api/onboarding/${O.id}/llm-calls`).catch(() => []);
  const el = $("#llmCalls"); if (!el) return;
  el.innerHTML = calls.length ? tableHTML(calls.map((c) => ({ id: c.id, purpose: c.purpose, model: c.model, status: c.status,
      seconds: c.latency_ms != null ? (c.latency_ms / 1000).toFixed(1) : "–", tokens: c.total_tokens, error: c.error || "" })),
      { maxHeight: 260, click: true, render: { status: (v) => statusPill(v === "ok" ? "done" : "failed") } }) + `<div id="callDetail"></div>`
    : `<div class="muted">No calls yet.</div>`;
  $$("#llmCalls tr.click").forEach((tr) => tr.onclick = async () => {
    const c = await api(`/api/llm-calls/${calls[Number(tr.dataset.i)].id}`);
    const pretty = (t) => { try { return JSON.stringify(JSON.parse(t), null, 2); } catch { return t || "–"; } };
    $("#callDetail").innerHTML = `<div class="section-title">System instruction</div><pre class="yaml">${esc(c.system_prompt)}</pre>
      <div class="section-title">What the LLM was given</div><pre class="yaml">${esc(pretty(c.user_prompt))}</pre>
      <div class="section-title">What it answered</div><pre class="yaml">${esc(c.parsed ? JSON.stringify(c.parsed, null, 2) : pretty(c.response_text))}</pre>`;
  });
}

async function approve() {
  const note = prompt("Approve and save this Institution Pack? Add a note for the audit trail (optional):", "");
  if (note === null) return;
  try {
    const r = await api(`/api/onboarding/${O.id}/approve`, { method: "POST", body: JSON.stringify({ note: note || null }) });
    toast(`Saved ${r.files.join(" and ")}`); await refresh(); loadSessions();
  } catch (e) { toast(e.message, "bad"); }
}

async function reject() {
  const note = prompt("Reject this proposal? Say why (optional):", "");
  if (note === null) return;
  try { await api(`/api/onboarding/${O.id}/reject`, { method: "POST", body: JSON.stringify({ note: note || null }) }); await refresh(); loadSessions(); }
  catch (e) { toast(e.message, "bad"); }
}

async function testRun() {
  try {
    const r = await api(`/api/onboarding/${O.id}/test-run`, { method: "POST" });
    toast(`Test day started for ${O.s.institution_name}`);
    location.href = `/admin?run=${encodeURIComponent(r.run_id)}`;
  } catch (e) { toast(e.message, "bad"); }
}

Shell.mount({ page: "onboarding", title: "Onboard a bank" }).then(async () => {
  if (!Shell.user || Shell.user.role !== "admin") $("#roleNote").hidden = false;
  $("#startBtn").onclick = start;
  const list = await loadSessions();
  const want = new URLSearchParams(location.search).get("session") || (list[0] && list[0].session_id);
  if (want) open(want);
});
