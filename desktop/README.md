# AI Smart Tender Desktop

Windows-дистрибутив: Electron (`start.exe`) + сервисы из [`services.json`](services.json).

## Сервисы (вкл/выкл)

Правится **`desktop/services.json`** → поле `"enabled": true|false`.

| id | Порт | Назначение |
|----|------|------------|
| openwebui | 18082 | UI (окно Electron) |
| tools | 18000 | Документы / Excel / fetch |
| tender-agent | 18010 | Браузерный поиск (headed по умолчанию) |
| dashboard | 18100 | Дашборд закупок |
| db | — | SQLite `%LOCALAPPDATA%\AISmartTender\data\tenders\_agent\agent.sqlite3` |
| postgres | — | **disabled** в desktop (только Docker) |

Отключённый сервис не собирается и не запускается.

## Данные

`%LOCALAPPDATA%\AISmartTender\data\` — меню **Файл → Открыть папку данных**.

Open WebUI: `%LOCALAPPDATA%\AISmartTender\webui-desktop\`.

## Сборка

```powershell
cd desktop
powershell -ExecutionPolicy Bypass -File .\build-desktop.ps1
```

Open WebUI: `desktop/vendor/OpenWebUI/` (или соседний freeze).

Результат: `electron/release/win-unpacked/start.exe` — копировать **всю** папку.

Версии: [`versions.json`](versions.json).

## Ключи LLM / Vision (пока из `.env`)

Electron при старте читает `.env` (не перезаписывает уже заданные переменные Windows):

1. `%LOCALAPPDATA%\AISmartTender\.env`
2. рядом с `start.exe`
3. корень репо `.env` (dev)
4. `tender-agent/.env`

Шаблон: [`desktop/.env.example`](.env.example) — `AGENT_LLM_*`, `AV_TARS_*`, `AV_PLANNER_*`, `TENDER_AGENT_VISION_ENABLED`.  
Позже ключи переедут в dashboard.

## Заметки по tender-agent

- `TENDER_AGENT_HEADLESS=false` в services.json (видимый Chromium для логина).
- Persistent browser profile — пока не делаем.
- Рядом с `TenderAgent.exe` кладётся `ms-playwright/` (Chromium).
