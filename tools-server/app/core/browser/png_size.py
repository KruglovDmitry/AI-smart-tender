"""PNG IHDR size helper (used by screenshot meta)."""

from __future__ import annotations

import base64
import struct


def png_pixel_size(image_b64: str) -> tuple[int, int] | None:
    """Read IHDR width/height from a base64 PNG. Returns None if not a PNG."""
    if not image_b64:
        return None
    try:
        raw = base64.b64decode(image_b64, validate=False)
    except Exception:
        return None
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if raw[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", raw[16:24])
    if w <= 0 or h <= 0:
        return None
    return int(w), int(h)
