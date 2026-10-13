"""Пары «подпись — значение» из основного текста страницы.

Словарь ниже — язык закупки, а не вёрстка конкретной площадки.
"""

from __future__ import annotations

import re

from .models import PageField, RawCard

_COLON = re.compile(r"^(.{2,90}?):\s*(.{1,400})$")
_DATE = re.compile(r"\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}")
_MONEY_TEXT = re.compile(r"(₽|₸|\$|руб\.?|тенге)", re.I)
_MONEY_AMOUNT = re.compile(r"\d[\d\s]*(?:[.,]\d+)?")
_NUMBER = re.compile(r"№\s*(\d{6,})")
_STATUS = re.compile(
    r"Подача заявок|Работа комиссии|Определение поставщика завершено|Приостановлено",
    re.I,
)
_INLINE = re.compile(
    r"(Начальная(?:\s+\([^)]+\))?\s+цена|НМЦК|Цена контракта|Стоимость|"
    r"Размещено|Опубликовано|Дата публикации|"
    r"Окончание подачи заявок|Окончание при[её]ма заявок|"
    r"Заказчик|Организация, осуществляющая размещение|"
    r"Объект закупки|Наименование закупки|Предмет контракта|Статус)"
    r"\s+(.+?)(?=\s+(?:Начальная|НМЦК|Цена контракта|Стоимость|Размещено|Опубликовано|"
    r"Дата публикации|Окончание подачи заявок|Окончание при[её]ма заявок|Заказчик|"
    r"Организация, осуществляющая размещение|Объект закупки|Наименование закупки|"
    r"Предмет контракта|Обновлено|Статус)\b|$)",
    re.I,
)
_MAX_FIELDS = 80
# Числовой идентификатор закупки в адресе карточки (/need/6245882, ?regNumber=0373…).
_HREF_ID = re.compile(r"(?:/|=)(\d{6,})(?:[/?&#]|$)")


def label_kind(label: str) -> str | None:
    low = label.casefold().strip(" :.-")
    if not low or re.search(r"обнов", low):
        return None
    if re.search(r"цен|нмцк|стоим", low):
        return "price"
    if re.search(r"окончан|подач\w*\s+заяв|при[её]м\w*\s+заяв|срок\s+подачи", low):
        return "deadline"
    if re.search(r"заказчик|организац", low):
        return "customer"
    if re.search(r"размещ|публикац|опублик", low):
        return "published"
    if re.search(r"объект\s+закупки|наименован|предмет", low):
        return "title"
    if re.search(r"статус|этап", low):
        return "status"
    return None


def _money_value(text: str) -> bool:
    stripped = _MONEY_TEXT.sub("", text).strip()
    return bool(_MONEY_AMOUNT.fullmatch(stripped))


