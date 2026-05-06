# 🏢 Property MR — Oracle 19c RAG SQL Agent

## 📌 Overview

A **Python-based conversational SQL agent** that connects to an Oracle 19c database and lets
business users query, analyse, and visualise property financial and revenue data using plain
English — no SQL knowledge required.

The agent uses **OCI Generative AI (Cohere Command A)** as its LLM backbone and follows a
full RAG pipeline:

```
Plain English Question
        │
        ▼
Intent Classification  →  CONVERSATIONAL / SELECT / CHART / INSERT / UPDATE / DELETE
        │
        ▼
SQL Generation         →  Schema-aware, aggregation-first, Oracle 19c syntax
        │
        ▼
SQL Validation         →  Safety check before hitting DB
        │
        ▼
Oracle Execution       →  Auto-retry with LLM-assisted SQL repair on error
        │
        ▼
Chart Rendering        →  (CHART intent only) matplotlib + seaborn PNG
        │
        ▼
Natural Language Answer →  Concise business-analyst summary
```

---

## 🏗️ Project Structure

```
oracle-rag-agent/
│
├── app.py                  # FastAPI REST API  ← run this for web/Postman access
├── main.py                 # CLI entry point   ← run this for terminal access
│
├── agent.py                # RAG pipeline orchestration
├── llm.py                  # OCI GenAI client (intent / SQL / response / chart)
├── prompt.py               # All LLM prompt templates
├── database.py             # Oracle connection pool + schema discovery
├── schema_metadata.py      # Persistent schema cache manager
├── visualizer.py           # Chart rendering (matplotlib + seaborn)
├── config.py               # Central configuration (.env loader)
├── logger.py               # Rotating file + console logger
│
├── .env                    # Credentials & tuning  ← never commit
├── requirements.txt        # Python dependencies
├── schema_metadata.json    # Auto-generated schema cache  ← gitignore this
│
├── charts/                 # Saved chart PNGs (auto-created)
├── logs/                   # Rotating log files (auto-created)
└── mr_csv/                 # Source CSV files (used by data loader only)
```

---

## ⚙️ Configuration

### `.env`

```env
# ── Oracle DB ──────────────────────────────────────────────
DB_USER=sys
DB_PASSWORD=abcd1234
DB_DSN=localhost:1521/ORCLPDB

# IMPORTANT — must match the schema that owns your tables.
# Even when connected as sys, all queries target this schema.
SCHEMA_OWNER=PROPERTY_MR_DB

DB_POOL_MIN=1
DB_POOL_MAX=5

# ── OCI GenAI ───────────────────────────────────────────────
OCI_CONFIG_PATH=D:\Oracle\Codes\oracle-agent\ap-hyderabad-1
OCI_PROFILE=DEFAULT
COMPARTMENT_OCID=ocid1.tenancy.oc1...<your_ocid>
MODEL_ID=cohere.command-a-03-2025

# ── Agent Tuning ────────────────────────────────────────────
MAX_RETRIES=2
MAX_RESPONSE_ROWS=75        # caps rows sent to LLM — prevents 429 rate limits
MAX_SCHEMA_TABLES=5         # schema tables included per SQL prompt

# ── Paths ───────────────────────────────────────────────────
# Always resolved relative to the project directory — not the working directory.
CHARTS_DIR=charts
METADATA_FILE=schema_metadata.json
```

> **`SCHEMA_OWNER` is mandatory** when `DB_USER` is a privileged account like `sys`.
> Without it the agent queries SYS's own tables, finds none of your application
> tables, and hallucinates SQL against non-existent tables.

---

## 📦 Installation

```bash
# 1. Clone / copy the project
cd oracle-rag-agent

# 2. Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux / Mac

# 3. Install dependencies
pip install -r requirements.txt
```

### `requirements.txt`

```
oracledb>=2.0.0
oci>=2.100.0
python-dotenv>=1.0.0
matplotlib>=3.8.0
seaborn>=0.13.0
pandas>=2.1.0
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
python-multipart>=0.0.9
```

---

## 🚀 Running the Agent

