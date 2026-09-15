// Guided tour. Plain language by default; the "Technical detail" switch reveals tools, modules and sources.
// Every figure on the page comes from the engine's API for the selected run - nothing is hardcoded.

const G = { runId: null, run: null, stages: [], worklist: null, comparison: null, model: null, audit: null, config: null,
            status: null, agents: null, replayTimer: null, livePoll: null, lastSeq: 0 };

// ------------------------------------------------------------------------------ vocabulary
const AGENTS = {
  "Supervisor Agent":   { mono: "SU", plain: "The coordinator", job: "Runs the day's steps in order and stops everything if a step fails.", never: "skip a step or change the order." },
  "Ingestion Agent":    { mono: "IN", plain: "The data collector", job: "Fetches today's accounts, balances, consents and contact history from the bank.", never: "change or clean the data it receives." },
  "Configuration Agent":{ mono: "CF", plain: "The rule keeper", job: "Loads the bank's rules and settings and locks them for the whole run.", never: "continue if the rules change part-way through." },
  "Data Steward Agent": { mono: "DS", plain: "The data guardian", job: "Checks the data is fresh and complete, and replaces names and account numbers with codes.", never: "let personal details reach the models." },
  "Feature Agent":      { mono: "FE", plain: "The analyst", job: "Turns raw records into the facts the models learn from — using only what was known at the time.", never: "use information from the future." },
  "Decision Agent":     { mono: "DE", plain: "The planner", job: "Asks the models how each member is likely to respond, then asks the solver for the best plan for the team's hours.", never: "invent a number — every figure comes from a model or the solver." },
  "Policy Agent":       { mono: "PO", plain: "The compliance checker", job: "Checks consent, contact limits and hardship rules — before planning, and again after.", never: "pass an action that breaks a rule." },
  "Explanation Agent":  { mono: "EX", plain: "The writer", job: "Writes the reason and the call script for each recommendation in plain language.", never: "write a number itself, or see who the member is." },
  "Collections manager":{ mono: "CM", plain: "You — the collections manager", job: "Reviews every recommendation and approves, changes or rejects it. Only approved actions are sent to the bank.", never: null, human: true },
};
const TEAM_ORDER = ["Supervisor Agent", "Ingestion Agent", "Configuration Agent", "Data Steward Agent", "Feature Agent",
                    "Decision Agent", "Policy Agent", "Explanation Agent", "Collections manager"];

const STAGE_PLAIN = {
  ingestion:     { title: "Collect today's data",             done: (m) => `<b>${fmt.int(m["Rows pulled"])}</b> records pulled from the bank` },
  configuration: { title: "Load the bank's rules",            done: (m) => `<b>${m["Actions"]}</b> possible actions and <b>${m["Eligibility rules"]}</b> rules locked in` },
  quality:       { title: "Check the data, hide identities",  done: (m) => `<b>${esc(m["Gates passed"])}</b> checks passed · personal details hidden` },
  features:      { title: "Prepare the facts",                done: (m) => `<b>${fmt.int(m["Accounts today"])}</b> members today · learned from <b>${fmt.int(m["Training decisions"])}</b> past decisions` },
  decision:      { title: "Decide who to contact",            done: (m) => `<b>${fmt.int(m["Selected"])}</b> actions planned` },
  governance:    { title: "Double-check every rule",          done: (m) => `<b>${fmt.int(m["Violations (optimised)"])}</b> rule breaks · <b>${fmt.int(m["Escalated"])}</b> flagged for a second look` },
  explanation:   { title: "Explain each decision",            done: (m) => `<b>${fmt.int(m["Fact sheets"])}</b> explanations written` },
  approval:      { title: "Wait for a person to approve",     done: (m) => `<b>${fmt.int(m["Awaiting approval"])}</b> waiting for the manager` },
};

const TYPES = [
  { key: "persuadable", name: "Will pay if we reach out", desc: "Contact changes the outcome. This is where the team's time pays off.", doit: "Contact them" },
  { key: "sure_thing", name: "Will pay anyway", desc: "They catch up on their own. A call uses time without changing anything.", doit: "Usually no contact needed" },
  { key: "lost_cause", name: "Won't pay whatever we do", desc: "Contact is unlikely to help. These often have the highest risk scores — which is why risk-first lists keep calling them.", doit: "Don't spend scarce time here" },
  { key: "sleeping_dog", name: "Contact makes it worse", desc: "Chasing them can backfire: complaints, disputes, or closing the account.", doit: "Leave them alone" },
];

const ACTION_PLAIN = { SMS: "Send a text reminder", CALL: "Call the member", PLAN: "Offer a payment plan", HARDSHIP: "Refer to the hardship programme" };
const CHECK_PLAIN = { suppression: "No legal or account holds", "contact policy": "Not contacted too often this week",
  sms_consent: "Agreed to receive texts", phone_consent: "Agreed to receive calls", plan_eligible: "Can be offered a payment plan",
  hardship_eligible: "Qualifies for the hardship programme" };
const FEED_LABEL = { scores: "risk score", accounts: "account", members: "member", consents: "consent", queue_history: "past contact", outcomes: "past outcome" };

const pct0 = (v) => fmt.pct(Math.abs(v), 0);

// Handoffs, translated. Keyed "from>to" where the same agent receives different tasks, otherwise by recipient.
const HANDOFF_PLAIN = {
  "Ingestion Agent": "collect today's accounts from the bank.",
  "Configuration Agent": "load the bank's rules and settings.",
  "Data Steward Agent": "check the data and hide personal details before anything else looks at it.",
  "Feature Agent": "prepare the facts the models will learn from.",
  "Supervisor Agent>Decision Agent": "work out who to contact, and how, within the team's hours.",
  "Decision Agent>Policy Agent": "which actions is each member allowed to receive today?",
  "Policy Agent>Decision Agent": "here is what each member is allowed — anything else can't be chosen.",
  "Supervisor Agent>Policy Agent": "re-check the finished plan against every rule and flag anything that needs a second look.",
  "Explanation Agent": "write the reason and the call script for each recommendation.",
  "Collections manager": "the recommendations are ready for your review in the Manager Console.",
  "Collections manager>Decision Agent": "why was this member chosen?",
};

