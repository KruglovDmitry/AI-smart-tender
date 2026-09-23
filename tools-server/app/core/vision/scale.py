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


def clamp_xy(x: int, y: int, size: tuple[int, int]) -> tuple[int, int]:
    """Clamp to [0, w-1] × [0, h-1] for any size box (PNG or viewport)."""
    w, h = int(size[0]), int(size[1])
    return (
        max(0, min(int(x), max(0, w - 1))),
        max(0, min(int(y), max(0, h - 1))),
    )


def clamp_css(x: int, y: int, viewport: tuple[int, int]) -> tuple[int, int]:
    return clamp_xy(x, y, viewport)


def norm1000_to_image_px(
    x: float,
    y: float,
    image_size: tuple[int, int],
) -> tuple[int, int]:
    """Map normalized 0..1000 coords onto PNG pixel space."""
    iw, ih = int(image_size[0]), int(image_size[1])
    return (
        int(round(float(x) * iw / 1000.0)),
        int(round(float(y) * ih / 1000.0)),
    )


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


def remap_candidates_to_css(
    candidates: list[Any],
    *,
    image_b64: str,
    viewport: tuple[int, int],
    image_size: tuple[int, int] | None = None,
) -> list[Any]:
    """
    If PNG pixel size differs from CSS viewport, rescale each candidate's x,y.
    Mutates candidate objects that have .x/.y attributes; also accepts dicts.
    Does NOT clamp — caller clamps to viewport after remap.
    """
    img = image_size or png_pixel_size(image_b64)
    if img is None:
        return candidates
    iw, ih = img
    vw, vh = int(viewport[0]), int(viewport[1])
    if iw == vw and ih == vh:
        return candidates
    out = []
    for c in candidates:
        if hasattr(c, "x") and hasattr(c, "y"):
            # image_to_css clamps; we want unclamped scale then final clamp outside —
            # use raw scale here to avoid double semantics
            nx = int(round(float(c.x) * (vw / iw))) if iw else int(c.x)
            ny = int(round(float(c.y) * (vh / ih))) if ih else int(c.y)
            c.x, c.y = nx, ny
            out.append(c)
        elif isinstance(c, dict):
            x = c.get("x") or 0
            y = c.get("y") or 0
            nx = int(round(float(x) * (vw / iw))) if iw else int(x)
            ny = int(round(float(y) * (vh / ih))) if ih else int(y)
            c = {**c, "x": nx, "y": ny}
            out.append(c)
        else:
            out.append(c)
    return out