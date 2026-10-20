const { app, BaseWindow, WebContentsView, dialog, ipcMain, Menu, shell } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

const APP_ROOT =
  process.env.OWUI_APP_ROOT ||
  path.join(process.env.LOCALAPPDATA || app.getPath("userData"), "AISmartTender");
const DATA_ROOT = process.env.OWUI_DATA_ROOT || path.join(APP_ROOT, "data");
const WEBUI_DATA_DIR =
  process.env.OWUI_DATA_DIR || path.join(APP_ROOT, "webui-desktop");
const LOG_DIR = path.join(WEBUI_DATA_DIR, "logs");
const SHELL_LOG = path.join(LOG_DIR, "electron.log");

const TOOLBAR_HEIGHT = 40;
// Упавший сервис перезапускается, но не чаще RESTART_LIMIT раз за RESTART_WINDOW_MS.
const RESTART_LIMIT = 3;
const RESTART_WINDOW_MS = 5 * 60 * 1000;
// Ключи моделей нужны только агенту: остальным сервисам (в том числе Open WebUI) их не передаём.
const AGENT_ONLY_ENV = /^(AGENT_LLM_|TENDER_AGENT_|AV_)|^QWEN_API_KEY$/;

/** @type {Map<string, import('child_process').ChildProcess>} */
const procs = new Map();
/** Состояние сервисов для верхней панели: id → {name, state, detail, restarts: number[]}. */
const serviceState = new Map();
let servicesManifest = null;
let baseCtx = null;
let quitting = false;

let mainWindow = null;
let toolbarView = null;
/** @type {Record<string, WebContentsView>} */
const views = {};
let currentView = "chat";

function log(msg) {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(SHELL_LOG, `[${new Date().toISOString()}] ${msg}\n`);
  } catch (_) {
    /* ignore */
  }
  console.log(msg);
}

/** Простой .env: KEY=VALUE, без export; # комментарии. Не перезаписывает уже заданные process.env. */
function parseEnvFile(filePath) {
  const out = {};
  const text = fs.readFileSync(filePath, "utf8");
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const i = line.indexOf("=");
    const key = line.slice(0, i).trim();
    let value = line.slice(i + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (key) out[key] = value;
  }
  return out;
}

/**
 * Запасной источник ключей агента (DOM LLM, VL, UI-TARS) — .env. Основной — вкладка «Агент»
 * в дашборде (data/tenders/_agent/settings.json), она сильнее .env.
 * Порядок: уже заданный process.env > LocalAppData > рядом с exe > корень репо > tender-agent/.env
 */
function loadDotEnvFiles() {
  const candidates = [
    path.join(APP_ROOT, ".env"),
    path.join(path.dirname(process.execPath), ".env"),
    path.resolve(__dirname, "..", "..", ".env"),
    path.resolve(__dirname, "..", "..", "tender-agent", ".env"),
  ];
  const loaded = [];
  for (const filePath of candidates) {
    if (!fs.existsSync(filePath)) continue;
    try {
      const parsed = parseEnvFile(filePath);
      let n = 0;
      for (const [key, value] of Object.entries(parsed)) {
        if (process.env[key] === undefined || process.env[key] === "") {
          process.env[key] = value;
          n += 1;
        }
      }
      loaded.push(`${filePath} (+${n})`);
    } catch (e) {
      log(`env skip ${filePath}: ${e}`);
    }
  }
  if (loaded.length) {
    log(`env loaded: ${loaded.join("; ")}`);
  } else {
    log(`env: .env не найден — ключи задаются во вкладке «Агент» дашборда`);
  }
}

function loadServices() {
  const candidates = [];
  if (app.isPackaged) {
    candidates.push(path.join(process.resourcesPath, "services.json"));
  }
  candidates.push(path.join(__dirname, "..", "services.json"));
  candidates.push(path.join(__dirname, "services.json"));
  for (const p of candidates) {
    if (fs.existsSync(p)) {
      const raw = JSON.parse(fs.readFileSync(p, "utf8"));
      log(`services from ${p}`);
      return raw;
    }
  }
  throw new Error("Не найден services.json");
}

function enabledServices(manifest) {
  return (manifest.services || []).filter((s) => s.enabled !== false);
}

function processServices(manifest) {
  return enabledServices(manifest).filter((s) => s.kind === "process");
}

function dbService(manifest) {
  return enabledServices(manifest).find((s) => s.kind === "sqlite") || null;
}

