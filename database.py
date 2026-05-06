# database.py
# Oracle 19c connection pool + read/write execution paths + schema discovery.
# Uses oracledb thin-mode (no Oracle Client needed).
#
from __future__ import annotations

import re

import oracledb
from config import config
from logger import get_logger
from schema_metadata import load as meta_load, save as meta_save, is_stale

logger = get_logger("database")

# ── Connection Pool ────────────────────────────────────────────────────────────

_pool: oracledb.ConnectionPool | None = None


def _get_pool() -> oracledb.ConnectionPool:
    global _pool
    if _pool is None:
        logger.info("Initializing Oracle connection pool...")

        connect_params: dict = dict(
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            dsn=config.DB_DSN,
            min=config.DB_POOL_MIN,
            max=config.DB_POOL_MAX,
            increment=1,
        )
        if config.DB_USER.lower() == "sys":
            connect_params["mode"] = oracledb.AUTH_MODE_SYSDBA

        _pool = oracledb.create_pool(**connect_params)
        logger.info(
            f"Connection pool ready "
            f"(user={config.DB_USER}, schema={config.effective_schema_owner}, "
            f"min={config.DB_POOL_MIN}, max={config.DB_POOL_MAX})"
        )
    return _pool


def _acquire() -> oracledb.Connection:
    """
    Acquire a connection from the pool and set CURRENT_SCHEMA to the
    application schema owner so all unqualified table references resolve
    correctly — even when connected as sys.
    """
    conn = _get_pool().acquire()
    owner = config.effective_schema_owner
    if owner and owner.upper() != config.DB_USER.upper():
        try:
            with conn.cursor() as cur:
                cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {owner}")
        except Exception as e:
            logger.warning(f"Could not set CURRENT_SCHEMA to '{owner}': {e}")
    return conn


# ── Public query / DML API ─────────────────────────────────────────────────────

def execute_query(sql: str, params: dict | None = None) -> list[dict]:
    """
    Execute a SELECT statement and return ALL rows as a list of dicts.
    No row cap is applied here — the SQL is expected to aggregate at DB level
    or carry an explicit FETCH FIRST clause when the user requests N rows.
    """
    params = params or {}
    logger.debug(f"Executing SELECT:\n{sql}")

    with _acquire() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [col[0] for col in cursor.description]
            rows    = cursor.fetchall()

    result = [dict(zip(columns, row)) for row in rows]
    logger.info(f"SELECT returned {len(result)} row(s)")
    return result


def execute_dml(sql: str, params: dict | None = None) -> int:
    """
    Execute INSERT / UPDATE / DELETE.
    Returns rows affected.  Commits on success, rolls back on failure.
    """
    params = params or {}
    logger.debug(f"Executing DML:\n{sql}")

    with _acquire() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(sql, params)
                affected = cursor.rowcount
                conn.commit()
                logger.info(f"DML committed — {affected} row(s) affected")
                return affected
            except Exception:
                conn.rollback()
                logger.warning("DML rolled back due to error")
                raise


def test_connection() -> bool:
    """Smoke-test the pool at startup."""
    try:
        with _acquire() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM DUAL")
        logger.info(
            f"Oracle connection test passed "
            f"(connected as {config.DB_USER}, working schema: {config.effective_schema_owner})"
        )
        return True
    except Exception as e:
        logger.error(f"Oracle connection test failed: {e}")
        return False


# ── Fingerprint (cheap change-detection) ──────────────────────────────────────

def _fetch_fingerprint() -> dict[str, int]:
    """
    Return {TABLE_NAME: column_count} for the application schema.
    Uses a single aggregated query — very fast regardless of table count.
    Filtered by SCHEMA_TABLES allow-list when configured.
    """
    owner = config.effective_schema_owner

    if config.SCHEMA_TABLES.strip():
        table_filter = [t.strip().upper() for t in config.SCHEMA_TABLES.split(",") if t.strip()]
        placeholders = ", ".join(f"'{t}'" for t in table_filter)
        sql = f"""
            SELECT table_name, COUNT(*) AS col_count
            FROM   all_tab_columns
            WHERE  owner = '{owner}'
              AND  table_name IN ({placeholders})
            GROUP  BY table_name
            ORDER  BY table_name
        """
    else:
        sql = f"""
            SELECT table_name, COUNT(*) AS col_count
            FROM   all_tab_columns
            WHERE  owner = '{owner}'
            GROUP  BY table_name
            ORDER  BY table_name
        """

    rows = execute_query(sql)
    return {r["TABLE_NAME"]: int(r["COL_COUNT"]) for r in rows}


