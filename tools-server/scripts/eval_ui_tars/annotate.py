"""Draw expected bbox + predicted crosshair on a PNG screenshot."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any


def annotate_screenshot(
    png: bytes,
    *,
    bbox: tuple[int, int, int, int] | None,
    pred: tuple[int, int] | None,
    out_path: Path,
    label: str = "",
) -> Path:
    """
    expected bbox = green rectangle; predicted point = red X + circle.
    Uses Pillow if available, else raw PNG passthrough (no annotation).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        out_path.write_bytes(png)
        return out_path

    img = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(img)

    if bbox is not None:
        x0, y0, x1, y1 = bbox
        for t in range(3):
            draw.rectangle(
                [x0 - t, y0 - t, x1 + t, y1 + t],
                outline=(0, 180, 0),
            )
        if label:
            draw.text((x0, max(0, y0 - 14)), label[:40], fill=(0, 140, 0))

    if pred is not None:
        x, y = pred
        r = 10
        draw.ellipse([x - r, y - r, x + r, y + r], outline=(220, 30, 30), width=2)
        draw.line([x - 14, y, x + 14, y], fill=(220, 30, 30), width=2)
        draw.line([x, y - 14, x, y + 14], fill=(220, 30, 30), width=2)

    img.save(out_path, format="PNG")
    return out_path


def annotation_meta(
    bbox: tuple[int, int, int, int] | None,
    pred: tuple[int, int] | None,
) -> dict[str, Any]:
    return {
        "expected_bbox_color": "green",
        "predicted_point_marker": "red_cross",
        "has_bbox": bbox is not None,
        "has_pred": pred is not None,
    }