function dbUrl(manifest) {
  const db = dbService(manifest);
  if (!db) return "";
  const abs = path.join(APP_ROOT, db.pathRelative.replace(/\//g, path.sep));
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  const posix = abs.replace(/\\/g, "/");
  return (db.urlTemplate || "sqlite:///{absolutePosix}").replace(
    "{absolutePosix}",
    posix
  );
}

function expandEnv(template, ctx) {
  if (template == null) return template;
  return String(template)
    .replace(/\{dataRoot\}/g, ctx.dataRoot)
    .replace(/\{host\}/g, ctx.host)
    .replace(/\{port\}/g, String(ctx.port))
    .replace(/\{dbUrl\}/g, ctx.dbUrl)
    .replace(/\{appRoot\}/g, ctx.appRoot);
}

function ensureDataFolders() {
  for (const sub of ["tenders", "catalogs", "uploads", "exports", "tenders/_agent"]) {
    fs.mkdirSync(path.join(DATA_ROOT, ...sub.split("/")), { recursive: true });
  }
  fs.mkdirSync(LOG_DIR, { recursive: true });
}

function readBuildInfo() {
  const candidates = [];
  if (app.isPackaged) {
    candidates.push(path.join(process.resourcesPath, "build-info.json"));
  }
  candidates.push(path.join(__dirname, "..", "dist", "build-info.json"));
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, "utf8"));
    } catch (_) {
      /* ignore */
    }
  }
  return null;
}

function resolveExe(resourceDir, exeName) {
  const candidates = [];
  if (app.isPackaged) {
    candidates.push(path.join(process.resourcesPath, resourceDir, exeName));
    candidates.push(path.join(path.dirname(process.execPath), resourceDir, exeName));
  } else {
    const dist = path.resolve(__dirname, "..", "dist");
    candidates.push(path.join(dist, resourceDir, exeName));
    candidates.push(path.join(__dirname, "..", "vendor", resourceDir, exeName));
  }
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return candidates[0];
}

function waitForHttp(url, timeoutMs, label) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (quitting) {
        reject(new Error("quitting"));
        return;
      }
      const req = http.get(url, (res) => {
        res.resume();
        log(`${label} ready status=${res.statusCode}`);
        resolve();
      });
      req.on("error", (err) => {
        if (Date.now() - started > timeoutMs) {
          reject(
            new Error(
              `${label} не ответил за ${timeoutMs / 1000} с: ${url}\n${err.message}`
            )
          );
          return;
        }
        setTimeout(tick, 800);
      });
      req.setTimeout(2000, () => req.destroy());
    };
    tick();
  });
}

function spawnHidden(exe, env, logPrefix) {
  if (!fs.existsSync(exe)) {
    throw new Error(`Не найден ${logPrefix}:\n${exe}`);
  }
  const out = fs.openSync(path.join(LOG_DIR, `${logPrefix}-out.log`), "a");
  const err = fs.openSync(path.join(LOG_DIR, `${logPrefix}-err.log`), "a");
  log(`starting ${exe}`);
  const proc = spawn(exe, [], {
    cwd: path.dirname(exe),
    env,
    windowsHide: true,
    stdio: ["ignore", out, err],
  });
  log(`${logPrefix} pid=${proc.pid}`);
  proc.on("error", (e) => log(`${logPrefix} spawn error: ${e}`));
  return proc;
}

function buildToolServerConnections(manifest) {
  return processServices(manifest)
    .filter((s) => s.toolServer)
    .map((s) => ({
      type: "openapi",
      url: `http://${s.host}:${s.port}`,
      path: "openapi.json",
      spec_type: "url",
      auth_type: "none",
      key: "",
      config: { enable: true },
      info: {
        id: s.toolServer.id,
        name: s.toolServer.name,
        description: s.toolServer.description || "",
      },
    }));
}

function serviceEnv(svc) {
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (svc.id !== "tender-agent" && AGENT_ONLY_ENV.test(key)) continue;
    env[key] = value;
  }
  const localCtx = { ...baseCtx, host: svc.host, port: svc.port };
  for (const [k, v] of Object.entries(svc.env || {})) {
    env[k] = expandEnv(v, localCtx);
  }
  if (svc.primaryUi) {
    env.HOST = svc.host;
    env.PORT = String(svc.port);
    env.DATA_DIR = WEBUI_DATA_DIR;
    env.WEBUI_SECRET_KEY =
      process.env.OWUI_SECRET_KEY || "ai-smart-tender-desktop-local";
    env.RAG_EMBEDDING_MODEL_AUTO_UPDATE = "false";
    env.WHISPER_MODEL_AUTO_UPDATE = "false";
    env.TOOL_SERVER_CONNECTIONS = JSON.stringify(
      buildToolServerConnections(servicesManifest)
    );
  }
  return env;
}

// ---------- состояние сервисов и верхняя панель ----------

function setState(svc, state, detail = "") {
  const item = serviceState.get(svc.id) || { restarts: [] };
  item.name = svc.displayName || svc.id;
  item.state = state;
  item.detail = detail;
  serviceState.set(svc.id, item);
  pushState();
}