# ── Full Schema Fetch from Oracle ──────────────────────────────────────────────

def _fetch_full_schema_from_oracle() -> dict:
    """
    Query ALL_TABLES + ALL_TAB_COLUMNS (+ foreign keys) for SCHEMA_OWNER.
    Returns the raw tables dict suitable for schema_metadata.save().

    tables dict format:
        {
            "TABLE_NAME": {
                "columns": [
                    {"name": "COL", "data_type": "VARCHAR2", "nullable": True},
                    ...
                ]
            },
            ...
        }
    """
    owner = config.effective_schema_owner
    logger.info(f"Fetching full schema from Oracle for owner='{owner}'...")

    # ── Which tables to describe ───────────────────────────────────────────────
    if config.SCHEMA_TABLES.strip():
        table_filter = [t.strip().upper() for t in config.SCHEMA_TABLES.split(",") if t.strip()]
        placeholders = ", ".join(f"'{t}'" for t in table_filter)
        tables_sql   = f"""
            SELECT table_name
            FROM   all_tables
            WHERE  owner = '{owner}'
              AND  table_name IN ({placeholders})
            ORDER  BY table_name
        """
    else:
        tables_sql = f"""
            SELECT table_name
            FROM   all_tables
            WHERE  owner = '{owner}'
            ORDER  BY table_name
        """

    table_rows = execute_query(tables_sql)
    if not table_rows:
        logger.warning(f"No tables found for owner='{owner}'.")
        return {}

    tables: dict = {}
    for row in table_rows:
        tbl = row["TABLE_NAME"]
        col_rows = execute_query(f"""
            SELECT column_name, data_type, nullable, data_length,
                   data_precision, data_scale, column_id
            FROM   all_tab_columns
            WHERE  owner      = '{owner}'
              AND  table_name = '{tbl}'
            ORDER  BY column_id
        """)

        columns = []
        for c in col_rows:
            columns.append({
                "name":      c["COLUMN_NAME"],
                "data_type": c["DATA_TYPE"],
                "nullable":  c["NULLABLE"] == "Y",
            })

        tables[tbl] = {"columns": columns}

    logger.info(f"Full schema fetch complete — {len(tables)} table(s)")
    return tables


# ── Schema Cache (in-process, built from metadata) ────────────────────────────
#
# After loading (from file or fresh Oracle fetch) we keep three in-memory
# structures that mirror the old _schema_blocks / _schema_cache / _schema_fk_lines
# variables so the rest of the codebase (prompt.py, get_schema_text, etc.)
# works without any changes.
#
_schema_cache:    str | None = None                # full human-readable schema text
_schema_blocks:   dict[str, tuple[str, list[str]]] | None = None  # {TABLE: (block_text, [cols])}
_schema_fk_lines: list[str] = []


