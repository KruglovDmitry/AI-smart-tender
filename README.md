# AI Smart Tender — этап 1

Локальный чат с LLM и документами: [Open WebUI](https://github.com/open-webui/open-webui) + Ollama (локальный Qwen) + облачные API (DeepSeek / Qwen).

Свой RAG и FastAPI **не входят** в этот этап. Документы передаются в модель через вложения в чате.

## Что получите

- UI как у ChatGPT / DeepSeek на `http://localhost:3000`
- Выбор моделей: локальный Qwen (Ollama) и/или DeepSeek / Qwen по API
- Загрузка файлов в чат (тендеры, каталоги, ZIP)
- Папки на диске: `data/tenders`, `data/catalogs`, `data/uploads`

## Требования

- Docker Desktop (Windows) с Docker Compose
- Для облачных моделей — API-ключи
- Для локального Qwen — достаточно RAM (для `qwen2.5:7b` комфортно от ~16 GB; GPU ускоряет, но не обязателен)

## Быстрый старт

### 1. Настройка окружения

```powershell
copy .env.example .env
```

Откройте `.env` и при необходимости укажите ключи.

**Только локальный Qwen** — ключи можно не трогать.

**DeepSeek:**

```env
OPENAI_API_BASE_URLS=https://api.deepseek.com/v1
OPENAI_API_KEYS=sk-ваш-ключ
```

**DeepSeek + Qwen (DashScope):**

```env
OPENAI_API_BASE_URLS=https://api.deepseek.com/v1;https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_API_KEYS=sk-deepseek;sk-qwen
```

Ключи: [DeepSeek](https://platform.deepseek.com/), [DashScope / Qwen](https://dashscope.console.aliyun.com/).

### 2. Запуск

```powershell
docker compose up -d
```

Откройте [http://localhost:3000](http://localhost:3000).

Первый зарегистрированный пользователь становится администратором.

### 3. Локальная модель Qwen

```powershell
.\scripts\pull-qwen.ps1
```

По умолчанию тянется `qwen2.5:7b`. Другая модель:

```powershell
.\scripts\pull-qwen.ps1 -Model "qwen2.5:14b"
```

В Open WebUI модель появится в списке (Ollama).

### 4. Облачные модели в UI (если не задали в `.env`)

Admin Panel → Settings → Connections → OpenAI:

| Провайдер | URL | Ключ |
|-----------|-----|------|
| DeepSeek | `https://api.deepseek.com/v1` | из platform.deepseek.com |
| Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | из DashScope |

Сохраните — модели подтянутся автоматически.

## Как работать с тендерами (этап 1)

1. Положите файлы в `data/tenders` и `data/catalogs` (для порядка на диске).
2. В чате Open WebUI **прикрепите** нужные файлы к сообщению (скрепка / upload).
3. Выберите модель (Qwen или DeepSeek).
4. Напишите задачу, например:

> Вот тендер и каталог. Подбери аналоги позиций из тендера по каталогу. Ответ таблицей: позиция → кандидат → почему.

Пока нет RAG: в контекст попадают только **прикреплённые** файлы (и то, что модель успевает «прочитать» по лимиту контекста). Большие архивы лучше давать частями.

## Полезные команды

```powershell
docker compose ps
docker compose logs -f open-webui
docker compose logs -f ollama
docker compose down
docker compose pull
docker compose up -d
```

Список локальных моделей Ollama:

```powershell
docker exec -it tender-ollama ollama list
```

## Структура

```
AI-smart-tender/
├── docker-compose.yml
├── .env.example
├── data/
│   ├── tenders/      # тендеры
│   ├── catalogs/     # эталоны / каталоги
│   └── uploads/      # временные файлы
└── scripts/
    └── pull-qwen.ps1
```

## Этап 2 (позже)

Когда документов станет много для ручных вложений — отдельный RAG по `data/tenders` и `data/catalogs` (поиск чанков, метаданные, автоматический контекст).

## Troubleshooting

| Проблема | Что проверить |
|----------|----------------|
| UI не открывается | `docker compose ps`, порт `WEBUI_PORT` в `.env` |
| Нет моделей Ollama | `.\scripts\pull-qwen.ps1`, логи `tender-ollama` |
| DeepSeek/Qwen не видны | ключи в `.env` или Connections в админке; URL с суффиксом `/v1` |
| Медленный локальный Qwen | меньшая модель (`7b`) или GPU в `docker-compose.yml` |
| Файл «не учитывается» | прикрепите к сообщению; слишком большой PDF — разбейте |

## Ссылки

- [Open WebUI](https://github.com/open-webui/open-webui)
- [Документация: OpenAI-compatible провайдеры](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/)
- [Ollama](https://ollama.com/)