def value_ok(kind: str, value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if kind == "price":
        return _money_value(text)
    if kind in {"published", "deadline"}:
        return bool(_DATE.search(text))
    if kind == "customer":
        return _DATE.search(text) is None and not _money_value(text) and len(text) >= 3
    if kind == "title":
        return len(text) >= 8
    if kind == "status":
        return len(text) <= 80
    return False


def _add(found: list[tuple[str, str]], seen: set[str], label: str, value: str) -> None:
    label = re.sub(r"\s+", " ", (label or "")).strip(" :")
    value = re.sub(r"\s+", " ", (value or "")).strip()
    if len(label) < 2 or len(label) > 90 or not value or len(value) > 400:
        return
    if label.casefold() == value.casefold():
        return
    key = f"{label.casefold()}|{value.casefold()}"
    if key in seen:
        return
    seen.add(key)
    found.append((label, value))


def pairs_from_text(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    index = 0
    while index < len(lines):
        line = lines[index]
        colon = _COLON.match(line)
        if colon:
            _add(found, seen, colon.group(1), colon.group(2))
            index += 1
            continue
        kind = label_kind(line)
        if kind and index + 1 < len(lines) and value_ok(kind, lines[index + 1]):
            _add(found, seen, line, lines[index + 1])
            index += 2
            continue
        inline = list(_INLINE.finditer(line))
        if inline:
            for matched in inline:
                kind = label_kind(matched.group(1)) or ""
                if value_ok(kind, matched.group(2)):
                    _add(found, seen, matched.group(1), matched.group(2))
        index += 1
    flat = re.sub(r"\s+", " ", text or "").strip()
    if flat and flat not in lines:
        for matched in _INLINE.finditer(flat):
            kind = label_kind(matched.group(1)) or ""
            if value_ok(kind, matched.group(2)):
                _add(found, seen, matched.group(1), matched.group(2))
    return found


def build_fields(pairs: list[dict] | None, text: str = "") -> list[PageField]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pair in pairs or []:
        if not isinstance(pair, dict):
            continue
        label = str(pair.get("label") or "")
        value = str(pair.get("value") or "")
        source = str(pair.get("source") or "struct")
        kind = label_kind(label)
        if source == "adjacent" and not (kind and value_ok(kind, value)):
            continue
        # Соседние контейнеры часто склеивают подпись со значением («Размещено 13.10.2026» → «Обновлено …»).
        # Такая «подпись» уже содержит данные и даёт второе, ложное значение поля.
        if source == "adjacent" and (
            len(label) > 40 or _DATE.search(label) or _MONEY_TEXT.search(label) or _INLINE.match(label.strip())
        ):
            continue
        _add(found, seen, label, value)
    for label, value in pairs_from_text(text):
        _add(found, seen, label, value)
    return [
        PageField(id=index, label=label, value=value)
        for index, (label, value) in enumerate(found[:_MAX_FIELDS], start=1)
    ]


def known_values(fields: list[PageField]) -> dict[str, str]:
    buckets: dict[str, list[str]] = {}
    for field in fields:
        kind = label_kind(field.label)
        if kind and value_ok(kind, field.value):
            buckets.setdefault(kind, [])
            if field.value not in buckets[kind]:
                buckets[kind].append(field.value)
    return {kind: values[0] for kind, values in buckets.items() if len(values) == 1}


def _title(known: dict[str, str], text: str, link_text: str, title_link: str = "") -> str:
    titled = (known.get("title") or "").strip()
    if titled:
        return titled[:300]
    # Текст ссылки на саму карточку: на zakupki.mos.ru это и есть название («Сервер»).
    named = re.sub(r"\s+", " ", title_link or "").strip()
    if len(named) >= 4 and label_kind(named) is None and not re.fullmatch(r"№?\s*\d+", named):
        return named[:300]
    link = (link_text or "").strip()
    if len(link) >= 8 and not re.fullmatch(r"№?\s*\d+", link):
        return link[:300]
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) >= 12 and label_kind(line) is None and not re.fullmatch(r"№?\s*\d+", line):
            return line[:300]
    return ""


def cards_from_blocks(blocks: list[dict] | None) -> list[RawCard]:
    cards: list[RawCard] = []
    seen: set[str] = set()
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        href = str(block.get("href") or "").strip()
        text = str(block.get("text") or "")
        if not href or href in seen or len(text) < 40:
            continue
        fields = build_fields(list(block.get("pairs") or []), text)
        known = known_values(fields)
        title = _title(known, text, str(block.get("link_text") or ""), str(block.get("title_link") or ""))
        if len(title) < 4:
            continue
        number = _NUMBER.search(text)
        href_id = _HREF_ID.search(href)
        # Блок без номера, цены и срока подачи — не закупка (новости, статьи, баннеры).
        if not (number or href_id or known.get("price") or known.get("deadline")):
            continue
        status = known.get("status")
        if not status:
            found_status = _STATUS.search(text)
            status = found_status.group(0) if found_status else None
        seen.add(href)
        cards.append(
            RawCard(
                id=number.group(1) if number else (href_id.group(1) if href_id else None),
                title=title,
                href=href,
                customer=known.get("customer"),
                published_at=known.get("published"),
                deadline=known.get("deadline"),
                price_text=known.get("price"),
                status=status,
                sources={"title": "block", "price": "block", "published_at": "block"},
            )
        )
        if len(cards) >= 20:
            break
    return cards