def _build_in_memory_cache(tables: dict) -> None:
    """Populate _schema_cache / _schema_blocks from the tables dict."""
    global _schema_cache, _schema_blocks, _schema_fk_lines

    blocks:     dict[str, tuple[str, list[str]]] = {}
    full_lines: list[str] = [
        f"Schema owner : {config.effective_schema_owner}",
        f"Tables found : {len(tables)}",
        "",
        "Available tables and columns:",
        "",
    ]

    for tbl, meta in tables.items():
        col_names: list[str] = [c["name"] for c in meta["columns"]]
        lines: list[str] = [f"Table: {tbl}", "  Columns:"]
        for c in meta["columns"]:
            nullable = "nullable" if c.get("nullable", True) else "not null"
            lines.append(
                f"    {c['name']:<35} {c['data_type']:<20} ({nullable})"
            )
        lines.append("")

        block_text      = "\n".join(lines)
        blocks[tbl]     = (block_text, col_names)
        full_lines.extend(lines)

    # FK lines (best-effort — may not exist when connected via metadata only)
    _schema_fk_lines = []
    try:
        owner = config.effective_schema_owner
        fk_rows = execute_query(f"""
            SELECT a.table_name  AS child_table,
                   a.column_name AS child_col,
                   c_pk.table_name  AS parent_table,
                   c_pk.column_name AS parent_col
            FROM all_cons_columns  a
            JOIN all_constraints   c    ON a.owner = c.owner
                                       AND a.constraint_name = c.constraint_name
            JOIN all_constraints   c_r  ON c.r_owner = c_r.owner
                                       AND c.r_constraint_name = c_r.constraint_name
            JOIN all_cons_columns  c_pk ON c_r.owner = c_pk.owner
                                       AND c_r.constraint_name = c_pk.constraint_name
                                       AND a.position = c_pk.position
            WHERE c.constraint_type = 'R'
              AND c.owner = '{owner}'
            ORDER BY a.table_name, a.column_name
        """)

        if fk_rows:
            full_lines.append("Foreign key relationships (use for JOINs):")
            for fk in fk_rows:
                line = (
                    f"  {fk['CHILD_TABLE']}.{fk['CHILD_COL']}"
                    f"  →  {fk['PARENT_TABLE']}.{fk['PARENT_COL']}"
                )
                full_lines.append(line)
                _schema_fk_lines.append(line)
            full_lines.append("")
    except Exception as fk_err:
        logger.debug(f"FK discovery skipped: {fk_err}")

    _schema_blocks = blocks
    _schema_cache  = "\n".join(full_lines)
    logger.info(
        f"In-memory schema cache built: {len(blocks)} table(s)"
        + (f", {len(_schema_fk_lines)} FK(s)" if _schema_fk_lines else "")
    )


# ── Schema Initialisation (called once at startup) ─────────────────────────────

def _ensure_schema_loaded() -> None:
    """
    Idempotent.  On first call:
      1. Try to load schema_metadata.json from disk.
      2. Fetch a cheap fingerprint from Oracle (one query).
      3. If metadata missing or stale → full re-fetch → save metadata.
      4. Build in-memory cache from whichever source we ended up with.
    """
    global _schema_blocks

    if _schema_blocks is not None:
        return  # already loaded this session

    # ── Step 1: try disk metadata ──────────────────────────────────────────────
    meta = meta_load()

    # ── Step 2: fingerprint from Oracle ───────────────────────────────────────
    try:
        fingerprint = _fetch_fingerprint()
    except Exception as e:
        logger.error(f"Could not fetch schema fingerprint: {e}")
        # If we have stale metadata, use it rather than nothing
        if meta and meta.get("tables"):
            logger.warning("Using cached metadata despite fingerprint failure.")
            _build_in_memory_cache(meta["tables"])
            return
        _schema_blocks = {}
        _schema_cache  = "Schema not available — Oracle fingerprint query failed."
        return

    if not fingerprint:
        owner = config.effective_schema_owner
        logger.warning(
            f"No tables found for owner='{owner}'. "
            f"Check SCHEMA_OWNER in .env — it must match the schema that owns "
            f"FINANCIAL_DATA and REVENUE_DATA (typically PROPERTY_MR_DB)."
        )
        _schema_blocks = {}
        _schema_cache  = (
            f"No tables found for schema owner '{owner}'. "
            f"Verify SCHEMA_OWNER in .env."
        )
        return

    # ── Step 3: decide whether we need a full Oracle fetch ────────────────────
    need_full_fetch = True
    if meta and meta.get("tables"):
        if not is_stale(meta, fingerprint):
            # Metadata is fresh — use it
            tables = meta["tables"]
            need_full_fetch = False

    if need_full_fetch:
        tables = _fetch_full_schema_from_oracle()
        if tables:
            meta_save(tables)
        else:
            _schema_blocks = {}
            _schema_cache  = "Full schema fetch returned no tables."
            return

    # ── Step 4: build in-memory cache ─────────────────────────────────────────
    _build_in_memory_cache(tables)


