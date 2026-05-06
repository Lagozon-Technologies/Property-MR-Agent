# 📊 Property MR Data Loader (Oracle 19c)

## 📌 Overview

This project provides a **Python-based data loader** that ingests two CSV spreadsheets into an Oracle 19c database.

It ensures:

* Clean column names
* Separate table creation (no merging)
* Proper logging
* Post-load datatype correction for analytics

---

## 🏗️ Architecture

```
Project Structure
│
├── db_loader.py          # Main data loading script
├── datatype_fixer.py     # Fixes column datatypes (NUMBER, DATE)
├── config.py             # Loads environment variables
├── logger.py             # Centralized logging
├── .env                  # Database credentials
└── mr_csv/               # Source CSV files
```

---

## ⚙️ Configuration

### `.env`

```env
DB_USER=PROPERTY_MR_DB
DB_PASSWORD=abcd1234
DB_DSN=localhost:1521/ORCLPDB
```

---

## 🚀 How It Works

### Step 1: Load CSV Data

Run:

```bash
python db_loader.py
```

This will:

* Connect to Oracle DB
* Read both CSV files
* Clean column names
* Create two tables:

  * `FINANCIAL_DATA`
  * `REVENUE_DATA`
* Insert all records

---

### Step 2: Fix Datatypes

Run:

```bash
python datatype_fixer.py
```

This will:

* Convert numeric columns → `NUMBER`
* Convert date columns → `DATE`
* Safely handle invalid values

---

## 🗄️ Database Schema

### Schema Name

```
PROPERTY_MR_DB
```

---

## 📘 Tables

---

### 1️⃣ FINANCIAL_DATA

Contains monthly financial metrics.

#### Columns

| Column        | Type     | Description            |
| ------------- | -------- | ---------------------- |
| REGION        | VARCHAR2 | Region name            |
| MAIN_PROJ     | VARCHAR2 | Project name           |
| PROPERTY_NAME | VARCHAR2 | Property identifier    |
| MNTH          | VARCHAR2 | Month name             |
| MNTH_NUM      | VARCHAR2 | Month number           |
| FTM_BUD       | NUMBER   | Full-time budget       |
| YTD_BUD       | NUMBER   | Year-to-date budget    |
| FTM_ACT       | NUMBER   | Full-time actual       |
| YTD_ACT       | NUMBER   | Year-to-date actual    |
| QTD_BUD       | NUMBER   | Quarter-to-date budget |
| QTD_ACT       | NUMBER   | Quarter-to-date actual |
| DATE          | DATE     | Record date            |

---

### 2️⃣ REVENUE_DATA

Contains revenue and collection-related data.

#### Columns

| Column          | Type     | Description         |
| --------------- | -------- | ------------------- |
| PROJECT_SEGMENT | VARCHAR2 | Project segment     |
| MAIN_PROJECT    | VARCHAR2 | Main project        |
| PROPERTY_NAME   | VARCHAR2 | Property name       |
| PROPERTY_CODE   | VARCHAR2 | Property code       |
| ZONE            | VARCHAR2 | Zone classification |
| STATUS          | VARCHAR2 | Status flag         |
| TOTAL_REVENUE   | NUMBER   | Total revenue       |
| AMOUNT_PAID     | NUMBER   | Paid amount         |
| DAYS            | NUMBER   | Days overdue        |
| REVENUE_DATE    | DATE     | Revenue date        |
| OVERDUEDUE_DATE | DATE     | Due date            |

*(Other categorical columns remain VARCHAR2)*

---

## 📊 Example Queries

### First 10 Rows Only
```sql
SELECT *
FROM REVENUE_DATA
FETCH FIRST 10 ROWS ONLY;
```

### Total Revenue

```sql
SELECT SUM(TOTAL_REVENUE) FROM REVENUE_DATA;
```

---

### Revenue by Property

```sql
SELECT PROPERTY_NAME, SUM(TOTAL_REVENUE)
FROM REVENUE_DATA
GROUP BY PROPERTY_NAME;
```

---

### Financial Summary by Region

```sql
SELECT REGION, SUM(FTM_ACT)
FROM FINANCIAL_DATA
GROUP BY REGION;
```

---

### Filter by Date

```sql
SELECT *
FROM REVENUE_DATA
WHERE REVENUE_DATE > DATE '2025-01-01';
```

---

## 🧾 Logging

* Logs are stored in `/logs`
* Format:

```
YYYY-MM-DD HH:MM:SS | LEVEL | MODULE | MESSAGE
```

---

## ⚠️ Important Notes

* Oracle does **not allow datatype changes on non-empty columns**, so `datatype_fixer.py` uses:

  * Add new column
  * Convert data
  * Drop old column
  * Rename new column

* Invalid numeric values are converted to `NULL`

---

## ✅ Current Status

| Feature       | Status     |
| ------------- | ---------- |
| Data Load     | ✅ Complete |
| Schema Design | ✅ Clean    |
| Datatypes     | ✅ Correct  |
| Logging       | ✅ Enabled  |
| SQL Ready     | ✅ Yes      |

---

## 🔮 Future Improvements

* Auto datatype detection during load
* Indexing for performance
* Partitioning large tables
* Integration with SQL/RAG Agent

---

## 👨‍💻 Author

Developed as part of Oracle 19c + Python data ingestion and analytics pipeline.

---
