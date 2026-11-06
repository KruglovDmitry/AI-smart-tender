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
powershell -ExecutionPolicy Bypass -File .\build-desktop.ps1 -Zip
powershell -ExecutionPolicy Bypass -File .\build-desktop.ps1 -Installer
```

`-Installer` собирает `AI-Smart-Tender-Setup-<версия>.exe` (нужен [Inno Setup 6](https://jrsoftware.org/isinfo.php)).
Мастер на русском: папка, ярлыки и компоненты. Невыбранные модули на диск не копируются;
после установки в `resources\services.json` остаются включёнными только они.

| Компонент | Что ставится |
|-----------|----------------|
| Оболочка и чат | `start.exe`, Open WebUI (обязательно) |
| Инструменты | ToolsServer |
| Поиск закупок | Tender Agent + Chromium |
| Дашборд | Dashboard |
| Модель знаний | `resources\models` |

Типы: полная / чат и инструменты / выборочная. Установка в `%LOCALAPPDATA%\Programs\AI Smart Tender`
(без прав администратора). Данные по-прежнему в `%LOCALAPPDATA%\AISmartTender`. Деинсталлятор их не удаляет.

Портативный вариант без установщика — папка `win-unpacked` или zip (`-Zip`).

Всё, что создаёт сборка, лежит **вне репозитория** — в соседней папке `..\AI-smart-tender-build`
(другое место — переменная `AST_BUILD_ROOT`, пути заданы в [`build-paths.ps1`](build-paths.ps1)):

| Папка | Что внутри |
|-------|------------|
| `vendor\OpenWebUI`, `vendor\models` | замороженный Open WebUI и модель векторов — входные данные сборки, класть вручную |
| `venvs\tools`, `venvs\agent`, `venvs\dashboard` | Python-окружения сервисов (создаются сами) |
| `work\` | временные файлы PyInstaller / Nuitka, сгенерированный конфиг electron-builder |
| `dist\` | exe сервисов, модели, `build-info.json` |
| `release\win-unpacked\start.exe` | готовая программа — копировать **всю** папку `win-unpacked` |
| `releases\` | zip (`-Zip`) и setup.exe (`-Installer`) для заказчика |

`work\` и `release\` можно удалять в любой момент; без `venvs\` и `dist\` следующая сборка будет дольше.

Версии: [`versions.json`](versions.json).

## Пересборка после правок

Скрипт установщика ([`installer/setup.iss`](installer/setup.iss)) менять не нужно: он копирует уже собранные папки. После правок в коде сервиса пересобирают exe и заново пакуют setup.

Только tools-server:

```powershell
cd desktop
powershell -ExecutionPolicy Bypass -File .\build_tools_exe.ps1
powershell -ExecutionPolicy Bypass -File .\build-desktop.ps1 -SkipServices -Installer
```

Для агента или дашборда — `build_agent_exe.ps1` / `build_dashboard_exe.ps1`, затем тот же `-SkipServices -Installer`. Без `-SkipServices` пересоберутся все сервисы, это дольше.

Новый файл заказчику: `..\AI-smart-tender-build\releases\AI-Smart-Tender-Setup-<версия>.exe`. Ставится поверх предыдущей версии, данные в `%LOCALAPPDATA%\AISmartTender` не затираются.

## Окно

При запуске сразу открывается окно загрузки со статусом каждого сервиса. Главное окно — когда готов Open WebUI;
остальные сервисы догружаются в фоне. Закрытие окна загрузки отменяет старт.

Сверху панель с кнопками **чат** (Open WebUI) и **дашборд** (`Ctrl+1` / `Ctrl+2` и меню «Вид»).
Если дашборд не установлен, вкладка скрыта. Справа — состояние сервисов (запускается / работает / не отвечает).

- Упавший сервис перезапускается (не больше 3 раз за 5 минут), логи — меню **Файл → Открыть логи**.
- Повторный запуск `start.exe` не поднимает вторую копию, а показывает уже открытое окно (или окно загрузки).
- Ссылки «в новой вкладке» открываются в системном браузере.

## Ключи моделей

Общее место — **дашборд → вкладка «Агент»**: DeepSeek, Qwen VL и UI-TARS. Эти значения читают и tender-agent, и tools-server.
Файл: `%LOCALAPPDATA%\AISmartTender\data\tenders\_agent\settings.json`. Изменение действует со следующего вызова.

`.env` нужен только для первого запуска. Оболочка переносит из него непустые ключи и адреса в файл дашборда и больше не подставляет их заново.
Ищется первый существующий файл:

1. `%LOCALAPPDATA%\AISmartTender\.env`
2. рядом с `start.exe`
3. корень репо `.env` (dev)
4. `tender-agent/.env`

Шаблон: [`desktop/.env.example`](.env.example). Когда срок лицензии истекает, ключи из файла дашборда стираются. Новые вводятся во вкладке «Агент».

## Сборка: что нужно рядом

- У каждого сервиса свой venv (`venvs\agent`, `venvs\dashboard`, `venvs\tools` в папке сборки), зависимости ставятся при каждой сборке.
- Библиотека зрения `ai_vision` берётся из соседнего репозитория `..\AI-vision` (или путь в `AI_VISION_PATH`) и попадает внутрь `TenderAgent.exe`.
- Chromium ставится сразу в `dist\TenderAgent\ms-playwright` папки сборки (только нужная версия).
- Для `-Installer` — Inno Setup 6 (`ISCC.exe`). Коммерческим сборкам JRSoftware просит купить лицензию
  (без ключа компилятор пишет non-commercial; установщик при этом собирается).

## Заметки по tender-agent

- `TENDER_AGENT_HEADLESS=false` в services.json (видимый Chromium для логина); переключается во вкладке «Агент».
- Persistent browser profile — пока не делаем.
- Рядом с `TenderAgent.exe` кладётся `ms-playwright/` (Chromium).
