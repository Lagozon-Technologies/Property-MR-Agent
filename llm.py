# llm.py
# OCI GenAI client wrapper (Cohere Command A) — six LLM call types.

from __future__ import annotations

import json
import re
import time

import oci

from config import config
from logger import get_logger
from prompt import (
    intent_prompt,
    conversational_prompt,
    sql_prompt,
    sql_fix_prompt,
    response_prompt,
    chart_suggestion_prompt,
)

logger = get_logger("llm")

VALID_INTENTS = {"SELECT", "INSERT", "UPDATE", "DELETE", "CHART", "CONVERSATIONAL"}

SUPPORTED_CHART_TYPES = {
    "bar", "horizontal_bar", "line", "pie",
    "scatter", "histogram", "box", "violin", "multi_series",
}

# Seconds to wait before each retry attempt on HTTP 429.
# Attempt 0 runs immediately; retries use these delays.
_RETRY_DELAYS = [5, 15, 30]

# ── OCI GenAI client (shared) ──────────────────────────────────────────────────

_oci_config = oci.config.from_file(config.OCI_CONFIG_PATH, config.OCI_PROFILE)
_client     = oci.generative_ai_inference.GenerativeAiInferenceClient(_oci_config)


def _call(prompt: str, temperature: float = 0.0, label: str = "llm") -> str:
    """
    Single OCI GenAI chat call with automatic retry on rate-limit (429) errors.

    Retry strategy
    ──────────────
    - HTTP 429 (TransientServiceError): wait _RETRY_DELAYS[attempt] seconds, then retry.
    - HTTP 400 (ServiceError, token limit): raise immediately.
    - Any other exception: raise immediately.

    Returns the stripped response text on success.
    """
    logger.debug(f"[{label}] Sending prompt ({len(prompt):,} chars)")

    chat_request = oci.generative_ai_inference.models.CohereChatRequest(
        message=prompt,
        max_tokens=1000,
        temperature=temperature,
    )
    chat_details = oci.generative_ai_inference.models.ChatDetails(
        compartment_id=config.COMPARTMENT_ID,
        serving_mode=oci.generative_ai_inference.models.OnDemandServingMode(
            model_id=config.MODEL_ID,
        ),
        chat_request=chat_request,
    )

    last_exc: Exception | None = None

    for attempt in range(len(_RETRY_DELAYS) + 1):
        if attempt > 0:
            delay = _RETRY_DELAYS[attempt - 1]
            logger.warning(
                f"[{label}] Rate limited (429) — waiting {delay}s before retry "
                f"{attempt}/{len(_RETRY_DELAYS)}…"
            )
            time.sleep(delay)

        try:
            response = _client.chat(chat_details=chat_details)
            content  = response.data.chat_response.text.strip()
            logger.debug(f"[{label}] Response: {content[:300]}")
            return content

        except oci.exceptions.TransientServiceError as exc:
            if exc.status == 429:
                last_exc = exc
                if attempt < len(_RETRY_DELAYS):
                    continue
                logger.error(f"[{label}] Rate limit persists after {attempt} retries. Giving up.")
                raise
            raise

        except oci.exceptions.ServiceError as exc:
            if exc.status == 400 and "too many tokens" in str(exc).lower():
                logger.error(
                    f"[{label}] Token limit exceeded ({len(prompt):,} chars). "
                    "Check MAX_SCHEMA_TABLES or SCHEMA_TABLES in .env."
                )
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError(f"[{label}] _call() exited retry loop unexpectedly")


# ── SQL / JSON extraction ──────────────────────────────────────────────────────