# ── Public schema API (interface unchanged — prompt.py uses these) ─────────────

def get_schema_text() -> str:
    """Return full schema text (all tables).  Used for display / debug."""
    _ensure_schema_loaded()
    return _schema_cache or "Schema not available."


def get_relevant_schema(user_query: str, max_tables: int | None = None) -> str:
    """
    Return a compact schema string containing ONLY the most query-relevant tables.

    Algorithm
    ─────────
    1. Tokenise the user query (lower, alphanumeric only).
    2. Score each table:
         +3 per token that matches any word in the table name
         +1 per token that matches any word in a column name
    3. Return the top max_tables tables (default: config.MAX_SCHEMA_TABLES).
    4. Append FK lines that mention any selected table.

    If total tables ≤ max_tables, the full schema is returned unchanged.
    """
    _ensure_schema_loaded()

    if not _schema_blocks:
        return _schema_cache or "No schema available."

    max_t = max_tables if max_tables is not None else config.MAX_SCHEMA_TABLES

    # ── Fast path: all tables fit ─────────────────────────────────────────────
    if len(_schema_blocks) <= max_t:
        return _schema_cache or ""

    # ── Relevance scoring ─────────────────────────────────────────────────────
    query_tokens = set(
        re.sub(r"[^a-z0-9]", " ", user_query.lower()).split()
    )
    stop = {"a", "an", "the", "of", "in", "for", "by", "to", "and", "or",
            "is", "are", "was", "all", "me", "my", "its", "with", "from"}
    query_tokens -= stop

    scores: dict[str, float] = {}
    for table_name, (_, col_names) in _schema_blocks.items():
        score       = 0.0
        tbl_tokens  = set(table_name.lower().split("_"))
        score      += len(query_tokens & tbl_tokens) * 3
        for col in col_names:
            col_tokens = set(col.lower().split("_"))
            score     += len(query_tokens & col_tokens)
        scores[table_name] = score

    sorted_tables = sorted(scores, key=lambda t: scores[t], reverse=True)
    top_tables    = sorted_tables[:max_t]

    logger.debug(
        f"Relevant schema: {len(top_tables)}/{len(_schema_blocks)} table(s) "
        f"for query {user_query!r:.60} — "
        + ", ".join(f"{t}({scores[t]:.0f})" for t in top_tables)
    )

    # ── Build compact schema text ─────────────────────────────────────────────
    lines = [
        f"Schema owner: {config.effective_schema_owner}",
        f"Relevant tables ({len(top_tables)} of {len(_schema_blocks)} available):",
        "",
    ]
    for tbl in top_tables:
        block, _ = _schema_blocks[tbl]
        lines.append(block)

    # ── Inject relevant FK lines ──────────────────────────────────────────────
    if _schema_fk_lines:
        top_set      = set(top_tables)
        relevant_fk  = [l for l in _schema_fk_lines if any(t in l for t in top_set)]
        if relevant_fk:
            lines.append("Foreign key relationships (relevant to this query):")
            lines.extend(relevant_fk)
            lines.append("")

    return "\n".join(lines)


def invalidate_schema_cache() -> None:
    """Force full schema re-discovery on the next call (also clears metadata file)."""
    global _schema_cache, _schema_blocks, _schema_fk_lines
    _schema_cache    = None
    _schema_blocks   = None
    _schema_fk_lines = []
    # Remove the metadata file so is_stale() triggers a full re-fetch
    from config import config as _cfg
    if _cfg.METADATA_FILE.exists():
        try:
            _cfg.METADATA_FILE.unlink()
            logger.info(f"Metadata file '{_cfg.METADATA_FILE}' deleted — will re-fetch on next query.")
        except Exception as e:
            logger.warning(f"Could not delete metadata file: {e}")
    logger.info("Schema cache invalidated.")
