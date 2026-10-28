"""Выдача ключей лицензии. В сборку не входит; секретный ключ — только у выдающего.

    py keygen.py init                      # один раз: создать пару ключей, открытый вписать в core.py
    py keygen.py issue --to "ООО Ромашка" --machine 1A2B-3C4D-5E6F-7A8B --modules search,documents --days 365
    py keygen.py issue --to "Демо" --machine 1A2B-3C4D-5E6F-7A8B --modules all             # бессрочно
    py keygen.py show <ключ>               # что внутри ключа

Секретный ключ по умолчанию: %USERPROFILE%\\.ast-license\\private.key (или --secret <файл>).
Его нельзя класть в репозиторий и терять: без него новые ключи не выдать, а старые не подделать.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smart_license import ed25519  # noqa: E402
from smart_license.core import MODULES, decode_key, encode_key  # noqa: E402

DEFAULT_SECRET = Path(os.environ.get("USERPROFILE") or Path.home()) / ".ast-license" / "private.key"
CORE = Path(__file__).resolve().parent / "smart_license" / "core.py"


def load_secret(path: Path) -> bytes:
    return bytes.fromhex(path.read_text(encoding="ascii").strip())


def cmd_init(args) -> None:
    path = Path(args.secret)
    if path.exists() and not args.force:
        sys.exit(f"Секретный ключ уже есть: {path}. Новый сделает все выданные ключи недействительными (--force).")
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret.hex(), encoding="ascii")
    public = ed25519.public_key(secret).hex()
    text = CORE.read_text(encoding="utf-8")
    text = re.sub(r'PUBLIC_KEY = bytes\.fromhex\("[^"]*"\)', f'PUBLIC_KEY = bytes.fromhex("{public}")', text)
    CORE.write_text(text, encoding="utf-8")
    print(f"Секретный ключ: {path} — сделайте резервную копию.\nОткрытый ключ вписан в {CORE}: {public}")


def cmd_issue(args) -> None:
    modules = list(MODULES) if args.modules == "all" else [m.strip() for m in args.modules.split(",") if m.strip()]
    unknown = [m for m in modules if m not in MODULES]
    if unknown:
        sys.exit(f"Неизвестные модули: {unknown}. Есть: {', '.join(MODULES)}")
    machine = args.machine.strip().upper()
    if machine != "*" and not re.fullmatch(r"[0-9A-F]{4}(-[0-9A-F]{4}){3}", machine):
        sys.exit("Код компьютера — 4 группы по 4 символа, как в окне «Лицензия» программы.")
    payload = {
        "v": 1,
        "id": uuid.uuid4().hex[:12],
        "to": args.to,
        "mc": machine,
        "mod": modules,
        "iss": date.today().isoformat(),
        "exp": (date.today() + timedelta(days=args.days)).isoformat() if args.days else None,
    }
    key = encode_key(payload, load_secret(Path(args.secret)))
    print(key)
    log = Path(args.secret).with_name("issued.log")
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{payload['iss']}\t{payload['id']}\t{payload['to']}\t{machine}\t{','.join(modules)}\t"
                f"{payload['exp'] or 'бессрочно'}\n")


def cmd_show(args) -> None:
    secret_path = Path(args.secret)
    public = ed25519.public_key(load_secret(secret_path)) if secret_path.exists() else None
    print(decode_key(args.key, public) if public else decode_key(args.key))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ключи лицензии AI Smart Tender")
    parser.add_argument("--secret", default=str(DEFAULT_SECRET))
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("issue")
    p.add_argument("--to", required=True, help="кому выдан ключ")
    p.add_argument("--machine", required=True, help="код компьютера из окна «Лицензия» (или * — любой)")
    p.add_argument("--modules", default="all", help=f"через запятую: {', '.join(MODULES)} или all")
    p.add_argument("--days", type=int, default=0, help="срок в днях; 0 — бессрочно")
    p.set_defaults(func=cmd_issue)
    p = sub.add_parser("show")
    p.add_argument("key")
    p.set_defaults(func=cmd_show)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
