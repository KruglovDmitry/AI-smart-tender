"""Unit tests for UI-TARS Mode A parser / registry."""

from __future__ import annotations

from app.core.vision import get_vision_backend
from app.core.vision.ui_tars.parser import (
    parse_grounding_response,
    parse_to_css,
    smart_resize,
)


def test_smart_resize_factor_28() -> None:
    h, w = smart_resize(900, 1280)
    assert h % 28 == 0 and w % 28 == 0


def test_parse_absolute_image_px() -> None:
    text = "Action: click(start_box='(640, 450)')"
    p = parse_grounding_response(text, image_width=1280, image_height=900)
    assert p.action == "click"
    assert p.x_image == 640 and p.y_image == 450
    assert p.note == "absolute_image_px"


def test_parse_not_found() -> None:
    p = parse_grounding_response(
        "Action: not_found()",
        image_width=1280,
        image_height=900,
    )
    assert p.action == "not_found"


def test_parse_to_css_identity_viewport() -> None:
    out = parse_to_css(
        "Action: click(start_box='(100, 200)')",
        image_width=1280,
        image_height=900,
        viewport=(1280, 900),
    )
    assert out["action"] == "click"
    assert out["x"] == 100 and out["y"] == 200


def test_registry_has_ui_tars() -> None:
    b = get_vision_backend("ui_tars")
    assert b.name == "ui_tars"
