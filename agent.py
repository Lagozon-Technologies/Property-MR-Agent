# agent.py
# RAG agent orchestration pipeline.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import oci

from config import config
from database import execute_query, execute_dml
from llm import (
    classify_intent,
    generate_conversational_response,
    generate_sql,
    fix_sql,
    generate_response,
    suggest_chart_config,
)
from logger import get_logger
from visualizer import generate_chart

logger = get_logger("agent")

# ── Constants ──────────────────────────────────────────────────────────────────

_BLOCKED_KEYWORDS = {"drop", "truncate", "alter", "create", "grant", "revoke", "exec", "execute"}
DML_INTENTS        = {"INSERT", "UPDATE", "DELETE"}
ANALYTICAL_INTENTS = {"SELECT", "CHART"}


# ── Result Dataclass ───────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    intent:        str
    sql:           str
    response:      str
    data:          list[dict] = field(default_factory=list)
    rows_affected: int        = 0
    chart_path:    Path | None = None
    error:         str | None  = None
    cancelled:     bool        = False
    row_count:     int         = 0

    @property
    def success(self) -> bool:
        return self.error is None and not self.cancelled


# ── SQL Validation ─────────────────────────────────────────────────────────────

def _validate_sql(sql: str, intent: str) -> tuple[bool, str]:
    """
    Lightweight safety check on generated SQL before it hits the database.

    IMPORTANT: This must be called AFTER _extract_sql() has already cleaned
    the LLM output down to pure SQL — otherwise explanation text in the
    response triggers false-positive blocked-keyword hits.
    """
    sql_lower = sql.lower()

    # Block dangerous DDL / privilege keywords (whole-word match only)
    tokens = set(sql_lower.split())
    for keyword in _BLOCKED_KEYWORDS:
        if keyword in tokens:
            return False, f"Blocked keyword detected: '{keyword}'"

    # Ensure the statement type matches the declared intent
    expected_starts = {
        "SELECT": "select",
        "CHART":  "select",
        "INSERT": "insert",
        "UPDATE": "update",
        "DELETE": "delete",
    }
    expected = expected_starts.get(intent, "select")
    if not sql_lower.strip().startswith(expected):
        return False, f"Expected SQL to start with '{expected.upper()}' for intent '{intent}'"

    # UPDATE / DELETE must include a WHERE clause
    if intent in ("UPDATE", "DELETE") and "where" not in sql_lower:
        return False, "UPDATE/DELETE without a WHERE clause is not permitted"

    return True, "OK"


# ── DB Execution with Retry ────────────────────────────────────────────────────

def _execute_with_retry(
    user_query: str,
    sql: str,
    intent: str,
) -> tuple[list[dict], int, str]:
    """
    Execute SQL against Oracle.  On DB error ask the LLM to fix the SQL and
    retry up to config.MAX_RETRIES times.
    Returns (data, rows_affected, final_sql).
    Raises on unrecoverable failure.
    """
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            if intent in DML_INTENTS:
                rows = execute_dml(sql)
                return [], rows, sql
            else:
                data = execute_query(sql)
                return data, 0, sql

        except Exception as db_err:
            error_msg = str(db_err)
            logger.warning(f"DB error (attempt {attempt + 1}): {error_msg}")

            if attempt < config.MAX_RETRIES:
                logger.info("Asking LLM to fix the SQL...")
                sql = fix_sql(user_query, sql, error_msg, intent)
                valid, reason = _validate_sql(sql, intent)
                if not valid:
                    logger.warning(f"Fixed SQL failed validation: {reason}")
                    continue
            else:
                raise


# ── Rate-limit guard ───────────────────────────────────────────────────────────

def _is_rate_limit_error(exc: Exception) -> bool:
    try:
        return (
            isinstance(exc, oci.exceptions.TransientServiceError)
            and exc.status == 429
        )
    except Exception:
        return False


# ── Main Agent ─────────────────────────────────────────────────────────────────

