// ==========================================================================
// Last Mile — Demo & Showcase Console Interactive Engine
// ==========================================================================

const D = {
  runId: null,
  status: null,
  worklist: null,
  comparison: null,
  model: null,
  audit: null,
  activeStep: 1,
  mode: "story",
  poll: null,
};

const SPEAKER_NOTES = {
  1: "Banks already have risk models that tell them who might default. But sorting by risk leads to calling Lost Causes who default anyway or Sure Things who cure on their own. Last Mile solves the daily operational question: Who do I contact today, with what action, and why?",
  2: "Our architecture enforces a sequential, deterministic 8-stage pipeline with strict tool ownership. Each agent has an accountable role and cannot invoke unauthorized tools. Everything is executed over clean HTTP interfaces.",
  3: "Rather than predicting default risk alone, our Causal T-Learner estimates incremental uplift. We divide accounts into four distinct quadrants, focusing 100% of scarce capacity on Persuadables and suppressing Sleeping Dogs that would backfire.",
  4: "We never commit a plan without simulation. 2,000 Monte Carlo iterations generate an honest range of recovery (P10 to P90), and SimPy discrete-event simulation models floor queues and stochastic agent call durations.",
  5: "Constrained Mixed-Integer Linear Programming (CBC MIP) solves the multi-choice knapsack assignment. By trading off one expensive high-risk account for three high-uplift accounts, the optimizer achieves +26% higher true recovery over traditional risk sorting.",
  6: "We establish a bulletproof division of labor. The LLM only drafts outreach templates with strict placeholders. Real mathematical models and solvers compute all figures. Digits outside placeholders are rejected by our regex validator (0 unstamped numbers).",
  7: "Governance is built-in, not an afterthought. Every recommendation requires explicit human approval with structured reason codes. All system events, tool calls, and decisions are sealed in an append-only, cryptographically verified SHA-256 hash chain.",
  8: "Our core engine is fully reusable across banking domains. By swapping declarative YAML configuration packs, the exact same engine powers NPA Early Warning, Member Retention, and Fraud Triage without modifying underlying code.",
};

// --------------------------------------------------------------------------
// Initialization & Data Loading
// --------------------------------------------------------------------------
async function initDemo() {
  await loadStatus();
  await loadRuns();
  setupStoryNavigation();
  setupRoiCalculator();
  setupEventListeners();
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    D.status = s;
    if ($("#statusChips")) $("#statusChips").innerHTML = `
      <span class="chip"><i class="dot ${s.bank.ok ? "ok" : "bad"}"></i>Bank API ${s.bank.ok ? `· ${esc(s.bank.as_of)}` : "offline"}</span>
      <span class="chip"><i class="dot ${s.llm_mode.startsWith("gemini") ? "ok" : "warn"}"></i>LLM: ${esc(s.llm_mode)}</span>
      <span class="chip">${esc(s.scenario)}</span>
      <span class="chip mono" title="Configuration SHA-256">cfg: ${esc(fmt.short(s.config_hash, 10))}</span>`;
    
    $("#statScenario").textContent = s.scenario;
    $("#statInst").textContent = s.institution;
    $("#statCfgHash").textContent = fmt.short(s.config_hash, 16);
    $("#statLLM").textContent = s.llm_mode;
  } catch (e) {
    if ($("#statusChips")) $("#statusChips").innerHTML = `<span class="chip"><i class="dot bad"></i>${esc(e.message)}</span>`;
  }
}

async function loadRuns(preferredRunId) {
  const runs = await api(`/api/runs${Shell.status && Shell.status.institution_id ? `?institution=${encodeURIComponent(Shell.status.institution_id)}` : ""}`);
  const sel = $("#runSelect");
  if (!runs || !runs.length) {
    sel.innerHTML = `<option value="">No runs available — click 'Run Pipeline'</option>`;
    return;
  }
  sel.innerHTML = runs.map((r) => `<option value="${esc(r.run_id)}">${esc(r.run_id)} · ${esc(r.status.replace(/_/g, " "))}</option>`).join("");
  const targetId = (preferredRunId && runs.some((r) => r.run_id === preferredRunId)) ? preferredRunId : runs[0].run_id;
  sel.value = targetId;
  await selectRun(targetId);
}

