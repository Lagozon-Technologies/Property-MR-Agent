# app.py
# FastAPI application for the Oracle RAG SQL Agent.
# Run locally:  uvicorn app:app --host 0.0.0.0 --port 8000 --reload
# Swagger UI :  http://192.168.1.148:8000/docs
# Redoc      :  http://192.168.1.148:8000/redoc

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from agent import run_agent, DML_INTENTS
from config import config
from database import test_connection, get_schema_text, invalidate_schema_cache
from logger import get_logger

logger = get_logger("app")

# ── App definition ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Property MR — Oracle RAG SQL Agent",
    description=(
        "Natural language interface to Oracle 19c property database.\n\n"
        "Submit plain-English questions and get SQL + answers + charts back.\n\n"
        "**DML operations (INSERT / UPDATE / DELETE) require `allow_dml: true` "
        "in the request body and still confirm via the API — use carefully.**"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS (allows Postman, browser, and future front-ends) ─────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup: warm up DB pool + schema cache ────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    logger.info("FastAPI startup — warming up DB connection and schema cache...")
    ok = test_connection()
    if not ok:
        logger.error(
            "⚠️  Oracle DB connection FAILED at startup. "
            "Check DB_DSN / DB_USER / DB_PASSWORD in .env"
        )
    else:
        # Triggers metadata load / Oracle fetch if needed
        get_schema_text()
        logger.info("Startup complete — agent is ready.")


# ── Request / Response models ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        examples=["What is the total FTM actual by region?"],
        description="Plain-English question about your Oracle data.",
    )
    allow_dml: bool = Field(
        default=False,
        description=(
            "Set to true to allow INSERT / UPDATE / DELETE queries. "
            "When false (default) DML queries return an error instead of executing."
        ),
    )
    include_chart_base64: bool = Field(
        default=False,
        description=(
            "When true and the intent is CHART, the response includes the chart "
            "PNG as a base64-encoded string in chart_base64. "
            "Useful for Postman / clients that cannot follow the chart_url."
        ),
    )


class QueryResponse(BaseModel):
    intent:         str   = Field(description="Classified query intent.")
    sql:            str   = Field(description="Generated Oracle SQL statement.")
    response:       str   = Field(description="Natural language answer.")
    row_count:      int   = Field(description="Number of rows returned by the DB query.")
    rows_affected:  int   = Field(description="Rows affected (DML only).")
    data:           list[dict[str, Any]] = Field(
                                default=[],
                                description="Raw query result rows (empty for DML / CONVERSATIONAL).")
    chart_url:      str | None = Field(default=None, description="Relative URL to download the chart PNG.")
    chart_base64:   str | None = Field(default=None, description="Chart PNG as base64 string (if requested).")
    error:          str | None = Field(default=None, description="Error message if the query failed.")
    success:        bool  = Field(description="True when no error occurred.")
    duration_ms:    int   = Field(description="End-to-end processing time in milliseconds.")


class HealthResponse(BaseModel):
    status:    str
    db:        str
    schema_owner: str
    db_user:   str
    db_dsn:    str
    tables:    int


class SchemaResponse(BaseModel):
    schema_owner: str
    schema_text:  str


# ── Middleware: request timing ─────────────────────────────────────────────────

@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    start    = time.perf_counter()
    response = await call_next(request)
    elapsed  = int((time.perf_counter() - start) * 1000)
    response.headers["X-Process-Time-Ms"] = str(elapsed)
    return response


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get(
    "/",
    summary="Welcome",
    tags=["Info"],
)
def root():
    """Basic info about the running agent."""
    return {
        "service":      "Property MR — Oracle RAG SQL Agent",
        "version":      "1.0.0",
        "docs":         "http://192.168.1.148:8000/docs",
        "health":       "http://192.168.1.148:8000/health",
        "query":        "POST http://192.168.1.148:8000/query",
        "schema_owner": config.effective_schema_owner,
    }


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    tags=["Info"],
)
def health():
    """
    Confirms the agent can reach Oracle DB and has loaded the schema.
    Use this as a liveness/readiness probe.
    """
    db_ok       = test_connection()
    schema_text = get_schema_text()
    table_count = schema_text.count("Table:")

    return HealthResponse(
        status       = "ok" if db_ok else "degraded",
        db           = "connected" if db_ok else "unreachable — check DB_DSN in .env",
        schema_owner = config.effective_schema_owner,
        db_user      = config.DB_USER,
        db_dsn       = config.DB_DSN,
        tables       = table_count,
    )