// The agents' own notes, translated. A null result hides the note unless technical detail is on.
const DECISION_PLAIN = [
  [/^Run pinned to configuration/, () => `Locked today's settings, so every step works from the same rules.`],
  [/^Decision date set to (\S+)/, (m) => `Working from the bank's data for <b>${esc(m[1])}</b>.`],
  [/^Configuration hash matches/, () => null],
  [/^All blocking gates passed/, () => `All essential data checks passed.`],
  [/^Data can support causal estimation/, () => `There's enough history to learn what works — passing the facts to the planner.`],
  [/^Feasibility checks failed/, () => `There isn't enough reliable history to learn what works, so no plan will be made.`],
  [/^Holdout Qini below threshold/, () => `The model didn't pass its test on unseen cases, so no plan will be published.`],
  [/^Optimiser estimate beats sort-by-risk by (\$[\d,.-]+)/, (m) => `The plan is estimated to prevent <b>${esc(m[1])}</b> more losses than calling the riskiest members first.`],
  [/^Optimised plan independently verified/, () => `The finished plan passed every rule check.`],
  [/^(\d+) distinct segment x action/, (m) => `Needs wording for <b>${esc(m[1])}</b> kinds of situation. Only the situation types are shared with the AI — never a member's details.`],
  [/^(\d+) AI draft\(s\) failed the checks/, (m) => `<b>${esc(m[1])}</b> pieces of AI wording didn't pass the checks, so the writer is asking the AI to fix them.`],
  [/^Run stopped at '([^']+)': (.*)$/, (m) => `The run stopped during "${esc(m[1])}": ${esc(m[2])}. Nothing was published.`],
];

// ------------------------------------------------------------------------------ narration
// Turns one trace event into a sentence a first-time viewer understands. Returns null for events not worth narrating.
function narrate(e) {
  const o = e.output || {};
  const who = AGENTS[e.agent] ? e.agent : e.agent;
  const plainName = (n) => (AGENTS[n] ? AGENTS[n].plain.replace(/^The /, "the ") : n);
  const base = { agent: who, cls: "", tech: [e.tool, e.module, e.ms ? fmt.ms(e.ms) : null].filter(Boolean).join(" · ") };
  if (e.status === "denied") return { ...base, cls: "problem", line: `Blocked: ${esc(e.message)}` };
  if (e.status === "error") return { ...base, cls: "problem", line: `Something went wrong: ${esc(e.message)}` };
  if (e.kind === "handoff") {
    const task = HANDOFF_PLAIN[`${e.agent}>${e.target}`] || HANDOFF_PLAIN[e.target] || esc(lowerFirst(e.message));
    return { ...base, tech: e.message, cls: "handoff", line: `Handed over to <b>${esc(plainName(e.target))}</b>: ${task}` };
  }
  if (e.kind === "decision") {
    const msg = e.message || "";
    for (const [re, fn] of DECISION_PLAIN) {
      const m = msg.match(re);
      if (m) { const line = fn(m); return line ? { ...base, tech: msg, line } : null; }
    }
    return { ...base, line: esc(msg) };
  }
  if (e.kind === "human") return { ...base, cls: "human", line: esc(e.message) };
  const t = {
    "bank.health": () => `Connected to <b>${esc(o.institution)}</b>. The data is up to date as of ${esc(o.as_of)}.`,
    "bank.list_feeds": () => `Found the <b>${(o.feeds || []).length}</b> data feeds the bank shares.`,
    "bank.fetch_feed": () => `Downloaded <b>${fmt.int(o.rows)}</b> ${FEED_LABEL[o.feed] || o.feed} records.`,
    "landing.store": () => null,
    "config.load_packs": () => `Loaded the bank's rules — <b>${(o.actions || []).length}</b> possible actions — and locked them with a fingerprint so they can't change mid-run.`,
    "config.map_fields": () => null,
    // counts come from the agent's own message: the trace shortens long lists, so counting o.gates would under-report
    "quality.run_gates": () => { const m = String(e.message || "").match(/(\d+)\/(\d+) gates passed/); return m
      ? `Ran <b>${m[2]}</b> data checks: ${m[1]} passed, ${fmt.int(o.quarantined_rows)} records set aside.` : `Ran the data quality checks.`; },
    "privacy.tokenise": () => `Replaced names, phone numbers and account numbers with codes. <b>No personal details reach the models.</b>`,
    "features.join_portfolio": () => `<b>${fmt.int(o.portfolio_rows)}</b> members are behind on a payment today.`,
    "features.calibrate_pd": () => `Turned the bank's risk grades into probabilities for the next month.`,
    "features.lookup_lgd": () => `Estimated how much would be lost if each account is never repaid.`,
    "features.build_training_set": () => `Studied <b>${fmt.int(o.training_rows)}</b> past collection decisions and what happened next.`,
    "features.build_online_features": () => null,
    "features.leakage_guard": () => (o.status === "clean" && (e.input || {}).frame === "training_set" ? `Confirmed no information from the future was used.` : null),
    "features.feasibility_profile": () => { const c = o.checks || []; return `Checked there's enough history to learn what works: ${c.filter((x) => x.passed).length} of ${c.length} checks passed.`; },
    "kpi.validate_uplift": () => (o.passed ? `Tested the model on past cases it had never seen — it picks out who responds better than chance for every action.` : `The model failed its test on unseen cases, so no plan will be published.`),
    "engine.train_uplift": () => `Trained the model that estimates how much each action changes a member's chance of catching up.`,
    "engine.score_actions": () => `Estimated the effect of every possible action for every member (<b>${fmt.int(o.pairs_scored)}</b> estimates).`,
    "policy.eligibility": () => `Checked consent, contact limits and hardship rules: <b>${fmt.int(o.pairs_allowed)}</b> actions allowed, <b>${fmt.int(o.pairs_blocked)}</b> blocked.`,
    "engine.compute_value": () => `Put a dollar value on every allowed action.`,
    "engine.assign_segments": () => `Grouped members by how they're likely to respond to contact.`,
    "engine.optimise": () => `Built the best plan for the team's time: <b>${fmt.int(o.selected)}</b> actions using <b>${fmt.int(o.minutes_used)}</b> of the ${fmt.int(o.minutes_limit)} minutes set aside for planned work.`,
    "engine.baselines": () => `Built two simpler plans to compare against: "riskiest first" and "biggest loss first".`,
    "engine.simulate_policies": () => `Simulated each plan ${fmt.int(o.runs)} times to see the likely range of results.`,
    "engine.simulate_floor": () => ((e.input || {}).plan === "optimised" ? `Simulated a working day on the phones: <b>${fmt.pct(o.completion_rate_mean, 0)}</b> of planned calls fit into the shift.` : null),
    "engine.explain_selection": () => `Re-ran the planner without this member to show what choosing someone else would cost.`,
    "policy.verify_plan": () => (o.plan === "optimised" ? `Re-checked the finished plan against every rule: <b>${fmt.int(o.count)}</b> problems found.` : null),
    "governance.escalate": () => `Flagged <b>${fmt.int(o.escalated_accounts)}</b> recommendations for a senior reviewer.`,
    "audit.append": () => `Recorded this step in the tamper-evident audit log.`,
    "provenance.build_facts": () => `Attached a source to every figure that will appear in an explanation.`,
    "llm.draft_templates": () => (o.rewrite ? `Sent the wording that failed the checks back to the AI, with the reasons, for <b>one</b> rewrite.`
      : String(o.mode || "").startsWith("gemini")
      ? `Asked the AI to draft reasons and call scripts — <b>without sharing any member's details</b>.`
      : `Used the approved standard wording for reasons and scripts (no AI model is connected).`),
    "provenance.validate_templates": () => { const n = (String(e.message || "").match(/(\d+) LLM draft\(s\) rejected/) || [])[1];
      return (e.input || {}).retry
        ? `Checked the rewrites: <b>${esc(n || "0")}</b> still failed and will use the approved standard wording instead.`
        : `Checked every piece of wording${n && n !== "0" ? ` — <b>${esc(n)}</b> drafts rejected (numbers written by the AI, repeated figures or awkward wording)` : " — all of it passed"}.`; },
    "worklist.publish": () => `Published <b>${fmt.int(o.published)}</b> recommendations for the manager to review.`,
    "bank.release_actions": () => `Sent <b>${fmt.int(o.released)}</b> approved actions to the bank.`,
  }[e.tool];
  const line = t ? t() : null;
  if (!line) return null;
  const maths = /^(engine|kpi|features)\./.test(e.tool || "");
  return { ...base, cls: maths ? "maths" : "", line };
}
function lowerFirst(s) { return s ? s.charAt(0).toLowerCase() + s.slice(1) : ""; }

