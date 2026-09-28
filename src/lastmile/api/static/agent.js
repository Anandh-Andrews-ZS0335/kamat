// The collection agent's queue. Everything comes from one scoped payload: the server decides which
// rows and which fields, and `can` decides which controls exist.
//
// Opening a row opens the account card - the surface the work is actually done from. The card shows
// the words to say, the few account facts needed to say them, and the outcome form. What the action
// is worth and what the model believes are not in the payload at all.
let agentData = null;
let dispositions = [];
let needsPromise = "PROMISE";
let openCard = null;                      // account_token whose card is open
let historyFor = null;                    // account_token whose revision history is expanded
let historyRows = [];
let filter = "all";

const STATE_LABEL = { awaiting_approval: "Awaiting approval", approved: "Approved", rejected: "Rejected",
                      released: "Ready to work", worked: "Worked" };

// The four channels, shown on every card so the agent can see what the platform is able to do.
// None of them fire: Last Mile decides and records, the bank sends. Clicking says so.
const CHANNELS = [
  { id: "CALL", label: "Call", note: "The bank places this call from its own dialler." },
  { id: "SMS", label: "Send SMS", note: "The bank sends this message on release." },
  { id: "PLAN", label: "Offer plan", note: "A plan offer is raised with the bank on release." },
  { id: "HARDSHIP", label: "Refer to hardship", note: "A hardship referral is raised with the bank on release." },
];

const FILTERS = [
  ["all", "All"],
  ["released", "Ready to work"],
  ["worked", "Worked"],
  ["waiting", "Waiting on manager"],
];

function matchesFilter(item, which = filter) {
  if (which === "all") return true;
  if (which === "worked") return !!item.attempt;
  if (which === "released") return item.released && !item.attempt;
  return !item.released;                  // waiting on the manager to approve and release
}

// ------------------------------------------------------------------------------ the account card
function scriptHTML(item) {
  if (item.script) {
    return `<div class="card-sec">
      <div class="card-h"><b>Outreach script</b><button class="btn small" data-copy="${esc(item.account_token)}">Copy</button></div>
      <blockquote class="script">${esc(item.script)}</blockquote>
      <p class="hint">Approved wording. Every figure in it comes from the bank's own record.</p>
    </div>`;
  }
  return `<div class="card-sec">
    <div class="card-h"><b>Outreach script</b></div>
    <p class="hint">Your manager has not released this account yet. The words to say appear here once they do.</p>
  </div>`;
}

function aboutHTML(item) {
  const facts = [
    ["Product", esc(item.product)],
    ["Balance", fmt.money(item.exposure)],
    ["Days past due", fmt.int(item.dpd)],
    ["Minimum due", fmt.money(item.min_payment)],
  ];
  return `<div class="card-sec"><div class="card-h"><b>About this account</b></div>
    <dl class="facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl></div>`;
}

function careHTML(item) {
  if (!item.escalated) return "";
  return `<div class="card-sec care"><div class="card-h"><b>Handle with care</b></div>
    <p>${esc(item.escalation_note || "A manager reviewed this account before release.")}</p></div>`;
}

function channelsHTML(item) {
  const chosen = item.final_action || item.action;
  return `<div class="card-sec"><div class="card-h"><b>Channels</b><span class="hint">Last Mile decides and records; the bank sends</span></div>
    <div class="channels">${CHANNELS.map((c) => `<button class="chan ${c.id === chosen ? "on" : ""}" type="button"
      data-chan="${esc(c.id)}" data-note="${esc(c.note)}">${esc(c.label)}</button>`).join("")}</div></div>`;
}

