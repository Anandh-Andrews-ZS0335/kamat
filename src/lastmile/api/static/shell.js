// The application shell: sidebar, top bar, environment status and the signed-in user.
// Each console page calls Shell.mount({page, title, context}) and keeps its own content in <main class="page">.

const ICONS = {
  today: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>',
  worklist: '<svg viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>',
  team: '<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0M17 11a3 3 0 1 0-2-5.2M21 20a5 5 0 0 0-4-4.9"/></svg>',
  report: '<svg viewBox="0 0 24 24"><path d="M6 3h9l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v6h6M9 14h6M9 17h4"/></svg>',
  agents: '<svg viewBox="0 0 24 24"><rect x="4" y="8" width="16" height="11" rx="2"/><path d="M12 8V5M9 3h6M8.5 13h.01M15.5 13h.01M9 16.5h6"/></svg>',
  config: '<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h6M14 17h6M4 12h2M10 12h10"/><circle cx="16" cy="7" r="2"/><circle cx="12" cy="17" r="2"/><circle cx="8" cy="12" r="2"/></svg>',
  guide: '<svg viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20v3H6.5A2.5 2.5 0 0 1 4 20.5z"/></svg>',
  demo: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
  bank: '<svg viewBox="0 0 24 24"><path d="M3 10 12 4l9 6M5 10v9M19 10v9M9 10v9M15 10v9M3 21h18"/></svg>',
  menu: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>',
  collapse: '<svg viewBox="0 0 24 24"><path d="M15 5 8 12l7 7"/></svg>',
  expand: '<svg viewBox="0 0 24 24"><path d="m9 5 7 7-7 7"/></svg>',
  sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
};

const NAV = [
  { group: "Operate", items: [
    { id: "manager", href: "/manager", label: "Today", icon: "today", title: "Business day, worklist and approvals" },
    { id: "report", href: "/report", label: "Daily reports", icon: "report", title: "One page per business day" },
  ]},
  { group: "Build", items: [
    { id: "admin", href: "/admin", label: "Runs & agents", icon: "agents", title: "Every stage, agent and tool call" },
    { id: "config", href: "/admin/config", label: "Configuration", icon: "config", title: "The three YAML rule packs" },
  ]},
  { group: "Explain", items: [
    { id: "guide", href: "/guide", label: "Guided tour", icon: "guide", title: "Plain-language walkthrough for clients" },
    { id: "demo", href: "/demo", label: "Demo showcase", icon: "demo", title: "The showcase deck for presentations" },
    { id: "bankdocs", href: "http://127.0.0.1:8001/docs", label: "Bank API", icon: "bank", title: "The bank's own API documentation", external: true },
  ]},
];

const ROLE_NOTE = {
  admin: "Full access, including saving configuration.",
  manager: "Runs the business day. Can read configuration but not save it.",
  guest: "Read-only. Nothing can be changed or released.",
};

