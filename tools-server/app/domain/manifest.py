"""manifest.json contract for the analytical agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"

MANIFEST_FIELDS = (
    "tender_id",
    "platform",
    "tender_url",
    "customer",
    "object",
    "price",
    "deadline",
    "files",
    "extracted_at",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_path(tender_dir: Path) -> Path:
    return Path(tender_dir) / MANIFEST_NAME


def empty_manifest(
    *,
    tender_id: str,
    platform: str,
    tender_url: str = "",
) -> dict[str, Any]:
    return {
        "tender_id": tender_id,
        "platform": platform,
        "tender_url": tender_url or "",
        "customer": None,
        "object": None,
        "price": None,
        "deadline": None,
        "files": [],
        "extracted_at": _utc_now(),
    }


def load_manifest(tender_dir: Path) -> dict[str, Any] | None:
    path = manifest_path(tender_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_manifest(tender_dir: Path, data: dict[str, Any]) -> Path:
    tender_dir = Path(tender_dir)
    tender_dir.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["extracted_at"] = _utc_now()
    path = manifest_path(tender_dir)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ensure_manifest(
    tender_dir: Path,
    *,
    tender_id: str,
    platform: str,
    tender_url: str = "",
) -> dict[str, Any]:
    existing = load_manifest(tender_dir)
    if existing:
        if tender_url and not existing.get("tender_url"):
            existing["tender_url"] = tender_url
        return existing
    data = empty_manifest(
        tender_id=tender_id,
        platform=platform,
        tender_url=tender_url,
    )
    save_manifest(tender_dir, data)
    return data


def upsert_overview_fields(tender_dir: Path, overview: dict[str, Any]) -> dict[str, Any]:
    """Copy key overview fields into manifest (customer/object/price/deadline/url)."""
    data = load_manifest(tender_dir) or empty_manifest(
        tender_id=str(overview.get("tender_id") or ""),
        platform=str(overview.get("platform") or ""),
        tender_url=str(overview.get("tender_url") or ""),
    )
    for src, dst in (
        ("tender_id", "tender_id"),
        ("platform", "platform"),
        ("tender_url", "tender_url"),
        ("customer", "customer"),
        ("object", "object"),
        ("price", "price"),
        ("deadline", "deadline"),
    ):
        val = overview.get(src)
        if val not in (None, "", "null"):
            data[dst] = val
    save_manifest(tender_dir, data)
    return data


def append_file(
    tender_dir: Path,
    *,
    name: str,
    sha256: str,
    bytes_count: int,
    source_url: str,
    content_type: str = "",
    tender_id: str = "",
    platform: str = "",
    tender_url: str = "",
) -> dict[str, Any]:
    data = ensure_manifest(
        tender_dir,
        tender_id=tender_id or str((load_manifest(tender_dir) or {}).get("tender_id") or ""),
        platform=platform or str((load_manifest(tender_dir) or {}).get("platform") or ""),
        tender_url=tender_url,
    )
    files = list(data.get("files") or [])
    entry = {
        "name": name,
        "sha256": sha256,
        "bytes": int(bytes_count),
        "source_url": source_url,
        "content_type": content_type or "",
    }
    # replace same name or same sha
    files = [
        f
        for f in files
        if f.get("name") != name and f.get("sha256") != sha256
    ]
    files.append(entry)
    data["files"] = files
    save_manifest(tender_dir, data)
    return data


def validate_manifest(data: dict[str, Any]) -> list[str]:
    """Return list of validation errors (empty = ok)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest is not an object"]
    for key in ("tender_id", "platform", "tender_url", "files", "extracted_at"):
        if key not in data:
            errors.append(f"missing {key}")
    if "files" in data and not isinstance(data["files"], list):
        errors.append("files must be a list")
    else:
        for i, f in enumerate(data.get("files") or []):
            if not isinstance(f, dict):
                errors.append(f"files[{i}] not object")
                continue
            for k in ("name", "sha256", "bytes", "source_url"):
                if k not in f:
                    errors.append(f"files[{i}] missing {k}")
    return errors