function outcomeHTML(item) {
  const a = item.attempt;
  if (!a) return "";
  const promise = a.promise_date
    ? ` · promised ${esc(a.promise_date)}${a.promise_amount ? ` for ${fmt.money(a.promise_amount)}` : ""}` : "";
  const revised = a.revisions > 1 ? ` · edited ${a.revisions - 1} time${a.revisions === 2 ? "" : "s"}` : "";
  return `<div class="outcome">
    <b>${esc(a.label)}</b>${promise}
    ${a.comment ? `<div class="note">${esc(a.comment)}</div>` : `<div class="note muted">No comment recorded.</div>`}
    <div class="meta">Recorded by ${esc(a.recorded_by)} at ${fmt.time(a.recorded_at)}${revised}
      ${a.revisions > 1 ? `· <button class="linky" data-history="${esc(item.account_token)}">${historyFor === item.account_token ? "hide" : "show"} history</button>` : ""}</div>
    ${historyFor === item.account_token ? historyHTML() : ""}
  </div>`;
}

function historyHTML() {
  if (!historyRows.length) return `<div class="hist muted">Loading…</div>`;
  return `<ol class="hist">${historyRows.map((h) => `<li><b>${esc(outcomeLabel(h.disposition))}</b>
    <span class="meta">${esc(h.recorded_by)} · ${fmt.time(h.recorded_at)}</span>
    ${h.comment ? `<div class="note">${esc(h.comment)}</div>` : ""}</li>`).join("")}</ol>`;
}

function outcomeLabel(id) {
  const d = dispositions.find((x) => x.id === id);
  return d ? d.label : id;
}

function formHTML(item) {
  const a = item.attempt || {};
  const opts = dispositions.map((d) =>
    `<option value="${esc(d.id)}" ${a.disposition === d.id ? "selected" : ""}>${esc(d.label)}</option>`).join("");
  const isPromise = (a.disposition || dispositions[0]?.id) === needsPromise;
  return `<form class="oform" data-form="${esc(item.account_token)}">
    <div class="row">
      <label>What happened? <select name="disposition">${opts}</select></label>
      <label>Time spent <input name="spent_minutes" type="number" min="0" max="600" style="width:80px"
        placeholder="${esc(String(item.minutes ?? ""))}"> min</label>
      <span class="promise" ${isPromise ? "" : "hidden"}>
        <label>Promised for <input name="promise_date" type="date" value="${esc(a.promise_date || "")}"></label>
        <label>Amount <input name="promise_amount" type="number" step="0.01" min="0" style="width:120px" value="${a.promise_amount ?? ""}"></label>
      </span>
    </div>
    <textarea name="comment" maxlength="2000" placeholder="What was said, what to do next…">${esc(a.comment || "")}</textarea>
    <div class="row">
      <button class="btn primary small" type="submit">${a.disposition ? "Save change" : "Record outcome"}</button>
      <span class="hint">${a.disposition ? "Edits are kept as a new entry; the earlier one stays on the record."
                                          : "Recorded against your name and the audit trail."}</span>
    </div>
  </form>`;
}

function cardHTML(item) {
  const canRecord = agentData.can.record_outcome && item.released;
  return `<div class="card">
    <div class="card-sec"><div class="card-h"><b>Do this</b></div>
      <p class="doit"><span class="action">${esc(item.action_label || item.final_action || item.action)}</span>
        <span class="hint">about ${fmt.int(item.minutes)} min</span></p></div>
    ${scriptHTML(item)}
    ${aboutHTML(item)}
    ${careHTML(item)}
    ${channelsHTML(item)}
    ${item.attempt ? outcomeHTML(item) : ""}
    ${canRecord ? `<div class="card-sec"><div class="card-h"><b>${item.attempt ? "Correct the outcome" : "Record outcome"}</b></div>
      ${formHTML(item)}</div>`
      : `<div class="card-sec"><p class="hint">You can record an outcome once this account has been released.</p></div>`}
  </div>`;
}