async function selectRun(id) {
  D.runId = id;
  try {
    const [runInfo, wl, comp, mod, aud] = await Promise.all([
      api(`/api/runs/${id}`),
      api(`/api/runs/${id}/worklist`).catch(() => null),
      api(`/api/runs/${id}/comparison`).catch(() => null),
      api(`/api/runs/${id}/model`).catch(() => null),
      api(`/api/runs/${id}/audit`).catch(() => null),
    ]);
    D.worklist = wl;
    D.comparison = comp;
    D.model = mod;
    D.audit = aud;
    renderAllPanels(runInfo);
  } catch (e) {
    toast(`Failed to load run data: ${e.message}`, "bad");
  }
}

// --------------------------------------------------------------------------
// Render Panels & Bind Data
// --------------------------------------------------------------------------
function renderAllPanels(runInfo) {
  renderPipelineStages(runInfo);
  renderUpliftQuadrants();
  renderSimulationMetrics();
  renderOptimizationBenchmark();
  renderDivisionOfLabor();
  renderGovernanceSummary();
  populateCounterfactualAccounts();
}

function renderPipelineStages(runInfo) {
  if (!runInfo || !runInfo.stages) return;
  const flow = $("#pipelineFlow");
  if (!flow) return;
  flow.innerHTML = runInfo.stages.map((s) => `
    <div class="pipeline-node ${esc(s.status)}">
      <span class="ord">Stage ${String(s.ord).padStart(2, "0")} · ${s.ms != null ? fmt.ms(s.ms) : esc(s.status)}</span>
      <span class="name">${esc(s.title)}</span>
      <span class="agent">${(s.agents || []).map(esc).join(" · ")}</span>
      <span class="tech">${Object.entries(s.metrics || {})[0] ? `${esc(Object.entries(s.metrics)[0][0])}: <b>${esc(fmt.value(Object.entries(s.metrics)[0][1]))}</b>` : "Verified"}</span>
    </div>
  `).join("");
}

function renderUpliftQuadrants() {
  if (D.model && D.model.segments) {
    const s = D.model.segments;
    $("#cntPersuadable").textContent = fmt.int(s.persuadable || 0);
    $("#cntSureThing").textContent = fmt.int(s.sure_thing || 0);
    $("#cntLostCause").textContent = fmt.int(s.lost_cause || 0);
    $("#cntSleepingDog").textContent = fmt.int(s.sleeping_dog || 0);
  } else if (D.worklist && D.worklist.items) {
    const counts = { persuadable: 0, sure_thing: 0, lost_cause: 0, sleeping_dog: 0 };
    D.worklist.items.forEach((i) => { if (counts[i.segment] !== undefined) counts[i.segment]++; });
    $("#cntPersuadable").textContent = fmt.int(counts.persuadable);
    $("#cntSureThing").textContent = fmt.int(counts.sure_thing);
    $("#cntLostCause").textContent = fmt.int(counts.lost_cause);
    $("#cntSleepingDog").textContent = fmt.int(counts.sleeping_dog);
  }
}

function renderSimulationMetrics() {
  if (D.comparison && D.comparison.summary && D.comparison.summary.optimised) {
    const opt = D.comparison.summary.optimised;
    if (opt.mc) {
      $("#mcP10").textContent = fmt.money(opt.mc.value_p10);
      $("#mcP50").textContent = fmt.money(opt.mc.value_p50);
      $("#mcP90").textContent = fmt.money(opt.mc.value_p90);
    }
    if (opt.des) {
      $("#desCompletion").textContent = fmt.pct(opt.des.completion_rate_mean, 0);
      $("#desMinutes").textContent = `${fmt.int(opt.minutes)} min`;
    }
  }
}

function renderOptimizationBenchmark() {
  if (!D.comparison || !D.comparison.summary) return;
  const s = D.comparison.summary;
  const opt = s.optimised, risk = s.sort_by_risk, val = s.sort_by_value;
  if (opt) $("#bmValOpt").textContent = fmt.money(opt.est_value);
  if (risk) $("#bmValRisk").textContent = fmt.money(risk.est_value);
  if (val) $("#bmValExp").textContent = fmt.money(val.est_value);

  if (D.comparison.evaluation) {
    const ev = D.comparison.evaluation;
    $("#bmLiftRisk").textContent = fmt.pct(ev.true_lift_vs_sort_by_risk, 0);
    const harmDiff = (ev.plans?.sort_by_risk?.harmful_actions || 0) - (ev.plans?.optimised?.harmful_actions || 0);
    $("#bmHarmAvoided").textContent = `${harmDiff} fewer harmful actions`;
  } else if (opt && risk) {
    const lift = (opt.est_value - risk.est_value) / (risk.est_value || 1);
    $("#bmLiftRisk").textContent = fmt.pct(lift, 0) + " (estimated)";
  }
}

