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

## Окно

Сверху панель с двумя кнопками: **чат** (Open WebUI) и **дашборд**, также `Ctrl+1` / `Ctrl+2` и меню «Вид».
Справа в панели — состояние агента, инструментов и дашборда (запускается / работает / не отвечает).

- Окно открывается, как только готов Open WebUI; остальные сервисы догружаются в фоне.
- Упавший сервис перезапускается (не больше 3 раз за 5 минут), логи — меню **Файл → Открыть логи**.
- Повторный запуск `start.exe` не поднимает вторую копию, а показывает уже открытое окно.
- Ссылки «в новой вкладке» открываются в системном браузере.

## Ключи и настройки агента

Основное место — **дашборд → вкладка «Агент»**: DOM-модель, VL-планировщик и UI-TARS (адрес, ключ, модель),
зрение вкл/выкл, видимость браузера, скачивание документов, лимиты. Кнопки «Проверить» делают пробный запрос к модели.
Сохраняется в `%LOCALAPPDATA%\AISmartTender\data\tenders\_agent\settings.json` (ключи — открытым текстом)
и действует со следующего поиска без перезапуска.

Запасной вариант — `.env` (настройки из дашборда сильнее). Electron при старте читает первый найденный:

1. `%LOCALAPPDATA%\AISmartTender\.env`
2. рядом с `start.exe`
3. корень репо `.env` (dev)
4. `tender-agent/.env`

Шаблон: [`desktop/.env.example`](.env.example). Переменные `AGENT_LLM_*`, `TENDER_AGENT_*`, `AV_*`, `QWEN_API_KEY`
передаются только сервису tender-agent.

## Сборка: что нужно рядом

- У каждого сервиса свой venv (`desktop/.venv-agent`, `.venv-dashboard`, `.venv-tools`), зависимости ставятся при каждой сборке.
- Библиотека зрения `ai_vision` берётся из соседнего репозитория `..\AI-vision` (или путь в `AI_VISION_PATH`) и попадает внутрь `TenderAgent.exe`.
- Chromium ставится сразу в `dist/TenderAgent/ms-playwright` (только нужная версия).

## Заметки по tender-agent

- `TENDER_AGENT_HEADLESS=false` в services.json (видимый Chromium для логина); переключается во вкладке «Агент».
- Persistent browser profile — пока не делаем.
- Рядом с `TenderAgent.exe` кладётся `ms-playwright/` (Chromium).
