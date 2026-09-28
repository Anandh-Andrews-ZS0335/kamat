function escUser(s) { return esc(String(s ?? "")); }
const ROLE_LABEL = { manager: "Collections manager", collector: "Collection agent" };

// The server decides which roles this person may create; the form only reflects that decision.
function setRoles(roles) {
  const select = $("#role");
  select.innerHTML = roles.map((r) => `<option value="${r}">${escUser(ROLE_LABEL[r] || r)}</option>`).join("");
  const only = roles.length === 1 && roles[0] === "collector";
  $("#roleField").hidden = only;
  $("#scopeNote").textContent = only
    ? "You can add collection agents for your own floor. Manager accounts are created by an administrator."
    : "";
  syncCollectorField();
}
async function loadUsers() {
  try {
    const data = await api("/api/users");
    setRoles(data.roles);
    const onlyAgents = data.roles.length === 1 && data.roles[0] === "collector";
    $("#listTitle").textContent = onlyAgents ? "Your collection agents" : "Active console users";
    $("#userCount").textContent = onlyAgents
      ? `${data.users.length} collection agent${data.users.length === 1 ? "" : "s"}`
      : `${data.users.length} managed account${data.users.length === 1 ? "" : "s"}`;
    $("#users").innerHTML = data.users.length ? tableHTML(data.users.map((u) => ({
      name: `${escUser(u.display_name)}<br><span class="mono">${escUser(u.username)}</span>`, role: escUser(u.role),
      collector: u.collector_id ? `<span class="mono">${escUser(u.collector_id)}</span>` : "—",
      created: `${escUser(u.created_by)}<br><span class="muted">${fmt.time(u.created_at)}</span>`
    }))) : `<div class="empty">No accounts yet. Add a collection agent above; environment accounts remain available for initial administration.</div>`;
  } catch (e) { $("#users").innerHTML = `<div class="callout bad">${escUser(e.message)}</div>`; }
}
function syncCollectorField() { const show = $("#role").value === "collector"; $("#collectorField").hidden = !show; $("[name=collector_id]").required = show; }
$("#role").onchange = syncCollectorField;
$("#userForm").onsubmit = async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); const msg = $("#formMsg"); msg.textContent = "Creating user…";
  const body = Object.fromEntries(form.entries()); if (body.role !== "collector") body.collector_id = null;
  try { await api("/api/users", {method:"POST", body:JSON.stringify(body)}); event.currentTarget.reset(); syncCollectorField(); msg.textContent = "User created. Share the password through an approved secure channel."; await loadUsers(); }
  catch (e) { msg.textContent = e.message; }
};
Shell.mount({page:"users",title:"Users & access"}).then(loadUsers);
