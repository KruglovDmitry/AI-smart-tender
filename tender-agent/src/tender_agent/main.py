from __future__ import annotations

import asyncio
import sys

from .api.service import run_task
from .config import Settings


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Передайте текст задания аргументом.", file=sys.stderr)
        return 2
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    settings = Settings.from_env()
    result = asyncio.run(run_task(" ".join(args), settings=settings))
    print(result.model_dump_json(indent=2))
    return 0 if result.status in {"completed", "partial", "needs_user"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
