"""Map image-pixel coordinates to CSS viewport pixels (device scale factor)."""

from __future__ import annotations

import base64
import struct
from typing import Any


def png_pixel_size(image_b64: str) -> tuple[int, int] | None:
    """Read IHDR width/height from a base64 PNG. Returns None if not a PNG."""
    if not image_b64:
        return None
    try:
        raw = base64.b64decode(image_b64, validate=False)
    except Exception:
        return None
    # PNG signature (8) + IHDR length(4) + type(4) + width(4) + height(4)
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if raw[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", raw[16:24])
    if w <= 0 or h <= 0:
        return None
    return int(w), int(h)


def image_to_css(
    x: float,
    y: float,
    *,
    image_size: tuple[int, int],
    viewport: tuple[int, int],
) -> tuple[int, int]:
    """
    Convert coordinates from PNG pixel space to CSS viewport pixels.
    If sizes match (scale=1), returns rounded ints clamped to viewport.
    """
    iw, ih = int(image_size[0]), int(image_size[1])
    vw, vh = int(viewport[0]), int(viewport[1])
    if iw <= 0 or ih <= 0 or vw <= 0 or vh <= 0:
        return int(x), int(y)
    sx = vw / iw
    sy = vh / ih
    cx = int(round(float(x) * sx))
    cy = int(round(float(y) * sy))
    return clamp_css(cx, cy, viewport)


def clamp_css(x: int, y: int, viewport: tuple[int, int]) -> tuple[int, int]:
    vw, vh = int(viewport[0]), int(viewport[1])
    return (
        max(0, min(int(x), max(0, vw - 1))),
        max(0, min(int(y), max(0, vh - 1))),
    )


def remap_candidates_to_css(
    candidates: list[Any],
    *,
    image_b64: str,
    viewport: tuple[int, int],
) -> list[Any]:
    """
    If PNG pixel size differs from CSS viewport, rescale each candidate's x,y.
    Mutates candidate objects that have .x/.y attributes; also accepts dicts.
    """
    img = png_pixel_size(image_b64)
    if img is None:
        return candidates
    iw, ih = img
    vw, vh = int(viewport[0]), int(viewport[1])
    if iw == vw and ih == vh:
        return candidates
    out = []
    for c in candidates:
        if hasattr(c, "x") and hasattr(c, "y"):
            nx, ny = image_to_css(c.x, c.y, image_size=img, viewport=viewport)
            c.x, c.y = nx, ny
            out.append(c)
        elif isinstance(c, dict):
            nx, ny = image_to_css(
                c.get("x") or 0,
                c.get("y") or 0,
                image_size=img,
                viewport=viewport,
            )
            c = {**c, "x": nx, "y": ny}
            out.append(c)
        else:
            out.append(c)
    return out
