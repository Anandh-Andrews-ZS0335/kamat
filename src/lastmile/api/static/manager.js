const M = { runId: null, data: null, tab: "worklist", open: null, compare: null };
const D = { day: null, poll: null, team: null, teamHistory: [] };
const SEG_COLORS = { persuadable: "var(--good)", sure_thing: "var(--ink-3)", lost_cause: "var(--bad)", sleeping_dog: "var(--warn)", uncertain: "var(--violet)" };
const SEG_LABEL = { persuadable: "Persuadable", sure_thing: "Sure thing", lost_cause: "Lost cause", sleeping_dog: "Sleeping dog", uncertain: "Uncertain" };

async function loadRuns(pref) {
  const runs = (await api(`/api/runs${Shell.status && Shell.status.institution_id ? `?institution=${encodeURIComponent(Shell.status.institution_id)}` : ""}`)).filter((r) => ["awaiting_approval", "released"].includes(r.status));
  $("#runSelect").innerHTML = runs.length ? runs.map((r) => `<option value="${esc(r.run_id)}">${esc(r.run_id)} · ${esc(r.status.replace(/_/g, " "))}</option>`).join("")
    : `<option value="">No runs awaiting approval</option>`;
  const today = D.day && D.day.run && runs.find((r) => r.run_id === D.day.run.run_id) ? D.day.run.run_id : null;
  const id = pref && runs.find((r) => r.run_id === pref) ? pref : today || (runs[0] && runs[0].run_id);
  if (!id) {
    $("#worklist").innerHTML = `<div class="empty">No worklist yet. Confirm today's team and start today's run above.</div>`;
    $("#bulkApprove").disabled = true; $("#release").disabled = true;
    return;
  }
  $("#runSelect").value = id;
  await selectRun(id);
}

async function selectRun(id) {
  M.runId = id; M.compare = null;
  await loadWorklist();
  if (M.tab !== "worklist") showTab(M.tab);
}

async function loadWorklist() {
  M.data = await api(`/api/runs/${M.runId}/worklist`);
  const d = M.data, s = d.summary;
  $("#runSelect").title = `${d.run.run_id} · model ${d.run.model_run_id || "–"} · LLM ${d.run.llm_mode || "–"}`;
  const dec = s.decisions || {};
  const total = s.selected || 1;
  const bar = [["approved", "var(--good)"], ["edited", "var(--primary)"], ["rejected", "var(--bad)"]]
    .map(([k, c]) => `<i style="width:${((dec[k] || 0) / total) * 100}%;background:${c}"></i>`).join("");
  $("#summary").innerHTML = `
    <div class="tile"><div class="k">Recommendations</div><div class="v">${fmt.int(s.selected)}</div><div class="s">ranked by the CBC solver</div></div>
    <div class="tile"><div class="k">Est. avoided loss</div><div class="v">${fmt.money(s.est_value)}</div>
      <div class="s">${s.mc ? `Monte Carlo P10–P90 ${fmt.money(s.mc.value_p10)} – ${fmt.money(s.mc.value_p90)}` : ""}</div></div>
    <div class="tile"><div class="k">Team today</div><div class="v">${fmt.int(s.collectors_working)} collectors</div><div class="s">${fmt.int(s.minutes)} of ${fmt.int(s.plannable_minutes)} plannable minutes used</div></div>
    <div class="tile"><div class="k">Escalated</div><div class="v">${fmt.int(s.escalated)}</div><div class="s">need individual review</div></div>
    <div class="tile"><div class="k">Decisions</div><div class="v">${fmt.int((dec.approved || 0) + (dec.edited || 0) + (dec.rejected || 0))} / ${fmt.int(s.selected)}</div>
      <div class="s">${fmt.int(dec.approved || 0)} approved · ${fmt.int(dec.edited || 0)} edited · ${fmt.int(dec.rejected || 0)} rejected</div><div class="decisions-bar">${bar}</div></div>
    <div class="tile"><div class="k">Released to bank</div><div class="v">${fmt.int(s.released)}</div><div class="s">${d.run.status === "released" ? "sent" : "not yet sent"}</div></div>`;
  const pending = d.items.filter((i) => i.decision === "pending" && !i.escalations.length).length;
  const ready = d.items.filter((i) => ["approved", "edited"].includes(i.decision) && !i.released).length;
  $("#bulkApprove").textContent = `Approve all non-escalated (${pending})`;
  $("#bulkApprove").disabled = pending === 0;
  $("#release").textContent = `Release approved to bank (${ready})`;
  $("#release").disabled = ready === 0;
  const actions = [...new Set(d.items.map((i) => i.action))], segs = [...new Set(d.items.map((i) => i.segment))];
  const keepA = $("#fAction").value, keepS = $("#fSegment").value, keepC = $("#fCollector").value;
  $("#fCollector").innerHTML = `<option value="">All collectors</option>` + d.queues.filter((q) => q.tasks).map((q) =>
    `<option value="${esc(q.collector_id)}" ${q.collector_id === keepC ? "selected" : ""}>${esc(q.name)} (${q.tasks})</option>`).join("");
  $("#bulkApprove").disabled = $("#bulkApprove").disabled || d.run.status === "superseded" || d.day_closed;
  $("#release").disabled = $("#release").disabled || d.run.status === "superseded" || d.day_closed;
  $("#fAction").innerHTML = `<option value="">All actions</option>` + actions.map((a) => `<option ${a === keepA ? "selected" : ""}>${esc(a)}</option>`).join("");
  $("#fSegment").innerHTML = `<option value="">All segments</option>` + segs.map((x) => `<option value="${esc(x)}" ${x === keepS ? "selected" : ""}>${esc(SEG_LABEL[x] || x)}</option>`).join("");
  renderWorklist();
}

function ciBar(u, lo, hi, scale = 0.5) {
  const pos = (v) => Math.max(0, Math.min(100, 50 + (v / scale) * 50));
  return `<div class="ci" title="uplift ${fmt.pts(u)} (${fmt.pts(lo)} to ${fmt.pts(hi)})"><span class="band" style="left:${pos(lo)}%;width:${Math.max(1, pos(hi) - pos(lo))}%"></span>
    <span class="pt" style="left:calc(${pos(u)}% - 1px)"></span></div>`;
}