### Option A — FastAPI REST API (Postman / browser / any client)

```bash
python app.py
```

or

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

| URL | Description |
|-----|-------------|
| `http://localhost:8000/docs` | Interactive Swagger UI — test all endpoints in browser |
| `http://localhost:8000/redoc` | ReDoc API documentation |
| `http://192.168.1.148:8000/docs` | Access from another device on the same network |

### Option B — CLI (interactive terminal)

```bash
python main.py
```

| CLI Command | Action |
|-------------|--------|
| `exit` / `quit` | Stop the agent |
| `schema` | Print the full discovered schema |
| `refresh` | Force full schema re-fetch from Oracle |

---

## 🌐 API Reference

### Base URL
```
http://localhost:8000          (local machine)
http://192.168.1.148:8000      (other devices on same network)
```

---

### `GET /health`

Confirms the agent can reach Oracle DB and has loaded the schema.

**Response:**
```json
{
  "status":       "ok",
  "db":           "connected",
  "schema_owner": "PROPERTY_MR_DB",
  "db_user":      "sys",
  "db_dsn":       "localhost:1521/ORCLPDB",
  "tables":       2
}
```

---

### `GET /schema`

Returns the full schema text as discovered from Oracle — all tables, columns, and datatypes.

---

### `POST /schema/refresh`

Deletes the local metadata cache and forces a full re-fetch from Oracle.
Use this after adding or altering tables in `PROPERTY_MR_DB`.

---

### `POST /query` ⭐ Main endpoint

**Request body:**

```json
{
  "question":             "Show total FTM actual by region as a bar chart",
  "allow_dml":            false,
  "include_chart_base64": false
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | string | required | Plain-English question about your data |
| `allow_dml` | bool | `false` | Set `true` to permit INSERT / UPDATE / DELETE |
| `include_chart_base64` | bool | `false` | Include chart PNG as base64 string in the response |

**Response:**

```json
{
  "intent":        "CHART",
  "sql":           "SELECT REGION, SUM(NVL(FTM_ACT,0)) AS TOTAL_FTM_ACT FROM FINANCIAL_DATA GROUP BY REGION ORDER BY TOTAL_FTM_ACT DESC",
  "response":      "NCR leads with the highest FTM actual of 24,310 followed by ...",
  "row_count":     8,
  "rows_affected": 0,
  "data":          [{"REGION": "NCR", "TOTAL_FTM_ACT": 24310}, ...],
  "chart_url":     "/chart/chart_bar_20260506_142301.png",
  "chart_base64":  null,
  "error":         null,
  "success":       true,
  "duration_ms":   4821
}
```

| Field | Description |
|-------|-------------|
| `intent` | Classified intent: `SELECT`, `CHART`, `INSERT`, `UPDATE`, `DELETE`, `CONVERSATIONAL` |
| `sql` | Exact Oracle SQL that was executed |
| `response` | Natural language business-analyst summary |
| `row_count` | Number of rows the DB query returned |
| `rows_affected` | Rows affected (DML only, else 0) |
| `data` | Raw result rows as array of objects |
| `chart_url` | Relative path to download the chart PNG — use with `GET /chart/{filename}` |
| `chart_base64` | Chart PNG as base64 string (only when `include_chart_base64: true`) |
| `error` | `null` on success, error message string on failure |
| `success` | `true` when no error occurred |
| `duration_ms` | Total end-to-end processing time in milliseconds |

---

### `GET /chart/{filename}`

Downloads a generated chart PNG by filename.
The filename comes from the `chart_url` field in a `/query` response.

```
GET /chart/chart_bar_20260506_142301.png
```

---

## 📬 Postman Testing Guide

### Setup

1. Open Postman
2. Create a new **Collection** → name it `Property MR RAG Agent`
3. Set a **Collection Variable**: `base_url = http://192.168.1.148:8000`

---

### Request 1 — Health Check

```
GET {{base_url}}/health
```

---

### Request 2 — View Schema

```
GET {{base_url}}/schema
```

---

### Request 3 — Data Query

```
POST {{base_url}}/query
Content-Type: application/json

{
  "question": "What is the total FTM actual by region?"
}
```