// ---------------------------------------------------------------------------------------- render
function renderAgent() {
  const { items, summary, can, scope, queue } = agentData;
  $("#subtitle").textContent = `${agentData.run.as_of_date} · ${items.length} assigned item${items.length === 1 ? "" : "s"}`;

  const worked = items.filter((i) => i.attempt).length;
  const plannedMinutes = items.reduce((t, i) => t + (i.minutes || 0), 0);
  const usedMinutes = items.reduce((t, i) => t + ((i.attempt && i.attempt.spent_minutes) || 0), 0);
  const tiles = [
    ["Assigned", fmt.int(summary.assigned), "Accounts in your queue"],
    ["Ready to work", fmt.int(summary.released), "Approved and sent to the bank"],
    ["Worked", `${fmt.int(worked)} of ${fmt.int(summary.released)}`, "You have recorded an outcome"],
    ["Waiting for manager", fmt.int(summary.pending), "Not yet approved for contact"],
    ["Minutes", `${fmt.int(usedMinutes)} of ${fmt.int(plannedMinutes)}`, "Recorded against planned"],
  ];
  if (queue && queue.completion_simulated != null) {
    tiles.push(["Likely to finish", fmt.pct(queue.completion_simulated, 0), "Simulated against your shift"]);
  }
  $("#summary").innerHTML = tiles.map(([k, v, s]) =>
    `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join("");

  const shown = items.filter((i) => matchesFilter(i));   // not `filter(matchesFilter)`: the index would land in `which`
  $("#filters").innerHTML = FILTERS.map(([id, label]) => {
    const n = items.filter((i) => matchesFilter(i, id)).length;
    return `<button class="chip-btn ${filter === id ? "on" : ""}" type="button" data-filter="${id}">${label} <b>${fmt.int(n)}</b></button>`;
  }).join("");

  $("#queue").innerHTML = shown.length ? `<div class="queue-list">${shown.map((item) => {
    const state = item.state || "awaiting_approval";
    const position = items.indexOf(item) + 1;
    const isOpen = openCard === item.account_token;
    return `<article class="task ${isOpen ? "open" : ""}" data-token="${esc(item.account_token)}">
      <span class="rank">${position}</span>
      <div class="task-main" data-open="${esc(item.account_token)}" role="button" tabindex="0">
        <h3><span class="action">${esc(item.final_action || item.action)}</span> · ${esc(item.account_token)}</h3>
        <p>${esc(item.product)} · ${fmt.int(item.dpd)} days past due · about ${fmt.int(item.minutes)} min${item.escalated ? " · needs care" : ""}</p>
      </div>
      <span class="state ${esc(state)}">${esc(STATE_LABEL[state] || state)}</span>
      <div class="actions">
        ${can.reorder ? `<button class="btn small" data-move="up" ${position > 1 ? "" : "disabled"} aria-label="Move earlier">&uarr;</button>
        <button class="btn small" data-move="down" ${position < items.length ? "" : "disabled"} aria-label="Move later">&darr;</button>` : ""}
        <button class="btn small" data-open="${esc(item.account_token)}" aria-expanded="${isOpen}">${isOpen ? "Close" : "Open"}</button>
      </div>
      ${isOpen ? cardHTML(item) : ""}
    </article>`;
  }).join("")}</div>` : `<div class="empty">${items.length ? "Nothing in this filter." : "No queue has been assigned to you today."}</div>`;

  $("#save").hidden = !can.reorder;
  wire();

  const promised = items.filter((i) => i.attempt && i.attempt.disposition === needsPromise).length;
  const reached = items.filter((i) => i.attempt && ["REACHED", "PROMISE", "ALREADY_PAID"].includes(i.attempt.disposition)).length;
  $("#results").innerHTML = `<div class="queue-meta">
      <span>${fmt.int(worked)} worked</span>
      <span>${fmt.int(reached)} reached the customer</span>
      <span>${fmt.int(promised)} promised to pay</span>
      <span>${fmt.int(summary.pending)} awaiting approval</span>
      <span>${esc(scope.display || scope.collector_id || "")}</span>
    </div>
    <p class="muted" style="margin:12px 0 0">What you record here goes back into tomorrow's plan: contact history,
      how long calls really take, and any stop such as a wrong number. Whether the account actually caught up is
      confirmed separately by the bank, and is only known 30 days after the action.</p>`;
}

// ------------------------------------------------------------------------------------------ wire
function wire() {
  $$("[data-filter]").forEach((button) => button.onclick = () => { filter = button.dataset.filter; renderAgent(); });

  $$("[data-open]").forEach((el) => {
    const toggle = () => {
      openCard = openCard === el.dataset.open ? null : el.dataset.open;
      historyFor = null;
      renderAgent();
      if (openCard) $(`.task[data-token="${CSS.escape(openCard)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    };
    el.onclick = toggle;
    el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } };
  });

  $$("[data-move]").forEach((button) => button.onclick = (e) => {
    e.stopPropagation();
    const items = agentData.items;
    const card = button.closest(".task");
    const at = items.findIndex((i) => i.account_token === card.dataset.token);
    const other = button.dataset.move === "up" ? at - 1 : at + 1;
    if (other < 0 || other >= items.length) return;
    [items[at], items[other]] = [items[other], items[at]];
    $("#save").disabled = false;
    renderAgent();
  });

  // Showcase only: the platform can drive each channel, but nothing is sent from this page.
  $$("[data-chan]").forEach((button) => button.onclick = () => toast(button.dataset.note));

  $$("[data-copy]").forEach((button) => button.onclick = async () => {
    const item = agentData.items.find((i) => i.account_token === button.dataset.copy);
    try { await navigator.clipboard.writeText(item.script); toast("Script copied"); }
    catch { toast("Could not copy the script", "bad"); }
  });

  $$("[data-history]").forEach((button) => button.onclick = async () => {
    const token = button.dataset.history;
    if (historyFor === token) { historyFor = null; return renderAgent(); }
    historyFor = token;
    historyRows = [];
    renderAgent();
    try {
      const h = await api(`/api/agent/today/outcome/${encodeURIComponent(token)}`);
      historyRows = h.history;
    } catch (e) { toast(e.message, "bad"); }
    renderAgent();
  });

  $$("[data-form]").forEach((form) => {
    const sel = form.querySelector("[name=disposition]");
    const promise = form.querySelector(".promise");
    sel.onchange = () => { promise.hidden = sel.value !== needsPromise; };
    form.onsubmit = async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const isPromise = data.get("disposition") === needsPromise;
      const body = {
        account_token: form.dataset.form,
        disposition: data.get("disposition"),
        comment: data.get("comment") || null,
        promise_date: isPromise ? (data.get("promise_date") || null) : null,
        promise_amount: isPromise && data.get("promise_amount") ? Number(data.get("promise_amount")) : null,
        spent_minutes: data.get("spent_minutes") ? Number(data.get("spent_minutes")) : null,
      };
      try {
        await api("/api/agent/today/outcome", { method: "POST", body: JSON.stringify(body) });
        toast("Outcome recorded");
        await loadAgent();
      } catch (e) { toast(e.message, "bad"); }
    };
  });
}

