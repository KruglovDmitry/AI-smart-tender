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

Исходники `tools-server` не нужны — образ берётся с Docker Hub
(`dim4098/ai-smart-tender-tools`).

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

Образ tools-server:

```env
TOOLS_IMAGE=dim4098/ai-smart-tender-tools:1.3.0
```

(замените на ваш Docker Hub после `docker push`)

## 3. Запуск

```powershell
docker compose up -d
```

Первый раз скачает образы (Open WebUI + tools-server с Playwright — несколько ГБ).

Открыть: http://localhost:3000  
Swagger tools: http://localhost:8000/docs

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

## Сборка и push образа на Docker Hub

```powershell
docker login
.\scripts\push-tools-image.ps1
```

В клиентском `.env`: `TOOLS_IMAGE=dim4098/ai-smart-tender-tools:1.3.0`.

## Остановка

```powershell
docker compose down
```

Данные WebUI остаются в volume `open-webui-data`, файлы — в `data/` и в папке `TENDERS_HOST_PATH`.
