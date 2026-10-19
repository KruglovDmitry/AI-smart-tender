"""Точка входа: dashboard как Windows exe (PyInstaller onedir)."""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def _ensure_app() -> None:
    import importlib.util

    if importlib.util.find_spec("app.main") is not None:
        return
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "dashboard",
        Path(getattr(sys, "_MEIPASS", here)),
        Path(sys.executable).resolve().parent / "_internal",
    ]
    for c in candidates:
        if (c / "app" / "main.py").is_file():
            sys.path.insert(0, str(c))
            return
    raise RuntimeError("Пакет dashboard (app.main) не найден")


def main() -> None:
    multiprocessing.freeze_support()
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    data = Path(os.environ.get("DATA_ROOT") or Path(local) / "AISmartTender" / "data")
    data.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_ROOT"] = str(data.resolve())
    os.environ.setdefault("HOST", "127.0.0.1")
    os.environ.setdefault("PORT", "18100")

    _ensure_app()

    host = os.environ["HOST"]
    port = int(os.environ["PORT"])
    print(f"Dashboard data: {os.environ['DATA_ROOT']}")
    print(f"Dashboard DB:   {os.environ.get('TENDER_DB_URL', '(none)')}")
    print(f"Dashboard URL:  http://{host}:{port}")

    import uvicorn

    loop = "none" if sys.platform == "win32" else "auto"
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=1,
        loop=loop,
    )


if __name__ == "__main__":
    main()