async function loadAgent() {
  try {
    if (!dispositions.length) {
      const d = await api("/api/agent/dispositions");
      dispositions = d.dispositions;
      needsPromise = d.needs_promise;
    }
    agentData = await api("/api/worklist");
    renderAgent();
  } catch (e) {
    $("#queue").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    $("#subtitle").textContent = "Your queue appears once a manager has run and assigned today's work.";
  }
}

$("#save").onclick = async () => {
  try {
    await api("/api/agent/today/queue", { method: "PUT", body: JSON.stringify({ account_tokens: agentData.items.map((i) => i.account_token) }) });
    $("#save").disabled = true;
    toast("Queue order saved");
  } catch (e) { toast(e.message, "bad"); }
};

$("#copyResults").onclick = async () => {
  if (!agentData) return;
  const s = agentData.summary;
  const worked = agentData.items.filter((i) => i.attempt).length;
  const text = `Last Mile queue for ${agentData.run.as_of_date}: ${s.assigned} assigned, ${s.released} released, ${worked} worked, ${s.pending} awaiting approval.`;
  try { await navigator.clipboard.writeText(text); toast("Summary copied"); }
  catch { toast("Could not copy the summary", "bad"); }
};

Shell.mount({ page: "agent", title: "My queue" }).then(loadAgent);