// ------------------------------------------------------------------------------ loading
async function init() {
  const tech = $("#techToggle");
  try { tech.checked = localStorage.getItem("lm-guide-tech") === "1"; } catch { /* storage unavailable */ }
  document.body.classList.toggle("tech-on", tech.checked);
  tech.onchange = () => {
    document.body.classList.toggle("tech-on", tech.checked);
    try { localStorage.setItem("lm-guide-tech", tech.checked ? "1" : "0"); } catch { /* ignore */ }
  };
  scrollSpy();
  renderTypes(null);
  $("#heroStart").onclick = () => { $("#c3").scrollIntoView({ behavior: "smooth" }); startLive(); };
  $("#runStart").onclick = startLive;
  $("#runReplay").onclick = replay;
  $("#memberSelect").onchange = (e) => e.target.value && loadStory(e.target.value);

  const [status, agents] = await Promise.all([api("/api/status").catch(() => null), api("/api/agents").catch(() => null)]);
  G.status = status; G.agents = agents;
  renderTeam();
  await loadRuns();
}

async function loadRuns(prefer) {
  const runs = (await api("/api/runs").catch(() => [])).filter((r) => ["awaiting_approval", "released"].includes(r.status));
  if (!runs.length) {
    $("#runChip").innerHTML = `No finished runs yet`;
    renderHero(null);
    renderStages(null);
    return;
  }
  const id = prefer && runs.some((r) => r.run_id === prefer) ? prefer : runs[0].run_id;
  $("#runChip").innerHTML = `Showing <select id="runSel" aria-label="Choose run">${runs.map((r) =>
    `<option value="${esc(r.run_id)}" ${r.run_id === id ? "selected" : ""}>${esc(r.as_of_date || "")} · ${esc(r.run_id.slice(4, 19))} · ${esc(r.status.replace(/_/g, " "))}</option>`).join("")}</select>`;
  $("#runSel").onchange = (e) => selectRun(e.target.value);
  await selectRun(id);
}

async function selectRun(id) {
  G.runId = id;
  const get = (p) => api(p).catch(() => null);
  const [run, wl, cmp, model, audit, cfg] = await Promise.all([
    get(`/api/runs/${id}`), get(`/api/runs/${id}/worklist`), get(`/api/runs/${id}/comparison`),
    get(`/api/runs/${id}/model`), get(`/api/runs/${id}/audit`), get(`/api/runs/${id}/config`)]);
  Object.assign(G, { run: run && run.run, stages: (run && run.stages) || [], worklist: wl, comparison: cmp, model, audit, config: cfg });
  renderHero(); renderTypes(model && model.segments); renderStages(null); renderPlan(); renderMembers(); renderProof(); renderPromises();
  $("#managerLink").href = `/manager?run=${encodeURIComponent(id)}`;
}

// ------------------------------------------------------------------------------ hero & chapter 1
function capacity() {
  const c = G.config && G.config.institution && G.config.institution.capacity;
  if (!c) return null;
  const r = G.config.roster;   // the manager's team for that day, when the run had one
  if (r) {
    const working = r.collectors.filter((x) => x.present && x.shift_minutes > 0);
    return { agents: working.length, total: working.reduce((a, x) => a + x.shift_minutes, 0), buffer: c.planning_buffer };
  }
  return { agents: c.agents, total: c.agents * c.minutes_per_agent, buffer: c.planning_buffer };
}
function stageMetric(stage, key) {
  const s = G.stages.find((x) => x.stage === stage);
  return s && s.metrics ? s.metrics[key] : undefined;
}

