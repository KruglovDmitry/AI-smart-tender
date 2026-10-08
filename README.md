# AI Smart Tender

Локальный чат с LLM и документами: [Open WebUI](https://github.com/open-webui/open-webui) + облачные API + **OpenAPI Tool Server** (тендеры, fetch, platform agent, Excel).

Установка у клиента: **[INSTALL.md](INSTALL.md)** (`start.bat` или `docker compose up -d`).

## Что получите

- UI на `http://localhost:3000` (чат-модели через Connections в UI; Ollama не нужна)
- Дашборд ТЕНАГ на `http://localhost:3100` (мониторинг площадок, скачанные тендеры, статус)
- Вложения файлов в чат + Knowledge
- Tools: документы из папки тендеров / `data/`, fetch URL, platform agent, Excel

## Требования

- Docker Desktop (Windows) с Docker Compose
- API-ключ для чата (в UI) и для platform agent (`.env` → `AGENT_LLM_*`)

## Быстрый старт

```powershell
copy .env.example .env
# AGENT_LLM_API_KEY=... ; при необходимости TENDERS_HOST_PATH=C:/path/to/tenders
docker compose up -d
```

Откройте [http://localhost:3000](http://localhost:3000) (чат) или [http://localhost:3100](http://localhost:3100) (дашборд ТЕНАГ).  
Swagger tools: [http://localhost:8000/docs](http://localhost:8000/docs).

Разработка (mount исходников):

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Дистрибутив для заказчика (без исходников, образ с Hub):

```powershell
cd C:\Users\Dima\Desktop\Work\AI-smart-tender
.\scripts\pack-dist.bat --zip
```

Результат: `dist\ai-smart-tender\` и `dist\ai-smart-tender-YYYYMMDD.zip`.  
Альтернатива: `py -3 scripts\pack_dist.py --zip`
## Подключение Tool Server в Open WebUI

Tools работают **без изменения UI** — через штатные OpenAPI Tool Servers.

### 1. Сервер инструментов

В `.env` уже задан `TOOL_SERVER_CONNECTIONS` на `http://tools-server:8000`.  
Swagger: [http://localhost:8000/docs](http://localhost:8000/docs).

Если добавляешь вручную:

| Где | URL |
|-----|-----|
| Admin → Integrations → Tools (**Global**) | `http://tools-server:8000` |
| User → Integrations (из браузера) | `http://localhost:8000` |

После добавления нажми проверку и **Сохранить**.

### 2. Сделать tools доступными **в чате**

Сервер сам по себе недостаточен — tools нужно включить у модели/чата:

**Навсегда для модели (рекомендуется):**
1. Рабочее пространство → **Модели** → **Тендер агент** (или deepseek) → ✎
2. Секция **Tools** — отметь `list_documents`, `read_document`, `read_folder`, `fetch_url`, `run_platform_task`, `run_tender_download`
3. Advanced → **Function Calling = Native**
4. Сохранить

**Только для текущего чата:**
1. В поле ввода нажми **+**
2. Включи нужные Tools
3. Напиши запрос заново

После этого модель сможет вызывать tools. Без этого шага она видит только Knowledge Open WebUI.

Документация: [Tools in chat](https://docs.openwebui.com/features/extensibility/plugin/tools/).

### Примеры запросов агенту

> Покажи файлы в папке tenders и прочитай sample-tender.txt вместе с каталогом catalogs/sample-catalog.csv. Подбери аналоги.

> Через run_tender_download обработай карточку https://… — скачай документацию и кратко опиши лот.

> Открой ссылку https://example.com и кратко перескажи, о чём страница.

> Проверь на zakupki.gov.ru новые тендеры по ключевым словам «счётчик газа», скачай документы по новым лотам.

Положите свои файлы в `data/tenders` и `data/catalogs` на диске сервера — агент увидит их через tools.

## Platform agent (`run_platform_task`)

LangChain-агент в стиле **AI-booking**: `create_openai_tools_agent` + `AgentExecutor`
(системный промпт в `agent/loop.py`).

- ищет тендеры на платформе (`platform_url` + `keywords`)
- дедуплицирует через SQLite (`data/_state/seen_tenders.sqlite3`)
- открывает **новые** карточки и скачивает документацию

Пример:

```powershell
curl -X POST http://localhost:8000/run_platform_task `
  -H "Content-Type: application/json" `
  -d '{"platform_url":"https://zakupki.gov.ru/","keywords":"счётчик газа","max_new_tenders":2}'
```

## Single tender download (`run_tender_download`)

Тонкий вход: adapter `open_card` → `collect_documents` → `download` в `data/tenders/<host>/<tender_id>/` + `manifest.json`.

Замена старого `run_browser_task` (удалён). Нужны те же `AGENT_LLM_*` / Playwright, что и для platform agent.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/run_tender_download `
  -Method POST -ContentType "application/json" `
  -Body '{"tender_url":"https://zakupki.gov.ru/epz/order/notice/.../common-info.html?regNumber=..."}'
```

## Platform agent notes

Режим tools: `platform` (adapter + DOM). Старые `full` / `browser` / vision сняты.

## Как устроено чтение документов

Извлечение текста повторяет **default engine** Open WebUI ([loaders/main.py](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/retrieval/loaders/main.py)):

| Формат | Способ |
|--------|--------|
| PDF | PyPDFLoader |
| DOCX | Docx2txtLoader |
| TXT/MD/… | TextLoader + encoding detect |
| CSV | CSVLoader |
| XLSX | pandas |
| PPTX | python-pptx |
| ZIP | распаковка + извлечение вложенных файлов |
| HTML | BSHTMLLoader |

То есть в контекст модели попадает **извлечённый текст**, как после добавления файла в чат (не отдельный «чужой» RAG).

## Просмотр ссылок

`fetch_url` скачивает страницу и вычищает основной контент через **trafilatura** (title + body + таблицы) — тот же класс задач, что web-browsing в ChatGPT/DeepSeek.

## Полезные команды

```powershell
docker compose ps
docker compose logs -f tools-server
docker compose up -d --build tools-server
curl http://localhost:8000/health
curl "http://localhost:8000/list_documents?path=tenders"
```

## Структура

```
AI-smart-tender/
├── docker-compose.yml
├── .env.example
├── prompts/tender-agent-system.txt
├── tools-server/          # OpenAPI tools для Open WebUI
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
├── data/
│   ├── tenders/
│   ├── catalogs/
│   └── uploads/
└── scripts/pull-qwen.ps1
```

## Дальше

- Bitrix24 как ещё один tool
- Свой RAG по `data/`, если объём документов вырастет

## Troubleshooting

| Проблема | Что проверить |
|----------|----------------|
| Tools не видны | URL `http://tools-server:8000` как **Global** tool server; `docker compose ps` |
| Нет доступа к файлам | файлы лежат в `./data/...`, volume смонтирован |
| fetch_url пустой | страница требует JS/логин — тогда только публичный HTML |
| Модель не вызывает tools | включите Tools у модели; DeepSeek/Qwen с function calling |
