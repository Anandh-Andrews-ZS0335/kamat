// Business KPIs: seven questions for a business analyst, each with its measure, source and how far to trust it.
const K = { data: null };
const STATUS = { measured: ["good", "Measured"], partial: ["pri", "Partly measured"], waiting: ["", "Waiting for outcomes"] };
const shortDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
const longDate = (iso) => iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "–";
const pct = (v, d = 0) => v == null ? "–" : fmt.pct(v, d);

async function load() {
  const w = $("#windowSel").value;
  $("#cards").innerHTML = `<div class="panel"><div class="empty">Calculating KPIs…</div></div>`;
  $("#details").innerHTML = "";
  try { K.data = await api(`/api/kpis?window=${w}`); }
  catch (e) { $("#cards").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
  render();
}

function kpi(id) { return K.data.kpis.find((k) => k.id === id); }

// ------------------------------------------------------------------------------------------ cards
function card(k, big, sub) {
  const [cls, text] = STATUS[k.status] || ["", k.status];
  return `<a class="panel kcard" href="#kpi-${k.id}">
    <div class="q"><span class="n">KPI ${k.id}</span><b>${esc(k.question)}</b></div>
    <div class="big">${big}</div><div class="sub">${sub}</div>
    <div class="foot"><span class="pill ${cls}">${esc(text)}</span><span class="muted" style="font-size:11.5px">How it's measured →</span></div></a>`;
}

function render() {
  const d = K.data;
  if (!d.window_days) {
    $("#truth").innerHTML = `<span>No business days with a worklist yet.</span>`;
    $("#cards").innerHTML = `<div class="panel"><div class="empty">Run and close some business days first. For the simulated bank:
      <span class="mono">uv run python scripts/simulate_days.py --days 35 --yes</span></div></div>`;
    return;
  }
  Shell.setContext(`${d.institution} · ${shortDate(d.from)} – ${shortDate(d.to)}`);
  const firstOutcomeDay = d.days.find((p) => !p.outcomes_known);
  $("#truth").innerHTML = `
    <span><b>${d.window_days}</b> business days, ${esc(longDate(d.from))} – ${esc(longDate(d.to))}</span>
    <span class="pill">outcomes arrive ${d.outcome_days} days after an action</span>
    ${firstOutcomeDay ? `<span>Actions from ${esc(shortDate(firstOutcomeDay.date))} onward are still inside their window.</span>` : ""}
    <span class="pill warn">no holdout group yet</span><span>Results are <b>observed</b>, not proven to be caused by Last Mile.</span>`;

  const k1 = kpi(1).headline, k2 = kpi(2).headline, k3 = kpi(3).headline, k4 = kpi(4).headline, k5 = kpi(5).headline, k6 = kpi(6).headline, k7 = kpi(7).headline;
  const waitingFrom = d.days[0] ? longDate(d.days[0].outcomes_from) : "–";
  const sdLeft = k6.sleeping_dogs ? 1 - k6.sleeping_dogs_contacted / k6.sleeping_dogs : null;
  const then = k6.portfolio_then || {}, now = k6.portfolio_now || {};
  $("#cards").innerHTML = [
    card(kpi(1), k1.paid_per_collector_hour != null ? `${fmt.money(k1.paid_per_collector_hour)}<span class="sub"> / collector hour</span>` : "–",
      k1.days_measured ? `${fmt.money(k1.paid_30d)} paid within 30 days · ${fmt.int(k1.collector_hours)} collector hours · ${k1.days_measured} day(s) with outcomes`
        : `First outcomes on ${esc(waitingFrom)}.`),
    card(kpi(2), pct(k2.cure_rate, 1), k2.outcomes_known ? `${fmt.int(k2.cured)} of ${fmt.int(k2.outcomes_known)} released accounts caught up within 30 days`
      : `First outcomes on ${esc(waitingFrom)}.`),
    card(kpi(3), pct(k3.contacted_worse_rate, 1), k3.contacted_n ? `of contacted accounts worsened within 30 days · accounts not contacted: ${pct(k3.not_contacted_worse_rate, 1)}`
      : "Needs a run about 30 days after the first contacts."),
    card(kpi(4), pct(k4.released_share), `of plannable collector minutes released · ${pct(k4.planned_share)} planned · ${pct(k4.reach_rate)} of calls reached the customer`),
    card(kpi(5), `${fmt.int(k5.planned_breaches)} <span class="sub">in Last Mile's plans</span>`,
      k5.member_days_over_daily_cap != null ? `${fmt.int(k5.member_days_over_daily_cap)} customer-day(s) over the daily cap in the bank's contact log (30 days)` : "No contact log yet."),
    card(kpi(6), pct(sdLeft, 1), `of predicted "sleeping dog" account-days left alone · ${fmt.int(k6.hardship_referrals)} hardship referrals released · complaints ${fmt.int(then.complaints_12m)} → ${fmt.int(now.complaints_12m)}`),
    card(kpi(7), `${fmt.int(k7.fully_explained)} / ${fmt.int(k7.released_checked)}`, "recent released actions have a stored reason with a source for every figure"),
  ].join("");
  renderDetails();
}

// ----------------------------------------------------------------------------------------- charts
function niceMax(v, pctScale) {
  if (pctScale) return Math.min(1, Math.max(0.2, Math.ceil((v * 1.1) * 5) / 5));   // 20% steps: quarters land on clean ticks
  if (!v) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v))), f = v / exp;
  return [1, 2, 2.5, 5, 10].find((s) => s >= f * 1.1) * exp;
}

