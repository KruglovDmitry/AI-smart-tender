(() => {
  const $ = (sel) => document.querySelector(sel);
  const titles = {
    dashboard: ["Dashboard", "Реальные данные с диска и tools-server"],
    run: ["Запуск мониторинга", "POST /run_platform_task и /run_tender_download"],
    tenders: ["Скачанные тендеры", "Скан /data/tenders"],
    status: ["Статус системы", "GET /health tools-server + KPI с диска"],
  };

  function showPanel(name) {
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    document.querySelectorAll(".nav .it").forEach((b) => b.classList.remove("active"));
    const panel = $(`#panel-${name}`);
    if (panel) panel.classList.add("active");
    const nav = document.querySelector(`.nav .it[data-panel="${name}"]`);
    if (nav) nav.classList.add("active");
    const t = titles[name] || titles.dashboard;
    $("#page-title").textContent = t[0];
    $("#page-sub").textContent = t[1];
  }

  document.querySelectorAll(".nav .it").forEach((btn) => {
    btn.addEventListener("click", () => showPanel(btn.dataset.panel));
  });
  document.querySelectorAll("[data-goto]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.goto;
      if (target === "run-card") {
        showPanel("run");
        $("#run-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
      } else {
        showPanel(target);
      }
    });
  });
  $("#btn-refresh")?.addEventListener("click", () => refreshAll());

  function fmtDate(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
    } catch {
      return iso;
    }
  }

  function renderTenders(list, el, limit) {
    if (!el) return;
    const rows = limit ? list.slice(0, limit) : list;
    if (!rows.length) {
      el.innerHTML = '<div class="empty">Пока нет скачанных тендеров в /data/tenders</div>';
      return;
    }
    el.innerHTML = rows
      .map(
        (t) => `<div class="tr">
        <div class="nm">${esc(t.platform || "—")}</div>
        <div>${esc(t.tender_id || "—")}</div>
        <div>${t.files ?? 0}</div>
        <div class="mu">${esc(t.path || "")}</div>
        <div class="dt">${esc(fmtDate(t.modified_at))}</div>
      </div>`
      )
      .join("");
  }

  function renderActivity(items) {
    const el = $("#activity-list");
    if (!el) return;
    if (!items?.length) {
      el.innerHTML = '<div class="empty">Нет логов в data/_logs/agent</div>';
      return;
    }
    el.innerHTML = items
      .slice(0, 12)
      .map(
        (a) => `<div class="actv">
        <div class="av">📋</div>
        <div>
          <div class="t">${esc(a.file || "log")}</div>
          <div class="d">${esc(a.line || "")}</div>
          <div class="tm">${esc(fmtDate(a.modified_at))}</div>
        </div>
      </div>`
      )
      .join("");
  }

  function pill(ok, textOk, textBad) {
    return ok
      ? `<span class="pill ok">${textOk}</span>`
      : `<span class="pill bad">${textBad}</span>`;
  }

  function renderStatus(st) {
    const el = $("#status-list");
    if (!el || !st) return;
    const th = st.tools_server || {};
    const health = th.health || {};
    const rows = [
      ["Dashboard", '<span class="pill ok">ok</span>'],
      ["DATA_ROOT", esc(st.data_root || "—")],
      [
        "tools-server",
        th.ok
          ? pill(true, "доступен", "")
          : `<span class="pill bad">ошибка</span> ${esc(th.error || "")}`,
      ],
      ["tools URL", esc(th.url || "—")],
      [
        "agent_llm_configured",
        st.agent_llm_configured
          ? pill(true, "да", "")
          : pill(false, "", "нет"),
      ],
      ["tools status", esc(health.status || "—")],
      ["primary_model", esc(health.primary_model || "—")],
      ["Тендеров на диске", String(st.tenders_count ?? "—")],
      ["Файлов", String(st.files_count ?? "—")],
      [
        "seen_tenders",
        st.seen_tenders_count == null ? "н/д" : String(st.seen_tenders_count),
      ],
    ];
    el.innerHTML = rows
      .map(
        ([k, v]) =>
          `<div class="status-row"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`
      )
      .join("");
  }

  function applyStatus(st) {
    $("#kpi-tenders").textContent = st.tenders_count ?? "—";
    $("#kpi-files").textContent = st.files_count ?? "—";
    const llm = !!st.agent_llm_configured;
    $("#kpi-llm").textContent = llm ? "OK" : "нет";
    const hint = $("#kpi-llm-hint");
    hint.textContent = llm ? "настроен" : "проверьте AGENT_LLM_*";
    hint.className = "dl " + (llm ? "ok" : "bad");
    $("#kpi-seen").textContent =
      st.seen_tenders_count == null ? "н/д" : st.seen_tenders_count;
    renderActivity(st.activity || []);
    renderStatus(st);
  }

  async function refreshAll() {
    try {
      const [stRes, tRes] = await Promise.all([
        fetch("/api/status"),
        fetch("/api/tenders?limit=200"),
      ]);
      const st = await stRes.json();
      const td = await tRes.json();
      applyStatus(st);
      const list = td.tenders || [];
      renderTenders(list, $("#tenders-preview"), 8);
      renderTenders(list, $("#tenders-full"));
      $("#tenders-full-count").textContent = String(td.count ?? list.length);
      $("#tenders-updated").textContent = "обновлено";
    } catch (e) {
      console.error(e);
      $("#tenders-preview").innerHTML =
        '<div class="empty">Ошибка загрузки статуса</div>';
    }
  }

  function setBusy(box, btn, busy, msg) {
    if (btn) btn.disabled = busy;
    if (!box) return;
    box.classList.add("show");
    box.classList.remove("ok", "err");
    if (busy) {
      box.innerHTML = `<span class="spinner"></span>${esc(msg || "Выполняется…")}`;
    }
  }

  function showResult(box, ok, text) {
    box.classList.add("show");
    box.classList.toggle("ok", ok);
    box.classList.toggle("err", !ok);
    box.textContent = text;
  }

  async function postJson(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data;
    try {
      data = await r.json();
    } catch {
      data = { detail: await r.text() };
    }
    if (!r.ok) {
      const detail = data?.detail ?? data;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail, null, 2));
    }
    return data;
  }

  $("#form-platform")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const box = $("#platform-status");
    const btn = $("#btn-platform");
    const subdir = $("#download_subdir").value.trim();
    const body = {
      platform_url: $("#platform_url").value,
      keywords: $("#keywords").value.trim(),
      max_new_tenders: Number($("#max_new_tenders").value) || 2,
    };
    if (subdir) body.download_subdir = subdir;
    setBusy(box, btn, true, "Мониторинг запущен — может занять несколько минут…");
    try {
      const data = await postJson("/api/run/platform", body);
      showResult(box, true, JSON.stringify(data, null, 2));
      await refreshAll();
    } catch (e) {
      showResult(box, false, e.message || String(e));
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $("#form-tender")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const box = $("#tender-status");
    const btn = $("#btn-tender");
    const subdir = $("#tender_subdir").value.trim();
    const body = {
      tender_url: $("#tender_url").value.trim(),
      max_files: Number($("#max_files").value) || 5,
    };
    if (subdir) body.download_subdir = subdir;
    setBusy(box, btn, true, "Скачивание карточки — подождите…");
    try {
      const data = await postJson("/api/run/tender", body);
      showResult(box, true, JSON.stringify(data, null, 2));
      await refreshAll();
    } catch (e) {
      showResult(box, false, e.message || String(e));
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  refreshAll();
  setInterval(refreshAll, 60000);
})();