---

### Request 4 — Chart Query

```
POST {{base_url}}/query
Content-Type: application/json

{
  "question": "Show total revenue by property segment as a horizontal bar chart",
  "include_chart_base64": false
}
```

After getting the response, copy `chart_url` (e.g. `/chart/chart_horizontal_bar_....png`) and:

```
GET {{base_url}}/chart/chart_horizontal_bar_20260506_142301.png
```

In Postman click **Send and Download** to save the PNG.

---

### Request 5 — DML (with confirmation)

```
POST {{base_url}}/query
Content-Type: application/json

{
  "question":   "Update the status to PAID for property XYZ where revenue date is 2025-01-01",
  "allow_dml":  true
}
```

> When `allow_dml` is `false` (default), DML queries return a safe message
> explaining that `allow_dml: true` is required — they do **not** execute.

---

### Example questions to test

```
# Aggregations
What is the total FTM actual across all regions?
Show total revenue by property segment.
What is the average YTD budget by region?
How many properties are in each zone?

# Filters
Which properties have days overdue greater than 90?
Show properties in NCR with FTM actual greater than 1000.
List properties where status is OVERDUE.

# Rankings
Top 10 properties by total revenue.
Top 5 regions by FTM actual.
Which region has the highest YTD budget?

# Charts
Plot total FTM actual by region as a bar chart.
Show monthly revenue trend as a line chart.
Revenue by project segment as a pie chart.
Compare FTM budget vs FTM actual by region — multi-series chart.
Distribution of overdue days as a histogram.

# Conversational
Hello
What can you do?
Which tables do you have access to?
```

---

## 🗄️ Database Schema

### Connection Details

| Setting | Value |
|---------|-------|
| Host | `localhost:1521/ORCLPDB` |
| Connected as | `sys` (SYSDBA) |
| Working schema | `PROPERTY_MR_DB` |
| Connection mode | `oracledb` thin mode (no Oracle Client required) |

Every acquired connection runs `ALTER SESSION SET CURRENT_SCHEMA = PROPERTY_MR_DB`
so all generated SQL uses **unqualified table names** and resolves correctly
regardless of which DB user is connected.

---

### Table: `FINANCIAL_DATA` — 2,654 rows

Monthly financial metrics per property.

| Column | Type | Description |
|--------|------|-------------|
| REGION | VARCHAR2 | Region name |
| MAIN_PROJ | VARCHAR2 | Project name |
| PROPERTY_NAME | VARCHAR2 | Property identifier |
| MNTH | VARCHAR2 | Month name |
| MNTH_NUM | NUMBER | Month number |
| FTM_BUD | NUMBER | Full-time budget |
| YTD_BUD | NUMBER | Year-to-date budget |
| FTM_ACT | NUMBER | Full-time actual |
| YTD_ACT | NUMBER | Year-to-date actual |
| QTD_BUD | NUMBER | Quarter-to-date budget |
| QTD_ACT | NUMBER | Quarter-to-date actual |
| "DATE" | DATE | Record date ⚠️ Oracle reserved word — always double-quoted in SQL |

---

### Table: `REVENUE_DATA` — 4,724 rows

Revenue, collection, and overdue data per property unit.