def run_agent(
    user_query: str,
    confirm_callback: Callable[[str, str], bool] | None = None,
) -> AgentResult:
    """
    Execute the full RAG pipeline for a user query.

    Args:
        user_query:        Natural language question from the user.
        confirm_callback:  Called before executing DML.
                           Receives (intent, sql) → return True to proceed.
                           Pass None to disable DML (read-only mode).
    Returns:
        AgentResult with all pipeline outputs.
    """
    logger.info(f"Agent received query: {user_query!r}")

    # ── Step 1: Classify intent ──────────────────────────────────────────────
    try:
        intent_result = classify_intent(user_query)
    except Exception as e:
        if _is_rate_limit_error(e):
            msg = (
                "The AI service is currently rate-limited. "
                "Please wait a minute and try again."
            )
        else:
            msg = f"Could not classify intent: {e}"
        logger.error(msg)
        return AgentResult(intent="UNKNOWN", sql="", response=msg, error=msg)

    intent = intent_result["intent"]

    # ── Step 2: Route — Conversational ──────────────────────────────────────
    if intent == "CONVERSATIONAL":
        logger.info("Routing to conversational handler — no SQL required")
        try:
            response = generate_conversational_response(user_query)
        except Exception as e:
            response = (
                "I'm having trouble reaching the AI service right now. "
                "Please try again in a moment."
                if _is_rate_limit_error(e) else str(e)
            )
        return AgentResult(intent=intent, sql="", response=response)

    # ── Step 3: Generate SQL (with validation retry loop) ────────────────────
    sql = ""
    valid, reason = False, ""
    for attempt in range(config.MAX_RETRIES + 1):
        try:
            sql = generate_sql(user_query, intent, retry=attempt)
        except Exception as e:
            if _is_rate_limit_error(e):
                msg = "AI service rate-limited during SQL generation. Please retry in a moment."
                return AgentResult(intent=intent, sql="", response=msg, error=msg)
            raise

        valid, reason = _validate_sql(sql, intent)
        if valid:
            break
        logger.warning(f"SQL validation failed (attempt {attempt + 1}): {reason}")

    if not valid:
        logger.error(f"SQL rejected after {config.MAX_RETRIES + 1} attempts: {reason}")
        return AgentResult(
            intent=intent,
            sql=sql,
            response=(
                f"I could not generate a safe SQL query for your request.\n"
                f"Reason: {reason}"
            ),
            error=reason,
        )

    # ── Step 4: DML confirmation ─────────────────────────────────────────────
    if intent in DML_INTENTS:
        if confirm_callback is None:
            msg = "DML operations require explicit confirmation. No confirm_callback provided."
            logger.warning(msg)
            return AgentResult(intent=intent, sql=sql, response=msg, error=msg)

        approved = confirm_callback(intent, sql)
        if not approved:
            logger.info("DML cancelled by user")
            return AgentResult(
                intent=intent,
                sql=sql,
                response="Operation cancelled by user.",
                cancelled=True,
            )

    # ── Step 5: Execute against Oracle (with DB-error retry) ─────────────────
    try:
        data, rows_affected, sql = _execute_with_retry(user_query, sql, intent)
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unrecoverable DB error: {error_msg}")
        return AgentResult(
            intent=intent,
            sql=sql,
            response=(
                "The database returned an error that could not be automatically resolved.\n"
                f"Details: {error_msg}"
            ),
            error=error_msg,
        )

    row_count = len(data)
    logger.info(f"Query returned {row_count} row(s)")

    # ── Step 6: Visualize (CHART intent only) ────────────────────────────────
    chart_path: Path | None = None
    if intent == "CHART":
        if data:
            try:
                columns    = list(data[0].keys())
                chart_cfg  = suggest_chart_config(user_query, columns, row_count=row_count)
                chart_path = generate_chart(
                    data=data,
                    chart_type=chart_cfg["chart_type"],
                    category_column=chart_cfg["category_column"],
                    value_column=chart_cfg["value_column"],
                    title=chart_cfg["title"],
                    value_columns=chart_cfg.get("value_columns"),
                )
                logger.info(f"Chart saved: {chart_path}")
            except Exception as e:
                if _is_rate_limit_error(e):
                    logger.warning("Rate-limited during chart config generation — skipping chart")
                else:
                    logger.error(f"Chart generation failed: {e}")
        else:
            logger.warning("No data returned — skipping chart generation")

    # ── Step 7: Cap data rows before sending to response LLM ─────────────────
    # Sending thousands of raw rows causes 179k-char prompts → OCI 429 errors.
    # The NL summary only needs a representative sample to answer the question.
    truncated       = row_count > config.MAX_RESPONSE_ROWS
    data_for_llm    = data[:config.MAX_RESPONSE_ROWS] if truncated else data

    if truncated:
        logger.info(
            f"Capping response data: {row_count} rows → {config.MAX_RESPONSE_ROWS} "
            f"sent to LLM (truncated=True)"
        )

    # ── Step 8: Generate natural language response ────────────────────────────
    try:
        response_text = generate_response(
            user_query=user_query,
            data=data_for_llm if intent not in DML_INTENTS else [],
            intent=intent,
            rows_affected=rows_affected,
            truncated=truncated,
        )
    except Exception as e:
        if _is_rate_limit_error(e):
            response_text = (
                "Query executed successfully but the AI service is rate-limited "
                "right now and could not generate a summary. "
                f"The query returned {row_count} row(s)."
            )
            logger.warning("Rate-limited during response generation — returning raw count.")
        else:
            raise

    return AgentResult(
        intent=intent,
        sql=sql,
        response=response_text,
        data=data,
        rows_affected=rows_affected,
        chart_path=chart_path,
        row_count=row_count,
    )