function renderWorklist() {
  const q = $("#fSearch").value.trim().toLowerCase(), a = $("#fAction").value, sg = $("#fSegment").value, dc = $("#fDecision").value, onlyEsc = $("#fEsc").checked;
  const col = $("#fCollector").value;
  const rows = M.data.items.filter((i) => (!q || i.account_token.includes(q)) && (!a || i.action === a) && (!sg || i.segment === sg)
    && (!dc || i.decision === dc) && (!onlyEsc || i.escalations.length) && (!col || i.collector_id === col));
  if (col) rows.sort((x, y) => (x.queue_position || 0) - (y.queue_position || 0));  // one person's queue, in working order
  $("#fCount").textContent = `${rows.length} of ${M.data.items.length}`;
  $("#worklist").innerHTML = rows.length ? `<div class="tbl-wrap" style="max-height:calc(100vh - 330px);border:0;border-radius:0">
    <table class="tbl"><thead><tr><th class="r">Rank</th><th>Collector · #</th><th>Account</th><th>Product</th><th class="r">Balance</th><th class="r">DPD</th><th>Grade</th>
    <th>Segment</th><th>Action</th><th>Uplift (90% interval)</th><th class="r">Value</th><th class="r">Min</th><th>Flags</th><th>Decision</th></tr></thead><tbody>
    ${rows.map((i) => `<tr class="click ${M.open === i.account_token ? "sel" : ""}" data-t="${esc(i.account_token)}">
      <td class="r num">${i.rank}</td><td class="who">${i.collector_name ? `${esc(i.collector_name)} <span class="mono">#${i.queue_position || "–"}</span>` : "–"}</td><td class="mono">${esc(i.account_token)}</td><td>${esc(i.product)}</td>
      <td class="r num">${fmt.money(i.exposure)}</td><td class="r num">${i.dpd}</td><td class="mono">${esc(i.risk_grade)}</td>
      <td>${segPill(i.segment, i.segment_label)}</td><td><b>${esc(i.action_label)}</b></td>
      <td><div style="display:flex;gap:8px;align-items:center">${ciBar(i.uplift, i.uplift_low, i.uplift_high)}<span class="num">${fmt.pts(i.uplift)}</span></div></td>
      <td class="r num">${fmt.money(i.action_value)}</td><td class="r num">${i.minutes}</td>
      <td>${i.escalations.map((f) => `<span class="flag" title="${esc(f.detail)}">${esc(f.code.replace(/_/g, " "))}</span>`).join("")}</td>
      <td>${statusPill(i.decision)}${i.decision === "edited" ? ` <span class="mono muted">→ ${esc(i.final_action)}</span>` : ""}${i.released ? ' <span class="pill good">sent</span>' : ""}</td>
    </tr>`).join("")}</tbody></table></div>` : `<div class="empty">No recommendations match these filters.</div>`;
  $$("#worklist tr.click").forEach((tr) => tr.onclick = () => openAccount(tr.dataset.t));
}

function srcClass(src) {
  if (!src) return "";
  if (src.startsWith("model:")) return "src-model";
  if (src.startsWith("solver:")) return "src-solver";
  if (src.startsWith("identity:")) return "src-identity";
  return "";
}

function renderSegments(segs) {
  return segs.map((s) => s.key ? `<span class="fig ${srcClass(s.source)}" data-src="${esc(s.source)}" title="${esc(s.key)} — ${esc(s.source)}${s.run_id ? " · " + esc(s.run_id) : ""}">${esc(s.text)}</span>` : esc(s.text)).join("");
}

