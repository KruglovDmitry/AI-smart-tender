"""Снимок страницы для модели: адрес, видимый текст, интерактивные элементы с номерами.

Никаких выводов о странице («это поле поиска», «это выдача») здесь нет — их делает модель.
Код только помечает элементы атрибутом ``data-ta-ref``, чтобы действие попало в тот же элемент,
и отмечает CAPTCHA: её агент не обходит.
"""

from __future__ import annotations

import hashlib
import re

from ..tenders.models import Element, Observation
from .runtime import BrowserRuntime

CAPTCHA_RE = re.compile(r"captcha|капча|я не робот|подтвердите, что вы не", re.I)
# Росэлторг 27.10: после двух «Показать ещё» 30+ лотов, и строки ниже 300-го элемента оставались без ссылки —
# закупка в тексте есть, а чтение выдачи отбрасывало её («нет ссылки на карточку»). Модели для выбора
# действия всё равно уходит не больше ELEMENTS_FOR_MODEL (agent/loop.py).
MAX_ELEMENTS = 1000
MAX_TEXT = 120_000

_SNAPSHOT_JS = """() => {
  if (!document.body) return { pending: true };
  document.querySelectorAll('[data-ta-ref]').forEach(el => el.removeAttribute('data-ta-ref'));
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const clean = (text) => String(text || '').replace(/\\s+/g, ' ').trim();
  const hrefOf = (el) => {
    const raw = el.getAttribute('href');
    if (!raw || /^javascript:/i.test(raw)) return null;
    try { return new URL(raw, location.href).href; } catch (e) { return null; }
  };
  const nameOf = (el) => {
    const img = el.querySelector && el.querySelector('img[alt]');
    return clean(el.innerText || el.textContent).slice(0, 120)
      || clean(el.getAttribute('aria-label')) || clean(el.getAttribute('title'))
      || (img ? clean(img.getAttribute('alt')) : '');
  };
  const selector = 'a[href], button, input:not([type=hidden]), textarea, select, summary, [onclick], '
    + '[role=button], [role=link], [role=tab], [role=menuitem], [role=option], [role=checkbox], [role=combobox]';
  // Вложенное в ссылку или кнопку — часть того же элемента.
  const insideControl = (el) => !!(el.parentElement && el.parentElement.closest('a[href], button'));
  const tagged = new Set([...document.querySelectorAll(selector)].filter(el => visible(el) && !insideControl(el)));
  // Самодельные контролы (выпадающий список «Сортировать по» на ЕИС — div и ul/li со скриптом):
  // курсор-«рука» без стандартной разметки. Курсор наследуется, поэтому берём верхний такой элемент,
  // а в списках (ul/ol/listbox/menu) — каждый пункт; обёртки над ссылками и над пунктами не берём.
  const pointer = (el) => !!el && el.nodeType === 1 && getComputedStyle(el).cursor === 'pointer';
  const isList = (el) => !!el && (/^(UL|OL)$/.test(el.tagName) || /^(listbox|menu)$/.test(el.getAttribute('role') || ''));
  const candidates = [];
  // tr — строки таблиц выдачи, которые открывают карточку скриптом (Росатом: <tr> без ссылок).
  // a без href — ссылка-кнопка со скриптом (Сбер-АСТ, раздел Банка России: имя файла — <a> без адреса).
  for (const el of document.querySelectorAll('a:not([href]), div, span, li, label, i, img, svg, tr, td, p, h1, h2, h3, h4, ul, ol')) {
    if (tagged.has(el) || !pointer(el) || insideControl(el)) continue;
    const parent = el.parentElement;
    if (pointer(parent) && !(el.tagName === 'LI' || isList(parent))) continue;
    if (el.querySelector(selector) || !visible(el)) continue;
    const text = clean(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title'));
    if (!text && !el.querySelector('img, svg') && el.tagName !== 'IMG' && el.tagName.toLowerCase() !== 'svg') continue;
    candidates.push(el);
  }
  const custom = new Set(candidates.filter(el => !candidates.some(other => other !== el && el.contains(other))));
  custom.forEach(el => tagged.add(el));
  // Переключатели со спрятанным input (сортировка Росэлторга: label > input[type=radio] невидимый + текст):
  // сам input не виден и не попадает в снимок, поэтому кликать нужно по подписи.
  const toggles = new Map();
  for (const label of document.querySelectorAll('label')) {
    const input = label.querySelector('input[type=radio], input[type=checkbox]')
      || (label.htmlFor && document.getElementById(label.htmlFor));
    if (!input || !/^(radio|checkbox)$/.test(input.type) || visible(input) || !visible(label)) continue;
    if (insideControl(label) || !clean(label.innerText)) continue;
    [...label.querySelectorAll('*')].forEach(child => { tagged.delete(child); custom.delete(child); });
    tagged.add(label);
    toggles.set(label, input);
  }
  // Ссылки на файлы в свёрнутых блоках («Приложения к извещению» на Росэлторге): их не видно,
  // но скачать по адресу можно и не раскрывая блок. Модель видит их с пометкой «скрыт».
  const fileHref = /\\.(pdf|docx?|xlsx?|zip|rar|7z|rtf|odt|ods)(\\?|$)|filestore|\\/download|\\/documents?\\/|attachment|getfile|file\\.html\\?uid=/i;
  const hidden = new Set([...document.querySelectorAll('a[href]')]
    .filter(el => !tagged.has(el) && !visible(el) && (el.hasAttribute('download') || fileHref.test(hrefOf(el) || '')))
    .slice(0, 40));
  hidden.forEach(el => tagged.add(el));
  // Признаки, которых нет в тексте: направление сортировки, выбранный пункт (ЕИС: два пункта
  // «Дате размещения» отличаются только классом -reverse и иконкой).
  const hintOf = (el) => {
    const marks = String(el.getAttribute('class') || '').split(/\\s+/)
      .filter(token => /asc|desc|revers|up\\b|down\\b|select|active|current|checked|sort/i.test(token));
    for (const name of ['aria-sort', 'aria-selected', 'aria-checked', 'aria-expanded', 'type', 'data-sort', 'data-direction']) {
      const value = el.getAttribute(name);
      if (value && !(name === 'type' && /^(INPUT|BUTTON)$/.test(el.tagName))) marks.push(name + '=' + value);
    }
    if (hidden.has(el)) marks.unshift('скрыт');
    if (toggles.has(el) && toggles.get(el).checked) marks.unshift('выбрано');
    return marks.join(' ').slice(0, 120);
  };
  const nodes = [...tagged].sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
  const elements = nodes.slice(0, %(max_elements)d).map((el, index) => {
    const ref = index + 1;
    const tag = (el.tagName || '').toLowerCase();
    el.setAttribute('data-ta-ref', String(ref));
    return {
      ref,
      tag,
      role: el.getAttribute('role') || (toggles.has(el) ? toggles.get(el).type : (custom.has(el) ? 'clickable' : '')),
      name: nameOf(el),
      href: tag === 'a' ? hrefOf(el) : null,
      placeholder: el.getAttribute('placeholder'),
      input_type: tag === 'input' ? (el.getAttribute('type') || 'text') : null,
      value: (tag === 'input' || tag === 'textarea' || tag === 'select') ? String(el.value || '').slice(0, 120) : null,
      download: el.hasAttribute('download'),
      hint: hintOf(el)
    };
  });
  const text = String(document.body.innerText || '')
    .replace(/[ \\t\\u00a0]+/g, ' ')
    .replace(/\\s*\\n\\s*/g, '\\n')
    .trim()
    .slice(0, %(max_text)d);
  return {
    url: location.href,
    title: document.title || '',
    text,
    elements,
    scroll: {
      y: Math.round(window.scrollY),
      height: Math.round(document.documentElement.scrollHeight),
      viewport: Math.round(window.innerHeight)
    }
  };
}""" % {"max_elements": MAX_ELEMENTS, "max_text": MAX_TEXT}


