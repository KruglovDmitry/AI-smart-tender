"""Smoke-test UI-TARS-1.5 endpoint (open, no auth)."""
from __future__ import annotations

import base64
import json
import re
import struct
import time
import zlib
from pathlib import Path

import httpx

BASE = "http://10.127.0.41:8000"
MODEL = "ui-tars"

GROUNDING = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format

Action: ...


## Action Space
click(point='<point>x1 y1</point>')

## User Instruction
{instruction}
"""


def _png_solid(w: int, h: int, rgb=(0, 0, 200)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    r, g, b = rgb
    raw = b""
    for _ in range(h):
        raw += b"\x00" + bytes([r, g, b]) * w
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _png_button(w: int = 400, h: int = 200) -> tuple[bytes, tuple[int, int, int, int]]:
    """Gray bg + red button rect; return png bytes and button bbox (x0,y0,x1,y1)."""
    from array import array

    # build raw RGB rows
    bg = (240, 240, 240)
    btn = (200, 40, 40)
    x0, y0, x1, y1 = 120, 70, 280, 130  # expected click center ~ (200, 100)

    rows = []
    for y in range(h):
        row = bytearray()
        row.append(0)
        for x in range(w):
            if x0 <= x < x1 and y0 <= y < y1:
                row.extend(btn)
            else:
                row.extend(bg)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    return png, (x0, y0, x1, y1)


def parse_point(text: str) -> tuple[int, int] | None:
    # click(point=' x y ') or start_box='(x,y)' or <point>x y</point>
    patterns = [
        r"point\s*=\s*'?\s*<?point>?\s*(\d+)\s+(\d+)",
        r"start_box\s*=\s*'?\(\s*(\d+)\s*,\s*(\d+)\s*\)",
        r"<point>\s*(\d+)\s+(\d+)\s*</point>",
        r"click\([^\d]*(\d+)\s*,\s*(\d+)",
        r"\((\d+)\s*,\s*(\d+)\)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def call(instruction: str, png: bytes, *, max_tokens: int = 256) -> dict:
    b64 = base64.b64encode(png).decode("ascii")
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": GROUNDING.format(instruction=instruction)},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    t0 = time.perf_counter()
    r = httpx.post(f"{BASE}/v1/chat/completions", json=payload, timeout=180.0)
    ms = int((time.perf_counter() - t0) * 1000)
    out = {
        "http_status": r.status_code,
        "latency_ms": ms,
        "raw_http": r.text[:2000],
    }
    if r.status_code >= 400:
        return out
    data = r.json()
    content = (
        ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    )
    out["content"] = content
    out["point_raw"] = parse_point(content)
    return out


def main() -> None:
    print("=== 1) models ===")
    m = httpx.get(f"{BASE}/v1/models", timeout=15.0)
    print(m.status_code, m.text[:400])

    print("\n=== 2) solid blue smoke ===")
    r1 = call("Click the blue rectangle in the center of the image.", _png_solid(128, 64))
    print(json.dumps({k: r1[k] for k in r1 if k != "raw_http"}, ensure_ascii=False, indent=2))
    if r1.get("http_status", 0) >= 400:
        print("HTTP body:", r1.get("raw_http"))

    print("\n=== 3) red button grounding ===")
    png, bbox = _png_button()
    cx, cy = (bbox[0] + bbox[1]) // 2, (bbox[2] + bbox[3]) // 2  # wrong - fix
    cx = (bbox[0] + bbox[2]) // 2
    cy = (bbox[1] + bbox[3]) // 2
    r2 = call("Click the red button.", png)
    print(json.dumps({k: r2[k] for k in r2 if k != "raw_http"}, ensure_ascii=False, indent=2))
    print("expected_center_css", (cx, cy), "bbox", bbox)
    pt = r2.get("point_raw")
    if pt:
        # UI-TARS often returns 0..1000 normalized
        px, py = pt
        if max(px, py) > 400:  # likely normalized
            abs_x = int(px / 1000 * 400)
            abs_y = int(py / 1000 * 200)
            print("interpreted_as_norm1000 ->", (abs_x, abs_y))
            hit = bbox[0] <= abs_x < bbox[2] and bbox[1] <= abs_y < bbox[3]
        else:
            abs_x, abs_y = px, py
            print("interpreted_as_pixels ->", (abs_x, abs_y))
            hit = bbox[0] <= abs_x < bbox[2] and bbox[1] <= abs_y < bbox[3]
        dist = ((abs_x - cx) ** 2 + (abs_y - cy) ** 2) ** 0.5
        print("hit_bbox", hit, "dist_px", round(dist, 1), "latency_ms", r2["latency_ms"])

    out_dir = Path("data/_ui_tars_smoke")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "button.png").write_bytes(png)
    (out_dir / "smoke.json").write_text(
        json.dumps({"blue": r1, "button": r2, "bbox": bbox, "expected": [cx, cy]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\nWrote", out_dir)


if __name__ == "__main__":
    main()
