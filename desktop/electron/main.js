const { app, BrowserWindow, dialog, Menu, shell } = require("electron");
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

/** @type {Map<string, import('child_process').ChildProcess>} */
const procs = new Map();
let mainWindow = null;
let servicesManifest = null;

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
 * Ключи агента (DOM LLM, VL, UI-TARS) пока из .env; позже — из dashboard.
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
    log(
      `env: файл не найден (положите .env в ${path.join(APP_ROOT, ".env")} или рядом с start.exe)`
    );
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
  proc.on("exit", (code, signal) => {
    log(`${logPrefix} exit code=${code} signal=${signal}`);
    procs.delete(logPrefix);
  });
  return proc;
}

function buildToolServerConnections(manifest) {
  return enabledServices(manifest)
    .filter((s) => s.kind === "process" && s.toolServer)
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

function startProcessService(svc, ctx) {
  const exe = resolveExe(svc.resourceDir, svc.exe);
  const env = { ...process.env };
  const localCtx = {
    ...ctx,
    host: svc.host,
    port: svc.port,
  };
  if (svc.env) {
    for (const [k, v] of Object.entries(svc.env)) {
      env[k] = expandEnv(v, localCtx);
    }
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
  const prefix = svc.logPrefix || svc.id;
  const proc = spawnHidden(exe, env, prefix);
  procs.set(prefix, proc);
  return proc;
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

function buildMenu(manifest) {
  const openLinks = enabledServices(manifest).filter(
    (s) => s.kind === "process" && (s.openInMenu || s.primaryUi)
  );
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
          label: `Открыть ${s.displayName || s.id}`,
          click: () => shell.openExternal(`http://${s.host}:${s.port}/`),
        })),
        { type: "separator" },
        { role: "quit", label: "Выход" },
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
              .map((s) => `• ${s.id} (${s.kind}${s.enabled === false ? ", off" : ""})`)
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

async function createWindow(uiUrl) {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    title: "AI Smart Tender",
    autoHideMenuBar: false,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    mainWindow.focus();
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  log(`loading ${uiUrl}`);
  await mainWindow.loadURL(uiUrl);
}

app.whenReady().then(async () => {
  try {
    ensureDataFolders();
    loadDotEnvFiles();
    servicesManifest = loadServices();
    const ctx = {
      dataRoot: DATA_ROOT,
      appRoot: APP_ROOT,
      dbUrl: dbUrl(servicesManifest),
      host: "127.0.0.1",
      port: 0,
    };
    log(`db ${ctx.dbUrl || "(disabled)"}`);

    const info = readBuildInfo();
    if (info) {
      log(`build product=${info.product} sha=${info.git_sha || "?"}`);
    }
    buildMenu(servicesManifest);

    const processes = enabledServices(servicesManifest).filter(
      (s) => s.kind === "process"
    );
    for (const svc of processes) {
      startProcessService(svc, ctx);
    }

    await Promise.all(
      processes.map((svc) =>
        waitForHttp(
          serviceUrl(svc),
          svc.healthTimeoutMs || 60000,
          svc.logPrefix || svc.id
        )
      )
    );

    const ui =
      processes.find((s) => s.primaryUi) ||
      processes[0];
    if (!ui) {
      throw new Error("Нет primaryUi сервиса в services.json");
    }
    await createWindow(`http://${ui.host}:${ui.port}/`);
    log("window opened");
  } catch (err) {
    log(`startup failed: ${err && err.stack ? err.stack : err}`);
    stopServers();
    dialog.showErrorBox(
      "Не удалось запустить",
      String(err && err.message ? err.message : err)
    );
    app.quit();
  }
});

app.on("window-all-closed", () => {
  stopServers();
  app.quit();
});

app.on("before-quit", () => {
  stopServers();
});