function renderHero() {
  const inst = G.status ? G.status.institution : "Your credit union";
  $("#heroInstitution").textContent = inst;
  const accounts = stageMetric("features", "Accounts today");
  const cap = capacity();
  if (accounts && cap) {
    $("#heroLede").innerHTML = `Today <b>${esc(inst)}</b> has <b>${fmt.int(accounts)}</b> members behind on a payment and
      <b>${cap.agents}</b> agents with <b>${fmt.int(cap.total)}</b> minutes between them. Last Mile works out who to contact,
      how, and why — then a person approves every action before anything is sent.`;
    $("#p1Accounts").textContent = fmt.int(accounts);
    $("#p1Minutes").textContent = fmt.int(cap.total);
    $("#p1MinutesNote").textContent = `${cap.agents} agents for a working day — enough for a small fraction of these members.`;
  }
  $("#heroHint").textContent = G.run ? `Showing the run for ${G.run.as_of_date} · ${G.run.status.replace(/_/g, " ")}` : "No finished run yet — start one to fill in this tour.";
}

function renderTypes(segments) {
  const total = segments ? Object.values(segments).reduce((a, b) => a + b, 0) : 0;
  $("#typeCards").innerHTML = TYPES.map((t) => `
    <article class="g-card g-type ${t.key}">
      ${segments ? `<span class="g-type-count">${fmt.int(segments[t.key] || 0)} today</span>` : ""}
      <h3>${esc(t.name)}</h3><p>${esc(t.desc)}</p>
      <div class="g-type-do">${esc(t.doit)}</div>
    </article>`).join("") + (segments && segments.uncertain
      ? `<p class="g-hint" style="grid-column:1/-1">The model isn't confident enough to place ${fmt.int(segments.uncertain)} of ${fmt.int(total)} members. These groups are the model's estimate, not a certainty.</p>` : "");
}

// ------------------------------------------------------------------------------ chapter 2
function renderTeam() {
  const tools = {};
  (G.agents ? G.agents.agents : []).forEach((a) => { tools[a.name] = a; });
  $("#teamGrid").innerHTML = TEAM_ORDER.map((name) => {
    const a = AGENTS[name], t = tools[name];
    return `<article class="g-agent ${a.human ? "human" : ""}">
      <span class="g-mono">${a.mono}</span>
      <h3>${esc(a.plain.replace(/^./, (c) => c.toUpperCase()))}</h3>
      <span class="role">${esc(a.human ? "Human decision maker" : name)}</span>
      <p class="job">${esc(a.job)}</p>
      ${a.never ? `<p class="never"><b>Never allowed to</b> ${esc(a.never)}</p>` : `<p class="never" style="color:var(--good)"><b style="color:var(--good)">Has the final say.</b> Nothing reaches a member without approval.</p>`}
      ${t ? `<div class="tech">Tools: ${t.tools.map(esc).join(", ") || "coordinates only"}<br>Code: ${t.modules.map(esc).join(", ")}</div>` : ""}
    </article>`;
  }).join("");
}

// ------------------------------------------------------------------------------ chapter 3
function stageOwner(s) {
  const agents = s.agents || [];
  return s.stage === "approval" ? "Collections manager" : agents[0];
}

function renderStages(liveState) {
  const stages = liveState ? liveState.stages : G.stages;
  if (!stages || !stages.length) {
    $("#stageList").innerHTML = Object.entries(STAGE_PLAIN).map(([k, v]) => `<li class="g-stage"><span class="g-mono">·</span><div><h4>${esc(v.title)}</h4></div></li>`).join("");
    return;
  }
  $("#stageList").innerHTML = stages.map((s) => {
    const owner = stageOwner(s), plain = STAGE_PLAIN[s.stage] || { title: s.title, done: () => "" };
    const status = s.status === "running" ? "working" : s.status === "waiting" ? "waiting-human" : s.status;
    const res = s.status === "done" || s.status === "waiting" ? plain.done(s.metrics || {}) :
      s.status === "running" ? "Working…" : s.status === "failed" ? "Stopped — see the log" : "Waiting its turn";
    const agentNames = (s.agents || []).map((n) => (AGENTS[n] ? AGENTS[n].plain : n));
    return `<li class="g-stage ${esc(status)}">
      <span class="g-mono" title="${esc(owner)}">${AGENTS[owner] ? AGENTS[owner].mono : "·"}</span>
      <div><h4>${esc(plain.title)}</h4><div class="who">${esc(agentNames.join(" + "))}${s.ms ? ` · ${fmt.ms(s.ms)}` : ""}</div>
        <div class="res">${res}</div><div class="tech who">${esc(s.title)} · ${esc((s.agents || []).join(", "))}</div></div>
    </li>`;
  }).join("");
}

function feedAppend(e, container) {
  const n = narrate(e);
  if (!n) return;
  const a = AGENTS[n.agent];
  const div = document.createElement("div");
  div.className = `g-ev ${n.cls}`;
  div.innerHTML = `<span class="g-mono">${a ? a.mono : "··"}</span>
    <div class="line"><b>${esc(a ? a.plain.replace(/^./, (c) => c.toUpperCase()) : n.agent)}</b> — ${n.line}</div>
    <div class="meta tech">${esc(n.tech)}</div>`;
  const empty = container.querySelector(".g-empty");
  if (empty) empty.remove();
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  $("#feedCount").textContent = `${container.children.length} updates`;
}

function stopAnimations() {
  clearTimeout(G.replayTimer); G.replayTimer = null;
  clearInterval(G.livePoll); G.livePoll = null;
}