async function openAccount(token) {
  M.open = token;
  $("#drawer").classList.add("open");
  $("#drawerTitle").textContent = token;
  $("#drawerBody").innerHTML = `<div class="empty">Loading…</div>`;
  $$("#worklist tr.click").forEach((tr) => tr.classList.toggle("sel", tr.dataset.t === token));
  const d = await api(`/api/runs/${M.runId}/accounts/${token}`);
  const rec = d.recommendation, appr = d.approval, locked = M.data.items.find((i) => i.account_token === token)?.released;
  const reasons = M.data.reason_codes.map((r) => `<option value="${esc(r)}">${esc(r.replace(/_/g, " "))}</option>`).join("");
  let checksHTML = "", lastGrp = null;
  d.checks.forEach((c) => {
    if (c.group !== lastGrp) { checksHTML += `<div class="grp">${esc(c.group.replace(/_/g, " "))}</div>`; lastGrp = c.group; }
    checksHTML += `<div><span class="${c.passed ? "ok" : "no"}">${c.passed ? "✓" : "✕"}</span><span class="mono">${esc(c.check)}</span><span class="muted">${esc(c.detail)}</span></div>`;
  });
  $("#drawerBody").innerHTML = `
    ${rec ? `
    <div class="rec-card">
      <div class="eyebrow">Recommended · rank ${rec.rank}</div>
      <div class="act">${esc(rec.facts.action_label.value)}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center">
        ${segPill(rec.segment, rec.facts.segment_label.value)}
        <span class="pill ${rec.template_source === "llm" ? "violet" : ""}">${rec.template_source === "llm" ? "LLM template" : "fallback template"}</span>
        ${(rec.escalations || []).map((f) => `<span class="flag" title="${esc(f.detail)}">${esc(f.code.replace(/_/g, " "))}</span>`).join("")}
      </div>
      <div class="tiles" style="margin-top:10px">
        <div class="tile"><div class="k">Action value</div><div class="v">${esc(rec.facts.action_value.display)}</div></div>
        <div class="tile"><div class="k">Uplift</div><div class="v">${esc(rec.facts.uplift.display)}</div><div class="s">${esc(rec.facts.uplift_low.display)} to ${esc(rec.facts.uplift_high.display)}</div></div>
        <div class="tile"><div class="k">Expected loss</div><div class="v">${esc(rec.facts.expected_loss.display)}</div></div>
        <div class="tile"><div class="k">Base cure</div><div class="v">${esc(rec.facts.base_cure.display)}</div><div class="s">without contact</div></div>
      </div>
    </div>
    <div class="section-title">Why — every figure is sourced</div>
    <div class="prose" id="rationale">${renderSegments(rec.rationale)}</div>
    <div class="legend"><span><span class="fig">bank / calc</span></span><span><span class="fig src-model">uplift model</span></span>
      <span><span class="fig src-solver">solver</span></span><span><span class="fig src-identity">identity vault</span></span><span>Hover any figure for its source.</span></div>
    <div class="section-title">Outreach script <span class="muted">(first name rejoined from the identity vault only here)</span></div>
    <div class="prose">${renderSegments(rec.script)}</div>

    <div class="section-title">Your decision</div>
    <div class="decide">
      ${appr ? `<div class="callout ${appr.decision === "rejected" ? "bad" : "good"}">Current: <b>${esc(appr.decision)}</b>${appr.final_action ? ` → ${esc(appr.final_action)}` : ""} by ${esc(appr.approver_id)}${appr.reason_code ? ` · ${esc(appr.reason_code)}` : ""}</div>` : ""}
      ${locked ? `<div class="callout">Released to the bank — decisions are locked.</div>` : `
      <div class="row"><button class="btn good" id="dApprove">Approve ${esc(rec.facts.action_label.value)}</button></div>
      <div class="row"><select class="input" id="dEditAction">${rec.allowed_actions.filter((x) => x !== rec.action).map((x) => `<option>${esc(x)}</option>`).join("")}</select>
        <select class="input" id="dEditReason"><option value="">Reason…</option>${reasons}</select>
        <button class="btn" id="dEdit" ${rec.allowed_actions.length > 1 ? "" : "disabled"}>Change action</button></div>
      <div class="row"><select class="input" id="dRejectReason"><option value="">Reason…</option>${reasons}</select>
        <button class="btn bad" id="dReject">Reject</button></div>
      <textarea class="input" id="dNote" placeholder="Note for the audit trail (optional)"></textarea>`}
    </div>` : `<div class="callout warn">Not in today's worklist. The review below shows why.</div>`}

    <div class="section-title">Why this account, and not another? <span class="muted">solver counterfactual</span></div>
    <button class="btn" id="explainBtn">Ask the Decision Agent</button>
    <div id="explainOut" style="margin-top:8px"></div>

    <div class="section-title">All options for this account</div>
    ${tableHTML(d.alternatives.map((a) => ({ action: a.action, allowed: a.allowed, value: a.value, uplift: a.uplift, low: a.uplift_lower, high: a.uplift_upper, minutes: a.minutes, blocked_by: a.blocked_reasons || "" })),
      { maxHeight: 260, render: { value: (v) => `<span class="num">${fmt.money(v)}</span>`, uplift: (v) => `<span class="num">${fmt.pts(v)}</span>`,
        low: (v) => `<span class="num">${fmt.pts(v)}</span>`, high: (v) => `<span class="num">${fmt.pts(v)}</span>` } })}

    <div class="section-title">Policy checks</div>
    <div class="checks">${checksHTML}</div>

    <div class="section-title">Account data (tokenised)</div>
    ${kvHTML(d.attributes)}`;

  $("#explainBtn").onclick = async () => {
    $("#explainOut").innerHTML = `<span class="muted">Re-solving…</span>`;
    try {
      const x = await api(`/api/runs/${M.runId}/accounts/${token}/explain`, { method: "POST" });
      const list = (arr) => (arr || []).slice(0, 8).map((r) => `<span class="mono">${esc(r.account_token)}</span> ${esc(r.action)} ${fmt.money(r.value)}`).join("<br>");
      if (x.was_selected) {
        $("#explainOut").innerHTML = `<div class="callout good">Dropping this account lowers the plan's estimated value by <b>${fmt.money(x.cost_of_change)}</b>.
          The solver would fill its slot with:<br>${list(x.would_be_replaced_by) || "nothing that adds value"}</div>`;
      } else if (x.reason === "outcompeted") {
        $("#explainOut").innerHTML = `<div class="callout warn">Forcing <b>${esc(x.forced_action)}</b> (worth ${fmt.money(x.forced_value)}) costs the plan <b>${fmt.money(x.cost_of_change)}</b> overall, because it would displace:<br>${list(x.would_displace)}</div>`;
      } else if (x.reason === "no_positive_value") {
        $("#explainOut").innerHTML = `<div class="callout bad">No action is estimated to add value. Best option ${esc(x.best_action)} is worth ${fmt.money(x.best_value)} — contact is expected to help too little, or to hurt.</div>`;
      } else {
        $("#explainOut").innerHTML = `<div class="callout bad">No action is allowed by policy for this account.</div>`;
      }
    } catch (e) { $("#explainOut").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  };
  if (!rec || locked) return;
  const send = async (body) => {
    try {
      await api(`/api/runs/${M.runId}/accounts/${token}/decision`, { method: "POST", body: JSON.stringify({ ...body, reason_text: $("#dNote").value || null }) });
      toast(`${body.decision} ${token}`); await loadWorklist(); openAccount(token); loadDay();
    } catch (e) { toast(e.message, "bad"); }
  };
  $("#dApprove").onclick = () => send({ decision: "approved" });
  $("#dEdit").onclick = () => send({ decision: "edited", final_action: $("#dEditAction").value, reason_code: $("#dEditReason").value || null });
  $("#dReject").onclick = () => send({ decision: "rejected", reason_code: $("#dRejectReason").value || null });
}

async function renderCompare() {
  const el = $("#tab-compare");
  el.innerHTML = `<div class="empty">Loading…</div>`;
  M.compare = await api(`/api/runs/${M.runId}/comparison`);
  const c = M.compare, s = c.summary;
  const names = { sort_by_risk: "Sort by risk score", sort_by_value: "Sort by expected loss", optimised: "Optimised (uplift + CBC)" };
  const lo = Math.min(0, ...Object.values(s).map((p) => p.mc.value_p10)), hi = Math.max(...Object.values(s).map((p) => p.mc.value_p90));
  const pos = (v) => ((v - lo) / (hi - lo || 1)) * 100;
  const card = (k) => {
    const p = s[k], segTotal = Object.values(p.segments).reduce((a, b) => a + b, 0) || 1;
    return `<article class="policy ${k === "optimised" ? "win" : ""}">
      <div class="eyebrow">${k === "optimised" ? "Recommended" : "Baseline"}</div><h3>${esc(names[k])}</h3>
      <div class="big">${fmt.money(p.est_value)}</div><div class="muted">estimated avoided loss · ${fmt.int(p.selected)} actions · ${fmt.int(p.minutes)} min</div>
      <div class="range" title="Monte Carlo P10–P90">${lo < 0 ? `<span class="zero" style="left:${pos(0)}%"></span>` : ""}
        <span class="band" style="left:${pos(p.mc.value_p10)}%;width:${Math.max(1, pos(p.mc.value_p90) - pos(p.mc.value_p10))}%"></span>
        <span class="mid" style="left:${pos(p.mc.value_p50)}%"></span></div>
      <div class="muted num">P10 ${fmt.money(p.mc.value_p10)} · P50 ${fmt.money(p.mc.value_p50)} · P90 ${fmt.money(p.mc.value_p90)}</div>
      <div class="stack">${Object.entries(p.segments).map(([g, n]) => `<i style="width:${(n / segTotal) * 100}%;background:${SEG_COLORS[g]}" title="${esc(SEG_LABEL[g])} ${n}"></i>`).join("")}</div>
      <div class="stack-legend">${Object.entries(p.segments).map(([g, n]) => `<span><i class="sw" style="background:${SEG_COLORS[g]}"></i>${esc(SEG_LABEL[g])} ${n}</span>`).join("")}</div>
      <div class="tiles" style="margin-top:10px">
        <div class="tile"><div class="k">Harmful by estimate</div><div class="v">${fmt.int(p.negative_value_actions)}</div><div class="s">${
          k === "optimised" ? "zero by construction — the solver never picks negative estimates; see the benchmark for true harm"
                            : "actions the model expects to backfire"}</div></div>
        ${p.des ? `<div class="tile"><div class="k">Fits the shift</div><div class="v">${fmt.pct(p.des.completion_rate_mean, 0)}</div><div class="s">voice tasks completed (SimPy)</div></div>` : ""}
      </div></article>`;
  };
  const ev = c.evaluation;
  el.innerHTML = `
    <div class="policies">${["sort_by_risk", "sort_by_value", "optimised"].map(card).join("")}</div>
    <div class="callout" style="margin-top:12px">Same policy, same capacity, same per-member limits — only the selection rule differs.
      Optimised beats the risk sort by <b>${fmt.money(s.optimised.est_value - s.sort_by_risk.est_value)}</b> on the model's estimates.
      Estimates are the model's own view; the synthetic benchmark scores all three against the bank's sealed truth.</div>
    <div class="eval" id="evalBox">${ev ? evalHTML(ev) : `<button class="btn" id="runEval">Run synthetic benchmark (sealed truth)</button>`}</div>
    <div class="two">
      <div><div class="section-title">Chosen by the optimiser, skipped by the risk sort</div>${compareTable(c.only_in_optimised)}</div>
      <div><div class="section-title">Chosen by the risk sort, skipped by the optimiser</div>${compareTable(c.only_in_risk_sort)}
        <p class="note">Click any row to see why — including the solver's counterfactual.</p></div>
    </div>`;
  $$("#tab-compare tr.click").forEach((tr) => tr.onclick = () => openAccount(tr.dataset.t));
  if ($("#runEval")) $("#runEval").onclick = async () => {
    $("#evalBox").innerHTML = `<span class="muted">Scoring against sealed truth…</span>`;
    try { const e = await api(`/api/runs/${M.runId}/evaluate`, { method: "POST" }); $("#evalBox").innerHTML = evalHTML(e); }
    catch (e) { $("#evalBox").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  };
}

function compareTable(rows) {
  if (!rows.length) return `<div class="empty">None</div>`;
  return `<div class="tbl-wrap" style="max-height:420px"><table class="tbl"><thead><tr><th>Account</th><th>Action</th><th>Segment</th><th>Grade</th><th class="r">Balance</th><th class="r">Est. value</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="click" data-t="${esc(r.account_token)}"><td class="mono">${esc(r.account_token)}</td><td>${esc(r.action)}</td><td>${segPill(r.segment, SEG_LABEL[r.segment])}</td>
      <td class="mono">${esc(r.risk_grade)}</td><td class="r num">${fmt.money(r.exposure)}</td><td class="r num">${fmt.money(r.value)}</td></tr>`).join("")}</tbody></table></div>`;
}

