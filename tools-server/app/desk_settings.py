"""Поля общего файла дашборда (tenders/_agent/settings.json).

Пока файла нет, значение берётся из окружения. Когда файл есть, отсутствующее поле
не подменяется окружением: пустой секрет остаётся пустым.
"""

from __future__ import annotations

import os

def text(name: str, default: str = "", env: tuple[str, ...] = ()) -> str:
    from . import model_config

    data = model_config._stored()
    if data is not None:
        if name not in data:
            return default
        raw = data.get(name)
        if raw is None:
            return default
        return str(raw).strip() or default
    for key in env:
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return default


def integer(name: str, default: int, env: tuple[str, ...] = ()) -> int:
    raw = text(name, str(default), env)
    try:
        return int(raw)
    except ValueError:
        return default