function chart(host, { days, series, kind = "line", percent = false, money = false, waitingText }) {
  const W = 760, H = 250, L = 62, R = 70, T = 14, B = 30;
  const values = series.flatMap((s) => s.values.filter((v) => v != null));
  const yMax = niceMax(Math.max(0, ...values), percent);
  const band = (W - L - R) / Math.max(1, days.length);
  const x = (i) => L + band * i + band / 2, y = (v) => T + (H - T - B) * (1 - v / yMax);
  const f = (v) => v == null ? "–" : percent ? fmt.pct(v, 1) : money ? fmt.money(v) : fmt.int(v);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * yMax);
  let svg = ticks.map((t) => `<line class="${t === 0 ? "base" : "gridline"}" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>
    <text class="axis" x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${percent ? Math.round(t * 100) + "%" : money ? "$" + fmt.int(t) : fmt.int(t)}</text>`).join("");
  const xi = [...new Set([0, Math.floor((days.length - 1) / 2), days.length - 1])];
  svg += xi.map((i) => `<text class="axis" x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(shortDate(days[i].date))}</text>`).join("");

  if (kind === "column") {
    const s = series[0], w = Math.min(24, band * 0.6);
    svg += days.map((dd, i) => {
      const v = s.values[i];
      if (v == null) return `<rect class="wait" x="${x(i) - w / 2}" y="${y(0) - 3}" width="${w}" height="3" rx="1.5"/>`;
      const top = y(v), h = Math.max(0, y(0) - top), r = Math.min(4, h);
      return `<path fill="${s.color}" d="M${x(i) - w / 2},${y(0)} V${top + r} Q${x(i) - w / 2},${top} ${x(i) - w / 2 + r},${top} H${x(i) + w / 2 - r} Q${x(i) + w / 2},${top} ${x(i) + w / 2},${top + r} V${y(0)} Z"/>`;
    }).join("");
    const lastI = s.values.map((v, i) => v != null ? i : -1).filter((i) => i >= 0).pop();
    if (lastI != null) svg += `<text class="lbl" x="${x(lastI)}" y="${y(s.values[lastI]) - 6}" text-anchor="middle">${f(s.values[lastI])}</text>`;
  } else {
    const ends = [];
    series.forEach((s) => {
      let d = "", pen = false;
      s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i)},${y(v)} `; pen = true; });
      svg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      const lastI = s.values.map((v, i) => v != null ? i : -1).filter((i) => i >= 0).pop();
      if (lastI != null) {
        svg += `<circle cx="${x(lastI)}" cy="${y(s.values[lastI])}" r="4.5" fill="${s.color}" stroke="var(--surface)" stroke-width="2"/>`;
        ends.push({ x: x(lastI) + 10, y: y(s.values[lastI]) + 4, text: f(s.values[lastI]) });
      }
    });
    ends.sort((a, b) => a.y - b.y).forEach((e, i, all) => {        // keep end labels from sitting on each other
      if (i && e.y - all[i - 1].y < 14) e.y = all[i - 1].y + 14;
      svg += `<text class="lbl" x="${e.x}" y="${e.y}">${esc(e.text)}</text>`;
    });
  }
  svg += `<line id="xh" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="var(--line-strong)" stroke-width="1" visibility="hidden"/>`;
  svg += days.map((_, i) => `<rect data-i="${i}" x="${L + band * i}" y="${T}" width="${band}" height="${H - T - B}" fill="transparent"/>`).join("");

  host.innerHTML = `${series.length > 1 ? `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : ""}
    <div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map((s) => s.name).join(", "))} by business day">${svg}</svg><div class="tip"></div></div>
    <details class="tbl"><summary>Show as a table</summary>${tableHTML(days.map((dd, i) => Object.fromEntries([["business_day", dd.date],
      ...series.map((s) => [s.name, s.values[i] == null ? (waitingText ? waitingText(dd) : "–") : f(s.values[i])])])), { maxHeight: 280 })}</details>`;
  const svgEl = host.querySelector("svg"), tip = host.querySelector(".tip"), xh = host.querySelector("#xh");
  host.querySelectorAll("rect[data-i]").forEach((r) => {
    r.addEventListener("mousemove", (ev) => {
      const i = Number(r.dataset.i), box = svgEl.getBoundingClientRect(), scale = box.width / W;
      xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("visibility", "visible");
      tip.innerHTML = `<b>${esc(longDate(days[i].date))}</b>` + series.map((s) => `<div class="row"><i style="background:${s.color}"></i>${esc(s.name)}:
        <strong>${s.values[i] == null ? esc(waitingText ? waitingText(days[i]) : "–") : f(s.values[i])}</strong></div>`).join("");
      tip.style.display = "block";
      const left = Math.min(box.width - tip.offsetWidth - 4, Math.max(4, x(i) * scale + 12));
      tip.style.left = `${left}px`; tip.style.top = `${Math.max(0, ev.clientY - box.top - 40)}px`;
    });
    r.addEventListener("mouseleave", () => { tip.style.display = "none"; xh.setAttribute("visibility", "hidden"); });
  });
}

// ---------------------------------------------------------------------------------------- details
function explain(k) {
  return `<div class="explain">
    <div><b>How it is measured</b>${esc(k.definition)}</div>
    <div><b>Where the numbers come from</b>${esc(k.source)}</div>
    ${k.caveat ? `<div><b>How far to trust it</b>${esc(k.caveat)}</div>` : ""}
    ${k.gap ? `<div><b>Needed to complete it</b>${esc(k.gap)}</div>` : ""}</div>`;
}

function section(k, body) {
  const [cls, text] = STATUS[k.status] || ["", k.status];
  return `<section class="panel sec" id="kpi-${k.id}"><div class="panel-h"><span class="eyebrow">KPI ${k.id}</span><h2>${esc(k.question)}</h2>
    <span class="pill ${cls}">${esc(text)}</span></div><div class="panel-b">${body}<div style="margin-top:var(--s4)">${explain(k)}</div></div></section>`;
}

function renderDetails() {
  const d = K.data, days = d.days;
  const s1 = getComputedStyle(document.documentElement).getPropertyValue("--s1").trim() || "#00809A";
  const s2 = getComputedStyle(document.documentElement).getPropertyValue("--s2").trim() || "#C26F12";
  const wait = (dd) => `waiting until ${shortDate(dd.outcomes_from)}`;
  const k4 = kpi(4).headline, k5 = kpi(5).headline, k6 = kpi(6).headline, k7 = kpi(7);
  const then = k6.portfolio_then || {}, now = k6.portfolio_now || {};

  $("#details").innerHTML = [
    section(kpi(1), `<div id="c1"></div>`),
    section(kpi(2), `<div id="c2"></div>`),
    section(kpi(3), `<div id="c3"></div>`),
    section(kpi(4), `<div class="tiles" style="margin-bottom:var(--s4)">
        <div class="tile"><div class="k">Planned</div><div class="v">${pct(k4.planned_share)}</div><div class="s">of plannable minutes filled by the plan</div></div>
        <div class="tile"><div class="k">Released</div><div class="v">${pct(k4.released_share)}</div><div class="s">approved and sent — escalated items wait for a person</div></div>
        <div class="tile"><div class="k">Fits the shift</div><div class="v">${pct(k4.fits_shift)}</div><div class="s">of each queue finishes in time (simulated)</div></div>
        <div class="tile"><div class="k">Reached the customer</div><div class="v">${pct(k4.reach_rate)}</div><div class="s">of calls, reported by the bank</div></div>
        <div class="tile"><div class="k">Estimated value</div><div class="v">${k4.est_value_per_hour != null ? fmt.money(k4.est_value_per_hour) : "–"}</div><div class="s">per collector hour, model estimate</div></div>
      </div><div id="c4"></div>`),
    section(kpi(5), `<div class="tiles" style="margin-bottom:var(--s4)">
        <div class="tile"><div class="k">In Last Mile's plans</div><div class="v">${fmt.int(k5.planned_breaches)}</div><div class="s">every plan re-checked; a breach stops publishing</div></div>
        <div class="tile"><div class="k">Over the daily cap</div><div class="v">${fmt.int(k5.member_days_over_daily_cap)}</div><div class="s">customer-days above ${fmt.int(k5.daily_cap)} contact/day in the bank's log</div></div>
        <div class="tile"><div class="k">Over the weekly cap now</div><div class="v">${fmt.int(k5.members_over_weekly_cap_now)}</div><div class="s">customers above ${fmt.int(k5.weekly_cap)} contacts in 7 days</div></div>
      </div>
      ${k5.member_days_over_daily_cap ? `<div class="finding"><b>Finding.</b> ${fmt.int(k5.involving_last_mile_release)} of these ${fmt.int(k5.member_days_over_daily_cap)} customer-days
        had a Last Mile action released the same day, and the bank's own process contacted the same customer too. Last Mile's plan respects the cap on
        its own; the two processes do not see each other's same-day contacts. Fix: share today's bank contacts before planning, or route all contact through one queue.</div>` : ""}`),
    section(kpi(6), `<div class="tiles">
        <div class="tile"><div class="k">Sleeping dogs left alone</div><div class="v">${pct(k6.sleeping_dogs ? 1 - k6.sleeping_dogs_contacted / k6.sleeping_dogs : null, 1)}</div>
          <div class="s">${fmt.int(k6.sleeping_dogs_contacted)} contacted of ${fmt.int(k6.sleeping_dogs)} account-days predicted to react badly</div></div>
        <div class="tile"><div class="k">Hardship referrals</div><div class="v">${fmt.int(k6.hardship_referrals)}</div><div class="s">released; each needs a named reviewer first</div></div>
        <div class="tile"><div class="k">Contacts per customer</div><div class="v">${k6.contacts_per_customer != null ? k6.contacts_per_customer.toFixed(2) : "–"}</div><div class="s">per contacted customer, per day</div></div>
        <div class="tile"><div class="k">Complaints (12 months)</div><div class="v">${fmt.int(then.complaints_12m)} → ${fmt.int(now.complaints_12m)}</div><div class="s">across the overdue book, first vs latest day</div></div>
        <div class="tile"><div class="k">Opted out of contact</div><div class="v">${fmt.int(then.opted_out)} → ${fmt.int(now.opted_out)}</div><div class="s">do-not-call or cease-contact, first vs latest day</div></div>
      </div>
      ${k6.hardship_referrals === 0 ? `<p class="note">No hardship referral was released in this period: they are always escalated and were not approved individually.</p>` : ""}`),
    section(k7, `<p class="note" style="margin-top:0">Pick any released action: the reason, the source of every figure, who approved it and the solver's
        "why this customer and not another" open in the Today console.</p>
      ${tableHTML(k7.recent.map((r) => ({ released: new Date(r.released_at).toLocaleString(), account: r.account_token, action: r.action,
        approved_by: r.released_by, why: r })), { maxHeight: 360, render: {
        account: (v) => `<span class="mono">${esc(v)}</span>`,
        why: (r) => `<a class="btn small" href="/manager?run=${encodeURIComponent(r.run_id)}&account=${encodeURIComponent(r.account_token)}">Why this customer? →</a>` } })}`),
  ].join("");

  const measured1 = days.map((p) => p.outcomes_known ? p.paid / (p.team_minutes / 60) : null);
  chart($("#c1"), { days, kind: "column", money: true, waitingText: wait,
    series: [{ name: "Paid within 30 days per collector hour", color: s1, values: measured1 }] });
  chart($("#c2"), { days, percent: true, waitingText: wait,
    series: [{ name: "Caught up within 30 days", color: s1, values: days.map((p) => p.outcomes_known ? p.cured / p.outcomes_known : null) }] });
  chart($("#c3"), { days, percent: true, waitingText: (dd) => `waiting until about ${shortDate(dd.outcomes_from)}`,
    series: [{ name: "Contacted by Last Mile", color: s1, values: days.map((p) => p.worse.status === "measured" ? p.worse.contacted : null) },
             { name: "Not contacted", color: s2, values: days.map((p) => p.worse.status === "measured" ? p.worse.not_contacted : null) }] });
  chart($("#c4"), { days, percent: true,
    series: [{ name: "Collector minutes released", color: s1, values: days.map((p) => p.plannable_minutes ? p.released_minutes / p.plannable_minutes : null) },
             { name: "Calls that reached the customer", color: s2, values: days.map((p) => p.voice_executed ? p.reached / p.voice_executed : null) }] });
}

$("#windowSel").onchange = load;
Shell.mount({ page: "kpis", title: "Business KPIs" }).then(load);
