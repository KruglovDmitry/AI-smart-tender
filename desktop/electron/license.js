const MODULE_NAMES = {
  search: "поиск закупок",
  documents: "документы",
  dashboard: "дашборд",
  analysis: "анализ тендеров",
  criteria: "критерии поиска",
};

const $ = (id) => document.getElementById(id);

function describe(s) {
  const modules = (s.modules || []).map((m) => MODULE_NAMES[m] || m).join(", ");
  if (s.state === "licensed") {
    const until = s.expires ? `до ${s.expires} (осталось ${s.days_left} дн.)` : "бессрочно";
    return { cls: "ok", text: `Лицензия действует ${until}.${s.customer ? `<br>Владелец: ${s.customer}` : ""}<br>Модули: ${modules}` };
  }
  if (s.state === "trial") return { cls: "ok", text: `${s.message}<br>Пробный период до ${s.expires}, модули: ${modules}` };
  return { cls: "bad", text: s.message || "Лицензии нет." };
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function render(s) {
  const view = describe({ ...s, customer: s.customer && escapeHtml(s.customer), message: s.message && escapeHtml(s.message) });
  $("state").className = `state ${view.cls}`;
  $("state").innerHTML = view.text;
  $("code").textContent = s.machine_code || "?";
}

async function load() {
  try {
    render(await window.licenseApi.get());
  } catch (err) {
    $("state").className = "state bad";
    $("state").textContent = "Сервисы ещё запускаются — откройте окно через минуту (Справка → Лицензия).";
  }
}

$("copy").addEventListener("click", () => navigator.clipboard.writeText($("code").textContent));

$("apply").addEventListener("click", async () => {
  const key = $("key").value.trim();
  if (!key) return;
  $("msg").className = "msg";
  $("msg").textContent = "Проверяю…";
  try {
    const res = await window.licenseApi.submit(key);
    if (res.ok) {
      render(res.status);
      $("msg").className = "msg ok";
      $("msg").textContent = "Ключ принят.";
      $("key").value = "";
    } else {
      $("msg").className = "msg err";
      $("msg").textContent = res.error;
    }
  } catch (err) {
    $("msg").className = "msg err";
    $("msg").textContent = String(err.message || err);
  }
});

load();