const Shell = {
  user: null,
  status: null,

  async mount({ page, title, context = "" }) {
    const collapsed = localStorage.getItem("lm.side") === "collapsed";
    if (collapsed) document.body.classList.add("side-collapsed");
    this.applyTheme(localStorage.getItem("lm.theme") || "system");

    document.body.insertAdjacentHTML("afterbegin", `
      <div class="scrim" id="scrim"></div>
      <aside class="side" id="side" aria-label="Main">
        <a class="side-brand" href="/manager"><span class="mark">LM</span><span><b>Last Mile</b><small>Collections</small></span></a>
        <nav class="side-nav">${NAV.map((g) => `<div class="side-group">${g.group}</div>` + g.items.map((i) => `
          <a class="side-link ${i.id === page ? "on" : ""}" href="${i.href}" title="${esc(i.title)}"${i.external ? ' target="_blank" rel="noopener"' : ""}
             ${i.id === page ? 'aria-current="page"' : ""}>${ICONS[i.icon]}<span>${esc(i.label)}</span>
             ${i.id === "manager" ? '<span class="badge hidden" id="navPending"></span>' : ""}</a>`).join("")).join("")}
        </nav>
        <div class="side-foot">
          <div class="env"><span>Business day</span><b id="envDate">—</b></div>
          <button class="icon-btn" id="collapseBtn" title="Collapse or expand the sidebar" aria-label="Collapse or expand the sidebar">
            ${collapsed ? ICONS.expand : ICONS.collapse}</button>
        </div>
      </aside>

      <header class="top">
        <button class="icon-btn" id="menuBtn" aria-label="Open navigation">${ICONS.menu}</button>
        <div class="crumb"><h1>${esc(title)}</h1><span class="ctx" id="shellContext">${esc(context)}</span></div>
        <div class="top-actions">
          <div class="chips" id="shellStatus"></div>
          <div id="pageActionsSlot" class="page-actions"></div>
          <div class="user">
            <button class="user-btn" id="userBtn" aria-haspopup="menu" aria-expanded="false">
              <span class="avatar" id="avatar">–</span>
              <span class="who"><b id="userName">…</b><span id="userRole"></span></span>
            </button>
            <div class="menu" id="userMenu" role="menu">
              <div class="head"><b id="menuName">…</b><span id="menuRole"></span></div>
              <div class="label">Appearance</div>
              <div class="seg" id="themeSeg">
                <button data-theme="light" type="button">Light</button>
                <button data-theme="dark" type="button">Dark</button>
                <button data-theme="system" type="button">System</button>
              </div>
              <div class="sep"></div>
              <a class="row" href="/guide" role="menuitem">${ICONS.guide}Guided tour</a>
              <a class="row" href="/report" role="menuitem">${ICONS.report}Daily reports</a>
              <div class="sep"></div>
              <form method="post" action="/logout"><button class="row" type="submit" role="menuitem">Sign out</button></form>
            </div>
          </div>
        </div>
      </header>`);

    const actions = document.getElementById("pageActions");
    if (actions) { actions.hidden = false; document.getElementById("pageActionsSlot").append(...actions.childNodes); actions.remove(); }

    $("#collapseBtn").onclick = () => {
      const now = document.body.classList.toggle("side-collapsed");
      localStorage.setItem("lm.side", now ? "collapsed" : "open");
      $("#collapseBtn").innerHTML = now ? ICONS.expand : ICONS.collapse;
    };
    $("#menuBtn").onclick = () => document.body.classList.toggle("side-open");
    $("#scrim").onclick = () => document.body.classList.remove("side-open");
    $("#userBtn").onclick = (e) => { e.stopPropagation(); const m = $("#userMenu"); const open = m.classList.toggle("open"); $("#userBtn").setAttribute("aria-expanded", open); };
    document.addEventListener("click", (e) => { if (!e.target.closest(".user")) { $("#userMenu").classList.remove("open"); $("#userBtn").setAttribute("aria-expanded", "false"); } });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#userMenu").classList.remove("open"); document.body.classList.remove("side-open"); } });
    $$("#themeSeg button").forEach((b) => b.onclick = () => { this.applyTheme(b.dataset.theme); localStorage.setItem("lm.theme", b.dataset.theme); });
    this.applyTheme(localStorage.getItem("lm.theme") || "system");   // now the buttons exist, mark the active one

    await Promise.all([this.loadUser(), this.loadStatus()]);
    return this;
  },

  applyTheme(mode) {
    if (mode === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
    $$("#themeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.theme === mode));
  },

  async loadUser() {
    try { this.user = await api("/api/me"); } catch { this.user = null; }
    const u = this.user;
    if (!u) return;
    document.body.dataset.role = u.role;
    const initials = u.username.slice(0, 2).toUpperCase();
    $("#avatar").textContent = initials;
    $("#userName").textContent = u.username;
    $("#userRole").textContent = u.role;
    $("#menuName").textContent = `${u.username} · ${u.role}`;
    $("#menuRole").textContent = ROLE_NOTE[u.role] || "";
    const who = document.getElementById("dlgWho");        // configuration editor: who is saving
    if (who) who.value = u.username;
  },

  // The environment strip: which bank day the engine is on, whether the bank answers, and which LLM writes wording.
  async loadStatus() {
    let s = null;
    try { s = await api("/api/status"); } catch { /* shown as unreachable below */ }
    this.status = s;
    const chips = $("#shellStatus");
    if (!s) { chips.innerHTML = `<span class="chip"><i class="dot bad"></i>Engine unreachable</span>`; return; }
    const llmOk = String(s.llm_mode || "").startsWith("gemini");
    chips.innerHTML = `
      <span class="chip" title="The bank's business date"><i class="dot ${s.bank.ok ? "ok" : "bad"}"></i>${s.bank.ok ? `Bank · ${esc(s.bank.as_of)}` : "Bank unreachable"}</span>
      <span class="chip" title="Which model writes the explanation wording"><i class="dot ${llmOk ? "ok" : "warn"}"></i>${esc(s.llm_mode)}</span>
      <span class="chip mono" title="Configuration the next run will use">cfg ${esc(String(s.config_hash || "").slice(0, 8))}</span>`;
    $("#envDate").textContent = s.bank.ok ? s.bank.as_of : "bank offline";
    if (!$("#shellContext").textContent) $("#shellContext").textContent = `${s.institution} · ${s.scenario}`;
  },

  setContext(text) { $("#shellContext").textContent = text || ""; },

  // Count of undecided recommendations, shown on the sidebar's Today item.
  setPending(n) {
    const b = $("#navPending");
    if (!b) return;
    b.textContent = n > 999 ? "999+" : String(n);
    b.classList.toggle("hidden", !n);
  },

  can(action) {
    const role = this.user && this.user.role;
    if (role === "admin") return true;
    if (role === "manager") return action !== "config.save";
    return false;
  },
};
