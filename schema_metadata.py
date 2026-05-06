# schema_metadata.py
# Manages a persistent JSON file that caches Oracle schema structure.
#
# Lifecycle
# ─────────
#   First run   : no metadata file exists → full schema fetch from Oracle → file written.
#   Later runs  : lightweight fingerprint (table names + column counts) fetched from Oracle.
#                 If fingerprint matches stored hash → load from file (no full re-fetch).
#                 If fingerprint differs             → full re-fetch → file updated.
#
# This keeps startup fast (one small query instead of N column queries) while
# ensuring the agent always reflects the true schema.

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config
from logger import get_logger

logger = get_logger("schema_metadata")

_PATH: Path = config.METADATA_FILE


# ── Helpers ────────────────────────────────────────────────────────────────────

def _stable_hash(tables: dict) -> str:
    """Deterministic MD5 of the tables dict (JSON with sorted keys)."""
    return hashlib.md5(
        json.dumps(tables, sort_keys=True).encode("utf-8")
    ).hexdigest()


# ── Public API ─────────────────────────────────────────────────────────────────

def load() -> Optional[dict]:
    """
    Load and return the metadata dict from disk.
    Returns None if the file is absent or unreadable (triggers a fresh fetch).
    """
    if not _PATH.exists():
        logger.info(f"No metadata file at '{_PATH}' — will fetch schema from Oracle.")
        return None
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        logger.info(
            f"Metadata loaded from '{_PATH}' "
            f"(last updated: {data.get('last_updated', 'unknown')})"
        )
        return data
    except Exception as exc:
        logger.warning(f"Could not read metadata file: {exc}. Will re-discover schema.")
        return None


def save(tables: dict) -> None:
    """
    Persist the tables dict to the metadata JSON file.

    tables format:
        {
            "TABLE_NAME": {
                "columns": [
                    {"name": "COL", "data_type": "VARCHAR2", "nullable": true},
                    ...
                ]
            },
            ...
        }
    """
    payload = {
        "schema_owner":  config.SCHEMA_OWNER or config.DB_USER.upper(),
        "last_updated":  datetime.now().isoformat(timespec="seconds"),
        "schema_hash":   _stable_hash(tables),
        "tables":        tables,
    }
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    logger.info(
        f"Schema metadata saved → '{_PATH}' "
        f"({len(tables)} table(s))"
    )


def is_stale(meta: dict, fingerprint: dict[str, int]) -> bool:
    """
    Return True when the stored metadata no longer matches the live Oracle schema.

    fingerprint: {TABLE_NAME: column_count}
        Computed from a single cheap aggregate query (see database._fetch_fingerprint).

    Stale conditions:
        • Set of table names has changed.
        • Column count for any table has changed.
    """
    stored_tables: dict = meta.get("tables", {})

    # ── Table-set comparison ──────────────────────────────────────────────────
    stored_names  = set(stored_tables.keys())
    current_names = set(fingerprint.keys())
    if stored_names != current_names:
        added   = current_names - stored_names
        removed = stored_names  - current_names
        logger.info(
            f"Schema changed — table set differs. "
            f"Added: {added or '∅'}  Removed: {removed or '∅'}"
        )
        return True

    # ── Column-count comparison ───────────────────────────────────────────────
    for tbl, live_count in fingerprint.items():
        stored_count = len(stored_tables.get(tbl, {}).get("columns", []))
        if stored_count != live_count:
            logger.info(
                f"Schema changed — '{tbl}' column count: "
                f"stored={stored_count}, live={live_count}"
            )
            return True

    logger.info("Schema fingerprint matches metadata — no re-fetch needed.")
    return False
