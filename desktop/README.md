# AI Smart Tender Desktop

Windows-дистрибутив: **Electron (`start.exe`) + Open WebUI + tools-server**.

## Что внутри

| Компонент | Порт | Назначение |
|-----------|------|------------|
| Open WebUI | `127.0.0.1:18082` | UI в окне Electron |
| tools-server | `127.0.0.1:18000` | `list_documents`, `read_folder`, Excel, fetch |

## Папка данных на ПК

Только песочница (не весь диск):

`%LOCALAPPDATA%\AISmartTender\data\`

- `tenders/`, `catalogs/`, `uploads/`, `exports/`

В меню приложения: **Файл → Открыть папку данных**.  
Данные Open WebUI: `%LOCALAPPDATA%\AISmartTender\webui-desktop\`.

## Версии

См. [versions.json](versions.json). При сборке пишется `dist/build-info.json` (попадает в `resources/`).

## Сборка

### 1. Open WebUI (один раз / при обновлении)

Соберите onedir в соседнем эксперименте `open-webui-freeze` (`build_exe.ps1`)  
или положите готовую папку в `desktop/vendor/OpenWebUI/` (внутри `OpenWebUI.exe`).

### 2. Полный дистрибутив

```powershell
cd desktop
powershell -ExecutionPolicy Bypass -File .\build-desktop.ps1
```

Опции:

- `-SkipOpenWebUI` — не копировать OWUI (уже лежит в `dist/OpenWebUI`)
- `-SkipTools` — не пересобирать ToolsServer
- `-OpenWebUISource 'C:\path\to\OpenWebUI'`
- `-Zip` — архив `dist/AI-Smart-Tender-Desktop-{version}.zip`

Результат: `electron/release/win-unpacked/start.exe`  
Копировать нужно **всю** папку `win-unpacked`.

### Только tools-server

```powershell
powershell -ExecutionPolicy Bypass -File .\build_tools_exe.ps1
```

## Dev-запуск Electron

Нужны уже собранные `dist/OpenWebUI` и `dist/ToolsServer`:

```bat
cd electron
npm install
npm start
```
