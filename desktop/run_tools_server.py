"""Точка входа: tools-server как Windows exe (PyInstaller onedir)."""

from __future__ import annotations

import importlib.util
import multiprocessing
import os
import sys
from pathlib import Path


def _data_root() -> Path:
    raw = os.environ.get("DATA_ROOT")
    if raw:
        path = Path(raw).expanduser()
    else:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(local) / "AISmartTender" / "data"
    path.mkdir(parents=True, exist_ok=True)
    for sub in ("tenders", "catalogs", "uploads", "exports"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _ensure_app_importable() -> None:
    """В onedir app может быть в PYZ или как папка в _MEIPASS / tools-server/."""
    if importlib.util.find_spec("app.main") is not None:
        return

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend(
            [
                meipass,
                meipass / "app",
                Path(sys.executable).resolve().parent,
                Path(sys.executable).resolve().parent / "_internal",
            ]
        )
    else:
        here = Path(__file__).resolve().parent
        repo = here.parent
        candidates.extend([repo / "tools-server", here])

    for candidate in candidates:
        # candidate is either .../app parent or .../app itself
        if (candidate / "main.py").is_file() and candidate.name == "app":
            sys.path.insert(0, str(candidate.parent))
            if importlib.util.find_spec("app.main") is not None:
                return
        if (candidate / "app" / "main.py").is_file():
            sys.path.insert(0, str(candidate))
            if importlib.util.find_spec("app.main") is not None:
                return

    raise RuntimeError(
        "Пакет tools-server (app.main) не найден. "
        f"Искали: {', '.join(str(c) for c in candidates)}"
    )


def main() -> None:
    multiprocessing.freeze_support()

    # DATA_ROOT / порты — до любого import app.*
    data = _data_root()
    os.environ["DATA_ROOT"] = str(data)
    os.environ.setdefault("TOOLS_HOST", "127.0.0.1")
    os.environ.setdefault("TOOLS_PORT", "18000")
    os.environ.setdefault(
        "TOOLS_PUBLIC_BASE_URL",
        f"http://127.0.0.1:{os.environ['TOOLS_PORT']}",
    )

    _ensure_app_importable()

    from app import config  # noqa: WPS433 — после env и sys.path

    host = os.environ.get("TOOLS_HOST", "127.0.0.1")
    port = int(os.environ.get("TOOLS_PORT", "18000"))

    print(f"Tools Server data: {config.DATA_ROOT}")
    print(f"Tools Server URL:  http://{host}:{port}")
    print(f"Health:            http://{host}:{port}/health")

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
