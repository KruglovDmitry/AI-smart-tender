const labels = { starting: "запускается", ready: "работает", failed: "не отвечает" };

const $ = (id) => document.getElementById(id);

window.splashApi.onState((state) => {
  const services = state.services || [];
  $("services").replaceChildren(...services.map((s) => {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = `dot ${s.state || "starting"}`;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = s.name;
    const st = document.createElement("span");
    st.className = "state";
    st.textContent = labels[s.state] || s.state || "";
    li.title = s.detail || "";
    li.append(dot, name, st);
    return li;
  }));

  const failed = Boolean(state.error);
  $("spinner").classList.toggle("off", failed);
  $("leadText").textContent = failed ? (state.message || "Не удалось запустить") : "Загрузка…";
  $("error").textContent = state.error || "";
  $("error").classList.toggle("show", failed);
  $("row").classList.toggle("show", failed);
});

$("close").addEventListener("click", () => window.splashApi.close());
