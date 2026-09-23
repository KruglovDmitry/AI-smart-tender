"""
Coordinate / action parser adapted from bytedance/UI-TARS action_parser
(Apache-2.0). Focused on Mode A grounding: click / not_found → CSS pixels.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

IMAGE_FACTOR = 28
MIN_PIXELS = 100 * 28 * 28
MAX_PIXELS = 16384 * 28 * 28
MAX_RATIO = 200

_NOT_FOUND_RE = re.compile(
    r"Action:\s*not_found\s*\(|\bnot_found\s*\(|element not found",
    re.I,
)
_POINT_RE = [
    re.compile(
        r"start_box\s*=\s*['\"]?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)",
        re.I,
    ),
    re.compile(
        r"point\s*=\s*['\"]?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\)",
        re.I,
    ),
    re.compile(
        r"start_box\s*=\s*['\"]?\s*([0-9.]+)\s+([0-9.]+)",
        re.I,
    ),
    re.compile(
        r"point\s*=\s*['\"]?\s*([0-9.]+)\s+([0-9.]+)",
        re.I,
    ),
    re.compile(r"\((\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\)"),
]


@dataclass
class ParsedGrounding:
    action: str  # click | not_found | none
    x_image: int | None = None  # pixels in original image space
    y_image: int | None = None
    raw_x: float | None = None
    raw_y: float | None = None
    model_type: str = "qwen25vl"
    note: str = ""


def round_by_factor(number: int, factor: int) -> int:
    return round(number / factor) * factor


def ceil_by_factor(number: int, factor: int) -> int:
    return math.ceil(number / factor) * factor


def floor_by_factor(number: int, factor: int) -> int:
    return math.floor(number / factor) * factor


def smart_resize(
    height: int,
    width: int,
    factor: int = IMAGE_FACTOR,
    min_pixels: int = MIN_PIXELS,
    max_pixels: int = MAX_PIXELS,
) -> tuple[int, int]:
    """UI-TARS / Qwen2.5-VL image preprocess size (h, w)."""
    if max(height, width) / max(min(height, width), 1) > MAX_RATIO:
        raise ValueError("absolute aspect ratio too large")
    h_bar = max(factor, round_by_factor(height, factor))
    w_bar = max(factor, round_by_factor(width, factor))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(int(height / beta), factor)
        w_bar = floor_by_factor(int(width / beta), factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(int(height * beta), factor)
        w_bar = ceil_by_factor(int(width * beta), factor)
    return h_bar, w_bar


def extract_raw_xy(text: str) -> tuple[float, float] | None:
    if not text:
        return None
    for pat in _POINT_RE:
        m = pat.search(text)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def qwen25vl_to_image_pixels(
    raw_x: float,
    raw_y: float,
    *,
    image_width: int,
    image_height: int,
) -> tuple[int, int]:
    """
    Official UI-TARS path for model_type=qwen25vl:
    model emits absolute coords in smart_resized space → map to original image.
    x_img = raw_x / smart_w * image_width
    """
    smart_h, smart_w = smart_resize(image_height, image_width)
    x = int(round(raw_x / max(smart_w, 1) * image_width))
    y = int(round(raw_y / max(smart_h, 1) * image_height))
    return x, y


def parse_grounding_response(
    text: str,
    *,
    image_width: int,
    image_height: int,
    model_type: str = "qwen25vl",
    prefer_absolute_image_px: bool = True,
) -> ParsedGrounding:
    """
    Parse UI-TARS Action text → image-pixel click or not_found.

    prefer_absolute_image_px: if raw coords already lie inside the original
    image bounds, treat them as image pixels (observed on UI-TARS-1.5-7B
    OpenAI-compatible serves). Otherwise apply qwen25vl smart_resize mapping.
    """
    text = (text or "").strip()
    if _NOT_FOUND_RE.search(text):
        return ParsedGrounding(action="not_found", note="model returned not_found")

    raw = extract_raw_xy(text)
    if raw is None:
        return ParsedGrounding(action="none", note="no coordinates in response")

    rx, ry = raw
    iw, ih = int(image_width), int(image_height)

    if prefer_absolute_image_px and 0 <= rx <= iw and 0 <= ry <= ih:
        xi, yi = int(round(rx)), int(round(ry))
        note = "absolute_image_px"
    elif model_type == "qwen25vl":
        xi, yi = qwen25vl_to_image_pixels(rx, ry, image_width=iw, image_height=ih)
        note = "qwen25vl_smart_resize"
    else:
        # relative 0..1000 grid
        xi = int(round(rx / 1000.0 * iw))
        yi = int(round(ry / 1000.0 * ih))
        note = "norm1000"

    xi = max(0, min(xi, max(0, iw - 1)))
    yi = max(0, min(yi, max(0, ih - 1)))
    return ParsedGrounding(
        action="click",
        x_image=xi,
        y_image=yi,
        raw_x=rx,
        raw_y=ry,
        model_type=model_type,
        note=note,
    )


def to_css_viewport(
    x_image: int,
    y_image: int,
    *,
    image_size: tuple[int, int],
    viewport: tuple[int, int],
) -> tuple[int, int]:
    iw, ih = image_size
    vw, vh = viewport
    if iw <= 0 or ih <= 0:
        return x_image, y_image
    cx = int(round(x_image * (vw / iw)))
    cy = int(round(y_image * (vh / ih)))
    return (
        max(0, min(cx, max(0, vw - 1))),
        max(0, min(cy, max(0, vh - 1))),
    )


def parse_to_css(
    text: str,
    *,
    image_width: int,
    image_height: int,
    viewport: tuple[int, int],
    model_type: str = "qwen25vl",
) -> dict[str, Any]:
    """Convenience: response → {action, x, y, ...} in CSS viewport pixels."""
    parsed = parse_grounding_response(
        text,
        image_width=image_width,
        image_height=image_height,
        model_type=model_type,
    )
    if parsed.action != "click" or parsed.x_image is None or parsed.y_image is None:
        return {
            "action": parsed.action,
            "x": None,
            "y": None,
            "x_image": parsed.x_image,
            "y_image": parsed.y_image,
            "raw_x": parsed.raw_x,
            "raw_y": parsed.raw_y,
            "note": parsed.note,
        }
    x, y = to_css_viewport(
        parsed.x_image,
        parsed.y_image,
        image_size=(image_width, image_height),
        viewport=viewport,
    )
    return {
        "action": "click",
        "x": x,
        "y": y,
        "x_image": parsed.x_image,
        "y_image": parsed.y_image,
        "raw_x": parsed.raw_x,
        "raw_y": parsed.raw_y,
        "note": parsed.note,
    }