function renderDivisionOfLabor() {
  if (D.model && D.model.governance) {
    const g = D.model.governance;
    const total = g.recommendations || 1;
    const complete = g.provenance_complete || total;
    $("#provStamped").textContent = `${fmt.pct(complete / total, 0)} (${complete}/${total})`;
  }
}

function renderGovernanceSummary() {
  if (D.worklist && D.worklist.summary) {
    const s = D.worklist.summary;
    const dec = s.decisions || {};
    $("#apprApproved").textContent = fmt.int(dec.approved || 0);
    $("#apprEscalated").textContent = fmt.int(s.escalated || 0);
    $("#apprReleased").textContent = fmt.int(s.released || 0);
  }
  if (D.audit && D.audit.chain) {
    const c = D.audit.chain;
    $("#auditChainStatus").textContent = c.valid ? "VALID ✓" : "BROKEN ✕";
    $("#auditChainStatus").style.color = c.valid ? "var(--good)" : "var(--bad)";
    $("#auditBlockCount").textContent = fmt.int(c.events || (D.audit.events ? D.audit.events.length : 0));
    $("#auditHeadHash").textContent = c.head ? fmt.short(c.head, 18) + "…" : "–";
  }
}

function populateCounterfactualAccounts() {
  if (!D.worklist || !D.worklist.items || !D.worklist.items.length) return;
  const sel = $("#cfAccountSelect");
  sel.innerHTML = D.worklist.items.slice(0, 30).map((i) =>
    `<option value="${esc(i.account_token)}">#${i.rank} · ${esc(i.account_token)} (${esc(i.action_label)}, ${fmt.money(i.action_value)})</option>`
  ).join("");
}

