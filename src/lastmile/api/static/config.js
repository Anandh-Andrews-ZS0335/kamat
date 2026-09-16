// Configuration editor. The server validates; this page explains and shows where things are.
const C = { files: [], packs: {}, file: null, original: "", check: null, section: null, field: null, versions: [] };
const RISK = { safe: ["good", "Everyday setting"], careful: ["warn", "Changes who is contacted or how"], expert: ["bad", "Must match the bank's data or the code"] };

async function init() {
  const r = await api("/api/config/files");
  C.files = r.files; C.packs = r.packs;
  $("#hashChip").innerHTML = r.current_hash ? `<span class="chip mono" title="Hash of the configuration the next run will use">next run: cfg ${esc(r.current_hash.slice(0, 12))}</span>` : "";
  $("#files").innerHTML = Object.entries(C.packs).map(([kind, p]) => `<div class="grp"><h3>${esc(p.title)}</h3><p>${esc(p.summary)}</p></div>
    ${C.files.filter((f) => f.kind === kind).map((f) => `<button class="file" data-k="${esc(f.kind)}" data-id="${esc(f.id)}">
      <span>${esc(f.id)}.yaml</span>${f.active ? '<span class="pill good" title="Used by the next run">in use</span>' : '<span class="pill" title="Not used by the default run">not used</span>'}</button>`).join("")}`).join("");
  $$(".file").forEach((b) => b.onclick = () => openFile(b.dataset.k, b.dataset.id));
  const q = new URLSearchParams(location.search);
  const first = C.files.find((f) => f.kind === q.get("kind") && f.id === q.get("id")) || C.files.find((f) => f.kind === "institutions" && f.active) || C.files[0];
  if (first) openFile(first.kind, first.id);
}

function dirty() { return $("#code").value !== C.original; }

async function openFile(kind, id) {
  if (C.file && dirty() && !confirm("You have unsaved edits. Discard them?")) return;
  const f = await api(`/api/config/files/${kind}/${id}`);
  C.file = f; C.original = f.text; C.check = f.check; C.section = null; C.field = null;
  history.replaceState(null, "", `/admin/config?kind=${kind}&id=${id}`);
  $$(".file").forEach((b) => b.classList.toggle("on", b.dataset.k === kind && b.dataset.id === id));
  Shell.setContext(`${f.pack.title} · ${f.id}.yaml`);
  $("#fileHead").innerHTML = `<span class="eyebrow">${esc(f.pack.title)}</span><h2 class="mono">${esc(f.path)}</h2>
    ${f.active ? '<span class="pill good">used by the next run</span>' : '<span class="pill">not used by the default run</span>'}
    <span class="muted">${f.history.length} earlier version(s)</span>`;
  $("#outline").innerHTML = Object.entries(f.sections).map(([k, s]) => `<button class="sec-chip" data-s="${esc(k)}" title="${esc(s.title)}">
    <i class="risk ${esc(s.risk)}"></i>${esc(k)}</button>`).join("") +
    `<span class="muted" style="font-size:11px;margin-left:auto;align-self:center"><i class="risk safe"></i> everyday <i class="risk careful"></i> careful <i class="risk expert"></i> expert</span>`;
  $$("#outline .sec-chip").forEach((b) => b.onclick = () => jumpToSection(b.dataset.s));
  $("#historySel").innerHTML = `<option value="">Earlier versions…</option>` + f.history.map((h) =>
    `<option value="${esc(h.version)}">${esc(new Date(h.saved_at).toLocaleString())} · ${esc(h.changed_by)} · ${esc(h.reason.slice(0, 40))}</option>`).join("");
  $("#code").value = f.text;
  updateState();
  renderResult();
  const firstSection = Object.keys(f.sections)[0];
  showGuide(firstSection, null);
}

// ------------------------------------------------------------------------------------------ editor
function lineOf(pos) { return $("#code").value.slice(0, pos).split("\n").length; }

function renderGutter() {
  const n = $("#code").value.split("\n").length;
  const errs = new Set((C.check && C.check.errors || []).map((e) => e.line).filter(Boolean));
  const cur = lineOf($("#code").selectionStart);
  let out = "";
  for (let i = 1; i <= n; i++) out += `<span class="${errs.has(i) ? "err" : i === cur ? "cur" : ""}">${i}</span>\n`;
  $("#gutter").innerHTML = out;
  $("#gutter").scrollTop = $("#code").scrollTop;
}

function updateState() {
  const d = dirty();
  $("#dirty").textContent = d ? "Unsaved edits — check them before saving" : "";
  $("#discardBtn").disabled = !d;
  $("#saveBtn").disabled = !(d && C.check && C.check.valid && C.check.text === $("#code").value);
  renderGutter();
}

