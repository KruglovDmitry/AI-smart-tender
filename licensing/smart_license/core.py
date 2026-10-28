"""Лицензия настольной сборки: подписанный ключ на модули или пробный период.

Ключ — строка ``AST1.<данные>.<подпись>``: данные (кому, код компьютера, модули, срок) подписаны
секретным ключом Ed25519, который есть только у выдающего (licensing/keygen.py). Здесь — открытый.

Пробный период: 14 дней на все модули с первого запуска на этом компьютере. Дата первого запуска
и последнее увиденное время лежат в файле и в реестре, с HMAC от подделки; берётся самое раннее
начало и самое позднее время. Часы, переведённые назад больше чем на час, блокируют работу.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from . import ed25519

PUBLIC_KEY = bytes.fromhex("de865188d9412932fee422b72c16a0606ea408f784c10b8d705726943eddc138")
MODULES = ("search", "documents", "dashboard", "analysis", "criteria")
TRIAL_DAYS = 14
CLOCK_SLACK = 3600  # на сколько секунд часы могут отстать от последнего увиденного времени
CACHE_SECONDS = 60
_STATE_SECRET = b"ast-state:7f3c9e1d5b2a4806"
_REG_PATH = r"Software\AISmartTender"
_REG_VALUE = "s"
USE_REGISTRY = True  # тесты выключают, чтобы не писать в реестр


@dataclass
class Status:
    state: str  # licensed | trial | expired | invalid | clock
    modules: list[str] = field(default_factory=list)
    machine_code: str = ""
    customer: str = ""
    expires: str | None = None  # дата окончания (ISO) или None — бессрочно
    days_left: int | None = None
    message: str = ""

    @property
    def active(self) -> bool:
        return self.state in ("licensed", "trial")

    def allows(self, module: str) -> bool:
        return self.active and module in self.modules

    def to_dict(self) -> dict:
        return {**asdict(self), "active": self.active}


def required() -> bool:
    """В собранной программе — всегда; в Docker и при разработке — только если включено явно."""
    return bool(getattr(sys, "frozen", False)) or os.environ.get("AST_LICENSE_REQUIRED") == "1"


def app_dir() -> Path:
    root = os.environ.get("AST_LICENSE_DIR") or os.path.join(os.environ.get("LOCALAPPDATA") or Path.home(),
                                                             "AISmartTender")
    return Path(root)


def key_path() -> Path:
    return app_dir() / "license.key"


def machine_code() -> str:
    raw = _machine_guid() or f"{os.environ.get('COMPUTERNAME', '')}|{os.environ.get('USERNAME', '')}"
    digest = hashlib.sha256(f"ast-machine:{raw.strip().lower()}".encode()).hexdigest().upper()[:16]
    return "-".join(digest[i:i + 4] for i in range(0, 16, 4))


def _machine_guid() -> str | None:
    try:
        import winreg  # noqa: PLC0415 — только Windows

        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, flags) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except (ImportError, OSError):
        return None


# --- ключ ---------------------------------------------------------------------------------------

def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def encode_key(payload: dict, secret: bytes) -> str:
    """Для генератора: данные → строка ключа."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return f"AST1.{_b64e(body)}.{_b64e(ed25519.sign(secret, body))}"


def decode_key(text: str, public: bytes | None = None) -> dict:
    """Строка ключа → данные; ValueError, если ключ испорчен или подпись не наша."""
    parts = "".join(text.split()).split(".")
    if len(parts) != 3 or parts[0] != "AST1":
        raise ValueError("Это не ключ лицензии AI Smart Tender.")
    try:
        body, signature = _b64d(parts[1]), _b64d(parts[2])
    except ValueError as exc:
        raise ValueError("Ключ повреждён.") from exc
    if not ed25519.verify(public or PUBLIC_KEY, body, signature):
        raise ValueError("Подпись ключа не сходится — ключ изменён или выдан не нами.")
    return json.loads(body)


def check_key(text: str, *, code: str | None = None, today: date | None = None) -> dict:
    """Данные ключа, если он подходит этому компьютеру и не истёк; иначе ValueError с причиной."""
    data = decode_key(text)
    code = code or machine_code()
    if data.get("mc") not in (code, "*"):
        raise ValueError(f"Ключ выдан для другого компьютера ({data.get('mc')}), код этого — {code}.")
    expires = data.get("exp")
    if expires and (today or date.today()).isoformat() > expires:
        raise ValueError(f"Срок действия ключа закончился {expires}.")
    return data


# --- пробный период и часы ----------------------------------------------------------------------

def _sign_state(state: dict, code: str) -> str:
    body = json.dumps(state, sort_keys=True, separators=(",", ":"))
    mac = hmac.new(_STATE_SECRET + code.encode(), body.encode(), hashlib.sha256).hexdigest()
    return _b64e(body.encode()) + "." + mac


