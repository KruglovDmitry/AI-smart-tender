"""Чтение базы закупок tender-agent (Postgres в Docker или SQLite на desktop).

Схему создаёт и ведёт tender-agent (`tender_agent/storage/tenders.py`); дашборд её
читает и меняет только одно — пометку мягкого удаления `tenders.deleted_at`.
Таблицы отражаются из базы (reflection), чтобы не держать вторую копию описания схемы.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from sqlalchemy import MetaData, Table, and_, create_engine, desc, func, inspect, or_, select, text, true, update
from sqlalchemy.engine import Engine

DATABASE_URL = os.getenv("TENDER_DB_URL", "")
TABLES = ("tenders", "tender_documents", "search_runs", "run_tenders")


class DatabaseNotReady(RuntimeError):
    pass


@lru_cache(maxsize=1)
def engine() -> Engine:
    if not DATABASE_URL:
        raise DatabaseNotReady("TENDER_DB_URL не задан")
    # SQLite: check_same_thread=False — FastAPI может читать из разных потоков.
    connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    return create_engine(DATABASE_URL, pool_pre_ping=True, future=True, connect_args=connect_args)


def _tables() -> dict[str, Table]:
    meta = MetaData()
    try:
        meta.reflect(engine(), only=TABLES)
    except Exception as exc:  # таблиц ещё нет: tender-agent не делал ни одного прогона
        raise DatabaseNotReady(f"База закупок недоступна или пуста: {exc}") from exc
    return {name: meta.tables[name] for name in TABLES}


def _alive(tenders: Table):
    """Не удалённые закупки. Колонки может ещё не быть, если агент не перезапускался после обновления."""
    return tenders.c.deleted_at.is_(None) if "deleted_at" in tenders.c else true()


def set_deleted(tender_id: int, deleted: bool) -> bool:
    """Мягкое удаление закупки (или возврат). False — такой закупки нет."""
    eng = engine()
    existing = {column["name"] for column in inspect(eng).get_columns("tenders")}
    if "deleted_at" not in existing:
        kind = "TIMESTAMP WITH TIME ZONE" if eng.dialect.name == "postgresql" else "DATETIME"
        with eng.begin() as conn:
            conn.execute(text(f"ALTER TABLE tenders ADD COLUMN deleted_at {kind}"))
    tenders = _tables()["tenders"]
    with eng.begin() as conn:
        done = conn.execute(update(tenders).where(tenders.c.id == tender_id)
                            .values(deleted_at=datetime.now(timezone.utc) if deleted else None))
    return done.rowcount > 0


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _row(row) -> dict:
    return {key: _iso(value) for key, value in row._mapping.items()}


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


_SORTS = {
    "published": lambda t: (desc(func.coalesce(t.c.published_at, "")), desc(t.c.id)),
    "deadline": lambda t: (func.coalesce(t.c.deadline, "9999-12-31"), desc(t.c.id)),
    "price": lambda t: (desc(func.coalesce(t.c.price, -1)), desc(t.c.id)),
    "first_seen": lambda t: (desc(t.c.first_seen), desc(t.c.id)),
}


def list_tenders(*, platform: str | None = None, q: str | None = None, new_days: int | None = None,
                 published_from: str | None = None, published_to: str | None = None,
                 min_price: float | None = None, max_price: float | None = None,
                 verified_only: bool = False, deleted: bool = False, sort: str = "published", limit: int = 50,
                 offset: int = 0) -> dict:
    tables = _tables()
    tenders, docs = tables["tenders"], tables["tender_documents"]
    if deleted and "deleted_at" not in tenders.c:
        return {"total": 0, "tenders": []}
    where = [tenders.c.deleted_at.is_not(None) if deleted else _alive(tenders)]
    if platform:
        where.append(tenders.c.platform == platform.lower().removeprefix("www."))
    if q:
        pattern = f"%{q}%"
        where.append(or_(tenders.c.title.ilike(pattern), tenders.c.customer.ilike(pattern),
                         tenders.c.external_id.ilike(pattern)))
    if new_days:
        where.append(tenders.c.first_seen >= _since(new_days))
    if published_from:
        where.append(tenders.c.published_at >= published_from)
    if published_to:
        where.append(tenders.c.published_at <= published_to)
    if min_price is not None:
        where.append(tenders.c.price >= min_price)
    if max_price is not None:
        where.append(tenders.c.price <= max_price)
    if verified_only:
        where.append(tenders.c.verification_status == "verified")
    condition = and_(*where) if where else None
    doc_count = (
        select(func.count(docs.c.id)).where(docs.c.tender_id == tenders.c.id).scalar_subquery().label("documents_count")
    )
    query = select(tenders, doc_count)
    total_query = select(func.count(tenders.c.id))
    if condition is not None:
        query = query.where(condition)
        total_query = total_query.where(condition)
    query = query.order_by(*_SORTS.get(sort, _SORTS["published"])(tenders)).limit(limit).offset(offset)
    with engine().connect() as conn:
        rows = [_row(row) for row in conn.execute(query)]
        total = conn.execute(total_query).scalar_one()
    return {"total": total, "tenders": rows}


def tender_detail(tender_id: int) -> dict | None:
    tables = _tables()
    tenders, docs, runs, links = (tables[n] for n in TABLES)
    with engine().connect() as conn:
        row = conn.execute(select(tenders).where(tenders.c.id == tender_id)).first()
        if row is None:
            return None
        item = _row(row)
        item["documents"] = [_row(d) for d in conn.execute(select(docs).where(docs.c.tender_id == tender_id))]
        item["runs"] = [
            _row(r)
            for r in conn.execute(
                select(runs.c.id, runs.c.created_at, runs.c.query, runs.c.status, links.c.position, links.c.is_new)
                .join(links, links.c.run_id == runs.c.id)
                .where(links.c.tender_id == tender_id)
                .order_by(desc(runs.c.created_at))
                .limit(20)
            )
        ]
    return item


def list_runs(*, platform: str | None = None, limit: int = 50) -> list[dict]:
    runs = _tables()["search_runs"]
    query = select(runs).order_by(desc(runs.c.created_at)).limit(limit)
    if platform:
        query = query.where(runs.c.platform == platform.lower().removeprefix("www."))
    with engine().connect() as conn:
        return [_row(row) for row in conn.execute(query)]


def summary() -> dict:
    tables = _tables()
    tenders, runs = tables["tenders"], tables["search_runs"]
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    alive = _alive(tenders)
    with engine().connect() as conn:
        total = conn.execute(select(func.count(tenders.c.id)).where(alive)).scalar_one()
        new_today = conn.execute(
            select(func.count(tenders.c.id)).where(and_(alive, tenders.c.first_seen >= today))).scalar_one()
        new_week = conn.execute(
            select(func.count(tenders.c.id)).where(and_(alive, tenders.c.first_seen >= _since(7)))).scalar_one()
        per_platform = {
            row.platform: {"tenders": row.n, "new_week": row.new_week}
            for row in conn.execute(
                select(
                    tenders.c.platform,
                    func.count(tenders.c.id).label("n"),
                    func.count(tenders.c.id).filter(tenders.c.first_seen >= _since(7)).label("new_week"),
                ).where(alive).group_by(tenders.c.platform)
            )
        }
        last_runs = conn.execute(
            select(runs.c.platform, func.max(runs.c.created_at).label("last_run"), func.count(runs.c.id).label("runs"))
            .group_by(runs.c.platform)
        ).all()
        for row in last_runs:
            item = per_platform.setdefault(row.platform, {"tenders": 0, "new_week": 0})
            item["last_run"] = _iso(row.last_run)
            item["runs"] = row.runs
            status = conn.execute(
                select(runs.c.status, runs.c.result_count, runs.c.new_count)
                .where(and_(runs.c.platform == row.platform, runs.c.created_at == row.last_run))
            ).first()
            if status is not None:
                item["last_status"], item["last_results"], item["last_new"] = status
        recent = [_row(r) for r in conn.execute(select(runs).order_by(desc(runs.c.created_at)).limit(10))]
    return {
        "tenders_total": total,
        "new_today": new_today,
        "new_week": new_week,
        "platforms": [{"platform": name, **values} for name, values in sorted(per_platform.items())],
        "recent_runs": recent,
    }


STAGE_ORDER = ("search", "sort", "navigate", "list", "card_fields", "documents_tab", "documents_files")
SOURCE_GROUPS = {
    "label": "rule", "block": "rule", "dom": "rule", "link": "rule", "page": "rule",
    "field": "llm", "llm": "llm", "llm_block": "llm",
}


def methods_summary(*, days: int = 30, platform: str | None = None) -> dict:
    """Как часто и насколько успешно работали эвристики, модель, известные адреса и зрение.

    Счётчики берутся из stats.methods прогонов; источники полей — из tenders.sources.
    """
    tables = _tables()
    runs, tenders = tables["search_runs"], tables["tenders"]
    since = _since(days)
    query = select(runs.c.platform, runs.c.stats).where(runs.c.created_at >= since)
    if platform:
        query = query.where(runs.c.platform == platform)
    stages: dict[str, dict[str, dict[str, int]]] = {}
    runs_count = 0
    with engine().connect() as conn:
        for row in conn.execute(query):
            runs_count += 1
            methods = (row.stats or {}).get("methods") or {}
            for stage, by_method in methods.items():
                for method, counts in by_method.items():
                    item = stages.setdefault(stage, {}).setdefault(method, {"used": 0, "ok": 0})
                    item["used"] += int(counts.get("used", 0))
                    item["ok"] += int(counts.get("ok", 0))
        sources: dict[str, dict[str, int]] = {}
        fields_query = select(tenders.c.sources).where(tenders.c.last_seen >= since)
        if platform:
            fields_query = fields_query.where(tenders.c.platform == platform)
        for row in conn.execute(fields_query):
            for field, source in (row.sources or {}).items():
                group = SOURCE_GROUPS.get(source, "other")
                bucket = sources.setdefault(field, {})
                bucket[group] = bucket.get(group, 0) + 1
    ordered = [
        {"stage": stage, "methods": [{"method": m, **c} for m, c in sorted(stages[stage].items())]}
        for stage in sorted(stages, key=lambda s: STAGE_ORDER.index(s) if s in STAGE_ORDER else 99)
    ]
    return {"days": days, "runs": runs_count, "stages": ordered, "field_sources": sources}
