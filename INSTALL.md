# Установка AI Smart Tender (Docker)

Нужен только **Docker Desktop**. Ollama не требуется.

## 1. Распаковать

Скопируйте папку дистрибутива на ПК. Внутри должны быть:

- `docker-compose.yml`
- `.env.example`
- `start.bat`
- `data/`
- `prompts/tender-agent-system.txt`
- `INSTALL.md`

Исходники не нужны — все образы берутся с Docker Hub: `dim4098/ai-smart-tender-open-webui`, `-tools`, `-agent`,
`-dashboard` (теги — версии из `desktop/versions.json`) и `postgres:16-alpine`.

## 2. Настроить `.env`

```powershell
copy .env.example .env
```

Обязательно для platform agent:

- `AGENT_LLM_API_KEY` — ключ DeepSeek (или другого API)
- `AGENT_LLM_BASE_URL` / `AGENT_LLM_MODEL`

Папка тендеров на диске пользователя (Docker Desktop понимает `/`):

```env
TENDERS_HOST_PATH=C:/Users/Name/Documents/Tenders
```

Остальные данные (exports, uploads, state) по умолчанию в `./data`.

Образы по умолчанию заданы в `docker-compose.yml`; переопределить (другой Docker Hub, другая версия) —
`OPEN_WEBUI_IMAGE`, `TOOLS_IMAGE`, `TENDER_AGENT_IMAGE`, `DASHBOARD_IMAGE` в `.env`.

## 3. Запуск

```powershell
docker compose up -d
```

Первый раз скачает образы (~14 ГБ: Open WebUI с моделью векторов ~8 ГБ, tender-agent с Chromium ~3.6 ГБ).

«Знания» Open WebUI сразу настроены как в desktop-версии: файлы разбирает tools-server (OCR, таблицы, архивы),
многоязычная модель векторов, гибридный поиск. Эти настройки применяются при **первом** запуске (пустой том
`open-webui-data`); на уже работающей установке их меняют в Админка → Настройки → Документы.

Открыть:
- чат Open WebUI: http://localhost:3000
- дашборд ТЕНАГ: http://localhost:3100
- Swagger tools: http://localhost:8000/docs

## 4. Первичная настройка в UI (обычно делает установщик)

1. Создать админ-аккаунт (signup).
2. Admin → Connections — добавить чат-модель (DeepSeek / OpenAI-compatible).
3. Убедиться, что Tool Server «Tender Tools» подключён (или Admin → Integrations → Tools → `http://tools-server:8000`).
4. Модель → Tools: включить нужные tools; Function Calling = Native.
5. Системный промпт — из `prompts/tender-agent-system.txt`.

## Важно про ключи

| Где | Для чего |
|-----|----------|
| Open WebUI → Connections | Ответы в чате |
| `.env` → `AGENT_LLM_*` | `run_platform_task` / `run_tender_download` внутри tools-server |

Это **два разных** места. Ключ только в UI не сделает platform agent рабочим.

## Разработка у нас

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

## Сборка дистрибутива (у разработчика)

```powershell
.\scripts\pack-dist.bat --zip
# или: py -3 scripts\pack_dist.py --zip
```

Результат: `dist/ai-smart-tender/` и zip рядом. Отдайте заказчику zip (без `.env`).

## Сборка и push образов на Docker Hub

```powershell
docker login
.\scripts\push-images.ps1            # все четыре образа, теги из desktop/versions.json
.\scripts\push-images.ps1 -NoPush    # только собрать
```

Для образа tender-agent рядом с репозиторием нужен `..\AI-vision` (или путь в `AI_VISION_PATH`).

## Остановка

```powershell
docker compose down
```

Данные WebUI остаются в volume `open-webui-data`, файлы — в `data/` и в папке `TENDERS_HOST_PATH`.