function pushState() {
  if (!toolbarView || toolbarView.webContents.isDestroyed()) return;
  const services = processServices(servicesManifest)
    .filter((s) => !s.primaryUi)
    .map((s) => {
      const item = serviceState.get(s.id) || {};
      return { name: s.displayName || s.id, state: item.state || "starting", detail: item.detail || "" };
    });
  toolbarView.webContents.send("shell:state", { view: currentView, services });
}

/** Запустить сервис и дождаться его /health; при падении — перезапуск (не больше RESTART_LIMIT за 5 минут). */
function startService(svc) {
  const prefix = svc.logPrefix || svc.id;
  setState(svc, "starting");
  const proc = spawnHidden(resolveExe(svc.resourceDir, svc.exe), serviceEnv(svc), prefix);
  procs.set(prefix, proc);
  proc.on("exit", (code, signal) => {
    log(`${prefix} exit code=${code} signal=${signal}`);
    if (procs.get(prefix) === proc) procs.delete(prefix);
    if (quitting) return;
    const item = serviceState.get(svc.id) || { restarts: [] };
    const now = Date.now();
    item.restarts = (item.restarts || []).filter((t) => now - t < RESTART_WINDOW_MS);
    if (item.restarts.length >= RESTART_LIMIT) {
      setState(svc, "failed", `упал ${RESTART_LIMIT} раза за 5 минут, см. логи ${prefix}-err.log`);
      return;
    }
    item.restarts.push(now);
    serviceState.set(svc.id, item);
    setState(svc, "starting", `перезапуск после падения (код ${code})`);
    setTimeout(() => {
      if (!quitting) startService(svc);
    }, 3000);
  });
  const ready = waitForHttp(serviceUrl(svc), svc.healthTimeoutMs || 60000, prefix);
  ready.then(
    () => {
      setState(svc, "ready");
      if (svc.id === "dashboard") loadDashboardView(svc);
      // Open WebUI после перезапуска: открытая страница чата потеряла сервер — перезагружаем.
      if (svc.primaryUi && views.chat) views.chat.webContents.reload();
    },
    (err) => {
      if (!quitting) setState(svc, "failed", String(err.message || err));
    }
  );
  return ready;
}

function stopServers() {
  for (const [label, proc] of procs.entries()) {
    if (!proc || !proc.pid) continue;
    log(`stopping ${label} pid=${proc.pid}`);
    try {
      spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      });
    } catch (e) {
      log(`taskkill ${label} failed: ${e}`);
    }
  }
  procs.clear();
}

function serviceUrl(svc) {
  return `http://${svc.host}:${svc.port}${svc.healthPath || "/"}`;
}

// ---------- окно: верхняя панель + чат + дашборд ----------

function placeholderUrl(text) {
  const html = `<!doctype html><meta charset="utf-8"><body style="font:15px Segoe UI,sans-serif;display:flex;
    align-items:center;justify-content:center;height:100vh;margin:0;color:#777">${text}</body>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function layout() {
  if (!mainWindow) return;
  const { width, height } = mainWindow.getContentBounds();
  toolbarView.setBounds({ x: 0, y: 0, width, height: TOOLBAR_HEIGHT });
  for (const view of Object.values(views)) {
    view.setBounds({ x: 0, y: TOOLBAR_HEIGHT, width, height: Math.max(0, height - TOOLBAR_HEIGHT) });
  }
}

function showView(name) {
  if (!views[name]) return;
  currentView = name;
  for (const [key, view] of Object.entries(views)) view.setVisible(key === name);
  views[name].webContents.focus();
  pushState();
}

function openExternalLinks(view) {
  // Ссылки «в новой вкладке» (карточки закупок, файлы) — в системный браузер, а не в новое окно Electron.
  view.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
}

function createWindow(chatUrl) {
  mainWindow = new BaseWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    title: "AI Smart Tender",
    autoHideMenuBar: false,
  });
  toolbarView = new WebContentsView({
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false },
  });
  views.chat = new WebContentsView({ webPreferences: { contextIsolation: true, nodeIntegration: false } });
  views.dashboard = new WebContentsView({ webPreferences: { contextIsolation: true, nodeIntegration: false } });
  for (const view of [toolbarView, views.chat, views.dashboard]) mainWindow.contentView.addChildView(view);
  openExternalLinks(views.chat);
  openExternalLinks(views.dashboard);

  toolbarView.webContents.loadFile(path.join(__dirname, "toolbar.html"));
  toolbarView.webContents.on("did-finish-load", pushState);
  log(`loading ${chatUrl}`);
  views.chat.webContents.loadURL(chatUrl);
  // Дашборд обычно готов раньше чата: тогда сразу грузим его, иначе — заглушку до готовности.
  const dashboard = processServices(servicesManifest).find((s) => s.id === "dashboard");
  if (dashboard && (serviceState.get("dashboard") || {}).state === "ready") {
    loadDashboardView(dashboard);
  } else {
    views.dashboard.webContents.loadURL(placeholderUrl("Дашборд запускается…"));
  }
  mainWindow.on("resize", layout);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  layout();
  showView("chat");
}

function loadDashboardView(svc) {
  if (!views.dashboard) return;
  const url = `http://${svc.host}:${svc.port}/`;
  log(`dashboard view ${url}`);
  views.dashboard.webContents.loadURL(url);
}

