"""OCR страниц PDF: RapidOCR (ONNX, без torch) с кириллической моделью PP-OCRv5.

Нужен для страниц, где шрифт вшит без ToUnicode, и для сканов. Около 10 с на страницу на CPU.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from . import pdf

log = logging.getLogger(__name__)

OCR_DPI = int(os.getenv("TOOLS_OCR_DPI", "200"))
_engine = None
_engine_lock = threading.Lock()
_unavailable: str | None = None


def _get_engine():
    global _engine, _unavailable
    with _engine_lock:
        if _engine is None and _unavailable is None:
            try:
                from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

                params = {
                    "Rec.lang_type": LangRec.CYRILLIC,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.model_type": ModelType.MOBILE,
                }
                # В сборке модели лежат рядом с программой, без скачивания.
                model_dir = os.getenv("TOOLS_OCR_MODEL_DIR")
                if model_dir:
                    params["Rec.model_path"] = str(Path(model_dir) / "cyrillic_PP-OCRv5_rec_mobile.onnx")
                _engine = RapidOCR(params=params)
            except Exception as e:
                _unavailable = f"{type(e).__name__}: {e}"
                log.warning("OCR unavailable: %s", _unavailable)
        return _engine


def available() -> bool:
    return _get_engine() is not None


def page_text(path: Path, n: int) -> str:
    engine = _get_engine()
    if engine is None:
        raise RuntimeError(f"OCR unavailable: {_unavailable}")
    png = pdf.render_png(path, n, dpi=OCR_DPI)
    res = engine(png)
    if not res or res.txts is None:
        return ""
    return _join_lines(res.boxes, res.txts)


def _join_lines(boxes, txts) -> str:
    """Склеить фрагменты в строки по вертикали, слева направо внутри строки."""
    items = []
    for box, txt in zip(boxes, txts):
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append((sum(ys) / 4, min(xs), max(ys) - min(ys), txt))
    items.sort()
    lines: list[list[tuple]] = []
    for it in items:
        if lines and abs(it[0] - lines[-1][0][0]) < max(6.0, it[2] * 0.5):
            lines[-1].append(it)
        else:
            lines.append([it])
    return "\n".join(" ".join(t[3] for t in sorted(line, key=lambda x: x[1])) for line in lines)