// --------------------------------------------------------------------------
// Interactive Handlers: Counterfactual, Benchmark, Run Pipeline
// --------------------------------------------------------------------------
function setupEventListeners() {
  $("#btnModeStory").onclick = () => setViewMode("story");
  $("#btnModeWorkbench").onclick = () => setViewMode("workbench");

  $("#runSelect").onchange = (e) => e.target.value && selectRun(e.target.value);

  $("#btnNewRun").onclick = async () => {
    try {
      const btn = $("#btnNewRun");
      btn.disabled = true;
      btn.textContent = "⏳ Starting agent run…";
      const res = await api("/api/runs", { method: "POST" });
      toast(`Run ${res.run_id} started. Agents working…`);
      pollForRunCompletion(res.run_id);
    } catch (e) {
      toast(e.message, "bad");
      $("#btnNewRun").disabled = false;
      $("#btnNewRun").textContent = "▶ Run Pipeline (~10s)";
    }
  };

  $("#btnRunBenchmark").onclick = async () => {
    if (!D.runId) return;
    try {
      const btn = $("#btnRunBenchmark");
      btn.disabled = true;
      btn.textContent = "⚡ Scoring against sealed truth…";
      const res = await api(`/api/runs/${D.runId}/evaluate`, { method: "POST" });
      toast("Synthetic benchmark completed successfully!");
      await selectRun(D.runId);
      btn.disabled = false;
      btn.textContent = "⚡ Run Sealed-Truth Benchmark";
    } catch (e) {
      toast(e.message, "bad");
      $("#btnRunBenchmark").disabled = false;
      $("#btnRunBenchmark").textContent = "⚡ Run Sealed-Truth Benchmark";
    }
  };

  $("#btnRunExplain").onclick = async () => {
    const token = $("#cfAccountSelect").value;
    if (!token || !D.runId) return;
    const box = $("#cfOutput");
    box.innerHTML = `<span class="muted">Re-solving optimization problem without ${esc(token)}…</span>`;
    try {
      const x = await api(`/api/runs/${D.runId}/accounts/${token}/explain`, { method: "POST" });
      const list = (arr) => (arr || []).slice(0, 5).map((r) => `<span class="mono">${esc(r.account_token)}</span> ${esc(r.action)} (${fmt.money(r.value)})`).join("<br>");
      if (x.was_selected) {
        box.innerHTML = `<div class="callout good">
          <b>Optimal Selection Justification:</b><br>
          Dropping account <span class="mono">${esc(token)}</span> would reduce total portfolio recovery by <b>${fmt.money(x.cost_of_change)}</b>.<br>
          The CBC solver would have to backfill its ${x.forced_action || "action"} slot with lower-yield alternative(s):<br>
          <div style="margin-top:6px;font-size:12px">${list(x.would_be_replaced_by) || "No feasible replacement"}</div>
        </div>`;
      } else if (x.reason === "outcompeted") {
        box.innerHTML = `<div class="callout warn">
          <b>Knapsack Opportunity Cost:</b><br>
          Forcing action <b>${esc(x.forced_action)}</b> (${fmt.money(x.forced_value)}) on this account would cause a net portfolio loss of <b>${fmt.money(x.cost_of_change)}</b>, because it would displace higher-efficiency accounts:<br>
          <div style="margin-top:6px;font-size:12px">${list(x.would_displace)}</div>
        </div>`;
      } else if (x.reason === "no_positive_value") {
        box.innerHTML = `<div class="callout bad">
          <b>Zero / Negative Uplift:</b><br>
          No action provides positive net avoided loss. Contact is estimated to produce zero lift or trigger reactance.
        </div>`;
      } else {
        box.innerHTML = `<div class="callout bad">Account is blocked by institutional policy constraints (e.g. cooling-off or channel consent).</div>`;
      }
    } catch (e) {
      box.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`;
    }
  };
}

function pollForRunCompletion(runId) {
  clearInterval(D.poll);
  D.poll = setInterval(async () => {
    try {
      const r = await api(`/api/runs/${runId}`);
      if (r.run.status !== "running") {
        clearInterval(D.poll);
        $("#btnNewRun").disabled = false;
        $("#btnNewRun").textContent = "▶ Run Pipeline (~10s)";
        toast(`Run ${runId} finished with status: ${r.run.status}`, r.run.status === "failed" ? "bad" : "good");
        await loadRuns(runId);
      }
    } catch {
      clearInterval(D.poll);
      $("#btnNewRun").disabled = false;
      $("#btnNewRun").textContent = "▶ Run Pipeline (~10s)";
    }
  }, 1000);
}

// --------------------------------------------------------------------------
// Presentation Story Mode Navigation
// --------------------------------------------------------------------------
function setupStoryNavigation() {
  $$(".story-step").forEach((btn) => {
    btn.onclick = () => {
      const step = parseInt(btn.dataset.step, 10);
      goToStoryStep(step);
    };
  });
}

function goToStoryStep(step) {
  D.activeStep = step;
  $$(".story-step").forEach((b) => b.classList.toggle("active", parseInt(b.dataset.step, 10) === step));
  $("#speakerNoteText").textContent = SPEAKER_NOTES[step] || "";
  const targetTopic = $(`#topic-${step}`);
  if (targetTopic) {
    targetTopic.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function setViewMode(mode) {
  D.mode = mode;
  $("#btnModeStory").classList.toggle("active", mode === "story");
  $("#btnModeWorkbench").classList.toggle("active", mode === "workbench");
  $("#storyNavPanel").style.display = mode === "story" ? "block" : "none";
}

// --------------------------------------------------------------------------
// Interactive ROI Calculator
// --------------------------------------------------------------------------
function setupRoiCalculator() {
  const calc = () => {
    const accounts = parseFloat($("#roiAccounts").value) || 50000;
    const delinquentBal = parseFloat($("#roiDelinquentBal").value) || 12000000;
    const baseCure = (parseFloat($("#roiBaseCure").value) || 45) / 100;
    const lift = (parseFloat($("#roiLift").value) || 26) / 100;

    const baseRecovered = delinquentBal * baseCure;
    const incrementalGain = baseRecovered * lift;
    $("#roiAnnualGain").textContent = fmt.money(incrementalGain);
  };

  ["#roiAccounts", "#roiDelinquentBal", "#roiBaseCure", "#roiLift"].forEach((id) => {
    $(id).addEventListener("input", calc);
  });
  calc();
}

// --------------------------------------------------------------------------
// Start on page load
// --------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => Shell.mount({ page: "demo", title: "Demo showcase" }).then(initDemo));
