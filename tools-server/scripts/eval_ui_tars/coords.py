"""
Mode A metrics: map UI-TARS response → CSS point, score vs expected_bbox.

Uses core.vision.ui_tars.parser (UI-TARS / Qwen2.5-VL coordinate path).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# tools-server on path so we share the production parser
_TOOLS = Path(__file__).resolve().parents[2]
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from app.core.vision.ui_tars.parser import parse_to_css  # noqa: E402


def bbox_dict_to_xyxy(bbox: dict[str, Any] | None) -> tuple[int, int, int, int] | None:
    """{x,y,width,height} → (x0,y0,x1,y1)."""
    if not bbox:
        return None
    try:
        x = int(bbox["x"])
        y = int(bbox["y"])
        w = int(bbox["width"])
        h = int(bbox["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, x + w, y + h


def xyxy_to_bbox_dict(box: tuple[int, int, int, int]) -> dict[str, int]:
    x0, y0, x1, y1 = box
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def hit_bbox(x: int, y: int, bbox: tuple[int, int, int, int], *, pad: int = 0) -> bool:
    x0, y0, x1, y1 = bbox
    return (x0 - pad) <= x <= (x1 + pad) and (y0 - pad) <= y <= (y1 + pad)


def dist_to_center(x: int, y: int, bbox: tuple[int, int, int, int]) -> float:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5


def bbox_diagonal(bbox: tuple[int, int, int, int]) -> float:
    w = max(1, bbox[2] - bbox[0])
    h = max(1, bbox[3] - bbox[1])
    return (w * w + h * h) ** 0.5


def score_prediction(
    response_text: str,
    bbox: tuple[int, int, int, int] | None,
    *,
    viewport: tuple[int, int],
    image_size: tuple[int, int],
    expect_not_found: bool = False,
    pad: int = 0,
) -> dict[str, Any]:
    """
    Mode A scoring.

    - hit_bbox: predicted point ∈ expected bbox
    - distance_px: to bbox center
    - distance_norm: distance / diagonal(bbox)
    - false_positive: expect_not_found and model emitted click
    """
    parsed = parse_to_css(
        response_text,
        image_width=image_size[0],
        image_height=image_size[1],
        viewport=viewport,
    )
    action = parsed.get("action") or "none"
    pred = None
    if action == "click" and parsed.get("x") is not None:
        pred = (int(parsed["x"]), int(parsed["y"]))

    out: dict[str, Any] = {
        "action": action,
        "pred_css": list(pred) if pred else None,
        "raw_x": parsed.get("raw_x"),
        "raw_y": parsed.get("raw_y"),
        "parse_note": parsed.get("note"),
        "hit_bbox": None,
        "distance_px": None,
        "distance_norm": None,
        "false_positive": False,
        "success": False,
    }

    if expect_not_found:
        # Correct: not_found or no click coords. Wrong: click → false_positive.
        if action == "click" and pred is not None:
            out["false_positive"] = True
            out["success"] = False
            out["hit_bbox"] = False
        else:
            out["success"] = action == "not_found" or pred is None
            out["hit_bbox"] = False
        return out

    if bbox is None or pred is None:
        out["success"] = False
        out["hit_bbox"] = False
        return out

    x, y = pred
    hit = hit_bbox(x, y, bbox, pad=pad)
    dist = dist_to_center(x, y, bbox)
    diag = bbox_diagonal(bbox)
    out["hit_bbox"] = hit
    out["distance_px"] = round(dist, 1)
    out["distance_norm"] = round(dist / diag, 4)
    out["success"] = bool(hit)
    out["expected_center"] = [
        (bbox[0] + bbox[2]) // 2,
        (bbox[1] + bbox[3]) // 2,
    ]
    out["bbox"] = list(bbox)
    return out


# Back-compat for any leftover callers
def parse_click_xy(text: str) -> tuple[float, float] | None:
    from app.core.vision.ui_tars.parser import extract_raw_xy

    return extract_raw_xy(text)