def _retryable(exc: Exception) -> bool:
    message = str(exc).lower()
    return "execution context was destroyed" in message or "navigation" in message


async def get_page_state(runtime: BrowserRuntime) -> Observation:
    page = runtime.current_page
    raw: dict | None = None
    last_error: Exception | None = None
    for _ in range(8):
        try:
            loaded = await page.evaluate(_SNAPSHOT_JS)
        except Exception as exc:
            if not _retryable(exc):
                raise
            last_error = exc
            await page.wait_for_timeout(400)
            continue
        if isinstance(loaded, dict) and not loaded.get("pending"):
            raw = loaded
            break
        await page.wait_for_timeout(400)
    if raw is None:
        if last_error is not None:
            raise last_error
        raw = {"url": page.url}
    obs = Observation(
        url=str(raw.get("url") or page.url),
        title=str(raw.get("title") or ""),
        text=str(raw.get("text") or ""),
        elements=[Element.model_validate(item) for item in raw.get("elements") or []],
        scroll={k: int(v) for k, v in (raw.get("scroll") or {}).items() if isinstance(v, (int, float))},
    )
    if CAPTCHA_RE.search(f"{obs.title} {obs.text[:3000]}"):
        obs.page_kind = "captcha"
    obs.signature = hashlib.sha256(f"{obs.url}|{obs.text}".encode("utf-8")).hexdigest()[:16]
    return obs


def page_changed(before: Observation, after: Observation) -> bool:
    return before.signature != after.signature


async def take_screenshot(runtime: BrowserRuntime) -> bytes:
    return await runtime.current_page.screenshot(type="png")