async function replay() {
  if (!G.runId) { toast("There's no finished run to replay yet.", "bad"); return; }
  stopAnimations();
  const events = await api(`/api/runs/${G.runId}/trace?limit=4000`);
  const feed = $("#feed");
  feed.innerHTML = "";
  $("#feedCount").textContent = "";
  const order = G.stages.map((s) => s.stage);
  const state = { stages: G.stages.map((s) => ({ ...s, status: "pending", ms: null })) };
  renderStages(state);
  const narratable = events.filter((e) => narrate(e));
  const step = Math.max(90, Math.min(520, 15000 / Math.max(1, narratable.length)));
  $("#runStatus").textContent = `Replaying the run for ${G.run.as_of_date}…`;
  let i = 0, current = null;
  const tick = () => {
    if (i >= events.length) {
      renderStages(null);
      $("#runStatus").textContent = `Replay finished — this is exactly what the agents did, step by step.`;
      return;
    }
    const e = events[i++];
    if (e.stage && e.stage !== current && order.includes(e.stage)) {
      state.stages.forEach((s) => {
        if (order.indexOf(s.stage) < order.indexOf(e.stage)) { const real = G.stages.find((x) => x.stage === s.stage); Object.assign(s, { status: real.status === "waiting" ? "waiting" : "done", ms: real.ms, metrics: real.metrics }); }
        if (s.stage === e.stage) s.status = "running";
      });
      current = e.stage;
      renderStages(state);
    }
    const shown = !!narrate(e);
    feedAppend(e, feed);
    G.replayTimer = setTimeout(tick, shown ? (e.kind === "handoff" ? step * 1.6 : step) : 0);
  };
  tick();
}

async function startLive() {
  stopAnimations();
  const btns = [$("#runStart"), $("#heroStart")];
  btns.forEach((b) => (b.disabled = true));
  try {
    const { run_id } = await api("/api/runs", { method: "POST" });
    $("#runStatus").textContent = "The agents are working — this takes about ten seconds…";
    const feed = $("#feed");
    feed.innerHTML = "";
    G.lastSeq = 0;
    G.livePoll = setInterval(async () => {
      const [r, ev] = await Promise.all([api(`/api/runs/${run_id}`), api(`/api/runs/${run_id}/trace?after=${G.lastSeq}`)]);
      ev.forEach((e) => feedAppend(e, feed));
      if (ev.length) G.lastSeq = ev[ev.length - 1].seq;
      renderStages({ stages: r.stages });
      if (r.run.status !== "running") {
        clearInterval(G.livePoll); G.livePoll = null;
        btns.forEach((b) => (b.disabled = false));
        if (["awaiting_approval", "released"].includes(r.run.status)) {
          $("#runStatus").textContent = "Done. The rest of the tour now shows this run.";
          await loadRuns(run_id);
          renderStages({ stages: r.stages });
        } else {
          $("#runStatus").textContent = `The run stopped: ${r.run.error || r.run.status}. Nothing was published.`;
        }
      }
    }, 800);
  } catch (e) {
    toast(e.message, "bad");
    btns.forEach((b) => (b.disabled = false));
  }
}

// ------------------------------------------------------------------------------ chapter 4
function renderPlan() {
  const c = G.comparison, w = G.worklist;
  if (!c || !w) { $("#planBody").innerHTML = `<div class="g-empty">The plan appears here once a run has finished.</div>`; return; }
  const opt = c.summary.optimised, mc = opt.mc || {}, cap = capacity();
  const actions = Object.entries(opt.actions || {}).sort((a, b) => b[1] - a[1]);
  const lo = Math.min(0, mc.value_p10), hi = mc.value_p90 || 1;
  const pos = (v) => ((v - lo) / (hi - lo || 1)) * 100;
  const used = opt.minutes, plannable = w.summary.plannable_minutes;
  const s = G.model && G.model.solver;
  $("#planBody").innerHTML = `
    <div class="g-answer">
      <article class="g-card"><h3>Members to contact</h3><div class="g-big">${fmt.int(opt.selected)}</div>
        <div class="g-actions-list">${actions.map(([a, n]) => `<div><span>${esc(ACTION_PLAIN[a] || a)}</span><b>${fmt.int(n)}</b></div>`).join("")}</div>
        <p>Chosen from ${fmt.int(stageMetric("features", "Accounts today"))} members behind on a payment.</p></article>
      <article class="g-card"><h3>Losses the plan is expected to prevent</h3><div class="g-big">${fmt.money(mc.value_p50)}</div>
        <div class="g-range"><span class="axis"></span><span class="band" style="left:${pos(mc.value_p10)}%;width:${Math.max(2, pos(mc.value_p90) - pos(mc.value_p10))}%"></span>
          <span class="mid" style="left:calc(${pos(mc.value_p50)}% - 1px)"></span></div>
        <div class="g-range-labels"><span>${fmt.money(mc.value_p10)}</span><span>likely range</span><span>${fmt.money(mc.value_p90)}</span></div>
        <p>The model's own estimate. <a href="#c6">Chapter 6</a> checks how accurate it really is.</p></article>
      <article class="g-card"><h3>Team time used</h3><div class="g-big">${fmt.int(used)}<span style="font-size:18px;color:var(--ink-3)"> min</span></div>
        <div class="g-meter"><i style="width:${Math.min(100, (used / (cap ? cap.total : plannable)) * 100)}%"></i></div>
        <p>Of ${fmt.int(cap ? cap.total : plannable)} minutes${cap ? `, keeping ${fmt.pct(cap.buffer, 0)} spare because calls run long` : ""}.
        In a simulated working day, ${fmt.pct(opt.des ? opt.des.completion_rate_mean : null, 0)} of the planned calls fit into the shift.</p></article>
      <article class="g-card"><h3>Need a second look</h3><div class="g-big">${fmt.int(w.summary.escalated)}</div>
        <p>Large balances, uncertain estimates and hardship referrals always go to a senior reviewer. They can't be approved in bulk.</p></article>
    </div>
    <div class="tech g-tech"><b>Under the hood.</b> The plan is a mixed-integer programme solved by CBC
      (${s ? `${esc(s.status)}, ${fmt.int(s.variables)} variables, ${fmt.int(s.constraints)} constraints, ${fmt.int(s.solve_ms)} ms` : "solver details unavailable"}).
      The loss range is the 10th–90th percentile of ${fmt.int(mc.runs)} Monte Carlo simulations; the working-day check is a SimPy discrete-event simulation.</div>`;
}