function evalHTML(e) {
  const p = e.plans;
  return `<div class="panel" style="box-shadow:none"><div class="panel-h"><h2>Synthetic benchmark — scored against sealed truth</h2><span class="pill violet">not available on a real portfolio</span></div>
    <div class="panel-b"><div class="tiles">
      <div class="tile"><div class="k">True value · optimised</div><div class="v">${fmt.money(p.optimised.true_value)}</div><div class="s">est. ${fmt.money(p.optimised.estimated_value)} · ${fmt.int(p.optimised.harmful_actions)} truly harmful</div></div>
      <div class="tile"><div class="k">True value · risk sort</div><div class="v">${fmt.money(p.sort_by_risk.true_value)}</div><div class="s">${fmt.int(p.sort_by_risk.harmful_actions)} truly harmful</div></div>
      <div class="tile"><div class="k">True value · value sort</div><div class="v">${fmt.money(p.sort_by_value.true_value)}</div><div class="s">${fmt.int(p.sort_by_value.harmful_actions)} truly harmful</div></div>
      <div class="tile"><div class="k">True lift vs risk sort</div><div class="v">${fmt.pct(e.true_lift_vs_sort_by_risk, 0)}</div></div>
      <div class="tile"><div class="k">True lift vs value sort</div><div class="v">${fmt.pct(e.true_lift_vs_sort_by_value, 0)}</div></div>
      <div class="tile"><div class="k">Capture of oracle</div><div class="v">${fmt.pct(e.capture_of_oracle, 0)}</div><div class="s">oracle ${fmt.money(e.oracle_true_value)}</div></div>
      <div class="tile"><div class="k">Estimate ÷ truth</div><div class="v">${(e.estimate_to_truth_ratio || 0).toFixed(2)}×</div><div class="s">model optimism</div></div>
    </div>
    <p class="note">Uplift correlation with truth: ${Object.entries(e.uplift_correlation_with_truth).map(([a, v]) => `${esc(a)} ${v.toFixed(2)}`).join(" · ")}</p></div></div>`;
}