ipcMain.on("shell:show", (_event, name) => showView(name));

function buildMenu(manifest) {
  const openLinks = processServices(manifest).filter((s) => s.openInMenu || s.primaryUi);
  const template = [
    {
      label: "Файл",
      submenu: [
        {
          label: "Открыть папку данных",
          click: async () => {
            ensureDataFolders();
            const err = await shell.openPath(DATA_ROOT);
            if (err) dialog.showErrorBox("Не удалось открыть папку", err);
          },
        },
        {
          label: "Открыть логи",
          click: async () => {
            fs.mkdirSync(LOG_DIR, { recursive: true });
            const err = await shell.openPath(LOG_DIR);
            if (err) dialog.showErrorBox("Не удалось открыть логи", err);
          },
        },
        { type: "separator" },
        ...openLinks.map((s) => ({
          label: `Открыть ${s.displayName || s.id} в браузере`,
          click: () => shell.openExternal(`http://${s.host}:${s.port}/`),
        })),
        { type: "separator" },
        { role: "quit", label: "Выход" },
      ],
    },
    {
      label: "Вид",
      submenu: [
        { label: "Чат", accelerator: "CmdOrCtrl+1", click: () => showView("chat") },
        { label: "Дашборд", accelerator: "CmdOrCtrl+2", click: () => showView("dashboard") },
        { type: "separator" },
        {
          label: "Обновить страницу",
          accelerator: "F5",
          click: () => views[currentView] && views[currentView].webContents.reload(),
        },
        {
          label: "Инструменты разработчика",
          accelerator: "CmdOrCtrl+Shift+I",
          click: () => views[currentView] && views[currentView].webContents.toggleDevTools(),
        },
      ],
    },
    {
      label: "Справка",
      submenu: [
        {
          label: "О программе",
          click: () => {
            const info = readBuildInfo();
            const enabled = enabledServices(manifest)
              .map((s) => `• ${s.id} (${s.kind})`)
              .join("\n");
            const lines = [
              "AI Smart Tender Desktop",
              info ? `Product: ${info.product || "?"}` : "Product: (dev)",
              info && info.git_sha ? `git: ${info.git_sha}` : null,
              "",
              "Сервисы:",
              enabled,
              "",
              `Data: ${DATA_ROOT}`,
              `DB: ${dbUrl(manifest) || "(none)"}`,
            ].filter((x) => x !== null);
            dialog.showMessageBox({
              type: "info",
              title: "О программе",
              message: lines.join("\n"),
            });
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------- запуск ----------

// Второй запуск start.exe не поднимает вторую копию сервисов (порты заняты, Open WebUI падал с кодом 3),
// а показывает уже открытое окно.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    try {
      ensureDataFolders();
      loadDotEnvFiles();
      servicesManifest = loadServices();
      baseCtx = {
        dataRoot: DATA_ROOT,
        appRoot: APP_ROOT,
        dbUrl: dbUrl(servicesManifest),
        host: "127.0.0.1",
        port: 0,
      };
      log(`db ${baseCtx.dbUrl || "(disabled)"}`);
      const info = readBuildInfo();
      if (info) log(`build product=${info.product} sha=${info.git_sha || "?"}`);
      buildMenu(servicesManifest);

      const processes = processServices(servicesManifest);
      const ui = processes.find((s) => s.primaryUi) || processes[0];
      if (!ui) throw new Error("Нет primaryUi сервиса в services.json");
      // Все сервисы стартуют сразу, но окно ждёт только чат: агент и дашборд догружаются в фоне,
      // их состояние видно в верхней панели, а медленный старт одного не закрывает приложение.
      const waits = new Map(processes.map((svc) => [svc.id, startService(svc)]));
      await waits.get(ui.id);
      createWindow(`http://${ui.host}:${ui.port}/`);
      log("window opened");
    } catch (err) {
      log(`startup failed: ${err && err.stack ? err.stack : err}`);
      quitting = true;
      stopServers();
      dialog.showErrorBox("Не удалось запустить", String(err && err.message ? err.message : err));
      app.quit();
    }
  });
}

app.on("window-all-closed", () => {
  quitting = true;
  stopServers();
  app.quit();
});

app.on("before-quit", () => {
  quitting = true;
  stopServers();
});
