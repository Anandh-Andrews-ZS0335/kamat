const S = { runId: null, run: null, stages: [], events: [], lastSeq: 0, stage: null, tab: "outputs", artifact: null,
            artOffset: 0, agentFilter: null, poll: null, agents: null };

const KIND_LABEL = { api: "API", tool: "TOOL", llm: "LLM", handoff: "HANDOFF", decision: "DECISION", human: "HUMAN", denied: "DENIED" };

async function loadStatus() {
  try {
    const s = await api("/api/status");
    $("#statusChips").innerHTML = `
      <span class="chip"><i class="dot ${s.bank.ok ? "ok" : "bad"}"></i>Bank API ${s.bank.ok ? `· as of ${esc(s.bank.as_of)}` : "unreachable"}</span>
      <span class="chip"><i class="dot ${s.llm_mode.startsWith("gemini") ? "ok" : "warn"}"></i>LLM · ${esc(s.llm_mode)}</span>
      <span class="chip">${esc(s.scenario)} · ${esc(s.institution)}</span>
      <span class="chip mono" title="Current configuration hash">cfg ${esc(fmt.short(s.config_hash, 12))}</span>`;
  } catch (e) { $("#statusChips").innerHTML = `<span class="chip"><i class="dot bad"></i>${esc(e.message)}</span>`; }
}

async function loadRuns(selectId) {
  const runs = await api("/api/runs");
  const sel = $("#runSelect");
  sel.innerHTML = runs.length ? runs.map((r) => `<option value="${esc(r.run_id)}">${esc(r.run_id)} · ${esc(r.status.replace(/_/g, " "))}</option>`).join("")
                             : `<option value="">No runs yet</option>`;
  const id = selectId || (runs[0] && runs[0].run_id);
  if (id) { sel.value = id; await selectRun(id); }
}

async function selectRun(id) {
  clearInterval(S.poll);
  const keep = id === S.runId ? { stage: S.stage, artifact: S.artifact, agentFilter: S.agentFilter } : { stage: null, artifact: null, agentFilter: null };
  Object.assign(S, { runId: id, events: [], lastSeq: 0, ...keep });
  $("#traceList").innerHTML = "";
  await refresh();
  if (S.run && S.run.status === "running") S.poll = setInterval(refresh, 900);
}

async function refresh() {
  if (!S.runId) return;
  const [data, ev] = await Promise.all([api(`/api/runs/${S.runId}`), api(`/api/runs/${S.runId}/trace?after=${S.lastSeq}`)]);
  S.run = data.run; S.stages = data.stages; S.calls = data.agent_calls;
  if (ev.length) { S.events.push(...ev); S.lastSeq = ev[ev.length - 1].seq; }
  if (!S.stage) {
    const active = S.stages.find((s) => s.status === "running") || S.stages.find((s) => s.status === "failed") || S.stages[0];
    S.stage = active && active.stage;
  } else if (S.run.status === "running") {
    const active = S.stages.find((s) => s.status === "running");
    if (active && $("#follow").checked) S.stage = active.stage;
  }
  renderBanner(); renderRail(); renderDetail(); renderTrace(ev.length > 0); renderAgentCalls();
  if (ev.some((e) => e.kind === "llm") || S.llmFor !== S.runId) loadLlmCalls();
  if (S.run.status !== "running") {
    if (S.poll) { clearInterval(S.poll); S.poll = null; loadRuns(S.runId); loadStatus(); }
  }
}