// ------------------------------------------------------------------------------ chapter 5
function renderMembers() {
  const items = (G.worklist && G.worklist.items) || [];
  $("#memberSelect").innerHTML = items.slice(0, 30).map((i) =>
    `<option value="${esc(i.account_token)}">#${i.rank} · ${esc(i.product)} · ${fmt.money(i.exposure)} · ${esc(ACTION_PLAIN[i.action] || i.action)}</option>`).join("");
  if (items.length) loadStory(items[0].account_token);
  else $("#storyBody").innerHTML = `<div class="g-empty">Recommendations appear here once a run has finished.</div>`;
}

function srcClass(src) {
  if (!src) return "";
  if (src.startsWith("model:")) return "src-model";
  if (src.startsWith("identity:")) return "src-identity";
  return "";
}
function segmentsHTML(segs) {
  return segs.map((s) => (s.key ? `<span class="fig ${srcClass(s.source)}" data-src="${esc(s.source)}" title="From ${esc(s.source)}">${esc(s.text)}</span>` : esc(s.text))).join("");
}
function plainBlocked(reasons) {
  return String(reasons || "").split("; ").filter(Boolean).map((r) => {
    const [grp, ...rest] = r.split(": ");
    return `${CHECK_PLAIN[grp] || grp.replace(/_/g, " ")} — no <span class="tech">(${esc(rest.join(": "))})</span>`;
  }).join("; ");
}

async function loadStory(token) {
  const box = $("#storyBody");
  box.innerHTML = `<div class="g-empty">Loading this member's story…</div>`;
  let d;
  try { d = await api(`/api/runs/${G.runId}/accounts/${token}`); } catch (e) { box.innerHTML = `<div class="g-empty">${esc(e.message)}</div>`; return; }
  const rec = d.recommendation;
  if (!rec) { box.innerHTML = `<div class="g-empty">This member isn't in today's plan.</div>`; return; }
  const f = rec.facts;
  const fact = (k, label, val) => `<div class="g-fact"><div class="k">${esc(label)}</div><div class="v">${esc(val)}</div><div class="src tech">${esc(f[k] ? f[k].source : "")}</div></div>`;

  const groups = {};
  d.checks.forEach((c) => { (groups[c.group] = groups[c.group] || []).push(c); });
  const needed = new Set(["suppression", "contact policy"]);
  ({ SMS: ["sms_consent"], CALL: ["phone_consent"], PLAN: ["phone_consent", "plan_eligible"], HARDSHIP: ["phone_consent", "hardship_eligible"] }[rec.action] || [])
    .forEach((g) => needed.add(g));
  const checkList = Object.entries(groups).map(([g, cs]) => {
    const ok = cs.every((c) => c.passed);
    return `<div class="g-check ${ok ? "ok" : "no"}"><i>${ok ? "✓" : "✕"}</i><div>${esc(CHECK_PLAIN[g] || g)}
      <small>${needed.has(g) ? "Needed for this action" : "Only needed for other actions"}${ok ? "" : ` · failed: <span class="tech">${cs.filter((c) => !c.passed).map((c) => esc(c.check)).join(", ")}</span>`}</small></div></div>`;
  }).join("");

  const opts = d.alternatives.slice().sort((a, b) => b.value - a.value);
  const maxV = Math.max(1, ...opts.filter((o) => o.allowed).map((o) => o.value));
  const optHTML = opts.map((o) => {
    const chosen = o.action === rec.action;
    if (!o.allowed) return `<div class="g-opt blocked"><span class="name">${esc(ACTION_PLAIN[o.action] || o.action)}</span><span class="bar"></span><span class="val">not allowed</span>
      <span class="why">${plainBlocked(o.blocked_reasons)}</span></div>`;
    return `<div class="g-opt ${chosen ? "chosen" : ""}"><span class="name">${esc(ACTION_PLAIN[o.action] || o.action)}${chosen ? " — chosen" : ""}</span>
      <span class="bar"><i style="left:0;width:${Math.max(0, (o.value / maxV) * 100)}%"></i></span><span class="val">${fmt.money(o.value)}</span>
      <span class="why">${o.value > 0 ? `Makes catching up <b>${fmt.pts(o.uplift).replace(" pts", " points")}</b> more likely` : `Expected to help too little, or to make things worse`}
        <span class="tech"> · uplift ${fmt.pts(o.uplift_lower)} to ${fmt.pts(o.uplift_upper)}</span></span></div>`;
  }).join("");

  const appr = d.approval;
  const esc_ = rec.escalations || [];
  box.innerHTML = `
    <ol class="g-journey">
      <li class="g-step"><span class="g-mono">IN</span><div>
        <h4>What the bank told us</h4><div class="who">${esc(AGENTS["Ingestion Agent"].plain)} and ${esc(AGENTS["Feature Agent"].plain)}</div>
        <div class="g-step-body"><div class="g-facts">
          ${fact("product_label", "Product", f.product_label.display)}
          ${fact("exposure", "Balance", f.exposure.display)}
          ${fact("dpd", "Days behind", f.dpd.display)}
          ${fact("risk_grade", "Bank's risk grade", f.risk_grade.display)}
          ${fact("expected_loss", "Likely loss if never repaid", f.expected_loss.display)}
          ${fact("base_cure", "Chance they catch up with no contact", f.base_cure.display)}
        </div></div></div></li>

      <li class="g-step"><span class="g-mono">PO</span><div>
        <h4>Is contact allowed?</h4><div class="who">${esc(AGENTS["Policy Agent"].plain)} — checked before and after planning</div>
        <div class="g-step-body"><div class="g-checks">${checkList}</div></div></div></li>

      <li class="g-step maths"><span class="g-mono">DE</span><div>
        <h4>What could we do — and what is each option worth?</h4><div class="who">${esc(AGENTS["Decision Agent"].plain)}, using the response model</div>
        <div class="g-step-body"><div class="g-options">${optHTML}</div>
          <p class="g-hint">Value = how much more likely the member is to catch up, multiplied by the loss that would otherwise happen.</p></div></div></li>

      <li class="g-step maths"><span class="g-mono">DE</span><div>
        <h4>Why this member, and not someone else?</h4><div class="who">${esc(AGENTS["Decision Agent"].plain)}, asking the solver</div>
        <div class="g-step-body"><p style="margin:0 0 10px">The team's time is limited, so choosing one member means not choosing another. The planner can re-run without this member to show the trade-off.</p>
          <button class="g-btn small" id="whyBtn">Show the trade-off</button><div id="whyOut"></div></div></div></li>

      <li class="g-step"><span class="g-mono">EX</span><div>
        <h4>The reason, in plain words</h4><div class="who">${esc(AGENTS["Explanation Agent"].plain)} · ${rec.template_source === "llm" ? "wording drafted by the AI" : "approved standard wording"} · every highlighted figure comes from the bank, a calculation or the model</div>
        <div class="g-step-body"><p class="g-quote">${segmentsHTML(rec.rationale)}</p></div></div></li>

      <li class="g-step"><span class="g-mono">EX</span><div>
        <h4>What the member will hear</h4><div class="who">The member's first name is added back only here, at the last step</div>
        <div class="g-step-body"><p class="g-quote script">${segmentsHTML(rec.script)}</p></div></div></li>

      <li class="g-step human"><span class="g-mono">CM</span><div>
        <h4>${appr ? `Decision recorded: ${esc(appr.decision)}` : "Waiting for your decision"}</h4><div class="who">Collections manager</div>
        <div class="g-step-body">
          ${esc_.length ? `<p style="margin:0 0 8px">Flagged for a second look: ${esc_.map((x) => `<span class="g-flag">${esc(x.detail)}</span>`).join("")}</p>` : ""}
          <p style="margin:0 0 12px">${appr ? `${esc(appr.approver_id)} ${esc(appr.decision)} this recommendation${appr.reason_code ? ` (${esc(appr.reason_code.replace(/_/g, " ").toLowerCase())})` : ""}.`
            : "Nothing has been sent. The manager can approve it, change the action, or reject it with a reason."}</p>
          <a class="g-btn primary small" href="/manager?run=${encodeURIComponent(G.runId)}">Review in the Manager Console</a>
        </div></div></li>
    </ol>`;

  $("#whyBtn").onclick = async () => {
    const out = $("#whyOut");
    out.innerHTML = `<div class="g-callout">Re-running the planner…</div>`;
    try {
      const x = await api(`/api/runs/${G.runId}/accounts/${token}/explain`, { method: "POST" });
      if (x.was_selected) {
        const r = x.would_be_replaced_by || [];
        const NOUN = { SMS: "a text to", CALL: "a call to", PLAN: "a payment plan for", HARDSHIP: "a hardship referral for" };
        const repl = r.length === 1 ? `The best use of that time instead would be ${NOUN[r[0].action] || "an action for"} another member, worth ${fmt.money(r[0].value)}.`
          : r.length > 1 ? `The best use of that time instead would be ${r.length} actions for other members, worth ${fmt.money(r.reduce((a, y) => a + y.value, 0))} together.`
          : "Nothing else would add value in that slot.";
        out.innerHTML = `<div class="g-callout good">Leaving this member out would cost the plan <b>${fmt.money(x.cost_of_change)}</b> in prevented losses. ${repl}</div>`;
      } else {
        out.innerHTML = `<div class="g-callout warn">This member is not in the plan.</div>`;
      }
    } catch (e) { out.innerHTML = `<div class="g-callout bad">${esc(e.message)}</div>`; }
  };
}