function cursorContext() {
  const ta = $("#code"), lines = ta.value.split("\n"), idx = lineOf(ta.selectionStart) - 1;
  let section = null, field = null, sub = null;
  for (let i = idx; i >= 0; i--) {
    const raw = lines[i], st = raw.trimStart();
    if (!st || st.startsWith("#")) continue;
    const ind = raw.length - st.length, m = st.replace(/^- /, "").match(/^\{?\s*([A-Za-z_][\w]*)\s*:/);
    if (ind === 0 && m) { section = m[1]; break; }
    if (m && ind > 0) { if (field === null) field = m[1]; else if (sub === null && ind < (lines[idx].length - lines[idx].trimStart().length)) sub = m[1]; }
  }
  return { section, field, parent: sub };
}

function onCursor() {
  renderGutter();
  const c = cursorContext();
  if (c.section && (c.section !== C.section || c.field !== C.field)) showGuide(c.section, c.field, c.parent);
}

function jumpToLine(line) {
  const ta = $("#code"), lines = ta.value.split("\n");
  const pos = lines.slice(0, Math.max(0, line - 1)).reduce((a, l) => a + l.length + 1, 0);
  ta.focus(); ta.setSelectionRange(pos, pos + (lines[line - 1] || "").length);
  ta.scrollTop = Math.max(0, (line - 6) * 20);
  onCursor();
}

function jumpToSection(s) {
  const i = $("#code").value.split("\n").findIndex((l) => new RegExp(`^${s}\\s*:`).test(l));
  if (i >= 0) jumpToLine(i + 1);
  showGuide(s, null);
}

// ------------------------------------------------------------------------------------------- guide
function showGuide(section, field, parent) {
  C.section = section; C.field = field;
  $$("#outline .sec-chip").forEach((b) => b.classList.toggle("on", b.dataset.s === section));
  const s = C.file.sections[section];
  if (!s) {
    $("#guide").innerHTML = `<div class="panel-b"><div class="lbl">Section</div><h3 class="mono">${esc(section || "–")}</h3>
      <p>No guide is written for this section. It is still validated when you check.</p></div>`;
    return;
  }
  const [cls, label] = RISK[s.risk] || ["", ""];
  const key = parent && s.fields[`${parent}.${field}`] ? `${parent}.${field}` : field;
  const fields = Object.entries(s.fields);
  $("#guide").innerHTML = `<div class="panel-h"><h2>What this section does</h2><div class="spacer"></div><span class="pill ${cls}">${esc(s.risk)}</span></div>
    <div class="panel-b">
      <span class="eyebrow mono">${esc(section)}</span>
      <h3>${esc(s.title)}</h3>
      <p>${esc(s.what)}</p>
      <div class="lbl">If you change it</div><p>${esc(s.effect)}</p>
      <p class="muted" style="font-size:11.5px"><i class="risk ${esc(s.risk)}"></i> ${esc(label)}</p>
      <div class="lbl">Read by</div><div class="agents">${s.used_by.map((a) => `<span class="chip">${esc(a)}</span>`).join("")}</div>
      ${fields.length ? `<div class="lbl">Settings</div><dl>${fields.map(([k, v]) => `<div class="${k === key || k === field ? "field-on" : ""}"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>` : ""}
    </div>`;
  const on = $("#guide .field-on");
  if (on) on.scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------------------------------------- check
async function runCheck() {
  const text = $("#code").value;
  $("#checkBtn").disabled = true;
  $("#result").innerHTML = `<span class="muted">Checking…</span>`;
  try {
    C.check = await api(`/api/config/files/${C.file.kind}/${C.file.id}/check`, { method: "POST", body: JSON.stringify({ text }) });
    C.check.text = text;
  } catch (e) { $("#result").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
  finally { $("#checkBtn").disabled = false; }
  renderResult(); updateState();
}

function fmtVal(v) { return v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v); }

function renderResult() {
  const c = C.check;
  if (!c) { $("#result").innerHTML = ""; return; }
  const isOriginal = !dirty();
  const errs = c.errors.map((e) => `<div class="item"><span class="pill bad">${esc(e.stage)}</span><div>
      <div>${esc(e.message)}</div>
      <div class="where">${e.path ? esc(e.path) : ""}${e.line ? ` · <button data-line="${e.line}">line ${e.line}</button>` : ""}</div>
      ${e.meaning ? `<div class="muted" style="font-size:12px;margin-top:2px">This setting: ${esc(e.meaning)}</div>` : ""}</div></div>`).join("");
  const warns = c.warnings.map((w) => `<div class="item"><span class="pill warn">Warning</span><div><div>${esc(w.message)}</div><div class="where">${esc(w.path || "")}</div></div></div>`).join("");
  const chg = c.changes.map((x) => `<div class="item"><span class="pill ${x.risk === "expert" ? "bad" : x.risk === "careful" ? "warn" : "pri"}">${esc(x.kind)}</span><div>
      <div class="chg"><b>${esc(x.path)}</b>: <del>${esc(fmtVal(x.old))}</del> → <ins>${esc(fmtVal(x.new))}</ins></div>
      ${x.meaning ? `<div style="font-size:12px;margin-top:2px">${esc(x.meaning)}</div>` : ""}
      ${x.effect ? `<div class="muted" style="font-size:12px;margin-top:2px">${esc(x.effect)}</div>` : ""}
      ${x.used_by && x.used_by.length ? `<div class="muted" style="font-size:11.5px;margin-top:2px">Read by ${esc(x.used_by.join(", "))}</div>` : ""}</div></div>`).join("");
  let head;
  if (c.errors.length) head = `<div class="callout bad"><b>${c.errors.length} problem(s) must be fixed before saving.</b> Nothing has been written.</div>`;
  else if (isOriginal) head = `<div class="callout good">This file is valid as it is on disk${c.warnings.length ? `, with ${c.warnings.length} warning(s)` : ""}. Edit it, then check your changes.</div>`;
  else head = `<div class="callout good"><b>All checks passed.</b> ${c.changes.length} change(s) below. ${c.config_hash_after ? `The next run would use configuration <span class="mono">${esc(c.config_hash_after.slice(0, 12))}</span>.` : ""} You can save.</div>`;
  $("#result").innerHTML = head + (errs ? `<div class="section-title">Problems</div>${errs}` : "") + (warns ? `<div class="section-title">Warnings</div>${warns}` : "")
    + (chg ? `<div class="section-title">What changes</div>${chg}` : "");
  $$("#result [data-line]").forEach((b) => b.onclick = () => jumpToLine(Number(b.dataset.line)));
}

// -------------------------------------------------------------------------------------------- save
function openSave() {
  const c = C.check;
  $("#dlgSummary").innerHTML = `<div class="section-title">${c.changes.length} change(s) to <span class="mono">${esc(C.file.path)}</span></div>
    ${c.changes.slice(0, 12).map((x) => `<div class="chg">${esc(x.path)}: <del>${esc(fmtVal(x.old))}</del> → <ins>${esc(fmtVal(x.new))}</ins></div>`).join("")}
    ${c.changes.length > 12 ? `<div class="muted">and ${c.changes.length - 12} more</div>` : ""}
    ${c.warnings.length ? `<div class="callout warn" style="margin-top:8px">${c.warnings.length} warning(s): ${esc(c.warnings.map((w) => w.message).join("; "))}</div>` : ""}`;
  $("#dlgMsg").textContent = "";
  $("#saveDlg").showModal();
  $("#dlgReason").focus();
}

async function doSave() {
  const reason = $("#dlgReason").value.trim();
  if (reason.length < 5) { $("#dlgMsg").textContent = "Please say why (at least a few words)."; return; }
  $("#dlgSave").disabled = true;
  try {
    const r = await api(`/api/config/files/${C.file.kind}/${C.file.id}`, { method: "PUT",
      body: JSON.stringify({ text: $("#code").value, base_sha: C.file.sha, reason }) });
    $("#saveDlg").close(); $("#dlgReason").value = "";
    toast(`Saved. The next run uses configuration ${String(r.config_hash_after || "").slice(0, 12)}`);
    const k = C.file.kind, id = C.file.id; C.file = null;
    await init(); await openFile(k, id);
  } catch (e) { $("#dlgMsg").textContent = e.message; }
  finally { $("#dlgSave").disabled = false; }
}

// ---------------------------------------------------------------------------------------- history
async function loadVersion(v) {
  if (!v) return;
  const h = C.file.history.find((x) => x.version === v);
  if (dirty() && !confirm("Replace your unsaved edits with this earlier version?")) { $("#historySel").value = ""; return; }
  const r = await api(`/api/config/files/${C.file.kind}/${C.file.id}/versions/${v}`);
  $("#code").value = r.text;
  $("#historySel").value = "";
  C.check = null; updateState();
  $("#result").innerHTML = `<div class="callout">Loaded the version from before the change on ${esc(new Date(h.saved_at).toLocaleString())} by ${esc(h.changed_by)}
    (“${esc(h.reason)}”). Check it, then save to restore it.</div>`;
  runCheck();
}

const code = $("#code");
code.addEventListener("input", () => { updateState(); });
code.addEventListener("scroll", () => { $("#gutter").scrollTop = code.scrollTop; });
["click", "keyup"].forEach((ev) => code.addEventListener(ev, onCursor));
code.addEventListener("keydown", (e) => {
  if (e.key === "Tab") {   // YAML needs spaces: Tab inserts two
    e.preventDefault();
    const s = code.selectionStart;
    code.setRangeText("  ", s, code.selectionEnd, "end"); updateState();
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); runCheck(); }
});
$("#checkBtn").onclick = runCheck;
$("#saveBtn").onclick = openSave;
$("#discardBtn").onclick = () => { if (confirm("Discard your edits?")) { code.value = C.original; C.check = C.file.check; updateState(); renderResult(); } };
$("#historySel").onchange = (e) => loadVersion(e.target.value);
$("#dlgClose").onclick = () => $("#saveDlg").close();
$("#dlgSave").onclick = doSave;
window.addEventListener("beforeunload", (e) => { if (C.file && dirty()) { e.preventDefault(); e.returnValue = ""; } });
Shell.mount({ page: "config", title: "Configuration" }).then(init);
