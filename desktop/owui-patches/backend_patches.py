"""Правки исходников Open WebUI перед сборкой OpenWebUI.exe (код уходит внутрь exe, поэтому только до PyInstaller).

Запуск: python backend_patches.py <site-packages/open_webui>. Повторный запуск ничего не меняет.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "AI Smart Tender:"

PATCHES = [
    (
        "retrieval/loaders/main.py",
        # Внешний загрузчик (tool-server) не ответил или упал — разбираем файл стандартным механизмом,
        # а не роняем загрузку в «Знания» и в чат.
        """        loader = self._get_loader(filename, file_content_type, file_path)
        docs = loader.load()
""",
        """        loader = self._get_loader(filename, file_content_type, file_path)
        try:
            docs = loader.load()
        except Exception as e:
            # AI Smart Tender: если внешний загрузчик (tool-server) недоступен, разбираем файл стандартно.
            if self.engine != 'external':
                raise
            log.warning(f'External document loader failed for {filename}: {e}; using the built-in loader')
            engine, self.engine = self.engine, ''
            try:
                docs = self._get_loader(filename, file_content_type, file_path).load()
            finally:
                self.engine = engine
""",
    ),
]


def main(pkg: Path) -> int:
    failed = 0
    for rel, old, new in PATCHES:
        f = pkg / rel
        text = f.read_text(encoding="utf-8")
        if MARKER in text and new in text:
            print(f"  already patched: {rel}")
            continue
        if text.count(old) != 1:
            print(f"  PATCH FAILED (source changed?): {rel}")
            failed += 1
            continue
        f.write_text(text.replace(old, new), encoding="utf-8")
        print(f"  patched: {rel}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
