from __future__ import annotations

import re

SECTION_RE = re.compile(r"закуп|тендер|торг|процедур|tender|procurement", re.I)
SEARCH_RE = re.compile(r"ключев|поиск|search|найти|запрос", re.I)
SUBMIT_RE = re.compile(r"найти|поиск|search|применить|показать", re.I)
NEXT_RE = re.compile(r"следующ|далее|^next$|показать ещё|показать еще", re.I)
# Пункт сортировки «по дате публикации/размещения, сначала новые». «Обновления» и «окончания» — не то.
SORT_RE = re.compile(
    r"дат[аеуы]\s+(?:публикац|размещ|опубликов)|по\s+дате\s+(?:публикац|размещ)|сначала\s+нов|новые\s+(?:сверху|сначала)",
    re.I,
)
SORT_WRONG_RE = re.compile(r"обновл|окончан|подач|измен", re.I)
# Контрол, который открывает выбор сортировки («Сортировать по: …», «Сортировка»).
SORT_MENU_RE = re.compile(r"сортир|упорядоч|^sort", re.I)
CAPTCHA_RE = re.compile(r"captcha|капча|я не робот|подтвердите, что вы не", re.I)
EMPTY_RE = re.compile(r"ничего не найдено|нет результатов|ничего не нашлось", re.I)
ERROR_RE = re.compile(r"не найден|страница не существует|\b404\b|\b500\b", re.I)
FILE_RE = re.compile(r"\.(pdf|docx?|xlsx?|zip|rar|7z)(\?|$)", re.I)
# Ссылка на файл без расширения в адресе: ЕИС отдаёт документы через /filestore/.../download/...?uid=.
DOC_HREF_RE = re.compile(r"filestore|/download|file\.html\?uid=|getfile|attachment", re.I)
CARD_HREF_RE = re.compile(r"tender|purchase|procedure|trade|zakup|notice", re.I)

CARD_SELECTOR = "article.tender-card, tr.tender-row, main[data-tender-id]"