@app.get(
    "/schema",
    response_model=SchemaResponse,
    summary="Show discovered schema",
    tags=["Info"],
)
def schema():
    """
    Returns the full schema text as discovered from Oracle.
    Useful for verifying the agent can see your tables and columns.
    """
    return SchemaResponse(
        schema_owner = config.effective_schema_owner,
        schema_text  = get_schema_text(),
    )


@app.post(
    "/schema/refresh",
    summary="Force schema re-fetch",
    tags=["Info"],
)
def schema_refresh():
    """
    Deletes the local metadata cache and re-fetches the full schema
    from Oracle. Use this after adding or altering tables.
    """
    invalidate_schema_cache()
    get_schema_text()   # triggers immediate re-fetch
    schema_text  = get_schema_text()
    table_count  = schema_text.count("Table:")
    return {
        "message": f"Schema refreshed — {table_count} table(s) loaded.",
        "tables":  table_count,
    }


@app.post(
    "/query",
    response_model=QueryResponse,
    summary="Submit a natural language question",
    tags=["Agent"],
)
def query(req: QueryRequest):
    """
    **Main endpoint.**

    Send a plain-English question and get back:
    - The classified intent
    - The generated Oracle SQL
    - A natural language answer
    - Raw result rows
    - An optional chart (PNG URL + optional base64)

    ---

    **Example questions to try in Postman:**

    ```
    What is the total FTM actual by region?
    Show total revenue by property segment.
    Top 10 properties by overdue amount.
    Compare FTM budget vs actual for each region. Plot a bar chart.
    Which properties have days overdue greater than 90?
    Show monthly revenue trend as a line chart.
    What is the total YTD actual?
    How many properties are in each zone?
    ```
    """
    t_start = time.perf_counter()

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    logger.info(f"POST /query — question: {req.question!r}")

    # ── DML guard ──────────────────────────────────────────────────────────────
    def _dml_callback(intent: str, sql: str) -> bool:
        """Auto-approve DML only when allow_dml=true in the request."""
        if req.allow_dml:
            logger.info(f"DML auto-approved via allow_dml=true  [{intent}]")
            return True
        logger.warning(f"DML blocked — allow_dml=false  [{intent}]\nSQL: {sql}")
        return False

    # ── Run agent ──────────────────────────────────────────────────────────────
    result = run_agent(
        user_query       = req.question,
        confirm_callback = _dml_callback,
    )

    duration_ms = int((time.perf_counter() - t_start) * 1000)

    # ── Chart handling ─────────────────────────────────────────────────────────
    chart_url    = None
    chart_base64 = None

    if result.chart_path and Path(result.chart_path).exists():
        chart_url = f"/chart/{Path(result.chart_path).name}"

        if req.include_chart_base64:
            with open(result.chart_path, "rb") as f:
                chart_base64 = base64.b64encode(f.read()).decode("utf-8")

    # ── DML blocked message ────────────────────────────────────────────────────
    response_text = result.response
    if result.intent in DML_INTENTS and result.cancelled and not req.allow_dml:
        response_text = (
            f"This is a {result.intent} operation. "
            f"To execute it, resend the request with  \"allow_dml\": true.\n\n"
            f"Generated SQL:\n{result.sql}"
        )

    logger.info(
        f"Query complete — intent={result.intent}  rows={result.row_count}  "
        f"duration={duration_ms}ms  error={result.error}"
    )

    return QueryResponse(
        intent        = result.intent,
        sql           = result.sql,
        response      = response_text,
        row_count     = result.row_count,
        rows_affected = result.rows_affected,
        data          = result.data,
        chart_url     = chart_url,
        chart_base64  = chart_base64,
        error         = result.error,
        success       = result.success,
        duration_ms   = duration_ms,
    )


@app.get(
    "/chart/{filename}",
    summary="Download a chart PNG",
    tags=["Agent"],
    response_class=FileResponse,
)
def get_chart(filename: str):
    """
    Returns the chart PNG file by filename.
    The filename comes from `chart_url` in the /query response.
    """
    # Security: strip any path traversal attempts
    safe_name = Path(filename).name
    path      = config.CHARTS_DIR / safe_name

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Chart '{safe_name}' not found. Charts are not persisted across restarts.",
        )

    return FileResponse(
        path        = str(path),
        media_type  = "image/png",
        filename    = safe_name,
    )


# ── Global error handler ───────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.method} {request.url}")
    return JSONResponse(
        status_code=500,
        content={
            "error":   "Internal server error",
            "detail":  str(exc),
            "success": False,
        },
    )


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host    = "0.0.0.0",
        port    = 8000,
        reload  = True,       # auto-reload on code changes during testing
        log_level = "info",
    )