def _extract_sql(text: str) -> str:
    """
    Robustly extract a raw SQL statement from LLM output.

    Cohere Command A sometimes wraps the SQL in prose + markdown code fences
    even when instructed not to.  This function handles all observed patterns:

      1. ```sql ... ```   (explicit SQL fence)
      2. ``` ... ```      (generic fence)
      3. No fence, but SQL keyword starts somewhere after explanation text
      4. Plain SQL with no wrapping at all
    """
    text = text.strip()

    # ── Pattern 1 & 2: extract content inside ANY code fence ─────────────────
    fence_match = re.search(
        r"```(?:sql)?\s*\n?(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        sql = fence_match.group(1).strip()
        # If multiple fences exist, keep only the last (most-corrected) block
        all_fences = re.findall(
            r"```(?:sql)?\s*\n?(.*?)```",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if all_fences:
            sql = all_fences[-1].strip()
        return sql.rstrip(";").strip()

    # ── Pattern 3: find first DML/SELECT keyword line ─────────────────────────
    sql_keywords = ("select", "insert", "update", "delete", "with", "merge")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(sql_keywords):
            return "\n".join(lines[i:]).strip().rstrip(";").strip()

    # ── Pattern 4: plain text, just clean backticks / "sql" prefix ───────────
    cleaned = text.strip("`")
    if cleaned.lower().startswith("sql"):
        cleaned = cleaned[3:].strip()
    return cleaned.rstrip(";").strip()


def _extract_json(text: str) -> str:
    """
    Robustly extract a JSON object from LLM output.
    Handles code fences, leading/trailing prose, and bare JSON.
    """
    text = text.strip()

    # Try code fence first
    fence_match = re.search(r"```(?:json)?\s*\n?(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()

    # Try to find a bare JSON object
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        return obj_match.group(0).strip()

    # Fallback: strip backticks
    cleaned = text.strip().strip("`")
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].strip()
    return cleaned


# ── Public API ─────────────────────────────────────────────────────────────────

def classify_intent(user_query: str) -> dict:
    """
    Returns {"intent": "SELECT|INSERT|UPDATE|DELETE|CHART|CONVERSATIONAL", "reasoning": "…"}
    Falls back to SELECT on parse error.
    """
    raw = _extract_json(_call(intent_prompt(user_query), label="intent"))
    try:
        result = json.loads(raw)
        intent = result.get("intent", "").upper()
        if intent not in VALID_INTENTS:
            raise ValueError(f"Unknown intent: {intent}")
        result["intent"] = intent
        logger.info(f"Intent: {intent} — {result.get('reasoning', '')}")
        return result
    except Exception as e:
        logger.warning(f"Intent parse failed ({e}), defaulting to SELECT. Raw: {raw}")
        return {"intent": "SELECT", "reasoning": "fallback"}


def generate_conversational_response(user_query: str) -> str:
    """Friendly reply for greetings, help requests, and small talk."""
    return _call(
        conversational_prompt(user_query),
        temperature=0.4,
        label="conversational",
    )


def generate_sql(user_query: str, intent: str, retry: int = 0) -> str:
    """
    Generate Oracle SQL for the given intent.
    Uses _extract_sql which correctly handles prose+code-fence responses from Cohere.
    """
    label = "sql" + ("_retry" * retry)
    raw   = _call(sql_prompt(user_query, intent), label=label)
    sql   = _extract_sql(raw)
    logger.info(f"Generated SQL:\n{sql}")
    return sql


def fix_sql(user_query: str, bad_sql: str, db_error: str, intent: str) -> str:
    """Ask the LLM to repair SQL that failed with a DB error."""
    raw = _call(sql_fix_prompt(user_query, bad_sql, db_error, intent), label="sql_fix")
    sql = _extract_sql(raw)
    logger.info(f"Fixed SQL:\n{sql}")
    return sql


def generate_response(
    user_query: str,
    data: list,
    intent: str,
    rows_affected: int = 0,
    truncated: bool = False,
) -> str:
    """
    Produce a natural language answer from aggregated DB result rows.
    data is already capped by the agent to MAX_RESPONSE_ROWS before this call.
    """
    return _call(
        response_prompt(user_query, data, intent, rows_affected, truncated=truncated),
        temperature=0.3,
        label="response",
    )


def suggest_chart_config(user_query: str, columns: list[str], row_count: int = 0) -> dict:
    """
    Returns:
        {
            "chart_type":      one of SUPPORTED_CHART_TYPES,
            "category_column": TEXT/LABEL column name,
            "value_column":    primary NUMERIC column name,
            "value_columns":   (multi_series only) list of NUMERIC column names,
            "title":           chart title string,
        }

    Falls back to sensible defaults on parse error.
    Handles edge cases: null category_column, same col for both axes, 1-row datasets.
    """
    raw = _extract_json(
        _call(chart_suggestion_prompt(user_query, columns, row_count), label="chart_cfg")
    )
    try:
        cfg = json.loads(raw)
        for key in ("chart_type", "value_column", "title"):
            if key not in cfg:
                raise KeyError(f"Missing key: {key}")

        chart_type = cfg.get("chart_type", "bar").lower()
        if chart_type not in SUPPORTED_CHART_TYPES:
            logger.warning(f"Unknown chart type '{chart_type}', falling back to bar")
            chart_type = "bar"
        cfg["chart_type"] = chart_type

        # ── Ensure category_column is present ──────────────────────────────
        cat = cfg.get("category_column")
        val = cfg.get("value_column")

        if not cat or cat == val:
            # Pick the first non-numeric-looking column as category
            text_cols = [
                c for c in columns
                if not any(kw in c.upper() for kw in
                           ("REVENUE", "AMOUNT", "BUDGET", "ACTUAL", "BUD",
                            "ACT", "TOTAL", "SUM", "COUNT", "AVG", "DAYS",
                            "NUM", "QTD", "YTD", "FTM", "COL_"))
            ]
            cfg["category_column"] = text_cols[0] if text_cols else columns[0]
            logger.warning(
                f"category_column was null/same-as-value — "
                f"inferred '{cfg['category_column']}' from column list"
            )

        # ── Validate multi_series ──────────────────────────────────────────
        if cfg["chart_type"] == "multi_series":
            val_cols = cfg.get("value_columns")
            if not isinstance(val_cols, list) or len(val_cols) < 2:
                logger.warning("multi_series missing value_columns; falling back to bar")
                cfg["chart_type"] = "bar"

        logger.info(f"Chart config: {cfg}")
        return cfg

    except Exception as e:
        logger.warning(f"Chart config parse failed ({e}), using default. Raw: {raw}")
        # Safe default: pick first text-ish col as category, first numeric-ish col as value
        text_cols = [c for c in columns if not any(
            kw in c.upper() for kw in ("REVENUE", "AMOUNT", "BUDGET", "ACT", "TOTAL", "SUM"))]
        num_cols  = [c for c in columns if c not in text_cols]
        return {
            "chart_type":      "horizontal_bar" if len(columns) > 2 else "bar",
            "category_column": text_cols[0] if text_cols else columns[0],
            "value_column":    num_cols[0] if num_cols else (columns[1] if len(columns) > 1 else columns[0]),
            "title":           "Query Results",
        }