function renderBanner() {
  const r = S.run;
  const dur = r.finished_at ? ((new Date(r.finished_at) - new Date(r.started_at)) / 1000).toFixed(1) + " s" : "running…";
  $("#runBanner").innerHTML = `
    <span class="rid">${esc(r.run_id)}</span>${statusPill(r.status)}
    <div class="meta">
      <span>as of <b>${esc(r.as_of_date || "–")}</b></span>
      <span>config <b>${esc(fmt.short(r.config_hash, 16))}</b></span>
      <span>model <b>${esc(r.model_run_id || "–")}</b></span>
      <span>LLM <b>${esc(r.llm_mode || "–")}</b></span>
      <span>duration <b>${esc(dur)}</b></span>
    </div>
    ${r.error ? `<div class="callout bad" style="flex-basis:100%">${esc(r.error)}</div>` : ""}
    ${r.status === "awaiting_approval" ? `<a class="btn primary" style="margin-left:auto" href="/manager?run=${esc(r.run_id)}">Review in Manager Console →</a>` : ""}`;
}

function renderRail() {
  $("#rail").innerHTML = S.stages.map((s) => {
    const metrics = Object.entries(s.metrics || {}).slice(0, 2);
    return `<button class="stage ${esc(s.status)} ${S.stage === s.stage ? "sel" : ""}" data-stage="${esc(s.stage)}">
      <div class="n"><span>${String(s.ord).padStart(2, "0")}</span><span><i class="dot ${ {done: "ok", running: "run", failed: "bad", waiting: "warn"}[s.status] || ""}"></i></span></div>
      <div class="t">${esc(s.title)}</div>
      <div class="ag">${(s.agents || []).map(esc).join(" · ")}</div>
      <div class="m">${s.ms != null ? `<span>time <b>${fmt.ms(s.ms)}</b></span>` : `<span>${esc(s.status)}</span>`}
        ${metrics.map(([k, v]) => `<span>${esc(k)} <b>${esc(fmt.value(v))}</b></span>`).join("")}</div>
    </button>`;
  }).join("");
  $$(".stage").forEach((b) => b.onclick = () => { S.stage = b.dataset.stage; S.artifact = null; S.artOffset = 0; renderRail(); renderDetail(); });
}