| Column | Type | Description |
|--------|------|-------------|
| PROJECT_SEGMENT | VARCHAR2 | Project segment |
| MAIN_PROJECT | VARCHAR2 | Main project |
| PROPERTY_NAME | VARCHAR2 | Property name |
| PROPERTY_CODE | VARCHAR2 | Property code |
| UNIT_CODE | VARCHAR2 | Unit code |
| UNIT_CODE1 | VARCHAR2 | Alternate unit code |
| ZONE | VARCHAR2 | Zone classification |
| STATUS | VARCHAR2 | Status flag |
| STATUS_DESC | VARCHAR2 | Status description |
| TOTAL_REVENUE | NUMBER | Total revenue |
| AMOUNT_PAID | NUMBER | Paid amount |
| DAYS | NUMBER | Days overdue |
| REVENUE_DATE | DATE | Revenue date |
| OVERDUEDUE_DATE | DATE | Due date |
| IOP_SENT_DATE | DATE | IOP sent date |
| CP_POSS_DATE | DATE | CP possession date |
| IOP_PRINT_DATE | DATE | IOP print date |
| DUENOT_DUE | VARCHAR2 | Due / not-due flag |
| REV_OVERVIEW | VARCHAR2 | Revenue overview category |
| COLLECTIBLE_STATUS | VARCHAR2 | Collectibility status |
| REVENUE_STATUS | VARCHAR2 | Revenue status |
| COL_30_DAYS | NUMBER | Collected in ≤ 30 days |
| COL_3160_DAYS | NUMBER | Collected in 31–60 days |
| COL_6190_DAYS | NUMBER | Collected in 61–90 days |
| COL_91180_DAYS | NUMBER | Collected in 91–180 days |
| COL_181365_DAYS | NUMBER | Collected in 181–365 days |
| COL_365_DAYS | NUMBER | Collected in > 365 days |

---

## 🧠 Schema Metadata Cache

Avoids a full column-by-column Oracle re-fetch on every startup.

```
Agent Startup
     │
     ├─ schema_metadata.json exists?
     │
     │  YES → Fetch lightweight fingerprint from Oracle
     │         (1 query: table names + column counts)
     │             │
     │         Fingerprint matches stored hash?
     │             │
     │         YES → Load from file  ✅ fast
     │         NO  → Full fetch from Oracle → overwrite file
     │
     NO  → Full fetch from Oracle → create file
```

- File is always written to the **project directory** — never the working directory —
  so it is found consistently no matter where you launch the agent from.
- Type `refresh` in CLI or call `POST /schema/refresh` in the API after schema changes.
- Add `schema_metadata.json` to `.gitignore`.

---

## 📊 Supported Chart Types

| Type | Best For |
|------|----------|
| `bar` | ≤ 10 categories, one numeric measure |
| `horizontal_bar` | > 10 categories, one numeric measure |
| `line` | Time-series / monthly trend |
| `pie` | Part-of-whole proportions (≤ 8 slices) |
| `scatter` | Correlation between two different numeric columns |
| `histogram` | Distribution / frequency of a single numeric column |
| `box` | Spread and outliers across categories |
| `violin` | Distribution shape across categories |
| `multi_series` | 2+ numeric measures compared side-by-side |

Charts auto-upgrade (e.g. `bar → horizontal_bar` when categories > 10) and
auto-correct swapped axes before rendering.

---

## 🔧 Known Oracle Quirks Handled

### `DATE` is an Oracle reserved word

The `DATE` column in `FINANCIAL_DATA` must always be double-quoted.
The SQL generation prompt explicitly instructs the LLM:

```sql
-- ✅ Correct
SELECT TO_CHAR("DATE", 'YYYY-MM') AS MONTH, SUM(NVL(FTM_ACT, 0)) AS TOTAL
FROM FINANCIAL_DATA
GROUP BY TO_CHAR("DATE", 'YYYY-MM')

-- ❌ Wrong — causes ORA-00936
SELECT TO_CHAR(DATE, 'YYYY-MM') AS MONTH ...
```

### Datatype change on non-empty columns

Oracle does not allow direct datatype changes on columns that contain data.
`datatype_fixer.py` uses the add-convert-drop-rename pattern:

```
1. Add new column with target type
2. Copy + convert existing data
3. Drop old column
4. Rename new column to original name
```

---

## 📝 Logging

```
logs/log_YYYYMMDD_HHMMSS.log
```

```
2026-05-06 14:22:01 | INFO     | oracle_bot.app      | POST /query — question: 'Show revenue by region'
2026-05-06 14:22:02 | INFO     | oracle_bot.llm      | Intent: SELECT
2026-05-06 14:22:03 | INFO     | oracle_bot.llm      | Generated SQL: SELECT REGION, SUM(NVL(TOTAL_REVENUE,0)) ...
2026-05-06 14:22:03 | INFO     | oracle_bot.database | SELECT returned 8 row(s)
2026-05-06 14:22:05 | INFO     | oracle_bot.llm      | [response] NCR leads with total revenue of ...
2026-05-06 14:22:05 | INFO     | oracle_bot.app      | Query complete — intent=SELECT  rows=8  duration=4210ms
```

