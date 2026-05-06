# config.py
# Central configuration — reads from .env, validates required keys.

import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Anchor all relative paths to the directory that contains config.py so the
# agent works correctly regardless of which directory it is launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Required environment variable '{key}' is not set in .env")
    return value


def _resolve(env_key: str, default: str) -> Path:
    """Return an absolute Path — relative values are anchored to _PROJECT_ROOT."""
    raw = os.getenv(env_key, default)
    p   = Path(raw)
    return p if p.is_absolute() else _PROJECT_ROOT / p


class Config:
    # ── Oracle DB ──────────────────────────────────────────
    DB_USER: str           = _require("DB_USER")
    DB_PASSWORD: str       = _require("DB_PASSWORD")
    DB_DSN: str            = _require("DB_DSN")
    DB_POOL_MIN: int       = int(os.getenv("DB_POOL_MIN", "1"))
    DB_POOL_MAX: int       = int(os.getenv("DB_POOL_MAX", "5"))

    # ── Schema owner ───────────────────────────────────────
    # When DB_USER is a privileged account (e.g. "sys") the agent must still
    # read and write against a specific application schema.
    # Set SCHEMA_OWNER=PROPERTY_MR_DB in .env to point the agent at that schema.
    # If left blank the agent uses DB_USER as the owner.
    SCHEMA_OWNER: str      = os.getenv("SCHEMA_OWNER", "").upper().strip()

    # ── Metadata file ──────────────────────────────────────
    # Anchored to _PROJECT_ROOT so it is always found on every launch
    # regardless of the working directory from which the agent is started.
    METADATA_FILE: Path    = _resolve("METADATA_FILE", "schema_metadata.json")

    # ── OCI GenAI ──────────────────────────────────────────
    OCI_CONFIG_PATH: str   = _require("OCI_CONFIG_PATH")
    OCI_PROFILE: str       = os.getenv("OCI_PROFILE", "DEFAULT")
    COMPARTMENT_ID: str    = _require("COMPARTMENT_OCID")
    MODEL_ID: str          = os.getenv("MODEL_ID", "cohere.command-a-03-2025")

    # ── Agent ──────────────────────────────────────────────
    MAX_RETRIES: int       = int(os.getenv("MAX_RETRIES", "2"))

    # Maximum rows forwarded to the response-generation LLM call.
    # Keeps prompt size under control to avoid OCI 429 rate-limit errors on
    # large unagregated result sets.  75 rows is enough for any NL summary.
    MAX_RESPONSE_ROWS: int = int(os.getenv("MAX_RESPONSE_ROWS", "75"))

    # ── Schema ─────────────────────────────────────────────
    SCHEMA_TABLES: str     = os.getenv("SCHEMA_TABLES", "")
    MAX_SCHEMA_TABLES: int = int(os.getenv("MAX_SCHEMA_TABLES", "5"))

    # ── Visualizer ─────────────────────────────────────────
    CHARTS_DIR: Path       = _resolve("CHARTS_DIR", "charts")

    # ── Derived helpers ────────────────────────────────────
    @property
    def effective_schema_owner(self) -> str:
        """The schema whose tables the agent works with (always UPPER)."""
        return self.SCHEMA_OWNER if self.SCHEMA_OWNER else self.DB_USER.upper()


config = Config()
config.CHARTS_DIR.mkdir(parents=True, exist_ok=True)

