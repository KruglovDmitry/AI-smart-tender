// Верхняя панель: две иконки (чат / дашборд) и статус сервисов.
const titles = { chat: "Чат", dashboard: "Дашборд" };
const labels = { starting: "запускается", ready: "работает", failed: "не отвечает" };

for (const view of ["chat", "dashboard"]) {
  document.getElementById(`tab-${view}`).addEventListener("click", () => window.shellApi.show(view));
}

window.shellApi.onState((state) => {
  const dashBtn = document.getElementById("tab-dashboard");
  dashBtn.hidden = state.hasDashboard === false;
  for (const view of ["chat", "dashboard"]) {
    const button = document.getElementById(`tab-${view}`);
    if (!button || button.hidden) continue;
    button.classList.toggle("active", state.view === view);
    button.setAttribute("aria-selected", String(state.view === view));
  }
  document.getElementById("title").textContent = titles[state.view] || "";
  const status = document.getElementById("status");
  status.replaceChildren(...(state.services || []).map((s) => {
    const item = document.createElement("span");
    const dot = document.createElement("span");
    dot.className = `dot ${s.state}`;
    item.append(dot, `${s.name}: ${labels[s.state] || s.state}`);
    item.title = s.detail || "";
    return item;
  }));
});
