"""База найденных закупок: всё, что агент вернул в прогонах.

Адрес базы — ``TENDER_AGENT_DB_URL`` (SQLAlchemy URL). В Docker это Postgres из
сервиса ``postgres`` (его же будет читать дашборд); без переменной — SQLite в
``data/tenders/_agent/agent.sqlite3`` (CLI, тесты).

Таблицы:
- ``tenders`` — закупка на площадке. Ключ: площадка + номер, а если номера нет — ссылка.
  Пустые поля нового прогона не затирают уже известные значения; ``first_seen`` /
  ``last_seen`` / ``seen_count`` показывают, когда закупка появилась и сколько раз встречалась.
- ``tender_documents`` — документы закупки (название, ссылка, путь к скачанному файлу).
- ``search_runs`` и ``run_tenders`` — какой запрос на какой площадке вернул какие закупки
  и в каком порядке: по ним видно, что нового с прошлого прогона.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    inspect,
    text,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

from ..api.schemas import DownloadRecord, TaskResult, TenderResult

metadata = MetaData()

tenders = Table(
    "tenders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("platform", String(255), nullable=False),
    Column("tender_key", String(1024), nullable=False),
    Column("external_id", String(255)),
    Column("url", Text),
    Column("title", Text),
    Column("customer", Text),
    Column("price", Float),
    Column("currency", String(16)),
    Column("published_at", String(10)),  # YYYY-MM-DD, как в ответе агента
    Column("deadline", String(10)),
    Column("status", Text),
    Column("verification_status", String(32)),
    Column("matched_keyword", String(255)),
    Column("sources", JSON),
    # Способ закупки, место поставки, обеспечения, ОКПД2 — что модель прочитала на карточке.
    Column("details", JSON),
    Column("first_seen", DateTime(timezone=True), nullable=False),
    Column("last_seen", DateTime(timezone=True), nullable=False),
    Column("seen_count", Integer, nullable=False, default=1),
    UniqueConstraint("platform", "tender_key", name="uq_tenders_platform_key"),
)
Index("ix_tenders_platform_published", tenders.c.platform, tenders.c.published_at)

tender_documents = Table(
    "tender_documents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tender_id", Integer, ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False),
    Column("name", Text),
    Column("url", Text, nullable=False),
    Column("local_path", Text),
    Column("size", Integer),
    Column("downloaded_at", DateTime(timezone=True)),
    UniqueConstraint("tender_id", "url", name="uq_documents_tender_url"),
)

search_runs = Table(
    "search_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("platform", String(255)),
    Column("start_url", Text),
    Column("query", Text),
    Column("task_text", Text),
    Column("status", String(32)),
    Column("result_count", Integer),
    Column("new_count", Integer),
    Column("errors", JSON),
    Column("stats", JSON),
    Column("trace_dir", Text),
)

run_tenders = Table(
    "run_tenders",
    metadata,
    Column("run_id", Integer, ForeignKey("search_runs.id", ondelete="CASCADE"), primary_key=True),
    Column("tender_id", Integer, ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True),
    Column("position", Integer, nullable=False),
    Column("is_new", Integer, nullable=False),
)

_FIELDS = (
    "external_id", "url", "title", "customer", "price", "currency", "published_at",
    "deadline", "status", "verification_status", "matched_keyword",
)


def platform_of(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def tender_key(tender: TenderResult) -> str:
    return (tender.id or "").strip() or (tender.url or "").strip()


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


_ENGINES: dict[str, Engine] = {}


def _engine(url: str) -> Engine:
    # Один движок на адрес: пул соединений Postgres не пересоздаётся на каждый запрос.
    if url not in _ENGINES:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        metadata.create_all(engine)
        _add_missing_columns(engine)
        _ENGINES[url] = engine
    return _ENGINES[url]


def _add_missing_columns(engine: Engine) -> None:
    """Колонки, добавленные после первого запуска (create_all существующие таблицы не меняет)."""
    existing = {column["name"] for column in inspect(engine).get_columns("tenders")}
    with engine.begin() as conn:
        if "details" not in existing:
            conn.execute(text("ALTER TABLE tenders ADD COLUMN details JSON"))


class TenderStore:
    def __init__(self, url: str | Path) -> None:
        if isinstance(url, Path):
            url.parent.mkdir(parents=True, exist_ok=True)
            url = sqlite_url(url)
        self.url = url
        self.engine = _engine(url)

    # ---------- запись ----------

    def save_run(self, text: str, result: TaskResult, trace_dir: str | None = None) -> dict:
        """Сохраняет закупки и документы прогона. Возвращает {"run_id", "new"}."""
        now = datetime.now(timezone.utc)
        task = result.task
        start_url = task.url if task else ""
        site = platform_of(start_url)
        new_count = 0
        with self.engine.begin() as conn:
            run_id = conn.execute(
                insert(search_runs).values(
                    created_at=now, platform=site, start_url=start_url,
                    query=(task.keywords[0] if task and task.keywords else None), task_text=text,
                    status=result.status, result_count=len(result.results), new_count=0,
                    errors=list(result.errors), stats=dict(result.stats), trace_dir=trace_dir,
                )
            ).inserted_primary_key[0]
            seen_ids: set[int] = set()
            for position, tender in enumerate(result.results, start=1):
                key = tender_key(tender)
                if not key:
                    continue
                platform = platform_of(tender.url) or site
                # Карточка на поддомене (44.sberbank-ast.ru) — всё та же площадка запуска.
                if site and (platform == site or platform.endswith("." + site)):
                    platform = site
                tender_id, is_new = self._upsert(conn, platform, key, tender, now)
                if tender_id in seen_ids:
                    continue
                seen_ids.add(tender_id)
                new_count += int(is_new)
                conn.execute(insert(run_tenders).values(run_id=run_id, tender_id=tender_id, position=position,
                                                        is_new=int(is_new)))
                names = {doc.get("url"): doc.get("name") for doc in tender.documents}
                for doc_url in tender.document_urls:
                    self._upsert_document(conn, tender_id, doc_url, name=names.get(doc_url))
                for download in result.downloads:
                    if download.status == "saved" and download.tender_url == tender.url:
                        self._upsert_document(conn, tender_id, download.source_url, name=download.name,
                                              local_path=download.path, size=download.size, downloaded_at=now)
            conn.execute(update(search_runs).where(search_runs.c.id == run_id).values(new_count=new_count))
        return {"run_id": int(run_id), "new": new_count}

    def _upsert(self, conn: Connection, platform: str, key: str, tender: TenderResult,
                now: datetime) -> tuple[int, bool]:
        values = {field: getattr(tender, "id" if field == "external_id" else field) for field in _FIELDS}
        row = conn.execute(
            select(tenders.c.id).where(and_(tenders.c.platform == platform, tenders.c.tender_key == key))
        ).first()
        if row is None:
            new_id = conn.execute(
                insert(tenders).values(platform=platform, tender_key=key, sources=dict(tender.sources),
                                       details=dict(tender.details) or None,
                                       first_seen=now, last_seen=now, seen_count=1, **values)
            ).inserted_primary_key[0]
            return int(new_id), True
        # Новое значение заменяет старое, пустое — не затирает.
        changes = {field: value for field, value in values.items() if value is not None}
        if tender.details:
            changes["details"] = dict(tender.details)
        conn.execute(
            update(tenders).where(tenders.c.id == row.id).values(
                sources=dict(tender.sources), last_seen=now, seen_count=tenders.c.seen_count + 1, **changes
            )
        )
        return int(row.id), False

    def _upsert_document(self, conn: Connection, tender_id: int, url: str, **values) -> None:
        values = {key: value for key, value in values.items() if value is not None}
        row = conn.execute(
            select(tender_documents.c.id, tender_documents.c.name).where(
                and_(tender_documents.c.tender_id == tender_id, tender_documents.c.url == url)
            )
        ).first()
        if row is None:
            conn.execute(insert(tender_documents).values(tender_id=tender_id, url=url, **values))
            return
        if row.name:
            # Название со страницы карточки понятнее имени скачанного файла.
            values.pop("name", None)
        if values:
            conn.execute(update(tender_documents).where(tender_documents.c.id == row.id).values(**values))

    # ---------- чтение ----------

    def keys_new_in_run(self, run_id: int) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(tenders.c.tender_key)
                .join(run_tenders, run_tenders.c.tender_id == tenders.c.id)
                .where(and_(run_tenders.c.run_id == run_id, run_tenders.c.is_new == 1))
            ).all()
        return [row.tender_key for row in rows]

    def search(self, *, platform: str | None = None, text: str | None = None, since: str | None = None,
               limit: int = 50) -> list[dict]:
        query = select(tenders)
        if platform:
            query = query.where(tenders.c.platform.like(f"%{platform.lower().removeprefix('www.')}%"))
        if text:
            pattern = f"%{text}%"
            query = query.where(or_(tenders.c.title.ilike(pattern), tenders.c.customer.ilike(pattern),
                                    tenders.c.external_id.ilike(pattern)))
        if since:
            query = query.where(tenders.c.first_seen >= datetime.fromisoformat(since).replace(tzinfo=timezone.utc))
        query = query.order_by(func.coalesce(tenders.c.published_at, "").desc(), tenders.c.id.desc()).limit(limit)
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(query)]
            ids = [row["id"] for row in rows]
            docs: dict[int, list[dict]] = {}
            if ids:
                for doc in conn.execute(select(tender_documents).where(tender_documents.c.tender_id.in_(ids))):
                    docs.setdefault(doc.tender_id, []).append(
                        {"name": doc.name, "url": doc.url, "local_path": doc.local_path}
                    )
        for row in rows:
            row["documents"] = docs.get(row["id"], [])
            for key in ("first_seen", "last_seen"):
                if isinstance(row[key], datetime):
                    row[key] = row[key].isoformat()
        return rows