def _read_state(raw: str | None, code: str) -> dict | None | bool:
    """dict — запись в порядке, None — записи нет, False — запись подделана."""
    if not raw:
        return None
    try:
        body_b64, mac = raw.strip().split(".")
        body = _b64d(body_b64)
    except ValueError:
        return False
    good = hmac.new(_STATE_SECRET + code.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, mac):
        return False
    state = json.loads(body)
    return state if isinstance(state, dict) else False


def _state_file() -> Path:
    return app_dir() / ".state"


def _load_raw() -> list[str | None]:
    raws: list[str | None] = []
    try:
        raws.append(_state_file().read_text(encoding="ascii"))
    except OSError:
        raws.append(None)
    if not USE_REGISTRY:
        return raws + [None]
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            raws.append(str(winreg.QueryValueEx(key, _REG_VALUE)[0]))
    except (ImportError, OSError):
        raws.append(None)
    return raws


def _save_raw(raw: str) -> None:
    try:
        _state_file().parent.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(raw, encoding="ascii")
    except OSError:
        pass
    if not USE_REGISTRY:
        return
    try:
        import winreg  # noqa: PLC0415

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            winreg.SetValueEx(key, _REG_VALUE, 0, winreg.REG_SZ, raw)
    except (ImportError, OSError):
        pass


def _trial_state(code: str, now: float) -> tuple[dict | None, str]:
    """Состояние пробного периода и причина отказа ('' — всё в порядке)."""
    states = [_read_state(raw, code) for raw in _load_raw()]
    if any(s is False for s in states):
        return None, "Данные о пробном периоде повреждены или изменены."
    found = [s for s in states if s]
    if found:
        state = {"first": min(float(s.get("first", now)) for s in found),
                 "seen": max(float(s.get("seen", 0)) for s in found)}
    else:
        state = {"first": now, "seen": now}
    if now < state["seen"] - CLOCK_SLACK:
        return state, "Часы компьютера переведены назад. Верните верное время и перезапустите программу."
    if now > state["seen"] or len(found) < 2:
        state["seen"] = max(state["seen"], now)
        _save_raw(_sign_state(state, code))
    return state, ""


# --- итог ---------------------------------------------------------------------------------------

def evaluate(now: float | None = None) -> Status:
    now = time.time() if now is None else now
    code = machine_code()
    state, problem = _trial_state(code, now)
    if problem and state is not None:
        return Status("clock", machine_code=code, message=problem)
    key_problem = ""
    try:
        text = key_path().read_text(encoding="utf-8")
    except OSError:
        text = ""
    if text.strip():
        try:
            data = check_key(text, code=code, today=datetime.fromtimestamp(now, timezone.utc).date())
        except ValueError as exc:
            key_problem = str(exc)
        else:
            expires = data.get("exp")
            days = (date.fromisoformat(expires) - datetime.fromtimestamp(now, timezone.utc).date()).days \
                if expires else None
            return Status("licensed", modules=[m for m in data.get("mod", []) if m in MODULES],
                          machine_code=code, customer=str(data.get("to") or ""), expires=expires,
                          days_left=days, message="Лицензия действует.")
    if state is None:
        return Status("invalid", machine_code=code, message=problem)
    used = int((now - state["first"]) // 86400)
    left = TRIAL_DAYS - used
    end = datetime.fromtimestamp(state["first"] + TRIAL_DAYS * 86400, timezone.utc).date().isoformat()
    if left > 0:
        message = f"Пробный период: осталось {left} дн." + (f" Ключ не принят: {key_problem}" if key_problem else "")
        return Status("trial", modules=list(MODULES), machine_code=code, expires=end, days_left=left,
                      message=message)
    message = key_problem or "Пробный период закончился. Чтобы продолжить, введите ключ лицензии."
    return Status("invalid" if key_problem else "expired", machine_code=code, expires=end, days_left=0,
                  message=message)


_cache: tuple[float, float, Status] | None = None
_lock = threading.Lock()


def _key_mtime() -> float:
    try:
        return key_path().stat().st_mtime
    except OSError:
        return 0.0


def status(*, fresh: bool = False) -> Status:
    global _cache
    with _lock:
        mtime = _key_mtime()
        if fresh or _cache is None or time.monotonic() - _cache[0] > CACHE_SECONDS or _cache[1] != mtime:
            _cache = (time.monotonic(), mtime, evaluate())
        return _cache[2]


def install_key(text: str) -> Status:
    """Проверить ключ и сохранить его; ValueError — ключ не подходит (старый ключ остаётся)."""
    check_key(text)
    key_path().parent.mkdir(parents=True, exist_ok=True)
    key_path().write_text("".join(text.split()), encoding="utf-8")
    return status(fresh=True)
