"""Подключение лицензии к сервису FastAPI: проверка модуля на каждый запрос и ручки /license."""

from __future__ import annotations

import html

from .core import install_key, required, status

OPEN_PATHS = ("/health", "/license")


def install(app, module: str) -> None:
    """Без лицензии на ``module`` сервис отвечает 403 на всё, кроме проверки здоровья и /license.
    В Docker и при разработке (не собранная программа) проверки нет, если не задан AST_LICENSE_REQUIRED=1."""
    from fastapi import Body, HTTPException  # noqa: PLC0415
    from fastapi.responses import HTMLResponse, JSONResponse  # noqa: PLC0415

    @app.get("/license", include_in_schema=False)
    def license_status():
        current = status()
        return {**current.to_dict(), "module": module, "required": required(), "allowed": current.allows(module)}

    @app.post("/license/key", include_in_schema=False)
    def license_key(key: str = Body(..., embed=True)):
        try:
            current = install_key(key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**current.to_dict(), "module": module, "allowed": current.allows(module)}

    if not required():
        return

    @app.middleware("http")
    async def license_guard(request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in OPEN_PATHS):
            return await call_next(request)
        current = status()
        if current.allows(module):
            return await call_next(request)
        detail = current.message if not current.active else f"Модуль «{module}» не входит в лицензию."
        if "text/html" in request.headers.get("accept", ""):
            # Страница (дашборд в окне программы) — понятный текст вместо JSON.
            return HTMLResponse(status_code=403, content=_BLOCKED_PAGE.format(detail=html.escape(detail)))
        return JSONResponse(status_code=403, content={"detail": f"Нет лицензии: {detail}", "license": current.state})


_BLOCKED_PAGE = """<!doctype html><meta charset="utf-8"><title>Нет лицензии</title>
<body style="font:15px Segoe UI,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;
margin:0;color:#777;text-align:center"><div><b>Нет лицензии</b><br><br>{detail}<br><br>
Ключ вводится в меню «Справка → Лицензия…».</div></body>"""