async function renderModel() {
  const el = $("#tab-model");
  el.innerHTML = `<div class="empty">Loading…</div>`;
  const m = await api(`/api/runs/${M.runId}/model`);
  const per = m.validation.per_action;
  const first = Object.keys(per)[0];
  el.innerHTML = `
    <div class="section-title">Holdout validation <span class="muted">(Qini above zero means ranking by uplift beats random targeting)</span></div>
    <div class="tiles">${Object.entries(per).map(([a, v]) => `<div class="tile"><div class="k">Qini · ${esc(a)}</div><div class="v">${v.qini.toFixed(3)}</div><div class="s">${fmt.int(v.holdout_rows)} holdout rows</div></div>`).join("")}</div>
    <div class="section-title">Predicted vs observed uplift by decile
      <select class="input" id="decileAction">${Object.keys(per).map((a) => `<option>${esc(a)}</option>`).join("")}</select></div>
    <div id="deciles"></div>
    <div class="two">
      <div><div class="section-title">Causal feasibility</div>${tableHTML(m.feasibility.checks, { maxHeight: 300 })}</div>
      <div><div class="section-title">Solver</div>${kvHTML(m.solver)}
        <div class="section-title">Governance</div>
        <div class="tiles">
          <div class="tile"><div class="k">Unstamped numbers</div><div class="v">${fmt.int(m.governance.unstamped_numbers)}</div><div class="s">digits written by the LLM — must be zero</div></div>
          <div class="tile"><div class="k">Provenance complete</div><div class="v">${fmt.int(m.governance.provenance_complete)} / ${fmt.int(m.governance.recommendations)}</div></div>
        </div>
        <div class="section-title">Overrides by reason <span class="muted">(labelled signal for retraining)</span></div>
        ${tableHTML(m.governance.overrides, { maxHeight: 200 })}</div>
    </div>
    <div class="section-title">Segments today</div>${kvHTML(m.segments)}`;
  const draw = (a) => $("#deciles").innerHTML = tableHTML(per[a].deciles.map((d) => ({ decile: d.decile, predicted_uplift: d.predicted, observed_uplift: d.observed, rows: d.n, treated: d.treated })),
    { maxHeight: 360, render: { predicted_uplift: (v) => `<span class="num">${fmt.pts(v)}</span>`, observed_uplift: (v) => `<span class="num">${v === null ? "–" : fmt.pts(v)}</span>` } });
  draw(first);
  $("#decileAction").onchange = (e) => draw(e.target.value);
}

async function renderAudit() {
  const el = $("#tab-audit");
  el.innerHTML = `<div class="empty">Loading…</div>`;
  const a = await api(`/api/runs/${M.runId}/audit`);
  el.innerHTML = `<div class="callout ${a.chain.valid ? "good" : "bad"}">Hash chain ${a.chain.valid ? "verified" : `BROKEN at event ${a.chain.broken_at_seq}`} · ${fmt.int(a.chain.events)} events across all runs
    ${a.chain.head ? ` · head <span class="mono">${esc(a.chain.head.slice(0, 20))}…</span>` : ""}. Rows are append-only: the database refuses updates and deletes.</div>
    <div style="margin-top:10px">${tableHTML(a.events.map((e) => ({ seq: e.seq, time: e.created_at, event: e.event_type, actor: e.actor, hash: e.hash.slice(0, 16), prev: e.prev_hash.slice(0, 16), payload: e.payload })),
      { maxHeight: 560, render: { hash: (v) => `<span class="mono">${esc(v)}</span>`, prev: (v) => `<span class="mono muted">${esc(v)}</span>`, time: (v) => `<span class="num">${esc(v)}</span>` } })}</div>`;
}

