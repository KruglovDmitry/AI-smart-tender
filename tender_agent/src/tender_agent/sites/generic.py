from __future__ import annotations

import re

SECTION_RE = re.compile(r"закуп|тендер|торг|процедур|tender|procurement", re.I)
SEARCH_RE = re.compile(r"ключев|поиск|search|найти|запрос", re.I)
SUBMIT_RE = re.compile(r"найти|поиск|search|применить|показать", re.I)
NEXT_RE = re.compile(r"следующ|далее|^next$|показать ещё|показать еще", re.I)
SORT_RE = re.compile(r"дате публикации|сначала новые|по дате", re.I)
CAPTCHA_RE = re.compile(r"captcha|капча|я не робот|подтвердите, что вы не", re.I)
EMPTY_RE = re.compile(r"ничего не найдено|нет результатов|ничего не нашлось", re.I)
ERROR_RE = re.compile(r"не найден|страница не существует|\b404\b|\b500\b", re.I)
FILE_RE = re.compile(r"\.(pdf|docx?|xlsx?|zip|rar|7z)(\?|$)", re.I)
CARD_HREF_RE = re.compile(r"tender|purchase|procedure|trade|zakup|notice", re.I)

CARD_SELECTOR = "article.tender-card, tr.tender-row, main[data-tender-id]"
