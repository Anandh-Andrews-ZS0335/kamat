// Daily report: one business day, frozen when the manager closes it. "What happened next" is always read live from the bank.
const R = { list: [], date: null };
const POLICY_NAME = { optimised: "Last Mile plan", sort_by_risk: "Riskiest first", sort_by_value: "Biggest loss first" };

function longDate(iso) {
  return iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "–";
}
function plusDays(iso, n) {
  const d = new Date(iso + "T00:00:00"); d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}
const when = (iso) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "–";

async function init() {
  R.list = await api("/api/reports");
  const want = new URLSearchParams(location.search).get("date");
  const sel = $("#dateSelect");
  sel.innerHTML = R.list.length ? R.list.map((r) => `<option value="${esc(r.business_date)}">${esc(r.business_date)}${r.closed ? " · closed" : " · open"}</option>`).join("")
    : `<option value="">No days yet</option>`;
  const date = want && R.list.some((r) => r.business_date === want) ? want : (R.list[0] && R.list[0].business_date);
  if (!date) { $("#report").innerHTML = `<div class="empty">No business day has a run yet. Start one from the <a href="/manager">Manager Console</a>.</div>`; return; }
  sel.value = date;
  await load(date);
}

async function load(date) {
  R.date = date;
  history.replaceState(null, "", `/report?date=${encodeURIComponent(date)}`);
  $("#report").innerHTML = `<div class="empty">Loading…</div>`;
  try { render(await api(`/api/reports/${encodeURIComponent(date)}`)); }
  catch (e) { $("#report").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
}

function section(title, intro, body) {
  return `<section class="panel r-sec"><div class="panel-h"><h2>${title}</h2></div><div class="panel-b">${intro ? `<p>${intro}</p>` : ""}${body}</div></section>`;
}

function render(r) {
  const final = r.status === "final";
  const head = `<section class="panel r-head">
      <div style="flex:1 1 360px"><span class="eyebrow">${esc(r.institution)} · ${esc(r.scenario)}</span>
        <h1>${esc(longDate(r.business_date))}</h1>
        <div class="sub">${final ? `<span class="stamp"><span class="pill good">Final</span> closed by ${esc(r.closed_by)} on ${esc(when(r.closed_at))}</span>`
          : `<span class="stamp"><span class="pill warn">Live</span> the day is still open — this report updates as decisions are made</span>`}</div>
        ${r.closed_note ? `<div class="sub" style="margin-top:4px">Note: ${esc(r.closed_note)}</div>` : ""}</div>
      <div class="sub">Generated ${esc(when(r.generated_at))}${r.run ? ` · run <span class="mono">${esc(r.run.run_id)}</span>` : ""}</div>
    </section>`;

  if (!r.run) {
    $("#report").innerHTML = head + section("No worklist", "", `<div class="empty">No worklist was produced for this day.</div>`) + trend();
    return;
  }
  const t = r.totals, decided = t.approved + t.edited + t.rejected;
  const pct = (n) => t.recommended ? (n / t.recommended) * 100 : 0;
  const headline = `<div class="big-tiles">
      <div class="tile"><div class="k">Team</div><div class="v">${fmt.int(r.team.working)} collectors</div>
        <div class="s">${fmt.int(r.team.total_minutes)} shift minutes, ${fmt.int(r.team.plannable_minutes)} planned</div></div>
      <div class="tile"><div class="k">Recommended</div><div class="v">${fmt.int(t.recommended)}</div>
        <div class="s">${fmt.int(t.voice_tasks)} for collectors · ${fmt.int(t.sms)} texts · ${fmt.int(t.escalated)} escalated</div></div>
      <div class="tile"><div class="k">Decided</div><div class="v">${fmt.int(decided)} / ${fmt.int(t.recommended)}</div>
        <div class="s">${t.approved} approved · ${t.edited} changed · ${t.rejected} rejected · ${t.pending} undecided</div>
        <div class="bar"><i style="width:${pct(t.approved)}%;background:var(--good)"></i><i style="width:${pct(t.edited)}%;background:var(--primary)"></i><i style="width:${pct(t.rejected)}%;background:var(--bad)"></i></div></div>
      <div class="tile"><div class="k">Sent to the bank</div><div class="v">${fmt.int(t.released)}</div>
        <div class="s">${t.first_release_at ? `from ${esc(when(t.first_release_at))}` : "nothing released"}</div></div>
      <div class="tile"><div class="k">Estimated avoided loss</div><div class="v">${fmt.money(t.est_value)}</div>
        <div class="s">${t.mc_p10 != null ? `likely ${fmt.money(t.mc_p10)} – ${fmt.money(t.mc_p90)}` : ""} · model estimate for the full plan</div></div>
      <div class="tile"><div class="k">Fits the shifts</div><div class="v">${r.simulation.fits_shift != null ? fmt.pct(r.simulation.fits_shift, 0) : "–"}</div>
        <div class="s">of collector tasks finish in time, simulated</div></div>
    </div>`;

  const teamNote = r.roster ? `${r.roster.source === "saved" ? `Team saved by ${esc(r.roster.saved_by || "–")}` : r.roster.source === "carried_forward" ? "Team carried forward from an earlier day" : "Default team from the configuration"}${r.roster.note && r.roster.source === "saved" ? ` — “${esc(r.roster.note)}”` : ""}.
    ${fmt.pct(r.team.planning_buffer, 0)} of each shift was kept spare because calls run long.` : "";
  const queues = tableHTML(r.queues.map((q) => ({
    collector: q.name + (q.present ? "" : " (not working)"), shift_min: q.shift_minutes, planned_min: q.planned_minutes,
    plannable_min: q.plannable_minutes, tasks: q.tasks, approved: q.approved, changed: q.edited, rejected: q.rejected,
    undecided: q.pending, sent: q.released, est_value: q.est_value, fits_shift: q.completion_simulated, flag: q.over_shift ? "over shift" : "" })),
    { maxHeight: 900, render: { est_value: (v) => `<span class="num">${fmt.money(v)}</span>`, fits_shift: (v) => `<span class="num">${v == null ? "–" : fmt.pct(v, 0)}</span>`,
      planned_min: (v) => `<span class="num">${fmt.int(v)}</span>`, plannable_min: (v) => `<span class="num">${v == null ? "–" : fmt.int(v)}</span>`,
      shift_min: (v) => `<span class="num">${v == null ? "–" : fmt.int(v)}</span>`, flag: (v) => v ? `<span class="pill warn">${esc(v)}</span>` : "" } });

  const actions = tableHTML(r.actions.map((a) => ({ action: a.label, recommended: a.recommended, approved_as: a.final, sent: a.released })), { maxHeight: 400 });
  const overrides = r.overrides.length ? tableHTML(r.overrides.map((o) => ({ reason: o.reason_code.replace(/_/g, " "), decision: o.decision, count: o.n })), { maxHeight: 300 })
    : `<div class="muted">No recommendation was changed or rejected.</div>`;

  const cmp = r.comparison, best = cmp.optimised.est_value;
  const comparison = tableHTML(Object.entries(cmp).map(([k, v]) => ({ approach: POLICY_NAME[k] || k, members: v.selected, est_avoided_loss: v.est_value,
    share_of_last_mile_estimate: k === "optimised" ? "" : (best ? fmt.pct(v.est_value / best, 0) : "") })),
    { render: { est_avoided_loss: (v) => `<span class="num">${fmt.money(v)}</span>` } });

  const f = r.follow_up;
  const matures = plusDays(r.business_date, 30);
  let follow;
  if (!f.available) follow = `<div class="callout warn">The bank could not be reached, so what happened next cannot be shown: ${esc(f.error)}</div>`;
  else if (!f.released) follow = `<div class="muted">Nothing was sent to the bank this day.</div>`;
  else follow = `<div class="tiles">
      <div class="tile"><div class="k">Carried out</div><div class="v">${fmt.int(f.executed)} / ${fmt.int(f.released)}</div>
        <div class="s">${f.awaiting_execution ? `${f.awaiting_execution} waiting — the bank acts when the day closes` : f.not_executed ? `${f.not_executed} not carried out (account no longer behind)` : "all carried out"}</div></div>
      <div class="tile"><div class="k">Reached the member</div><div class="v">${f.voice_executed ? fmt.pct(f.right_party_contacts / f.voice_executed, 0) : "–"}</div>
        <div class="s">${fmt.int(f.right_party_contacts)} of ${fmt.int(f.voice_executed)} calls spoke to the right person</div></div>
      <div class="tile"><div class="k">Caught up within 30 days</div><div class="v">${f.outcomes_known ? `${fmt.int(f.cured_30d)} / ${fmt.int(f.outcomes_known)}` : "not yet known"}</div>
        <div class="s">${f.outcomes_known ? `${fmt.money(f.amount_paid_30d)} paid` : `outcomes arrive from ${esc(matures)}, once 30 days have passed`}</div></div>
    </div>`;

  const runs = tableHTML(r.runs_today.map((x) => ({ started: when(x.started_at), run: x.run_id, status: x.status.replace(/_/g, " "),
    team: `${x.collectors_working} collectors · ${fmt.int(x.team_minutes)} min`, why: x.parent_run_id ? `re-plan of ${x.parent_run_id}` : (x.superseded_by ? `replaced by ${x.superseded_by}` : "") })),
    { maxHeight: 300, render: { run: (v) => `<span class="mono">${esc(v)}</span>` } });

  $("#report").innerHTML = head + `<section class="r-sec">${headline}</section>`
    + section("Team and queues", teamNote + " Each collector's queue was packed to fit their own shift.", queues)
    + section("What was recommended and decided", "Recommended actions, what the manager approved them as, and what was sent to the bank.",
      `<div class="two" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px"><div>${actions}</div><div><div class="section-title">Changes and rejections, by reason</div>${overrides}</div></div>`)
    + section("How the plan compares", "The same team, rules and hours, planned three ways. These are the model's own estimates, and the model scores its own plan: they overstate the gap. Real results only arrive after 30 days — on the simulated bank's benchmark (Manager Console → Sort vs Optimised) the true gain over riskiest-first was far smaller than these estimates suggest.", comparison)
    + section("What happened next", "Read live from the bank. Execution is known once the day closes; whether a member caught up is only known 30 days later.", follow)
    + section("Runs this day", "Every worklist produced for the day. A re-plan replaces the earlier worklist when the team changes.", runs)
    + section("Audit", "", `<div class="callout ${r.audit.chain.valid ? "good" : "bad"}">${r.audit.events_for_run} audit events for this run.
        Audit chain ${r.audit.chain.valid ? "verified" : `broken at event ${r.audit.chain.broken_at_seq}`} across ${fmt.int(r.audit.chain.events)} events.</div>`)
    + trend();
}

function trend() {
  if (!R.list.length) return "";
  return section("Day by day", "Every business day so far. Click a day to open its report.", `<div class="tbl-wrap"><table class="tbl trend"><thead><tr>
      <th>Day</th><th>Status</th><th class="r">Team</th><th class="r">Recommended</th><th class="r">Approved</th><th class="r">Changed</th><th class="r">Rejected</th><th class="r">Sent</th><th class="r">Est. avoided loss</th></tr></thead><tbody>
      ${R.list.map((d) => { const t = d.totals || {}; return `<tr class="click ${d.business_date === R.date ? "sel" : ""}" data-d="${esc(d.business_date)}">
        <td class="num">${esc(d.business_date)}</td><td>${d.closed ? '<span class="pill good">closed</span>' : '<span class="pill warn">open</span>'}</td>
        <td class="r num">${d.collectors_working ?? "–"}</td><td class="r num">${fmt.int(t.recommended)}</td><td class="r num">${fmt.int(t.approved)}</td>
        <td class="r num">${fmt.int(t.edited)}</td><td class="r num">${fmt.int(t.rejected)}</td><td class="r num">${fmt.int(t.released)}</td>
        <td class="r num">${fmt.money(t.est_value)}</td></tr>`; }).join("")}</tbody></table></div>`);
}

document.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-d]");
  if (tr) { $("#dateSelect").value = tr.dataset.d; load(tr.dataset.d); window.scrollTo({ top: 0 }); }
});
$("#dateSelect").onchange = (e) => e.target.value && load(e.target.value);
$("#printBtn").onclick = () => window.print();
init();
