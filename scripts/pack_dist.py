# Build customer distribution folder (+ optional zip).
# Usage:
#   py -3 scripts/pack_dist.py
#   py -3 scripts/pack_dist.py --zip

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Pack AI Smart Tender customer dist")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output folder (default: dist/ai-smart-tender)",
    )
    parser.add_argument("--zip", action="store_true", help="Also create a zip archive")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    out: Path = args.out or (root / "dist" / "ai-smart-tender")

    print(f"Packing distribution -> {out}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    compose_src = (root / "docker-compose.yml").read_text(encoding="utf-8")
    compose_dist = re.sub(
        r"(?m)^\s*build:\s*\./tools-server\s*\n",
        "",
        compose_src,
    )
    (out / "docker-compose.yml").write_text(compose_dist, encoding="utf-8")

    for name in (".env.example", "start.bat", "INSTALL.md"):
        shutil.copy2(root / name, out / name)

    for sub in ("tenders", "catalogs", "uploads", "exports"):
        d = out / "data" / sub
        d.mkdir(parents=True)
        (d / ".gitkeep").write_text("", encoding="utf-8")

    prompts = out / "prompts"
    prompts.mkdir()
    shutil.copy2(
        root / "prompts" / "tender-agent-system.txt",
        prompts / "tender-agent-system.txt",
    )

    (out / "README.txt").write_text(
        "\n".join(
            [
                "AI Smart Tender",
                "",
                "1. Install Docker Desktop and start it.",
                "2. copy .env.example .env",
                "3. Fill AGENT_LLM_API_KEY and TENDERS_HOST_PATH in .env",
                "4. Run start.bat  (or: docker compose up -d)",
                "5. Open http://localhost:3000",
                "",
                "Details: INSTALL.md",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print("Files:")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(out).as_posix()}")

    if args.zip:
        stamp = date.today().strftime("%Y%m%d")
        zip_path = out.parent / f"ai-smart-tender-{stamp}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in out.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=Path(out.name) / p.relative_to(out))
        print(f"Zip: {zip_path}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
