"""Заметки из прогонов (этап B плана docs/site-notes-plan.md).

После прогона модель читает сжатый трейс и предлагает, что дописать в заметку площадки (где сортировка,
как ведёт себя фильтр, где документы, ловушки) или что в заметке устарело. Предложения копятся в
кандидатах ``<data_dir>/_agent/sites/candidates/<домен>.json`` и в заметку не попадают сразу:

- кандидат становится действующим, когда тот же вывод подтвердили ``CONFIRMS`` удачных прогона;
- действующий возвращается в кандидаты, если с ним ``STRIKES`` прогона подряд прошли хуже
  (таймаут, лимит шагов, сбой или дольше ``SLOWER`` × время подтверждавших прогонов).

Действующие пункты модель видит вместе с заметкой площадки (``sites.notes_for``), с пометкой «авто».
Пользователь этого не видит и ничего не подтверждает — внутренний механизм.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

CONFIRMS = 2  # удачных прогонов, чтобы кандидат стал действующим
STRIKES = 2  # плохих прогонов подряд, чтобы действующий вернулся в кандидаты
SLOWER = 1.5  # «дольше обычного»: во столько раз медленнее подтверждавших прогонов
MAX_NEW = 3  # новых предложений за прогон
MAX_TEXT = 220
SECTIONS = ("Общее", "Выдача", "Карточка", "Ловушки")
DIGEST_STEPS = 60

PROPOSE_SYSTEM = (
    "Ты разбираешь трейс прогона браузерного агента на площадке закупок и ведёшь заметки об этой площадке "
    "для следующих прогонов. Тебе дают: итог прогона, шаги (этап, действие, рассуждение агента, итог действия), "
    "текущую заметку площадки и список кандидатов (id, раздел, текст, статус).\n"
    "Верни JSON {\"confirm\": [id], \"refute\": [id], \"new\": [{\"section\", \"text\", \"kind\", \"evidence\"}]}:\n"
    "- confirm — кандидаты, которые этот прогон подтвердил (агент сделал так, и это сработало);\n"
    "- refute — кандидаты, которые прогон опроверг (сайт ведёт себя иначе);\n"
    "- new — не больше трёх новых выводов, которых нет ни в заметке, ни в кандидатах. section — «Общее», "
    "«Выдача», «Карточка» или «Ловушки»; kind — «add» (дописать) или «remove» (пункт заметки устарел: text — "
    "дословно этот пункт); evidence — номера шагов.\n"
    "Пиши только то, что повторится в следующий раз и сэкономит шаги: где поиск, сортировка, фильтры, "
    "«показать ещё», как понять, что выдача кончилась, где документы на карточке, окна и проверки, что не "
    "срабатывает. Не пиши о конкретных закупках, датах, числах в выдаче и о самой цели задания. "
    "Не советуй входить в аккаунт, вводить данные или обходить проверки. Текст — одно предложение по-русски, "
    f"до {MAX_TEXT} символов. Если сказать нечего — пустые списки."
)


@dataclass
class Candidate:
    id: str
    section: str
    text: str
    kind: str = "add"  # add | remove
    status: str = "candidate"  # candidate | active
    confirms: list[str] = field(default_factory=list)  # прогоны, подтвердившие вывод
    refutes: list[str] = field(default_factory=list)
    strikes: int = 0  # плохих прогонов подряд с действующим пунктом
    baseline_seconds: float = 0.0  # медиана времени подтверждавших прогонов
    since: str = ""  # дата, когда пункт стал действующим
    evidence: str = ""


def domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


class CandidateStore:
    def __init__(self, root: Path) -> None:
        self.dir = root / "candidates"

    def path(self, domain: str) -> Path:
        return self.dir / f"{re.sub(r'[^a-z0-9.-]', '_', domain)}.json"

    def load(self, domain: str) -> list[Candidate]:
        try:
            data = json.loads(self.path(domain).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        fields = Candidate.__dataclass_fields__
        return [Candidate(**{k: v for k, v in item.items() if k in fields}) for item in data if isinstance(item, dict)]

    def save(self, domain: str, items: list[Candidate]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path(domain).write_text(json.dumps([asdict(c) for c in items], ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    def active(self, domain: str) -> list[Candidate]:
        return [c for c in self.load(domain) if c.status == "active"]


def run_quality(summary: dict) -> str:
    """good | bad | neutral. Нейтральный прогон (нужен человек, площадка закрыта входом) ничего не решает."""
    stats = summary.get("stats") or {}
    outcome = stats.get("loop_outcome")
    if summary.get("status") == "failed" or outcome in {"timeout", "actions", "model_error"}:
        return "bad"
    if summary.get("status") == "needs_user":
        return "neutral"
    return "good"


def update(items: list[Candidate], proposal: dict, run_id: str, summary: dict, *, today: str | None = None) -> list[Candidate]:
    """Применить предложение модели и итог прогона к кандидатам площадки."""
    quality = run_quality(summary)
    seconds = float(summary.get("seconds") or 0)
    by_id = {c.id: c for c in items}
    confirmed = {str(i) for i in proposal.get("confirm") or []} & by_id.keys()
    refuted = {str(i) for i in proposal.get("refute") or []} & by_id.keys()
    # Действующие пункты отвечают за прогон, в котором они были у модели.
    for item in items:
        if item.status != "active":
            continue
        slow = item.baseline_seconds and seconds > SLOWER * item.baseline_seconds
        if quality == "bad" or slow or item.id in refuted:
            item.strikes += 1
        elif quality == "good":
            item.strikes = 0
        if item.strikes >= STRIKES:
            item.status, item.confirms, item.strikes, item.since = "candidate", [], 0, ""
    for item_id in confirmed - refuted:
        item = by_id[item_id]
        if quality == "good" and run_id not in item.confirms:
            item.confirms.append(run_id)
    for item_id in refuted:
        item = by_id[item_id]
        item.refutes.append(run_id)
        if item.status == "candidate":
            item.confirms = []
    known = {_norm(c.text) for c in items}
    for new in (proposal.get("new") or [])[:MAX_NEW]:
        if not isinstance(new, dict):
            continue
        text = str(new.get("text") or "").strip()[:MAX_TEXT]
        section = str(new.get("section") or "")
        if not text or section not in SECTIONS or _norm(text) in known:
            continue
        kind = "remove" if new.get("kind") == "remove" else "add"
        item = Candidate(id=f"c{len(items) + 1}", section=section, text=text, kind=kind,
                         confirms=[run_id] if quality == "good" else [], evidence=str(new.get("evidence") or "")[:80])
        items.append(item)
        known.add(_norm(text))
    for item in items:
        if item.status == "candidate" and len(item.confirms) >= CONFIRMS:
            item.status = "active"
            item.since = today or date.today().isoformat()
            item.baseline_seconds = _baseline(item, summary)
    return items


def _baseline(item: Candidate, summary: dict) -> float:
    seconds = [float(s) for s in (summary.get("_confirm_seconds") or {}).get(item.id, []) if s]
    seconds.append(float(summary.get("seconds") or 0))
    seconds = [s for s in seconds if s > 0]
    return round(statistics.median(seconds), 1) if seconds else 0.0


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def digest(trace_dir: Path, domain: str) -> dict:
    """Сжатый трейс для модели: итог и шаги на страницах этой площадки."""
    def read_json(name: str) -> dict:
        try:
            return json.loads((trace_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    result = read_json("result.json")
    steps = []
    try:
        lines = (trace_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            step = json.loads(line)
        except ValueError:
            continue
        url = str(step.get("url") or "")
        host = domain_of(url)
        if host and host != domain and not host.endswith("." + domain):
            continue  # карточка увела на площадку-источник — это о другой площадке
        decision = step.get("decision") or {}
        steps.append({
            "n": step.get("step"),
            "phase": step.get("phase"),
            "path": urlparse(url).path[:80],
            "tool": decision.get("tool") or ("vision" if step.get("auto_vision") else None),
            "thought": str(decision.get("thought") or "")[:240],
            "outcome": str(step.get("note") or step.get("error") or "")[:240],
        })
    stats = result.get("stats") or {}
    return {
        "status": result.get("status"),
        "errors": (result.get("errors") or [])[:5],
        "loop_outcome": stats.get("loop_outcome"),
        "vision_calls": stats.get("vision_calls"),
        "llm_calls": stats.get("llm_calls"),
        "steps": steps[-DIGEST_STEPS:],
    }


def notes_lines(domain: str, store: CandidateStore, phase: str) -> tuple[list[str], set[str]]:
    """Действующие авто-пункты для этапа: строки, которые дописать, и тексты пунктов, которые убрать."""
    from . import _PHASE_SECTIONS  # noqa: PLC0415 — общий список разделов этапа

    sections = _PHASE_SECTIONS.get(phase, ())
    add, remove = [], set()
    for item in store.active(domain):
        if item.section not in sections:
            continue
        if item.kind == "remove":
            remove.add(_norm(item.text))
        else:
            add.append(f"[{item.section}] (авто {item.since}) {item.text}")
    return add, remove


async def learn(llm, trace_dir: Path, sites_dir: Path, summary: dict) -> dict | None:
    """Разобрать прогон и обновить кандидатов. Возвращает предложение модели (для трейса) или None."""
    from . import load as load_notes  # noqa: PLC0415

    domain = domain_of(str(summary.get("url") or ""))
    if not domain or summary.get("status") is None:
        return None
    for notes in load_notes(sites_dir):
        if any(domain == d or domain.endswith("." + d) for d in notes.domains):
            domain = notes.domains[0]
            current = {name: lines for name, lines in notes.sections.items()}
            break
    else:
        current = {}
    store = CandidateStore(sites_dir)
    items = store.load(domain)
    task = {
        "notes": current,
        "candidates": [{"id": c.id, "section": c.section, "text": c.text, "kind": c.kind, "status": c.status}
                       for c in items],
    }
    proposal = await llm.propose_notes(task, digest(trace_dir, domain))
    run_id = trace_dir.name
    seconds = {c.id: [] for c in items}
    for c in items:
        seconds[c.id] = [s for s in _run_seconds(trace_dir.parent, c.confirms)]
    store.save(domain, update(items, proposal, run_id, {**summary, "_confirm_seconds": seconds}))
    return proposal


def _run_seconds(runs_dir: Path, run_ids: list[str]) -> list[float]:
    try:
        lines = (runs_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if Path(str(row.get("trace") or "")).name in run_ids:
            found.append(float(row.get("seconds") or 0))
    return found
