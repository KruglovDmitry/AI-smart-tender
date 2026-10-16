const { app, BrowserWindow, dialog, Menu, shell } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

const OWUI_HOST = process.env.OWUI_HOST || "127.0.0.1";
const OWUI_PORT = Number(process.env.OWUI_PORT || 18082);
const TOOLS_HOST = process.env.TOOLS_HOST || "127.0.0.1";
const TOOLS_PORT = Number(process.env.TOOLS_PORT || 18000);
const OWUI_URL = `http://${OWUI_HOST}:${OWUI_PORT}/`;
const TOOLS_URL = `http://${TOOLS_HOST}:${TOOLS_PORT}/`;
const TOOLS_HEALTH = `http://${TOOLS_HOST}:${TOOLS_PORT}/health`;

const APP_ROOT =
  process.env.OWUI_APP_ROOT ||
  path.join(process.env.LOCALAPPDATA || app.getPath("userData"), "AISmartTender");
const DATA_ROOT = process.env.OWUI_DATA_ROOT || path.join(APP_ROOT, "data");
const WEBUI_DATA_DIR =
  process.env.OWUI_DATA_DIR || path.join(APP_ROOT, "webui-desktop");
const LOG_DIR = path.join(WEBUI_DATA_DIR, "logs");
const SHELL_LOG = path.join(LOG_DIR, "electron.log");

const TOOL_SERVER_CONNECTIONS = JSON.stringify([
  {
    type: "openapi",
    url: `http://${TOOLS_HOST}:${TOOLS_PORT}`,
    path: "openapi.json",
    spec_type: "url",
    auth_type: "none",
    key: "",
    config: { enable: true },
    info: {
      id: "tender-tools",
      name: "Tender Tools",
      description: "Documents, Excel, page fetch",
    },
  },
]);

let owuiProc = null;
let toolsProc = null;
let mainWindow = null;

function log(msg) {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(SHELL_LOG, `[${new Date().toISOString()}] ${msg}\n`);
  } catch (_) {
    /* ignore */
  }
  console.log(msg);
}

function ensureDataFolders() {
  for (const sub of ["tenders", "catalogs", "uploads", "exports"]) {
    fs.mkdirSync(path.join(DATA_ROOT, sub), { recursive: true });
  }
  fs.mkdirSync(LOG_DIR, { recursive: true });
}

function readBuildInfo() {
  const candidates = [];
  if (app.isPackaged) {
    candidates.push(path.join(process.resourcesPath, "build-info.json"));
  }
  candidates.push(path.join(__dirname, "..", "dist", "build-info.json"));
  candidates.push(path.join(__dirname, "build-info.json"));
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) {
        return JSON.parse(fs.readFileSync(p, "utf8"));
      }
    } catch (_) {
      /* ignore */
    }
  }
  return null;
}

function resolveExe(resourceName, exeName) {
  const candidates = [];
  if (app.isPackaged) {
    candidates.push(path.join(process.resourcesPath, resourceName, exeName));
    candidates.push(path.join(path.dirname(process.execPath), resourceName, exeName));
  } else {
    const dist = path.resolve(__dirname, "..", "dist");
    candidates.push(path.join(dist, resourceName, exeName));
    // Dev: sibling freeze experiment
    candidates.push(
      path.resolve(__dirname, "..", "..", "..", "open-webui-freeze", "dist", resourceName, exeName)
    );
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
  });
  return proc;
}

function startToolsServer() {
  const exe = resolveExe("ToolsServer", "ToolsServer.exe");
  const env = {
    ...process.env,
    DATA_ROOT,
    TOOLS_HOST,
    TOOLS_PORT: String(TOOLS_PORT),
    TOOLS_PUBLIC_BASE_URL: `http://${TOOLS_HOST}:${TOOLS_PORT}`,
  };
  toolsProc = spawnHidden(exe, env, "tools");
  toolsProc.on("exit", () => {
    toolsProc = null;
  });
}

function startOpenWebUI() {
  const exe = resolveExe("OpenWebUI", "OpenWebUI.exe");
  const env = {
    ...process.env,
    HOST: OWUI_HOST,
    PORT: String(OWUI_PORT),
    DATA_DIR: WEBUI_DATA_DIR,
    WEBUI_SECRET_KEY: process.env.OWUI_SECRET_KEY || "ai-smart-tender-desktop-local",
    RAG_EMBEDDING_MODEL_AUTO_UPDATE: "false",
    WHISPER_MODEL_AUTO_UPDATE: "false",
    TOOL_SERVER_CONNECTIONS,
  };
  owuiProc = spawnHidden(exe, env, "owui");
  owuiProc.on("exit", () => {
    owuiProc = null;
  });
}

function killTree(proc, label) {
  if (!proc || !proc.pid) return;
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

function stopServers() {
  killTree(owuiProc, "owui");
  killTree(toolsProc, "tools");
  owuiProc = null;
  toolsProc = null;
}

function buildMenu() {
  const template = [
    {
      label: "Файл",
      submenu: [
        {
          label: "Открыть папку данных",
          click: async () => {
            ensureDataFolders();
            const err = await shell.openPath(DATA_ROOT);
            if (err) {
              dialog.showErrorBox("Не удалось открыть папку", err);
            }
          },
        },
        {
          label: "Открыть логи",
          click: async () => {
            fs.mkdirSync(LOG_DIR, { recursive: true });
            const err = await shell.openPath(LOG_DIR);
            if (err) {
              dialog.showErrorBox("Не удалось открыть логи", err);
            }
          },
        },
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
            const lines = [
              "AI Smart Tender Desktop",
              info ? `Product: ${info.product || "?"}` : "Product: (dev)",
              info ? `tools-server: ${info.tools_server || "?"}` : null,
              info ? `open_webui: ${info.open_webui || "?"}` : null,
              info ? `electron_shell: ${info.electron_shell || "?"}` : null,
              info && info.git_sha ? `git: ${info.git_sha}` : null,
              "",
              `UI: ${OWUI_URL}`,
              `Tools: ${TOOLS_URL}`,
              `Data: ${DATA_ROOT}`,
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

async function createWindow() {
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

  log(`loading ${OWUI_URL}`);
  await mainWindow.loadURL(OWUI_URL);
}

app.whenReady().then(async () => {
  try {
    ensureDataFolders();
    const info = readBuildInfo();
    if (info) {
      log(
        `build product=${info.product} tools=${info.tools_server} owui=${info.open_webui} sha=${info.git_sha || "?"}`
      );
    }
    log("electron ready");
    log(`data root ${DATA_ROOT}`);
    log(`webui data ${WEBUI_DATA_DIR}`);
    buildMenu();
    startToolsServer();
    startOpenWebUI();
    await Promise.all([
      waitForHttp(TOOLS_HEALTH, 60_000, "tools"),
      waitForHttp(OWUI_URL, 180_000, "owui"),
    ]);
    await createWindow();
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
