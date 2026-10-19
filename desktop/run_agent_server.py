"""Точка входа: tender-agent как Windows exe (PyInstaller onedir)."""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def _data_dir() -> Path:
    raw = os.environ.get("TENDER_AGENT_DATA_DIR")
    if raw:
        path = Path(raw).expanduser()
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(local) / "AISmartTender" / "data" / "tenders"
    path.mkdir(parents=True, exist_ok=True)
    (path / "_agent").mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _ensure_package() -> None:
    import importlib.util

    if importlib.util.find_spec("tender_agent.server") is not None:
        return
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "tender-agent" / "src",
        Path(getattr(sys, "_MEIPASS", here)),
        Path(sys.executable).resolve().parent / "_internal",
    ]
    for c in candidates:
        if (c / "tender_agent" / "server.py").is_file():
            sys.path.insert(0, str(c))
            return
    raise RuntimeError("Пакет tender_agent не найден")


def main() -> None:
    multiprocessing.freeze_support()
    data = _data_dir()
    os.environ["TENDER_AGENT_DATA_DIR"] = str(data)
    os.environ.setdefault("TENDER_AGENT_HEADLESS", "false")
    os.environ.setdefault("HOST", "127.0.0.1")
    os.environ.setdefault("PORT", "18010")

    # Playwright browsers рядом с onedir
    if getattr(sys, "frozen", False):
        browsers = Path(sys.executable).resolve().parent / "ms-playwright"
        if browsers.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))

    _ensure_package()

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "18010"))
    print(f"Tender Agent data: {data}")
    print(f"Tender Agent URL:  http://{host}:{port}")
    print(f"Headless:          {os.environ.get('TENDER_AGENT_HEADLESS')}")
    print(f"Playwright path:   {os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '(default)')}")

    import uvicorn

    loop = "none" if sys.platform == "win32" else "auto"
    uvicorn.run(
        "tender_agent.server:app",
        host=host,
        port=port,
        workers=1,
        loop=loop,
    )


if __name__ == "__main__":
    main()