// ------------------------------------------------------------------------------ chapter 6
function renderProof() {
  const c = G.comparison;
  if (!c) { $("#proofBody").innerHTML = `<div class="g-empty">Results appear here once a run has finished.</div>`; return; }
  const ev = c.evaluation;
  const intro = `<p style="max-width:70ch;margin:0 0 18px;color:var(--ink-2)">This credit union is <b>simulated</b>, so we know the true effect of every
    action — sealed away where the engine can't see it. That lets us check the plan against what would really have happened.
    On a real bank's data this check isn't possible, which is exactly why we test here first.</p>`;
  if (!ev) {
    const s = c.summary;
    $("#proofBody").innerHTML = intro + `
      <div class="g-callout warn">These are the model's own estimates, which tend to be optimistic. Check them against what really happened:</div>
      <div class="g-bars" style="margin-top:14px">${[["sort_by_risk", "Riskiest first", "What many teams do today"], ["sort_by_value", "Biggest loss first", "A simple, sensible rule"], ["optimised", "Last Mile's plan", "Who responds, within the team's hours"]]
        .map(([k, n, d]) => barRow(n, d, s[k].est_value, Math.max(...Object.values(s).map((p) => p.est_value)), k === "optimised" ? "ours" : "")).join("")}</div>
      <button class="g-btn primary" id="evalBtn" style="margin-top:16px">Check against what really happened</button>`;
    $("#evalBtn").onclick = async () => {
      $("#evalBtn").disabled = true; $("#evalBtn").textContent = "Checking…";
      try { await api(`/api/runs/${G.runId}/evaluate`, { method: "POST" }); G.comparison = await api(`/api/runs/${G.runId}/comparison`); renderProof(); }
      catch (e) { toast(e.message, "bad"); $("#evalBtn").disabled = false; $("#evalBtn").textContent = "Check against what really happened"; }
    };
    return;
  }
  const p = ev.plans, max = ev.oracle_true_value;
  const vsRisk = ev.true_lift_vs_sort_by_risk, vsValue = ev.true_lift_vs_sort_by_value;
  const corr = ev.uplift_correlation_with_truth || {};
  const weakest = Object.entries(corr).sort((a, b) => a[1] - b[1])[0];
  $("#proofBody").innerHTML = intro + `
    <h3 class="g-sub" style="margin-top:0">Losses really prevented, same team and same hours</h3>
    <div class="g-bars">
      ${barRow("Riskiest first", "What many teams do today", p.sort_by_risk.true_value, max, "")}
      ${barRow("Biggest loss first", "A simple, sensible rule", p.sort_by_value.true_value, max, "")}
      ${barRow("Last Mile's plan", "Who responds, within the team's hours", p.optimised.true_value, max, "ours")}
      ${barRow("Perfect knowledge", "Not achievable — shows the ceiling", max, max, "oracle")}
    </div>
    <div class="g-verdict">
      <article class="g-card ${vsRisk >= 0 ? "good" : "warn"}"><h3>${vsRisk >= 0 ? `${pct0(vsRisk)} better than calling the riskiest first` : `${pct0(vsRisk)} worse than calling the riskiest first`}</h3>
        <p>It also made fewer contacts that really did harm: <b>${fmt.int(p.optimised.harmful_actions)}</b>, against ${fmt.int(p.sort_by_risk.harmful_actions)} for riskiest-first and ${fmt.int(p.sort_by_value.harmful_actions)} for biggest-loss-first.</p></article>
      <article class="g-card ${vsValue >= 0 ? "good" : "warn"}"><h3>${vsValue >= 0 ? `${pct0(vsValue)} better than biggest-loss-first` : `A simple biggest-loss-first rule did ${pct0(vsValue)} better`}</h3>
        <p>${vsValue >= 0 ? "Even a sensible rule of thumb leaves value on the table." :
          `On this book, the model is good at spotting who responds to some kinds of contact but less sure about others${weakest ? ` — weakest for “${esc((ACTION_PLAIN[weakest[0]] || weakest[0]).toLowerCase())}”` : ""}. When it's unsure, it can spend call time on the wrong members.`}</p>
        <p class="tech" style="margin-top:8px">How well estimated uplift matches the truth (correlation): ${Object.entries(corr).map(([a, v]) => `${esc(a)} ${v.toFixed(2)}`).join(" · ")}</p></article>
      <article class="g-card info"><h3>Treat dollar estimates as a ranking, not a forecast</h3>
        <p>The model expected <b>${fmt.money(p.optimised.estimated_value)}</b>; what really happened was ${fmt.money(p.optimised.true_value)} —
          about <b>${(ev.estimate_to_truth_ratio || 0).toFixed(1)}×</b> optimistic. Its plan still captured <b>${fmt.pct(ev.capture_of_oracle, 0)}</b> of what perfect knowledge could achieve.</p></article>
      <article class="g-card info"><h3>What would close the gap</h3>
        <p>Keep a small random group each month where the team does its usual thing, so the system learns what truly works; build up more history for each type of contact;
          and bring in signals that show who reacts badly to being chased.</p></article>
    </div>`;
}
function barRow(name, desc, value, max, cls) {
  return `<div class="g-bar-row ${cls}"><span class="name"><b>${esc(name)}</b><small>${esc(desc)}</small></span>
    <span class="track"><i style="width:${Math.max(1, (value / (max || 1)) * 100)}%"></i></span><span class="v">${fmt.money(value)}</span></div>`;
}

// ------------------------------------------------------------------------------ chapter 7
function renderPromises() {
  const w = G.worklist, m = G.model, a = G.audit;
  if (!w) { $("#promises").innerHTML = `<div class="g-empty">These checks run once a run has finished.</div>`; return; }
  const dec = w.summary.decisions || {};
  const violations = stageMetric("governance", "Violations (optimised)");
  const gov = m && m.governance;
  const promise = (state, title, body, proof) => `<article class="g-promise ${state}"><i>${state === "ok" ? "✓" : state === "warn" ? "!" : "✕"}</i>
    <div><h3>${esc(title)}</h3><p>${body}</p><div class="proof">${proof}</div></div></article>`;
  $("#promises").innerHTML = [
    promise("ok", "Nothing is sent without a person's approval",
      "Recommendations wait in the Manager Console. Only approved ones can be released to the bank, and each can be released once.",
      `${fmt.int(dec.approved || 0)} approved · ${fmt.int(dec.edited || 0)} changed · ${fmt.int(dec.rejected || 0)} rejected · ${fmt.int(dec.pending || 0)} waiting · ${fmt.int(w.summary.released)} sent`),
    promise(violations === 0 ? "ok" : "bad", "Every rule is checked twice",
      "Consent, contact limits and hardship rules are applied before planning — then an independent check re-tests the finished plan.",
      violations === 0 ? "0 rule breaks in today's plan" : `${fmt.int(violations)} rule breaks found — plan not published`),
    promise(gov && gov.unstamped_numbers === 0 ? "ok" : "bad", "The AI never writes a number",
      "The AI only drafts wording with blanks. Every figure is filled in from the bank's data, a calculation or the model.",
      gov ? `${fmt.int(gov.unstamped_numbers)} numbers written by the AI across ${fmt.int(gov.recommendations)} explanations` : "Not available"),
    promise(gov && gov.provenance_complete === gov.recommendations ? "ok" : "warn", "Every figure has a source",
      "Hover any highlighted figure in an explanation to see where it came from.",
      gov ? `${fmt.int(gov.provenance_complete)} of ${fmt.int(gov.recommendations)} explanations fully sourced` : "Not available"),
    promise("ok", "Personal details stay hidden",
      "Names, phone numbers and account numbers are replaced with codes before any analysis. The AI never sees who a member is.",
      `${fmt.int(stageMetric("quality", "PII columns removed") || 0)} kinds of personal detail removed · names return only in the final call script`),
    promise(a && a.chain.valid ? "ok" : "bad", "Every step is recorded and can't be changed",
      "Each step, decision and release is written to a log that the database refuses to edit or delete. Each entry is linked to the one before it, so tampering is detectable.",
      a ? (a.chain.valid ? `Log verified · ${fmt.int(a.chain.events)} entries intact` : `Log check failed at entry ${a.chain.broken_at_seq}`) : "Not available"),
  ].join("");
}

// ------------------------------------------------------------------------------ navigation
function scrollSpy() {
  const links = $$("#chapters a");
  const map = Object.fromEntries(links.map((l) => [l.getAttribute("href").slice(1), l]));
  if (!("IntersectionObserver" in window)) return;
  const obs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) { links.forEach((l) => l.classList.remove("on")); map[e.target.id] && map[e.target.id].classList.add("on"); }
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  Object.keys(map).forEach((id) => { const el = document.getElementById(id); if (el) obs.observe(el); });
}

document.addEventListener("DOMContentLoaded", init);
