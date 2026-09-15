# AI Smart Tender

Локальный чат с LLM и документами: [Open WebUI](https://github.com/open-webui/open-webui) + Ollama + облачные API (DeepSeek / Qwen) + **OpenAPI Tool Server** (папка на сервере + просмотр ссылок).

## Что получите

- UI как у ChatGPT / DeepSeek на `http://localhost:3000`
- Выбор моделей: локальный Qwen и/или DeepSeek / Qwen по API
- Вложения файлов в чат + Knowledge
- Tools без форка UI:
  - чтение документов из `data/` на сервере (извлечение текста как в Open WebUI)
  - просмотр внешней ссылки (как browse в DeepSeek/ChatGPT)

## Требования

- Docker Desktop (Windows) с Docker Compose
- Для облачных моделей — API-ключи
- Для локального Qwen — от ~16 GB RAM для `qwen2.5:7b`

## Быстрый старт

```powershell
copy .env.example .env
docker compose up -d --build
```

Откройте [http://localhost:3000](http://localhost:3000).  
Swagger tools: [http://localhost:8000/docs](http://localhost:8000/docs).

Локальный Qwen:

```powershell
.\scripts\pull-qwen.ps1
```

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
2. Секция **Tools** — отметь `list_documents`, `read_document`, `read_folder`, `fetch_url`, `run_browser_task`
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

> Через browser-агент обработай тендер https://… — скачай документацию и кратко опиши лот.

> Открой ссылку https://example.com и кратко перескажи, о чём страница.

Положите свои файлы в `data/tenders` и `data/catalogs` на диске сервера — агент увидит их через tools.

## Browser agent (`run_browser_task`)

Внутри tools-server крутится **агент с tools** (без фиксированного grounding):
`navigate`, `screenshot`, `click_xy`, `type_text`, `list_download_links`, `download_url`, `get_page_text`, `finish`.

Агент сам решает: DOM, vision или оба. Нужны переменные:

```env
AGENT_LLM_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
AGENT_LLM_API_KEY=sk-...
AGENT_LLM_MODEL=qwen-max
```

> `qwen-vl-max` на intl часто **не** делает function calling. Для агента с tools берите `qwen-max` / `qwen-plus`. Endpoint для intl-ключа: `dashscope-intl.aliyuncs.com`.

Скачивания: `data/tenders/_browser/`.

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