function renderDetail() {
  const s = S.stages.find((x) => x.stage === S.stage);
  if (!s) return;
  const events = S.events.filter((e) => e.stage === s.stage);
  const toolCalls = events.filter((e) => e.tool);
  const arts = s.artifacts || [];
  $("#detail").innerHTML = `
    <div class="detail-h">
      <span class="eyebrow">Stage ${s.ord}</span><h2>${esc(s.title)}</h2>${statusPill(s.status)}
      <div class="agents-inline">${(s.agents || []).map((a) => `<span class="chip">${esc(a)}</span>`).join("")}</div>
      <div class="spacer"></div><span class="muted num">${fmt.ms(s.ms)}</span>
    </div>
    <div class="panel-b"><div class="tiles">${Object.entries(s.metrics || {}).map(([k, v]) =>
      `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${esc(fmt.value(v))}</div></div>`).join("") || '<div class="muted">No metrics yet.</div>'}</div></div>
    <div class="tabs">
      ${["outputs", "calls", "data"].map((t) => `<button class="tab ${S.tab === t ? "on" : ""}" data-tab="${t}">${
        {outputs: "Outputs", calls: "Agent & tool calls", data: "Data"}[t]}<span class="count">${
        t === "calls" ? events.length : t === "data" ? arts.length : (s.notes || []).length}</span></button>`).join("")}
    </div>
    <div class="panel-b" id="tabBody"></div>`;
  $$(".tab", $("#detail")).forEach((b) => b.onclick = () => { S.tab = b.dataset.tab; renderDetail(); });
  const body = $("#tabBody");
  if (S.tab === "outputs") {
    body.innerHTML = (s.notes || []).map((n) => `<div class="section-title">${esc(n.title)}</div>
      ${n.type === "kv" ? kvHTML(n.data) : tableHTML(n.rows, { maxHeight: 360 })}
      ${n.note ? `<p class="note">${esc(n.note)}</p>` : ""}`).join("") || `<div class="empty">${s.status === "pending" ? "Not started." : "No outputs recorded."}</div>`;
  } else if (S.tab === "calls") {
    body.innerHTML = events.length ? `<div class="tbl-wrap" style="max-height:560px"><table class="tbl"><thead><tr>
      <th>#</th><th>Time</th><th>Kind</th><th>Agent</th><th>Tool / target</th><th>Module</th><th class="r">ms</th><th>Result</th></tr></thead><tbody>
      ${events.map((e) => `<tr class="click" data-seq="${e.seq}"><td class="num">${e.seq}</td><td class="num">${fmt.time(e.ts)}</td>
        <td><span class="pill k-${esc(e.kind)}">${esc(KIND_LABEL[e.kind] || e.kind)}</span></td><td><b>${esc(e.agent)}</b></td>
        <td class="mono">${esc(e.tool || (e.target ? "→ " + e.target : ""))}</td><td class="mono muted">${esc(e.module || "")}</td>
        <td class="r num">${e.ms || ""}</td><td class="wrap">${e.status !== "ok" ? statusPill(e.status) + " " : ""}${esc(e.message || "")}</td></tr>
        <tr class="hidden" id="io-${e.seq}"><td></td><td colspan="7">${e.output && e.output.llm_call_id ? `<div class="llm-full" data-call="${e.output.llm_call_id}"></div>` : ""}
          <pre style="margin:0;white-space:pre-wrap;font-size:11px;max-height:300px;overflow:auto">${
          esc(JSON.stringify({ input: e.input, output: e.output }, null, 2))}</pre></td></tr>`).join("")}
      </tbody></table></div><p class="note">${toolCalls.length} tool call(s). Click a row to see its input and output.</p>` : `<div class="empty">No events for this stage yet.</div>`;
    $$("tr.click", body).forEach((tr) => tr.onclick = () => {
      const io = $("#io-" + tr.dataset.seq);
      io.classList.toggle("hidden");
      const slot = io.querySelector(".llm-full");
      if (slot && !io.classList.contains("hidden") && !slot.dataset.loaded) loadLlmCall(slot);
    });
  } else {
    body.innerHTML = arts.length ? `<div class="art-list">${arts.map((a) => `<button class="art ${S.artifact === a.name ? "on" : ""}" data-name="${esc(a.name)}">
        <b>${esc(a.name)}</b><small>${fmt.int(a.rows)} rows · ${a.columns.length} cols${a.mask_columns && a.mask_columns.length ? " · PII masked" : ""}</small></button>`).join("")}</div>
      <div id="artBody"><div class="muted">Pick a table to preview it.</div></div>` : `<div class="empty">This stage produced no tables.</div>`;
    $$(".art", body).forEach((b) => b.onclick = () => { S.artifact = b.dataset.name; S.artOffset = 0; renderDetail(); });
    if (S.artifact) loadArtifact(arts.find((a) => a.name === S.artifact));
  }
}

async function loadLlmCalls() {
  S.llmFor = S.runId;
  const calls = await api(`/api/runs/${S.runId}/llm-calls`).catch(() => []);
  $("#llmCount").textContent = `${calls.length} call(s)`;
  if (!calls.length) {
    $("#llmList").innerHTML = `<div class="empty">No LLM calls in this run${S.run && S.run.llm_mode && !S.run.llm_mode.startsWith("gemini") ? ` — it used ${esc(S.run.llm_mode)}` : ""}.</div>`;
    $("#llmDetail").innerHTML = "";
    return;
  }
  $("#llmList").innerHTML = tableHTML(calls.map((c) => ({ id: c.id, time: fmt.time(c.created_at), agent: c.agent, purpose: c.purpose, model: `${c.provider} · ${c.model}`,
      status: c.status, http: c.http_status, latency: c.latency_ms, tokens_in: c.prompt_tokens, tokens_out: c.response_tokens, thinking: c.thinking_tokens,
      prompt_chars: c.prompt_chars, response_chars: c.response_chars, error: c.error || "" })),
    { maxHeight: 260, click: true, render: { status: (v) => statusPill(v === "ok" ? "done" : "failed"), latency: (v) => `<span class="num">${fmt.ms(v)}</span>` } });
  $$("#llmList tr.click").forEach((tr) => tr.onclick = () => {
    $$("#llmList tr.click").forEach((x) => x.classList.toggle("sel", x === tr));
    $("#llmDetail").innerHTML = `<div class="llm-full" data-call="${calls[Number(tr.dataset.i)].id}"></div>`;
    loadLlmCall($("#llmDetail .llm-full"));
  });
}