function showTab(t) {
  M.tab = t;
  $$("#tabs .tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  ["worklist", "queues", "compare", "model", "audit"].forEach((x) => $("#tab-" + x).classList.toggle("hidden", x !== t));
  if (t === "queues") renderQueues();
  if (t === "compare") renderCompare();
  if (t === "model") renderModel();
  if (t === "audit") renderAudit();
}

// ------------------------------------------------------------------------------------------ business day
const SHIFTS = [["360", "Full day · 6 h"], ["300", "5 h"], ["240", "Part time · 4 h"], ["180", "Half day · 3 h"], ["120", "2 h"], ["custom", "Custom…"]];
const STEP_ICON = { done: "✓", running: "…" };

function niceDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

async function loadDay() {
  try { D.day = await api("/api/day"); }
  catch (e) { $("#dayPanel").innerHTML = `<div class="day-msg" style="padding-top:12px"><div class="callout bad">${esc(e.message)}</div></div>`; return; }
  renderDay();
  if (D.day.business_date) {
    Shell.setContext(`${niceDate(D.day.business_date)} · ${D.day.closed ? "day closed" : (D.day.run ? D.day.run.status.replace(/_/g, " ") : "no run yet")}`);
    Shell.setPending(D.day.counts ? D.day.counts.pending : 0);
  }
  const running = D.day.run && D.day.run.status === "running";
  if (running && !D.poll) D.poll = setInterval(pollRun, 1500);
}

async function pollRun() {
  const before = D.day && D.day.run && D.day.run.run_id;
  await loadDay();
  if (!D.day.run || D.day.run.status !== "running") {
    clearInterval(D.poll); D.poll = null;
    const r = D.day.run;
    if (r && ["awaiting_approval", "released"].includes(r.status)) { toast(`Today's worklist is ready (${r.run_id})`); await loadRuns(r.run_id); }
    else if (before) {
      const run = await api(`/api/runs/${before}`).catch(() => null);
      if (run && ["failed", "aborted"].includes(run.run.status)) toast(`The run stopped: ${run.run.error || run.run.status}. See the Admin Console.`, "bad");
    }
  }
}

function renderDay() {
  const d = D.day, el = $("#dayPanel");
  if (!d.bank_ok) {
    el.innerHTML = `<div class="day-msg" style="padding-top:12px"><div class="callout bad"><b>The bank is not reachable.</b> ${esc(d.bank_error || "")}<br>
      Nothing can be planned until it is back. Start it with <span class="mono">make up</span>.</div></div>`;
    return;
  }
  const c = d.counts, run = d.run, t = d.roster, working = t.collectors.filter((x) => x.present && x.shift_minutes > 0);
  const minutes = working.reduce((a, x) => a + x.shift_minutes, 0);
  const steps = d.steps.map((s, i) => `<div class="step ${esc(s.status)}"><i>${STEP_ICON[s.status] || i + 1}</i>
    <div><b>${esc(s.title)}</b><span title="${esc(s.detail)}">${esc(s.detail)}</span></div></div>`).join("");

  let primary = "", msgs = [];
  const running = run && run.status === "running";
  if (d.closed) {
    msgs.push(`<div class="callout good">This business day is closed. The bank has not opened the next day yet.</div>`);
  } else if (running) {
    primary = `<button class="btn primary" disabled>Agents are planning…</button><a class="btn" href="/admin?run=${esc(run.run_id)}">Watch in Admin Console</a>`;
    msgs.push(`<div class="callout">${run.parent_run_id ? "Re-planning for the new team. The bank's data and the model are reused, so this takes seconds." :
      "The agents are pulling today's data, scoring every account and building a queue for each collector. This usually takes under a minute."}</div>`);
  } else if (t.source !== "saved" && !run) {
    primary = `<button class="btn primary needs-write" id="dayTeam">Confirm today's team</button><button class="btn needs-write" id="dayStart">Start with this team</button>`;
    msgs.push(`<div class="callout">Before the agents plan the day, check who is working. ${t.source === "carried_forward"
      ? `The team below is <b>carried forward</b> from ${esc(t.note ? t.note.replace("carried forward from ", "") : "the last saved day")}.`
      : `No team has been saved yet, so the <b>default team</b> from the configuration is shown.`}
      Each collector's queue is planned to fit their own shift.</div>`);
  } else if (!run) {
    primary = `<button class="btn primary needs-write" id="dayStart">Start today's run</button>`;
  } else if (d.team_changed_since_run) {
    primary = `<button class="btn primary needs-write" id="dayReplan">Re-plan for the new team</button>`;
    msgs.push(`<div class="callout warn"><b>The team changed after this worklist was made.</b> Re-planning chooses members again for the new team
      and replaces the current worklist${c.items - c.pending > 0 ? ` — the <b>${c.items - c.pending}</b> decision(s) made so far will need to be made again` : ""}.
      It reuses today's data and model, so it takes seconds.</div>`);
  } else if (c.pending) {
    msgs.push(`<div class="callout">${c.pending} of ${c.items} recommendations still need a decision. Review them below, or open <b>Team queues</b> to work collector by collector.</div>`);
    if (c.ready) primary = `<button class="btn primary needs-write" id="dayRelease">Release ${c.ready} approved</button>`;
  } else if (c.ready) {
    primary = `<button class="btn primary needs-write" id="dayRelease">Release ${c.ready} approved to the bank</button>`;
  } else {
    primary = `<button class="btn primary needs-write" id="dayClose">Close the day</button>`;
    msgs.push(`<div class="callout good">Everything is decided${c.released ? ` and ${c.released} action(s) are with the bank` : ""}.
      Closing the day freezes today's report and moves the bank to the next business day.</div>`);
  }
  const secondary = [
    !running && !d.closed && primary.indexOf("dayTeam") < 0 ? `<button class="btn needs-write" id="dayTeam2">Edit today's team</button>` : "",
    !running && !d.closed && primary.indexOf("dayClose") < 0 && run ? `<button class="btn needs-write" id="dayClose2">Close the day</button>` : "",
    `<a class="btn" href="/report?date=${encodeURIComponent(d.business_date)}">Today's report</a>`,
  ].join("");

  el.innerHTML = `<div class="day-top">
      <div class="day-date"><span class="eyebrow">Business day${d.simulation ? " · simulated bank" : ""}</span><b>${esc(niceDate(d.business_date))}</b>
        <span class="muted">${working.length} collectors · ${fmt.int(minutes)} min</span></div>
      <div class="day-actions">${primary}${secondary}</div>
    </div><div class="steps">${steps}</div>${msgs.length ? `<div class="day-msg">${msgs.join("")}</div>` : ""}`;

  const on = (id, fn) => { const b = $("#" + id); if (b) b.onclick = fn; };
  on("dayTeam", openTeam); on("dayTeam2", openTeam);
  on("dayStart", startToday); on("dayReplan", replanToday);
  on("dayRelease", () => $("#release").click());
  on("dayClose", closeDay); on("dayClose2", closeDay);
}

async function startToday() {
  try {
    if (D.day.roster.source !== "saved") {   // starting "with this team" confirms it, so the report names who worked
      await api(`/api/roster/${D.day.business_date}`, { method: "PUT", body: JSON.stringify({ collectors: D.day.roster.collectors, note: "confirmed at run start" }) });
    }
    const r = await api("/api/runs", { method: "POST" });
    toast(`Started ${r.run_id}`);
    await loadDay();
  } catch (e) { toast(e.message, "bad"); }
}

async function replanToday() {
  const c = D.day.counts, decided = c.items - c.pending;
  if (decided && !confirm(`Re-plan for the new team?\n\nThe current worklist will be replaced and its ${decided} decision(s) will need to be made again.`)) return;
  try {
    const r = await api(`/api/runs/${D.day.run.run_id}/replan`, { method: "POST", body: "{}" });
    toast(`Re-planning (${r.run_id})`);
    await loadDay();
  } catch (e) { toast(e.message, "bad"); }
}

async function closeDay(confirmed = false) {
  const d = D.day;
  if (confirmed !== true && !confirm(`Close ${niceDate(d.business_date)}?\n\nToday's report will be frozen and the bank will move to the next business day. Actions not yet released will not happen.`)) return;
  try {
    const r = await api("/api/day/close", { method: "POST", body: JSON.stringify({ confirm_unreleased: confirmed === true }) });
    toast(`Closed ${r.closed}. ${r.next_business_date ? `The bank is now on ${r.next_business_date}.` : ""}`);
    await loadDay(); await loadRuns();
    if (confirm("Open the report for the day you just closed?")) location.href = r.report_url;
  } catch (e) {
    if (/Close anyway\?/.test(e.message)) { if (confirm(e.message)) closeDay(true); }
    else toast(e.message, "bad");
  }
}

// --------------------------------------------------------------------------------------------- team
async function openTeam() {
  const d = D.day;
  $("#teamTitle").textContent = niceDate(d.business_date);
  $("#teamDrawer").classList.add("open");
  const r = await api(`/api/roster/${d.business_date}`);
  D.team = r.roster.collectors.map((c) => ({ ...c }));
  D.teamHistory = r.history;
  D.teamSource = r.roster;
  renderTeam();
}

function renderTeam(savedInfo) {
  const buf = D.day.planning_buffer, rows = D.team;
  const working = rows.filter((c) => c.present && c.shift_minutes > 0);
  const total = working.reduce((a, c) => a + Number(c.shift_minutes || 0), 0), plannable = total * (1 - buf);
  const src = D.teamSource;
  $("#teamBody").innerHTML = `
    <p class="team-intro">Tell the agents who is working today and for how long. The planner builds <b>one queue per collector</b>
      and fills it to <b>${fmt.pct(1 - buf, 0)}</b> of their shift, keeping ${fmt.pct(buf, 0)} spare because calls run long.
      Text reminders go to an automated queue and use nobody's time. If today's worklist already exists, saving a different team lets you re-plan it.</p>
    ${src.source !== "saved" ? `<div class="callout" style="margin-bottom:10px">${src.source === "carried_forward" ? `Carried forward: ${esc(src.note || "")}.` : "Default team from the Institution Pack — no team saved yet."} Check it and save.</div>` : ""}
    <div class="team-row head"><span>Working</span><span>Name</span><span>Shift</span><span class="h-custom">Minutes</span><span class="h-plan" style="text-align:right">Plannable</span><span></span></div>
    ${rows.map((c, i) => {
      const preset = SHIFTS.some(([v]) => v === String(c.shift_minutes)) ? String(c.shift_minutes) : "custom";
      return `<div class="team-row ${c.present ? "" : "off"}" data-i="${i}">
        <label class="toggle" title="${c.present ? "Working today" : "Not working today"}"><input type="checkbox" data-f="present" ${c.present ? "checked" : ""} aria-label="${esc(c.name)} working today"><span></span></label>
        <input class="input" data-f="name" value="${esc(c.name)}" aria-label="Name" maxlength="80">
        <select class="input" data-f="preset" aria-label="Shift" ${c.present ? "" : "disabled"}>${SHIFTS.map(([v, l]) => `<option value="${v}" ${v === preset ? "selected" : ""}>${l}</option>`).join("")}</select>
        <input class="input custom num" data-f="shift_minutes" type="number" min="0" max="720" step="15" value="${c.shift_minutes}" aria-label="Shift minutes" ${c.present ? "" : "disabled"}>
        <span class="plan">${c.present ? fmt.int(c.shift_minutes * (1 - buf)) + " min" : "off"}</span>
        <button class="btn small" data-f="remove" title="Remove ${esc(c.name)}" aria-label="Remove ${esc(c.name)}">✕</button>
      </div>`; }).join("")}
    <div style="margin:10px 0 14px"><button class="btn small needs-write" id="teamAdd">+ Add collector</button></div>
    <div class="tiles">
      <div class="tile"><div class="k">Working today</div><div class="v">${working.length} of ${rows.length}</div></div>
      <div class="tile"><div class="k">Shift minutes</div><div class="v">${fmt.int(total)}</div><div class="s">${(total / 60).toFixed(1)} hours in total</div></div>
      <div class="tile"><div class="k">Plannable</div><div class="v">${fmt.int(plannable)}</div><div class="s">about ${fmt.int(plannable / 12)} collector calls</div></div>
    </div>
    ${working.length ? "" : `<div class="callout warn" style="margin-top:10px">Nobody is working: only automated text reminders can be planned.</div>`}
    <div class="section-title">Note for the report <span class="muted">optional</span></div>
    <input class="input" id="teamNote" style="width:100%" placeholder="e.g. Lena on leave, Marcus leaves at 1pm" maxlength="300">
    <div style="display:flex;gap:8px;align-items:center;margin-top:12px">
      <button class="btn primary needs-write" id="teamSave">Save today's team</button><span class="muted" id="teamMsg"></span></div>
    <div id="teamAfter" style="margin-top:12px">${savedInfo || ""}</div>
    ${D.teamHistory.length ? `<div class="section-title">Saved today</div>${tableHTML(D.teamHistory.map((h) => ({
      saved_at: fmt.time(h.saved_at).slice(0, 8), by: h.saved_by, working: h.collectors.filter((c) => c.present).length,
      minutes: h.collectors.filter((c) => c.present).reduce((a, c) => a + c.shift_minutes, 0), note: h.note || "" })), { maxHeight: 200 })}` : ""}`;

  $$("#teamBody .team-row[data-i]").forEach((row) => {
    const c = D.team[Number(row.dataset.i)];
    row.querySelector('[data-f="present"]').onchange = (e) => { c.present = e.target.checked; renderTeam(); };
    row.querySelector('[data-f="name"]').oninput = (e) => { c.name = e.target.value; };
    row.querySelector('[data-f="preset"]').onchange = (e) => { if (e.target.value !== "custom") c.shift_minutes = Number(e.target.value); renderTeam(); };
    row.querySelector('[data-f="shift_minutes"]').onchange = (e) => { c.shift_minutes = Math.max(0, Math.min(720, Number(e.target.value) || 0)); renderTeam(); };
    row.querySelector('[data-f="remove"]').onclick = () => { D.team.splice(Number(row.dataset.i), 1); renderTeam(); };
  });
  $("#teamAdd").onclick = () => {
    const used = new Set(D.team.map((c) => c.id));
    let n = D.team.length + 1; while (used.has(`C${String(n).padStart(2, "0")}`)) n++;
    D.team.push({ id: `C${String(n).padStart(2, "0")}`, name: `Collector ${n}`, present: true, shift_minutes: 360 });
    renderTeam();
  };
  $("#teamSave").onclick = saveTeam;
}

async function saveTeam() {
  if (!D.team.length) { toast("Add at least one collector", "bad"); return; }
  if (D.team.some((c) => !c.name.trim())) { toast("Every collector needs a name", "bad"); return; }
  $("#teamSave").disabled = true;
  try {
    const r = await api(`/api/roster/${D.day.business_date}`, { method: "PUT", body: JSON.stringify({ collectors: D.team, note: $("#teamNote").value || null }) });
    toast("Today's team saved");
    await loadDay();
    const h = await api(`/api/roster/${D.day.business_date}`);
    D.teamHistory = h.history; D.teamSource = h.roster;
    renderTeam(r.replan_suggested ? `<div class="callout warn"><b>Today's worklist was planned for a different team.</b>
      Re-plan it so every queue matches who is working.<div style="margin-top:8px"><button class="btn primary" id="teamReplan">Re-plan now</button></div></div>`
      : (D.day.run ? "" : `<div class="callout good">Saved. Close this panel and start today's run when you are ready.</div>`));
    const b = $("#teamReplan"); if (b) b.onclick = () => { $("#teamDrawer").classList.remove("open"); replanToday(); };
  } catch (e) { toast(e.message, "bad"); $("#teamSave").disabled = false; }
}

// ------------------------------------------------------------------------------------------- queues
function renderQueues() {
  const el = $("#tab-queues");
  if (!M.data) { el.innerHTML = `<div class="empty">No worklist yet.</div>`; return; }
  const qs = M.data.queues, total = qs.reduce((a, q) => a + q.tasks, 0);
  el.innerHTML = `<p class="note" style="margin:0 0 12px">Each collector gets one queue, packed to fit their shift and worked from the highest value down.
    The bar shows minutes against the plannable part of the shift: <span class="sw" style="background:var(--good)"></span>approved
    <span class="sw" style="background:var(--primary)"></span>waiting for a decision. Changing an action can make a queue longer — a queue over its shift is outlined.</p>
    <div class="queues">${qs.map((q) => {
      const auto = q.collector_id === "automated", cap = q.plannable_minutes || 0;
      const pct = (v) => cap ? Math.min(100, (v / cap) * 100) : 0;
      const decided = q.approved + q.edited + q.rejected;
      return `<article class="q ${q.over_shift ? "over" : ""} ${q.present ? "" : "off"}">
        <h3>${esc(q.name)} ${!q.present ? '<span class="pill">not working</span>' : ""}${q.over_shift ? '<span class="pill warn">over shift</span>' : ""}</h3>
        ${auto ? `<div class="line">Sent automatically — uses no one's shift.</div>` : `
        <div class="meter" title="${fmt.int(q.committed_minutes)} approved + ${fmt.int(q.pending_minutes)} pending of ${fmt.int(cap)} plannable minutes">
          <i style="width:${pct(q.committed_minutes)}%;background:var(--good)"></i>
          <i style="left:${pct(q.committed_minutes)}%;width:${Math.max(0, Math.min(100 - pct(q.committed_minutes), pct(q.pending_minutes)))}%;background:var(--primary)"></i></div>
        <div class="line num">${fmt.int(q.planned_minutes)} of ${fmt.int(cap)} plannable min · shift ${fmt.int(q.shift_minutes)}</div>`}
        <div class="line"><b>${fmt.int(q.tasks)}</b> tasks · est. <b>${fmt.money(q.est_value)}</b> avoided loss</div>
        <div class="line">${decided} decided — ${q.approved} approved · ${q.edited} changed · ${q.rejected} rejected · ${q.pending} pending${q.released ? ` · <b>${q.released} sent</b>` : ""}</div>
        ${q.completion_simulated != null ? `<div class="line">Simulated day: <b>${fmt.pct(q.completion_simulated, 0)}</b> of this queue finishes in the shift</div>` : ""}
        <div class="chips-row">${Object.entries(q.actions).map(([a, n]) => `<span>${esc(a)} ${n}</span>`).join("")}</div>
        ${q.tasks ? `<div><button class="btn small" data-c="${esc(q.collector_id)}">Review ${esc(auto ? "texts" : "this queue")} →</button></div>` : ""}
      </article>`; }).join("")}</div>
    ${total ? "" : `<div class="empty">No tasks were planned.</div>`}`;
  $$("#tab-queues [data-c]").forEach((b) => b.onclick = () => { $("#fCollector").value = b.dataset.c; showTab("worklist"); renderWorklist(); });
}

$("#closeTeam").onclick = () => $("#teamDrawer").classList.remove("open");

$$("#tabs .tab").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
["#fSearch", "#fCollector", "#fAction", "#fSegment", "#fDecision", "#fEsc"].forEach((s) => $(s).addEventListener("input", renderWorklist));
$("#runSelect").onchange = (e) => e.target.value && selectRun(e.target.value);
$("#closeDrawer").onclick = () => { $("#drawer").classList.remove("open"); M.open = null; };
$("#showSrc").onchange = (e) => $("#drawerBody").classList.toggle("show-src", e.target.checked);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#closeDrawer").click(); $("#closeTeam").click(); } });
$("#bulkApprove").onclick = async () => {
  try { const r = await api(`/api/runs/${M.runId}/approve-bulk`, { method: "POST" }); toast(`Approved ${r.approved}; ${r.skipped_escalated} escalated left for review`); await loadWorklist(); loadDay(); }
  catch (e) { toast(e.message, "bad"); }
};
$("#release").onclick = async () => {
  if (!confirm("Release all approved actions to the bank for execution?")) return;
  try { const r = await api(`/api/runs/${M.runId}/release`, { method: "POST" }); toast(`Released ${r.released} actions to the bank`); await loadWorklist(); loadDay(); }
  catch (e) { toast(e.message, "bad"); }
};

Shell.mount({ page: "manager", title: "Today" })
  .then(() => {
    if (Shell.user && Shell.user.role === "guest") {
      $("#actionsNote").textContent = "Signed in as guest: you can read everything. Approving, releasing and closing the day need a manager account.";
    }
    return loadDay();
  })
  .then(() => loadRuns(new URLSearchParams(location.search).get("run")))
  .then(() => {   // deep link from Business KPIs: open one customer's explanation
    const account = new URLSearchParams(location.search).get("account");
    if (account && M.runId) openAccount(account);
  });