| Level | Destination |
|-------|-------------|
| DEBUG | Log file only |
| INFO | Log file + console |
| WARNING | Log file + console |
| ERROR | Log file + console |

---

## 🛠️ Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Agent hallucinates table names | `SCHEMA_OWNER` not set | Add `SCHEMA_OWNER=PROPERTY_MR_DB` to `.env` |
| `ORA-00936` on date queries | `DATE` column not quoted | Fixed in prompt — type `refresh` if it recurs |
| `schema_metadata.json` not found on re-launch | Stale CWD-relative path (old bug) | Fixed — now anchored to project directory |
| `429 Too Many Requests` from OCI | Large unaggregated result sent to LLM | Lower `MAX_RESPONSE_ROWS` in `.env` (default 75) |
| Chart crashes — `NoneType has no attribute upper` | `category_column` returned null by LLM | Fixed — both LLM and visualizer infer a fallback column |
| `Blocked keyword 'create'` on valid queries | LLM explanation text parsed as SQL (old bug) | Fixed — `_extract_sql()` strips prose before validation |
| Scatter plot maps same column to both axes | Single numeric column in result | Fixed — falls back to horizontal_bar automatically |
| DB query returns 0 rows | Table prefix in SQL (e.g. `PROPERTY_MR_DB.TABLE`) | Fixed — `CURRENT_SCHEMA` is set per connection |
| `uvicorn: command not found` | uvicorn not installed | Run `pip install uvicorn[standard]` |

---

## ✅ Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| Oracle Connection | ✅ Working | SYSDBA + `CURRENT_SCHEMA` per connection |
| Schema Discovery | ✅ Working | `ALL_*` views, owner-filtered |
| Schema Metadata Cache | ✅ Working | Persistent JSON, project-anchored path |
| Intent Classification | ✅ Working | 6 intents, JSON extraction handles Cohere formatting |
| SQL Generation | ✅ Working | Aggregation-first, `"DATE"` reserved word enforced |
| SQL Extraction | ✅ Fixed | Handles prose + code-fence wrapping from Cohere |
| SQL Validation | ✅ Fixed | No more false-positive `create` keyword blocks |
| DB Execution | ✅ Working | Retry loop with LLM-assisted SQL repair |
| NL Response | ✅ Fixed | Row cap (75) prevents 179k-char prompts and 429s |
| Chart Rendering | ✅ Fixed | Null category guard, scatter validation, axis auto-swap |
| 429 Rate Limiting | ✅ Handled | Graceful `AgentResult` returned — no loop crashes |
| DML Safety | ✅ Working | User confirmation required in CLI; `allow_dml` flag in API |
| FastAPI REST API | ✅ Working | `/query`, `/health`, `/schema`, `/chart/{file}` |
| Swagger UI | ✅ Working | Auto-generated at `/docs` |
| Logging | ✅ Working | Rotating per-run log file + console |

---

## 🚀 Deployment

For deploying to **Oracle Cloud Infrastructure (OCI)**, see [`DEPLOYMENT.md`](./DEPLOYMENT.md).

It covers:
- Creating an OCI Compute VM
- Uploading code and OCI config
- Oracle DB connectivity options (local / OCI DB)
- Running as a `systemd` service
- Nginx reverse proxy with HTTPS
- Security hardening checklist

---

## 🔮 Future Improvements

- [ ] Multi-turn conversation memory (remember previous query context)
- [ ] Export query results to Excel / CSV on request
- [ ] Streamlit web UI with embedded chart display
- [ ] Auto-indexing recommendations for slow queries
- [ ] OCI Vault integration for secrets (replace plain-text `.env` passwords)
- [ ] API key authentication middleware
- [ ] Table partitioning for large historical datasets

---

## 👨‍💻 Author

Developed as part of an Oracle 19c + Python data ingestion, analytics, and conversational
SQL agent pipeline for Property MR reporting.
