"""Признаки того, что «название» карточки — не предмет закупки: заказчик, цена, дата, статус, тип процедуры."""

from __future__ import annotations

import re

from .fields import junk_title, label_kind

_MONEY_OR_DATE = re.compile(r"^[\d\s.,:₽$₸%-]+(?:руб\.?|rub|kzt)?$|^\d{2}\.\d{2}\.\d{4}", re.I)
_ORG_START = re.compile(
    r"^(?:ООО|ОАО|ЗАО|ПАО|АО|ИП|ФГУП|ФГБУ|ФГБОУ|ГБУ|ГКУ|МБУ|МКУ|МУП|ГУП|ОБЩЕСТВО|АКЦИОНЕРНОЕ|ПУБЛИЧНОЕ|"
    r"ФЕДЕРАЛЬН\w+\s+ГОСУДАРСТВЕНН|ГОСУДАРСТВЕНН\w+\s+(?:БЮДЖЕТН|КАЗЕНН|АВТОНОМН|УНИТАРН)|МУНИЦИПАЛЬН|"
    r"УПРАВЛЕНИЕ|МИНИСТЕРСТВО|АДМИНИСТРАЦИЯ)\w*\b",
    re.I,
)
_PROCEDURE_OR_STATUS = re.compile(
    r"^(?:электронн\w+\s+(?:открыт\w+\s+)?(?:аукцион|запрос|конкурс)|открыт\w+\s+(?:аукцион|конкурс)|"
    r"конкурентн\w+\s+отбор|запрос\s+(?:котировок|предложений|цен)|отказ\s+организатора|ожидает|"
    r"подать\s+заявку|приём\s+заявок|прием\s+заявок|закупка\s+у\s+единственного)",
    re.I,
)
_STEM_MIN = 4


def suspicious_title(title: str | None) -> bool:
    text = re.sub(r"\s+", " ", title or "").strip()
    if len(text) < 4:
        return True
    return bool(
        junk_title(text)
        # Подпись поля («Заказчик», «Начальная цена») — только короткая строка: в длинном названии
        # «лицензии» тоже содержит «цен».
        or (len(text) <= 30 and label_kind(text))
        or _MONEY_OR_DATE.match(text)
        or _ORG_START.match(text)
        or _PROCEDURE_OR_STATUS.match(text)
    )
