(() => {
  const $ = (sel) => document.querySelector(sel);
  const PAGE = 50;
  const titles = {
    dashboard: ["Сводка", "Найденные закупки из базы tender-agent"],
    tenders: ["Закупки", "Всё, что агент нашёл на площадках"],
    runs: ["Прогоны", "Когда, где и что искал агент"],
    run: ["Поиск на площадке", "Запуск tender-agent: результат сразу попадает в базу"],
    status: ["Статус системы", "Сервисы и база"],
    methods: ["Агент", "Настройки tender-agent и как часто работают модель по DOM и зрение"],
  };
  const state = { offset: 0, total: 0 };
  // Поля, которые модель читает с карточки закупки (tenders.details).
  const DETAIL_LABELS = {
    procurement_method: "Способ закупки",
    delivery_place: "Место поставки",
    bid_security: "Обеспечение заявки",
    contract_security: "Обеспечение контракта",
    okpd2: "ОКПД2",
    source: "Опубликована на",
    documents_access: "Доступ к документам",
  };

  // ---------- навигация ----------

  function showPanel(name) {
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    document.querySelectorAll(".nav .it").forEach((b) => b.classList.remove("active"));
    $(`#panel-${name}`)?.classList.add("active");
    document.querySelector(`.nav .it[data-panel="${name}"]`)?.classList.add("active");
    const t = titles[name] || titles.dashboard;
    $("#page-title").textContent = t[0];
    $("#page-sub").textContent = t[1];
    if (name === "tenders") loadTenders();
    if (name === "runs") loadRuns();
    if (name === "methods") { loadAgentSettings(); loadMethods(); }
  }

  // ---------- настройки агента (вкладка «Агент») ----------
  // Владелец настроек — сам агент (/settings); дашборд их только показывает и меняет.
  // Ключи приходят маской: пустое поле ключа при сохранении не меняет ключ.

  const GROUPS = {
    dom: ["Модель по DOM", "dom"],
    vision: ["Зрение: VL-планировщик и UI-TARS", null],
    browser: ["Браузер", null],
    limits: ["Ограничения", null],
  };
  const SOURCES = { dashboard: "задано здесь", env: "из .env", default: "по умолчанию" };
  let agentOptions = [];

  function settingInput(o) {
    const id = `as-${o.key}`;
    const reset = o.source === "dashboard"
      ? `<button type="button" class="link" data-reset="${o.key}" title="Вернуть значение из .env / по умолчанию">сбросить</button>` : "";
    const source = `<span class="mu sm">${SOURCES[o.source] || o.source}</span> ${reset}`;
    if (o.kind === "bool") {
      return `<label class="check"><input type="checkbox" id="${id}" data-key="${o.key}" ${o.value ? "checked" : ""}> ${esc(o.label)}</label> ${source}`;
    }
    const type = o.kind === "secret" ? "password" : o.kind === "int" ? "number" : "text";
    const value = o.kind === "secret" ? "" : esc(String(o.value ?? ""));
    const placeholder = o.kind === "secret" ? (o.set ? `${o.value} — оставьте пустым, чтобы не менять` : "не задан") : "";
    return `<div class="field"><label for="${id}">${esc(o.label)} ${source}</label>
      <input type="${type}" id="${id}" data-key="${o.key}" value="${value}" placeholder="${esc(placeholder)}" autocomplete="off"></div>`;
  }

  function renderAgentSettings(data) {
    agentOptions = data.options || [];
    const blocks = Object.entries(GROUPS).map(([group, [title]]) => {
      const items = agentOptions.filter((o) => o.group === group);
      if (!items.length) return "";
      const checks = group === "dom"
        ? '<button type="button" class="btn secondary" data-check="dom">Проверить модель</button>'
        : group === "vision"
          ? '<button type="button" class="btn secondary" data-check="planner">Проверить VL</button> <button type="button" class="btn secondary" data-check="tars">Проверить UI-TARS</button>'
          : "";
      return `<fieldset class="settings-group"><legend>${title}</legend>${items.map(settingInput).join("")}
        ${checks ? `<div class="form-actions">${checks} <span class="mu" id="check-${group}"></span></div>` : ""}</fieldset>`;
    }).join("");
    $("#agent-settings").innerHTML = blocks + `<div class="form-actions">
        <button type="submit" class="btn primary">Сохранить</button> <span class="mu" id="agent-settings-saved"></span></div>`;
    $("#agent-settings-hint").textContent = (data.vision_available ? "" :
      "Внимание: библиотека зрения (ai_vision) не установлена в агенте — зрение работать не будет. ") +
      `Файл настроек: ${data.path}`;
  }

  async function loadAgentSettings() {
    try {
      renderAgentSettings(await getJson("/api/agent/settings"));
    } catch (e) {
      $("#agent-settings-hint").textContent = `Агент недоступен: ${e.message}`;
      $("#agent-settings").innerHTML = "";
    }
  }

  async function saveAgentSettings(values) {
    const r = await fetch("/api/agent/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ values }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    renderAgentSettings(data);
  }

  $("#agent-settings").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const values = {};
    for (const o of agentOptions) {
      const input = document.getElementById(`as-${o.key}`);
      if (!input) continue;
      if (o.kind === "bool") {
        if (input.checked !== Boolean(o.value)) values[o.key] = input.checked;
      } else if (o.kind === "secret") {
        if (input.value.trim()) values[o.key] = input.value.trim();
      } else if (input.value.trim() !== String(o.value ?? "")) {
        values[o.key] = o.kind === "int" ? Number(input.value) : input.value.trim();
      }
    }
    try {
      if (Object.keys(values).length) await saveAgentSettings(values);
      $("#agent-settings-saved").textContent = Object.keys(values).length ? "Сохранено" : "Изменений нет";
    } catch (e) {
      $("#agent-settings-saved").textContent = `Ошибка: ${e.message}`;
    }
  });

  $("#agent-settings").addEventListener("click", async (ev) => {
    const reset = ev.target.closest("[data-reset]");
    if (reset) {
      try { await saveAgentSettings({ [reset.dataset.reset]: null }); } catch (e) { alert(e.message); }
      return;
    }
    const check = ev.target.closest("[data-check]");
    if (!check) return;
    const out = $(check.dataset.check === "dom" ? "#check-dom" : "#check-vision");
    out.textContent = "Проверяю…";
    check.disabled = true;
    try {
      const r = await fetch("/api/agent/settings/check", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: check.dataset.check }),
      });
      const data = await r.json();
      out.textContent = `${check.textContent.replace("Проверить ", "")}: ${data.ok ? "✓ " : "✗ "}${data.detail || ""}`;
    } catch (e) {
      out.textContent = `Ошибка: ${e.message}`;
    } finally {
      check.disabled = false;
    }
  });

  document.querySelectorAll(".nav .it").forEach((btn) =>
    btn.addEventListener("click", () => showPanel(btn.dataset.panel))
  );
  document.addEventListener("click", (ev) => {
    const el = ev.target.closest("[data-goto]");
    if (!el) return;
    ev.preventDefault();
    const target = el.dataset.goto;
    if (target === "tenders-new") {
      resetFilters();
      $("#f-new").value = "7";
      $("#f-sort").value = "first_seen";
      showPanel("tenders");
    } else if (target.startsWith("platform:")) {
      resetFilters();
      $("#f-platform").value = target.slice(9);
      showPanel("tenders");
    } else {
      showPanel(target);
    }
  });
  $("#btn-refresh")?.addEventListener("click", () => refreshAll());

  // ---------- форматирование ----------

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }
  function fmtDay(ymd) {
    if (!ymd) return "—";
    const [y, m, d] = String(ymd).slice(0, 10).split("-");
    return d && m && y ? `${d}.${m}.${y}` : ymd;
  }
  function fmtPrice(v, cur) {
    if (v == null) return "—";
    const n = Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
    return `${n} ${cur === "RUB" || !cur ? "₽" : esc(cur)}`;
  }
  function isRecent(iso, days) {
    return iso && Date.now() - new Date(iso).getTime() < days * 86400000;
  }
  function statusPill(status) {
    const cls = { completed: "ok", partial: "warn", needs_user: "warn", failed: "bad" }[status] || "warn";
    const text = { completed: "готово", partial: "частично", needs_user: "нужен человек", failed: "ошибка" }[status] || status || "—";
    return `<span class="pill ${cls}">${esc(text)}</span>`;
  }

  async function getJson(url) {
    const r = await fetch(url);
    let data;
    try { data = await r.json(); } catch { data = { detail: await r.text() }; }
    if (!r.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${r.status}`);
    return data;
  }

  function dbBanner(error) {
    const el = $("#db-banner");
    if (!error) { el.classList.remove("show"); el.textContent = ""; return; }
    el.classList.add("show");
    el.textContent = `База закупок недоступна: ${error}. Проверьте контейнер tender-postgres и TENDER_DB_URL.`;
  }

  // ---------- сводка ----------

  async function loadSummary() {
    try {
      const s = await getJson("/api/db/summary");
      dbBanner(null);
      $("#kpi-total").textContent = s.tenders_total;
      $("#kpi-today").textContent = s.new_today;
      $("#kpi-week").textContent = s.new_week;
      $("#kpi-platforms").textContent = `площадок: ${s.platforms.length}`;
      $("#kpi-runs").textContent = `прогонов в базе: ${s.platforms.reduce((a, p) => a + (p.runs || 0), 0)}`;
      fillPlatformFilter(s.platforms);
      $("#platforms-list").innerHTML = s.platforms.length
        ? s.platforms.map((p) => `<div class="tr t-platforms clickable" data-goto="platform:${esc(p.platform)}">
            <div class="nm">${esc(p.platform)}</div>
            <div>${p.tenders}</div>
            <div>${p.new_week ? `<span class="pill ok">+${p.new_week}</span>` : "0"}</div>
            <div class="dt">${esc(fmtDateTime(p.last_run))}</div>
            <div>${p.last_status ? statusPill(p.last_status) : "—"}</div>
          </div>`).join("")
        : '<div class="empty">Пока ни одного прогона. Запустите поиск на вкладке «Поиск на площадке».</div>';
      $("#recent-runs").innerHTML = s.recent_runs.length
        ? s.recent_runs.map((r) => `<div class="actv">
            <div class="av">🔎</div>
            <div>
              <div class="t">${esc(r.platform)} · «${esc(r.query || "")}»</div>
              <div class="d">${statusPill(r.status)} найдено ${r.result_count ?? 0}, новых ${r.new_count ?? 0}</div>
              <div class="tm">${esc(fmtDateTime(r.created_at))}</div>
            </div></div>`).join("")
        : '<div class="empty">Нет прогонов</div>';
    } catch (e) {
      dbBanner(e.message);
    }
  }

  function fillPlatformFilter(platforms) {
    for (const id of ["#f-platform", "#m-platform"]) {
      const sel = $(id);
      const current = sel.value;
      sel.innerHTML = '<option value="">все</option>' +
        platforms.map((p) => `<option value="${esc(p.platform)}">${esc(p.platform)} (${p.tenders})</option>`).join("");
      sel.value = current;
    }
  }

  // ---------- закупки ----------

  function filterParams() {
    const p = new URLSearchParams();
    const add = (k, v) => { if (v !== "" && v != null) p.set(k, v); };
    add("q", $("#f-q").value.trim());
    add("platform", $("#f-platform").value);
    add("new_days", $("#f-new").value);
    add("published_from", $("#f-from").value);
    add("published_to", $("#f-to").value);
    add("min_price", $("#f-min").value);
    add("max_price", $("#f-max").value);
    if ($("#f-verified").checked) p.set("verified_only", "true");
    if ($("#f-deleted").checked) p.set("deleted", "true");
    p.set("sort", $("#f-sort").value);
    p.set("limit", PAGE);
    p.set("offset", state.offset);
    return p;
  }

  function resetFilters() {
    $("#filters").reset();
    state.offset = 0;
  }

  async function loadTenders() {
    const list = $("#tenders-list");
    list.innerHTML = '<div class="empty"><span class="spinner"></span>Загрузка…</div>';
    try {
      const data = await getJson(`/api/db/tenders?${filterParams()}`);
      dbBanner(null);
      state.total = data.total;
      $("#tenders-count").textContent = `${data.total}`;
      const from = data.total ? state.offset + 1 : 0;
      $("#pg-info").textContent = `${from}–${state.offset + data.tenders.length} из ${data.total}`;
      $("#pg-prev").disabled = state.offset === 0;
      $("#pg-next").disabled = state.offset + PAGE >= data.total;
      list.innerHTML = data.tenders.length
        ? data.tenders.map((t) => `<div class="tr t-tenders clickable" data-tender="${t.id}">
            <div class="dt">${esc(fmtDay(t.published_at))}</div>
            <div class="mu">${esc(t.platform)}</div>
            <div class="mu">${esc(t.external_id || "—")}</div>
            <div class="nm">${isRecent(t.first_seen, 1) ? '<span class="pill ok">новая</span> ' : ""}${esc(t.title || "—")}
              ${t.verification_status === "verified" ? "" : '<span class="pill warn" title="ключевое слово не найдено в названии или нет даты">проверить</span>'}
              ${t.documents_count ? `<span class="pill" title="документы">📎 ${t.documents_count}</span>` : ""}</div>
            <div class="mu">${esc(t.customer || "—")}</div>
            <div>${fmtPrice(t.price, t.currency)}</div>
            <div class="dt">${esc(fmtDay(t.deadline))}</div>
          </div>`).join("")
        : '<div class="empty">Ничего не найдено по этим фильтрам</div>';
    } catch (e) {
      dbBanner(e.message);
      list.innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`;
    }
  }

  $("#filters").addEventListener("submit", (ev) => { ev.preventDefault(); state.offset = 0; loadTenders(); });
  $("#f-reset").addEventListener("click", () => { resetFilters(); loadTenders(); });
  $("#pg-prev").addEventListener("click", () => { state.offset = Math.max(0, state.offset - PAGE); loadTenders(); });
  $("#pg-next").addEventListener("click", () => { state.offset += PAGE; loadTenders(); });

  // ---------- карточка закупки ----------

  document.addEventListener("click", async (ev) => {
    const row = ev.target.closest("[data-tender]");
    if (!row) return;
    openDrawer('<div class="empty"><span class="spinner"></span>Загрузка…</div>');
    try {
      const t = await getJson(`/api/db/tenders/${row.dataset.tender}`);
      const field = (k, v) => `<div class="status-row"><span class="k">${k}</span><span class="v">${v}</span></div>`;
      const sources = t.sources || {};
      const docs = (t.documents || []).map((d) => `<div class="doc">
          <a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.name || d.url)}</a>
          <div class="mu">${d.local_path ? `скачан: ${esc(d.local_path)}` : "не скачан — только ссылка на источник"}</div>
        </div>`).join("") || '<div class="empty">Документов не найдено</div>';
      const lotList = (t.details || {}).lots || [];
      const lots = lotList.length ? `<h3>Лоты процедуры (${lotList.length})</h3>` + lotList.map((l) => `<div class="doc">
          ${l.number ? `<span class="mu">${esc(l.number)}</span> ` : ""}${l.url ? `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a>` : esc(l.title || "—")}
          ${l.price ? `<div class="mu">${typeof l.price === "number" ? fmtPrice(l.price) : esc(l.price)}</div>` : ""}
        </div>`).join("") : "";
      const runs = (t.runs || []).map((r) => `<div class="mu">${esc(fmtDateTime(r.created_at))} · «${esc(r.query || "")}» · место ${r.position}${r.is_new ? " · впервые" : ""}</div>`).join("");
      openDrawer(`
        <h2>${esc(t.title || "Без названия")}</h2>
        <p class="hint"><a href="${esc(t.url)}" target="_blank" rel="noopener">Открыть на площадке ↗</a></p>
        ${t.deleted_at ? `<p class="hint">Удалена ${esc(fmtDateTime(t.deleted_at))}</p>` : ""}
        <div class="status-list">
          ${field("Площадка", esc(t.platform))}
          ${field("Номер", esc(t.external_id || "—"))}
          ${field("Заказчик", esc(t.customer || "—"))}
          ${field("Начальная цена", fmtPrice(t.price, t.currency))}
          ${field("Опубликована", esc(fmtDay(t.published_at)))}
          ${field("Подача заявок до", esc(fmtDay(t.deadline)))}
          ${field("Статус", esc(t.status || "—"))}
          ${Object.entries(DETAIL_LABELS).filter(([k]) => (t.details || {})[k]).map(([k, label]) => field(label, esc(t.details[k]))).join("")}
          ${field("Проверка", t.verification_status === "verified" ? '<span class="pill ok">подтверждена</span>' : '<span class="pill warn">проверить</span>')}
          ${field("Впервые найдена", esc(fmtDateTime(t.first_seen)))}
          ${field("Последний раз", `${esc(fmtDateTime(t.last_seen))} · встречалась ${t.seen_count} раз`)}
        </div>
        ${lots}
        <h3>Документы</h3>${docs}
        <h3>Прогоны</h3>${runs || '<div class="empty">—</div>'}
        <h3>Откуда поля</h3><div class="mu">${esc(Object.entries(sources).map(([k, v]) => `${k}: ${v}`).join(", ") || "—")}</div>
        <div class="drawer-actions">
          ${t.deleted_at
            ? `<button type="button" class="btn secondary" data-restore="${t.id}">Восстановить</button>`
            : `<button type="button" class="icon-btn danger" data-delete="${t.id}" title="Удалить закупку" aria-label="Удалить закупку">${TRASH_ICON}</button>`}
        </div>
      `);
    } catch (e) {
      openDrawer(`<div class="empty">Ошибка: ${esc(e.message)}</div>`);
    }
  });

  const TRASH_ICON = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/>
    <path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>`;

  // Мягкое удаление: закупка скрывается из списков, в базе остаётся (флажок «удалённые» — вернуть).
  document.addEventListener("click", async (ev) => {
    const button = ev.target.closest("[data-delete], [data-restore]");
    if (!button) return;
    const id = button.dataset.delete || button.dataset.restore;
    const removing = Boolean(button.dataset.delete);
    if (removing && !confirm("Удалить закупку из списка? Её можно будет вернуть через фильтр «удалённые».")) return;
    button.disabled = true;
    try {
      const r = await fetch(removing ? `/api/db/tenders/${id}` : `/api/db/tenders/${id}/restore`,
                            { method: removing ? "DELETE" : "POST" });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
      $("#drawer").hidden = true;
      loadTenders();
    } catch (e) {
      button.disabled = false;
      alert(`Не получилось: ${e.message}`);
    }
  });

  function openDrawer(html) {
    $("#drawer-content").innerHTML = html;
    $("#drawer").hidden = false;
  }
  $("#drawer-close").addEventListener("click", () => { $("#drawer").hidden = true; });
  $("#drawer").addEventListener("click", (ev) => { if (ev.target.id === "drawer") $("#drawer").hidden = true; });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") $("#drawer").hidden = true; });

  // ---------- прогоны ----------

  async function loadRuns() {
    const el = $("#runs-list");
    el.innerHTML = '<div class="empty"><span class="spinner"></span>Загрузка…</div>';
    try {
      const data = await getJson("/api/db/runs?limit=200");
      $("#runs-count").textContent = data.runs.length;
      el.innerHTML = data.runs.length
        ? data.runs.map((r) => {
            const errors = (r.errors || []).slice(0, 3).map((e) => `<div>• ${esc(String(e).slice(0, 160))}</div>`).join("");
            const stats = r.stats || {};
            const extra = [stats.vision_calls ? `зрение: ${stats.vision_calls}` : "", stats.list_reads ? `модель по выдаче: ${stats.list_reads}` : ""]
              .filter(Boolean).join(", ");
            return `<div class="tr t-runs">
              <div class="dt">${esc(fmtDateTime(r.created_at))}</div>
              <div class="nm clickable" data-goto="platform:${esc(r.platform)}">${esc(r.platform)}</div>
              <div>«${esc(r.query || "")}»</div>
              <div>${statusPill(r.status)}</div>
              <div>${r.result_count ?? 0} / ${r.new_count ? `<span class="pill ok">+${r.new_count}</span>` : "0"}${extra ? `<div class="mu">${esc(extra)}</div>` : ""}</div>
              <div class="mu">${errors}${r.trace_dir ? `<div>трейс: ${esc(r.trace_dir)}</div>` : ""}</div>
            </div>`;
          }).join("")
        : '<div class="empty">Пока ни одного прогона</div>';
    } catch (e) {
      dbBanner(e.message);
      el.innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`;
    }
  }

  // ---------- эффективность ----------

  const STAGES = {
    search: "Поиск по запросу", sort: "Сортировка по дате", navigate: "Навигация по незнакомой странице",
    list: "Чтение выдачи", card_fields: "Поля карточки", documents_tab: "Раздел «Документы»",
    documents_files: "Выбор файлов",
  };
  const METHODS = {
    rule: "эвристика", llm: "модель по DOM", llm_action: "действие модели",
    known_url: "адрес площадки", vision: "зрение (VL)",
  };
  const FIELDS = {
    title: "Предмет", customer: "Заказчик", published_at: "Дата публикации", deadline: "Срок подачи",
    price: "Цена", status: "Статус", id: "Номер", procurement_method: "Способ закупки",
    delivery_place: "Место поставки", bid_security: "Обеспечение заявки", contract_security: "Обеспечение контракта",
    okpd2: "ОКПД2",
  };

  async function loadMethods() {
    const p = new URLSearchParams({ days: $("#m-days").value });
    if ($("#m-platform").value) p.set("platform", $("#m-platform").value);
    try {
      const data = await getJson(`/api/db/methods?${p}`);
      $("#methods-hint").textContent = `Прогонов за период: ${data.runs}. Успех: поиск — выдача отфильтрована запросом; сортировка — включена; ` +
        "выдача — найдены записи; поля — заполнено хотя бы одно; «Документы» — после перехода нашлись файлы.";
      $("#methods-list").innerHTML = data.stages.length
        ? data.stages.map((st) => {
            const total = st.methods.reduce((a, m) => a + m.used, 0) || 1;
            return `<div class="stage"><h4>${esc(STAGES[st.stage] || st.stage)}</h4>` +
              st.methods.map((m) => {
                const rate = m.used ? Math.round((100 * m.ok) / m.used) : 0;
                return `<div class="mrow"><span>${esc(METHODS[m.method] || m.method)}</span>
                  <div class="bar" title="успешно ${m.ok} из ${m.used}"><span style="width:${rate}%"></span></div>
                  <span>${m.ok}/${m.used} · ${rate}%<span class="mu"> (${Math.round((100 * m.used) / total)}% попыток)</span></span></div>`;
              }).join("") + "</div>";
          }).join("")
        : '<div class="empty">Нет прогонов со счётчиками за этот период (они пишутся начиная с этой версии агента)</div>';
      const rows = Object.entries(data.field_sources);
      $("#sources-list").innerHTML = rows.length
        ? rows.map(([field, groups]) => {
            const total = Object.values(groups).reduce((a, b) => a + b, 0) || 1;
            const llm = groups.llm || 0;
            return `<div class="mrow"><span>${esc(FIELDS[field] || field)}</span>
              <div class="bar share" title="модель ${llm} из ${total}"><span style="width:${Math.round((100 * llm) / total)}%"></span></div>
              <span class="mu">эвристика ${groups.rule || 0} · модель ${llm}${groups.other ? ` · др. ${groups.other}` : ""}</span></div>`;
          }).join("")
        : '<div class="empty">Нет закупок за период</div>';
    } catch (e) {
      dbBanner(e.message);
      $("#methods-list").innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`;
    }
  }
  $("#m-days").addEventListener("change", loadMethods);
  $("#m-platform").addEventListener("change", loadMethods);

  // ---------- запуск поиска ----------

  $("#platform_url").addEventListener("change", () => {
    $("#custom-url-field").hidden = $("#platform_url").value !== "custom";
  });

  $("#form-platform").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const box = $("#platform-status");
    const btn = $("#btn-platform");
    const url = $("#platform_url").value === "custom" ? $("#custom_url").value.trim() : $("#platform_url").value;
    if (!url) { showResult(box, false, "Укажите адрес площадки"); return; }
    btn.disabled = true;
    box.className = "status-box show";
    box.innerHTML = '<span class="spinner"></span>Агент ищет на площадке — это может занять несколько минут…';
    try {
      const r = await fetch("/api/run/platform", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform_url: url,
          keywords: $("#keywords").value.trim(),
          max_new_tenders: Number($("#max_new_tenders").value) || 5,
          download_documents: $("#download_documents").checked,
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
      const stats = data.stats || {};
      const rows = (data.results || []).map((t) => `<div class="tr t-result">
          <div>${t.is_new ? '<span class="pill ok">новая</span>' : '<span class="pill">была</span>'}</div>
          <div class="nm">${esc(t.title || "—")}</div>
          <div>${fmtPrice(t.price, t.currency)}</div>
          <div class="dt">${esc(fmtDay(t.published_at))}</div>
        </div>`).join("");
      box.className = `status-box show ${data.status === "failed" ? "err" : "ok"}`;
      box.innerHTML = `<div>${statusPill(data.status)} найдено ${data.results?.length || 0}, новых ${stats.new_tenders ?? "—"}</div>
        ${(data.errors || []).length ? `<div class="mu">${data.errors.slice(0, 4).map((e) => "• " + esc(String(e).slice(0, 200))).join("<br>")}</div>` : ""}
        <div style="margin-top:10px">${rows}</div>
        <div class="form-actions"><button type="button" class="btn secondary" data-goto="platform:${esc(hostOf(url))}">Открыть в «Закупках»</button></div>`;
      refreshAll();
    } catch (e) {
      showResult(box, false, e.message || String(e));
    } finally {
      btn.disabled = false;
    }
  });

  function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
  }
  function showResult(box, ok, text) {
    box.className = `status-box show ${ok ? "ok" : "err"}`;
    box.textContent = text;
  }

  // ---------- статус ----------

  async function loadStatus() {
    try {
      const st = await getJson("/api/status");
      const agent = st.tender_agent || {};
      const health = agent.health || {};
      const llm = !!st.agent_llm_configured;
      $("#kpi-agent").textContent = agent.ok ? (llm ? "OK" : "без LLM") : "недоступен";
      const hint = $("#kpi-agent-hint");
      hint.textContent = agent.ok ? `модель: ${health.model || "—"}` : (agent.error || "").slice(0, 60);
      hint.className = "dl " + (agent.ok && llm ? "ok" : "bad");
      const pillOk = (ok, yes, no) => `<span class="pill ${ok ? "ok" : "bad"}">${ok ? yes : no}</span>`;
      const db = st.database || {};
      const rows = [
        ["tender-agent", agent.ok ? pillOk(true, "доступен") : `${pillOk(false, "", "ошибка")} ${esc(agent.error || "")}`],
        ["Модель агента", esc(health.model || "—")],
        ["LLM настроена", pillOk(llm, "да", "нет")],
        ["База закупок", db.ok ? pillOk(true, "доступна") : `${pillOk(false, "", "ошибка")} ${esc(db.error || "")}`],
        ["Закупок в базе", String(db.tenders_total ?? "—")],
        ["tools-server", (st.tools_server || {}).ok ? pillOk(true, "доступен") : pillOk(false, "", "ошибка")],
        ["DATA_ROOT", esc(st.data_root || "—")],
      ];
      $("#status-list").innerHTML = rows
        .map(([k, v]) => `<div class="status-row"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`).join("");
    } catch (e) {
      $("#status-list").innerHTML = `<div class="empty">Ошибка: ${esc(e.message)}</div>`;
    }
  }

  async function refreshAll() {
    await Promise.all([loadSummary(), loadStatus()]);
    if ($("#panel-tenders").classList.contains("active")) loadTenders();
    if ($("#panel-runs").classList.contains("active")) loadRuns();
    if ($("#panel-methods").classList.contains("active")) loadMethods();
  }

  refreshAll();
  setInterval(() => { loadSummary(); loadStatus(); }, 60000);
})();