// The trace keeps a summary of an LLM call; the full request and response are stored separately and loaded on demand.
async function loadLlmCall(slot) {
  slot.dataset.loaded = "1";
  slot.innerHTML = `<span class="muted">Loading the full LLM exchange…</span>`;
  try {
    const c = await api(`/api/llm-calls/${slot.dataset.call}`);
    const block = (title, text) => `<div class="section-title">${title}</div><pre style="margin:0;white-space:pre-wrap;font-size:11px;max-height:320px;overflow:auto;background:var(--panel-2);padding:8px;border-radius:6px">${esc(text ?? "–")}</pre>`;
    let prompt = c.user_prompt;
    try { prompt = JSON.stringify(JSON.parse(c.user_prompt), null, 2); } catch { /* keep as sent */ }
    let raw = c.response_text;
    try { raw = JSON.stringify(JSON.parse(c.response_text), null, 2); } catch { /* keep as received */ }
    slot.innerHTML = `<div class="tiles" style="margin-bottom:8px">
        <div class="tile"><div class="k">LLM call #${c.id}</div><div class="v">${statusPill(c.status === "ok" ? "done" : "failed")}</div><div class="s">${esc(c.purpose)}</div></div>
        <div class="tile"><div class="k">Model</div><div class="v" style="font-size:13px">${esc(c.provider)} · ${esc(c.model)}</div><div class="s">HTTP ${esc(c.http_status ?? "–")} · ${esc(c.finish_reason || "")}</div></div>
        <div class="tile"><div class="k">Latency</div><div class="v">${fmt.ms(c.latency_ms)}</div></div>
        <div class="tile"><div class="k">Tokens</div><div class="v">${fmt.int(c.total_tokens)}</div><div class="s">${fmt.int(c.prompt_tokens)} in · ${fmt.int(c.response_tokens)} out${c.thinking_tokens ? ` · ${fmt.int(c.thinking_tokens)} thinking` : ""}</div></div>
      </div>
      ${c.error ? `<div class="callout bad">${esc(c.error)}</div>` : ""}
      ${block("System instruction", c.system_prompt)}${block("Prompt sent (no account or member data)", prompt)}
      ${block("Raw response from the provider", raw)}${block("Parsed JSON used by the Explanation Agent", c.parsed == null ? null : JSON.stringify(c.parsed, null, 2))}
      ${slot.closest("#llmDetail") ? "" : `<div class="section-title">Summary kept in the trace</div>`}`;
  } catch (e) { slot.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
}

async function loadArtifact(meta) {
  if (!meta) return;
  const d = await api(`/api/runs/${S.runId}/artifacts/${meta.name}?limit=50&offset=${S.artOffset}`);
  $("#artBody").innerHTML = `<p class="note" style="margin:0 0 8px">${esc(meta.description)}${d.masked.length ? ` · masked: ${d.masked.map(esc).join(", ")}` : ""}</p>
    ${tableHTML(d.items, { columns: d.columns, maxHeight: 460 })}
    <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
      <button class="btn small" id="artPrev" ${S.artOffset === 0 ? "disabled" : ""}>← Prev</button>
      <span class="muted num">${fmt.int(d.offset + 1)}–${fmt.int(Math.min(d.offset + 50, d.rows))} of ${fmt.int(d.rows)}</span>
      <button class="btn small" id="artNext" ${d.offset + 50 >= d.rows ? "disabled" : ""}>Next →</button></div>`;
  $("#artPrev").onclick = () => { S.artOffset = Math.max(0, S.artOffset - 50); loadArtifact(meta); };
  $("#artNext").onclick = () => { S.artOffset += 50; loadArtifact(meta); };
}

function renderTrace(newEvents) {
  const agents = [...new Set(S.events.map((e) => e.agent))];
  $("#traceFilters").innerHTML = [`<button class="fchip ${!S.agentFilter ? "on" : ""}" data-a="">All</button>`,
    ...agents.map((a) => `<button class="fchip ${S.agentFilter === a ? "on" : ""}" data-a="${esc(a)}">${esc(a.replace(" Agent", ""))}</button>`)].join("");
  $$(".fchip").forEach((b) => b.onclick = () => { S.agentFilter = b.dataset.a || null; renderTrace(false); });
  const list = S.events.filter((e) => !S.agentFilter || e.agent === S.agentFilter || e.target === S.agentFilter);
  $("#traceCount").textContent = `${S.events.length} events`;
  $("#traceList").innerHTML = list.map((e) => `
    <div class="ev kind-${esc(e.kind)} st-${esc(e.status)}">
      <span class="when">${fmt.time(e.ts).slice(0, 8)}</span>
      <div class="who"><span class="pill k-${esc(e.status === "denied" ? "denied" : e.kind)}">${esc(KIND_LABEL[e.kind] || e.kind)}</span>
        <span class="agent">${esc(e.agent)}</span>
        ${e.target ? `<span class="arrow">→</span><span class="agent">${esc(e.target)}</span>` : ""}
        ${e.tool ? `<span class="arrow">·</span><span class="tool">${esc(e.tool)}</span>` : ""}
        ${e.ms ? `<span class="muted num" style="margin-left:auto">${fmt.ms(e.ms)}</span>` : ""}</div>
      ${e.message ? `<div class="msg">${esc(e.message)}</div>` : ""}
      ${e.module ? `<div class="mod">${esc(e.module)}</div>` : ""}
    </div>`).join("") || `<div class="empty">No events yet.</div>`;
  if (newEvents && $("#follow").checked) $("#traceList").scrollTop = $("#traceList").scrollHeight;
}

async function loadAgents() {
  S.agents = await api("/api/agents");
  renderAgentCalls();
  $("#toolCount").textContent = `${S.agents.tools.length} tools`;
  $("#toolTable").innerHTML = tableHTML(S.agents.tools.map((t) => ({ tool: t.name, kind: t.kind, owner: t.owners.join(", "), module: t.module, description: t.description })),
    { maxHeight: 380, render: { kind: (v) => `<span class="pill k-${esc(v)}">${esc(v)}</span>`, tool: (v) => `<span class="mono">${esc(v)}</span>`, module: (v) => `<span class="mono muted">${esc(v)}</span>` } });
}

function renderAgentCalls() {
  if (!S.agents) return;
  const calls = Object.fromEntries((S.calls || []).map((c) => [c.agent, c]));
  $("#agentsGrid").innerHTML = S.agents.agents.map((a) => `
    <article class="agent-card">
      <span class="calls">${calls[a.name] ? `${calls[a.name].n} calls · ${fmt.ms(calls[a.name].ms)}` : "idle"}</span>
      <h3>${esc(a.name)}</h3><div class="role">${esc(a.title)}</div>
      <div class="resp">${esc(a.responsibility)}</div>
      <div class="mods">${a.modules.map(esc).join("<br>")}</div>
      <div class="tools">${a.tools.map((t) => `<span>${esc(t)}</span>`).join("") || '<span class="muted">coordinates only</span>'}</div>
    </article>`).join("");
}

$("#runSelect").onchange = (e) => e.target.value && selectRun(e.target.value);
$("#startRun").onclick = async () => {
  $("#startRun").disabled = true;
  try { const r = await api("/api/runs", { method: "POST" }); toast(`Started ${r.run_id}`); await loadRuns(r.run_id); }
  catch (e) { toast(e.message, "bad"); }
  finally { $("#startRun").disabled = false; }
};

loadStatus(); loadAgents(); loadRuns(new URLSearchParams(location.search).get("run"));
