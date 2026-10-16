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
MAX_ELEMENTS = 300
MAX_TEXT = 40_000

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
  const nodes = [...document.querySelectorAll(selector)]
    .filter(el => visible(el) && !(el.parentElement && el.parentElement.closest('a[href], button')));
  const elements = nodes.slice(0, %(max_elements)d).map((el, index) => {
    const ref = index + 1;
    const tag = (el.tagName || '').toLowerCase();
    el.setAttribute('data-ta-ref', String(ref));
    return {
      ref,
      tag,
      role: el.getAttribute('role') || '',
      name: nameOf(el),
      href: tag === 'a' ? hrefOf(el) : null,
      placeholder: el.getAttribute('placeholder'),
      input_type: tag === 'input' ? (el.getAttribute('type') || 'text') : null,
      value: (tag === 'input' || tag === 'textarea' || tag === 'select') ? String(el.value || '').slice(0, 120) : null,
      download: el.hasAttribute('download')
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
