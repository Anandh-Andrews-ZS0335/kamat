// Shared helpers for both consoles. Every value interpolated into HTML goes through esc().
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  let body = null;
  try { body = await r.json(); } catch { body = null; }
  if (!r.ok) throw new Error((body && (body.detail || body.message)) || `${r.status} ${r.statusText}`);
  return body;
}

const fmt = {
  money: (v) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 }),
  int: (v) => (v === null || v === undefined) ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }),
  pct: (v, d = 1) => (v === null || v === undefined) ? "–" : (v * 100).toFixed(d) + "%",
  pts: (v) => (v === null || v === undefined) ? "–" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + " pts",
  ms: (v) => (v === null || v === undefined) ? "–" : v >= 1000 ? (v / 1000).toFixed(1) + " s" : v + " ms",
  time: (iso) => iso ? new Date(iso).toLocaleTimeString(undefined, { hour12: false }) + "." + String(new Date(iso).getMilliseconds()).padStart(3, "0") : "–",
  short: (s, n = 12) => s ? String(s).slice(0, n) : "–",
  value(v) {
    if (v === null || v === undefined) return "–";
    if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : (Math.abs(v) < 1 ? v.toFixed(4) : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  },
};

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), kind === "bad" ? 6000 : 3200);
}

function statusPill(status) {
  const map = { done: "good", released: "good", awaiting_approval: "warn", waiting: "warn", running: "pri", failed: "bad", aborted: "bad", pending: "", approved: "good", edited: "pri", rejected: "bad" };
  return `<span class="pill ${map[status] || ""}">${esc(String(status).replace(/_/g, " "))}</span>`;
}

function segPill(seg, label) {
  return `<span class="pill seg-${esc(seg)}">${esc(label || String(seg).replace(/_/g, " "))}</span>`;
}

// Generic table from rows of objects. Cells containing objects render as compact JSON.
function tableHTML(rows, opts = {}) {
  if (!rows || !rows.length) return `<div class="empty">No rows</div>`;
  const cols = opts.columns || Object.keys(rows[0]);
  const head = cols.map((c) => `<th class="${opts.right && opts.right.includes(c) ? "r" : ""}">${esc(c.replace(/_/g, " "))}</th>`).join("");
  const body = rows.map((r, i) => `<tr ${opts.click ? `class="click" data-i="${i}"` : ""}>${cols.map((c) => {
    const v = r[c];
    if (opts.render && opts.render[c]) return `<td>${opts.render[c](v, r)}</td>`;
    if (v !== null && typeof v === "object") return `<td><div class="cell-json">${esc(JSON.stringify(v))}</div></td>`;
    if (typeof v === "boolean") return `<td>${v ? '<span class="pill good">yes</span>' : '<span class="pill bad">no</span>'}</td>`;
    const numeric = typeof v === "number";
    return `<td class="${numeric ? "r num" : "wrap"}">${esc(fmt.value(v))}</td>`;
  }).join("")}</tr>`).join("");
  return `<div class="tbl-wrap" style="max-height:${opts.maxHeight || 420}px"><table class="tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function kvHTML(obj) {
  return `<dl class="kv">${Object.entries(obj || {}).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(fmt.value(v))}</dd>`).join("")}</dl>`;
}
