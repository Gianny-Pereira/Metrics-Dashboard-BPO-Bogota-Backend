# Metrics Dashboard — Backend API

Flask REST API that powers a workload and productivity metrics dashboard for a land management team split across two groups: **BPO** (Bogotá-based staff) and **TLS** (all other landmen).

---

## Table of Contents

1. [Setup](#setup)
2. [Data Model](#data-model)
3. [Filter System](#filter-system)
4. [BPO / TLS Team Logic](#bpo--tls-team-logic)
5. [Date Logic](#date-logic)
6. [Endpoints](#endpoints)
   - [GET /api/dashboard](#get-apidashboard) ← preferred for frontend
   - [GET /api/experience-by-state](#get-apiexperience-by-state)
   - [GET /api/availability](#get-apiavailability)
   - [GET /api/worklogs/summary](#get-apiworklogssummary)
   - [GET /api/aoi-hours](#get-apiaoi-hours)
   - [GET /api/warnings](#get-apiwarnings)
   - [GET /api/worklogs/date-range](#get-apiworklogsdate-range)
   - [GET /api/landmen](#get-apilandmen)
   - [Other endpoints](#other-endpoints)
7. [Performance](#performance)
8. [Recommended Indexes](#recommended-indexes)
9. [Validation Commands](#validation-commands)

---

## Setup

**Requirements:** Python 3.11+

```bash
# 1. Create and activate virtual environment
python -m venv venv
venv\Scripts\activate.bat        # Windows
# source venv/bin/activate       # macOS / Linux

# 2. Install dependencies
python -m pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env
# Edit .env and set DATABASE_URL and ADMIN_PASSWORD

# 4. Run database migrations
python -m flask db upgrade

# 5. Start the development server
python -m flask run --port 5000
```

> Always use `python -m flask` instead of `flask` directly to avoid PATH issues inside the venv.

**Environment variables (`.env`)**

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | yes | SQLAlchemy connection string. Use `sqlite:///bpo_dashboard.db` for local dev or a PostgreSQL URI for production. |
| `ADMIN_PASSWORD` | no | Password for destructive admin operations (default: `changeme`). |
| `DEBUG_FILTERS` | no | Set to `1` to enable per-request filter debug logs in `get_availability`. |

**Base URL:** `http://localhost:5000`

All responses are JSON. Error responses have the shape `{ "error": "..." }`.

---

## Data Model

### Landman
A land professional who logs work hours. Name is unique. Each landman is classified as **BPO** or **TLS** at query time based on the hardcoded BPO name list in `app.py`.

### WorkLog
The core time-entry record. Every row belongs to one Landman and stores:
- `date`, `hours`, `work_type`, `status`
- optional `county` and `state` (two-letter code, e.g. `"PA"`)
- optional FK to `Prospect` and `Project`
- `row_hash` — SHA-256 fingerprint used to skip duplicate imports

Hours are stored as actual hours (day-units from the Excel sheet are multiplied by 8 on import).

### Client
A company that hires landmen. Name is unique.

### Prospect
A named development area (AOI) scoped to a Client. Multiple worklogs may reference the same prospect.

### Project
A named engagement scoped to a Client, with optional start/end dates.

### County / State parsing
The Excel `County` column is stored in `"County, ST"` format (e.g. `"Fayette, PA"`). On import, `_split_county_state()` splits this into separate `county` and `state` columns. The `state` value is always stored as a two-letter abbreviation.

---

## Filter System

All dashboard endpoints accept the same optional query parameters. Filters combine with **AND** logic across dimensions and **OR** logic within the same dimension.

| Param | Description |
|-------|-------------|
| `team` | `BPO` or `TLS` — repeated for both: `?team=BPO&team=TLS` |
| `landman` | Landman name. Repeat for multiple: `?landman=A%20Criollo&landman=G%20Pereira`. Also accepts a single comma-separated value for backward compatibility. |
| `client` | Client name. Repeat for multiple. **Never split on commas** — client names may contain commas. |
| `state` | Two-letter code (`PA`) or full name (`Pennsylvania`). Both are normalised to the stored two-letter code. |
| `county` | County name, optionally with state: `Fayette` or `Fayette, PA`. Multiple county params are OR'd. |
| `prospect` | Prospect / AOI name. Repeat for multiple. |
| `period` | `YYYY-MM` — restricts to a full calendar month. |
| `start_date` | `YYYY-MM-DD` — start of an exact date range (use with `end_date`). |
| `end_date` | `YYYY-MM-DD` — end of an exact date range (use with `start_date`). |

**Date priority:** `start_date`/`end_date` → `period` → current calendar month (default).

**Examples:**

```
# BPO team, specific client
GET /api/dashboard?team=BPO&client=Arch%20Energy%20Management

# TLS team, Pennsylvania only, custom date range
GET /api/dashboard?team=TLS&state=PA&start_date=2025-01-01&end_date=2025-03-31

# Specific county+state
GET /api/dashboard?county=Fayette%2C%20PA

# Multiple landmen
GET /api/dashboard?landman=A%20Criollo&landman=G%20Pereira
```

---

## BPO / TLS Team Logic

The BPO team is defined by a fixed list of 46 landman names stored as `BPO_LANDMAN_NAMES` (a `frozenset`) near the top of `app.py`. **TLS is never hardcoded** — it is dynamically computed as every landman in the database whose name does not appear in the BPO list.

### Normalisation for matching

Names are normalised before comparison using `normalize_landman_name()`:
- lowercase
- trim + collapse multiple spaces
- strip diacritics via `unicodedata.normalize("NFD")` (so `"Montañez"` matches `"Montanez"`)

**Display names are never modified.** Normalisation is classification-only.

### Filter behavior

| `?team=` value | Result |
|----------------|--------|
| `BPO` | Only BPO landmen included |
| `TLS` | Only non-BPO landmen included |
| `BPO&team=TLS` | Both teams — equivalent to no team restriction |
| *(omitted)* | All landmen — same as both teams |

Team filter combines with explicit `landman=` filter using intersection: if a named landman is not in the selected team they are excluded.

---

## Date Logic

### Global date range

`GET /api/worklogs/date-range` returns the earliest and latest `WorkLog.date` in the entire database. **No filters are applied** — this always reflects full data coverage and is used by the frontend to initialise the date picker.

### Dashboard date filtering

All dashboard endpoints apply date filtering using one of three modes (in priority order):

1. **Exact range** — `?start_date=2025-01-01&end_date=2025-03-31`
2. **Calendar month** — `?period=2025-04`
3. **Default** — current calendar month (no date params needed)

---

## Endpoints

### GET /api/dashboard

**Preferred endpoint for frontend dashboard refreshes.** Returns all dashboard data in a single response. Accepts every filter param described in the [Filter System](#filter-system) section.

```
GET /api/dashboard
GET /api/dashboard?team=BPO&period=2025-04
GET /api/dashboard?team=TLS&client=Arch%20Energy%20Management&state=PA
```

**Response `200`**
```json
{
  "availability": [
    { "id": 1, "name": "A Criollo", "Field Work": 24.0, "Training - BPO": 8.0 }
  ],
  "summary": {
    "client": [
      { "label": "Arch Energy Management", "hours": 120.5 }
    ],
    "state": [
      { "label": "PA", "hours": 80.0 }
    ],
    "county": [
      { "label": "Fayette, PA", "county": "Fayette", "state": "PA", "hours": 40.0 }
    ]
  },
  "aoi_hours": [
    { "prospect": "AOI North", "avgHours": 6.5 }
  ],
  "warnings": [
    { "landman": "A Criollo", "date": "2025-04-01", "total": 6.0, "status": "incomplete" }
  ],
  "totals": {
    "total_hours": 2400.0,
    "project_hours": 320.0,
    "productivity_percent": 13.3
  }
}
```

Each section uses the same calculation and response shape as the corresponding individual endpoint. `totals.total_hours` is derived in-memory from the availability data — no extra database query.

---

### GET /api/experience-by-state

Identifies landmen with a minimum amount of experience in a specific state, calculated from the **date span** of their work (not just total hours logged).

#### Experience calculation

For each landman in the selected state (within the applied date range):

```
first_work_date   = earliest WorkLog.date matching all filters
last_work_date    = latest  WorkLog.date matching all filters
experience_days   = (last_work_date − first_work_date).days + 1   ← inclusive
experience_months = experience_days / 30.4375
```

#### Threshold and tolerance

```
effective_min_months = max(0, min_months − tolerance_months)
qualified            = experience_months >= effective_min_months
```

**Example:** `min_months=3`, `tolerance_months=0.5` → `effective_min_months=2.5`. Landmen with ≥ 2.5 months in the state qualify.

#### Parameters

| Param | Required | Default | Description |
|-------|----------|---------|-------------|
| `state` | **yes** | — | Two-letter code (`PA`) or full name (`Pennsylvania`). Both are normalised. |
| `min_months` | no | `3` | Minimum months of experience required. |
| `tolerance_months` | no | `0` | Tolerance subtracted from `min_months`. Cannot make effective threshold negative. |
| `team` | no | — | `BPO` or `TLS` — same as all other dashboard endpoints. |
| `landman` | no | — | Filter to specific landmen. |
| `client` | no | — | Filter to work logs tied to a specific client. |
| `county` | no | — | Filter by county, e.g. `Fayette` or `Fayette, PA`. |
| `prospect` | no | — | Filter to a specific prospect/AOI. |
| `period` | no | current month | `YYYY-MM` calendar month. |
| `start_date` | no | — | `YYYY-MM-DD` start of date range (use with `end_date`). |
| `end_date` | no | — | `YYYY-MM-DD` end of date range (use with `start_date`). |

Date filter priority: `start_date`/`end_date` → `period` → current calendar month.

Experience is calculated **only from work logs that fall within the applied date range** — the date filter applies fully.

#### Examples

```
# Basic — all landmen in PA, current month, default 3-month threshold
GET /api/experience-by-state?state=PA

# Full state name (same result)
GET /api/experience-by-state?state=Pennsylvania

# Custom threshold with tolerance
GET /api/experience-by-state?state=PA&min_months=3&tolerance_months=0.5

# BPO only
GET /api/experience-by-state?state=PA&team=BPO

# Specific client + date range
GET /api/experience-by-state?state=PA&client=Arch%20Energy%20Management&start_date=2026-01-01&end_date=2026-03-31

# County with state
GET /api/experience-by-state?state=PA&county=Fayette%2C%20PA
```

#### Response `200`

Results include **both qualified and non-qualified landmen**. Sorting: qualified first, then by `experience_months` descending, then by `total_hours` descending.

```json
{
  "state": "PA",
  "state_label": "Pennsylvania",
  "min_months": 3,
  "tolerance_months": 0.5,
  "effective_min_months": 2.5,
  "date_range": {
    "start_date": "2026-01-01",
    "end_date": "2026-03-31"
  },
  "results": [
    {
      "landman": "A Criollo",
      "team": "BPO",
      "state": "PA",
      "first_work_date": "2026-01-10",
      "last_work_date": "2026-03-25",
      "experience_days": 75,
      "experience_months": 2.46,
      "total_hours": 148.5,
      "clients": ["Arch Energy Management"],
      "prospects": ["AOI North", "Land Admin"],
      "client_count": 1,
      "prospect_count": 2,
      "qualified": false
    }
  ]
}
```

Each result row includes:

| Field | Description |
|-------|-------------|
| `clients` | Sorted list of unique client names from the filtered work logs for that landman/state. |
| `prospects` | Sorted list of unique prospect names from the filtered work logs for that landman/state. |
| `client_count` | Number of unique clients (`len(clients)`). |
| `prospect_count` | Number of unique prospects (`len(prospects)`). |

These lists respect all active filters — `client`, `prospect`, `team`, `landman`, `county`, and the date range — so they always reflect exactly the same dataset used to compute experience dates and hours.

Returns `"results": []` when no work logs match the filters — the response envelope is always present.

#### Response `400`

```json
{ "error": "state is required" }
```

Returned when the `state` param is missing or empty.

---

### GET /api/availability

Per-landman hour totals broken down by `work_type`. Accepts all filter params.

```
GET /api/availability?period=2025-04&team=BPO
```

**Response `200`**
```json
[
  { "id": 1, "name": "A Criollo", "Field Work": 24.0, "Holiday": 8.0 }
]
```

Each object has `id` and `name` plus one key per `work_type` found for that landman in the filtered period. `work_type` values are open strings from the source Excel sheet (e.g. `"Training - BPO"`, `"Holiday"`, `"Field Work"`).

---

### GET /api/worklogs/summary

Total hours aggregated by a single dimension. Accepts all filter params plus the required `group_by`.

| Param | Values | Required |
|-------|--------|----------|
| `group_by` | `client` \| `state` \| `county` | yes |

```
GET /api/worklogs/summary?group_by=client&team=TLS
GET /api/worklogs/summary?group_by=state&period=2025-04
GET /api/worklogs/summary?group_by=county&state=PA
```

**Response `200`** — ordered by hours descending.

`group_by=client` / `group_by=state`:
```json
[{ "label": "Arch Energy Management", "hours": 120.5 }]
```

`group_by=county`:
```json
[{ "label": "Fayette, PA", "county": "Fayette", "state": "PA", "hours": 40.0 }]
```

Rows with a null or empty grouping column are omitted.

**Response `400`** — invalid `group_by` value.

---

### GET /api/aoi-hours

Average hours per prospect. Accepts all filter params.

```
GET /api/aoi-hours?team=BPO&period=2025-04
```

**Response `200`**
```json
[{ "prospect": "AOI North", "avgHours": 6.5 }]
```

Falls back to a deterministic random sample keyed on client names when no real work-log data matches the filters.

---

### GET /api/warnings

Days where a landman's total hours are not exactly 8. Accepts all filter params.

```
GET /api/warnings?team=BPO&period=2025-04
```

**Response `200`**
```json
[
  { "landman": "A Criollo", "date": "2025-04-01", "total": 6.0, "status": "incomplete" },
  { "landman": "G Pereira",  "date": "2025-04-02", "total": 9.0, "status": "over" }
]
```

`status` is `"incomplete"` when `total < 8` or `"over"` when `total > 8`.

---

### GET /api/worklogs/date-range

Returns the earliest and latest `WorkLog.date` in the entire database. **No filters applied.**

```
GET /api/worklogs/date-range
```

**Response `200`**
```json
{ "min_date": "2024-01-02", "max_date": "2025-04-30" }
```

Returns `null` for both fields when the table is empty.

---

### GET /api/landmen

Returns all landmen ordered by name, each with their team classification.

```
GET /api/landmen
```

**Response `200`**
```json
[
  { "id": 1, "name": "A Criollo", "email": null, "role": null, "status": "active", "team": "BPO" },
  { "id": 2, "name": "M Sellers",  "email": null, "role": null, "status": "active", "team": "TLS" }
]
```

---

### Other endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/landmen/<id>/worklogs` | All work logs for a single landman, ordered by date descending. |
| `GET /api/clients` | All clients ordered by name. |
| `GET /api/prospects[?client_id=<id>]` | All prospects, optionally filtered by client. |
| `GET /api/projects[?client_id=<id>]` | All projects, optionally filtered by client. |
| `GET /api/worklogs/entries?landman=<name>&date=<YYYY-MM-DD>` | Individual work-log rows for a landman on a specific date. |
| `POST /api/worklogs` | Create a single work-log entry (JSON body). |
| `POST /api/import` | Upload a CSV, TSV, or XLSX file to bulk-import work logs. Idempotent — re-importing the same file is safe. |
| `GET /api/health` | Returns `{ "status": "ok" }`. |
| `POST /api/verify-password` | Verify the admin password. |
| `DELETE /api/clear-database` | Truncate all tables (requires admin password). |

---

## Performance

**Use `/api/dashboard` for all dashboard refresh calls.** It resolves filters once and runs all section queries in a single request, replacing what previously required 6 separate HTTP round trips.

The individual endpoints (`/api/availability`, `/api/worklogs/summary`, etc.) remain available for compatibility, isolated testing, and cases where only one section is needed.

**Query optimisation inside `/api/dashboard`:**
- All filter parameters are parsed and resolved exactly once.
- `totals.total_hours` is computed in-memory from the already-fetched availability rows — no extra database query.
- Three closure helpers (`_lm`, `_pc`, `_sc`) apply the team/landman, prospect/client, and state/county filters consistently across all sub-queries without copy-pasting filter logic.

---

## Recommended Indexes

The following indexes do not exist yet but would significantly improve dashboard query performance at scale. Add them via a new Alembic migration:

```python
# flask db migrate -m "add dashboard performance indexes"
# then add to the generated migration:

op.create_index("ix_worklogs_date",        "worklogs", ["date"])
op.create_index("ix_worklogs_landman_id",  "worklogs", ["landman_id"])
op.create_index("ix_worklogs_prospect_id", "worklogs", ["prospect_id"])
op.create_index("ix_worklogs_state",       "worklogs", ["state"])
op.create_index("ix_prospects_name",       "prospects", ["name"])
```

`Landman.name` and `Client.name` already have implicit indexes via their `unique=True` constraint. `WorkLog.row_hash` already has an explicit index for deduplication.

---

## Validation Commands

```bash
# Check all Python files compile cleanly
python -m compileall .

# Start the backend
python -m flask run --port 5000

# Quick smoke tests (requires a running server)
curl http://localhost:5000/api/health
curl http://localhost:5000/api/worklogs/date-range
curl http://localhost:5000/api/landmen

# Dashboard — no filters (defaults to current month)
curl http://localhost:5000/api/dashboard

# Dashboard — BPO team only
curl "http://localhost:5000/api/dashboard?team=BPO"

# Dashboard — TLS team only
curl "http://localhost:5000/api/dashboard?team=TLS"

# Experience by state — missing state (expect 400)
curl "http://localhost:5000/api/experience-by-state"

# Experience by state — basic
curl "http://localhost:5000/api/experience-by-state?state=PA"

# Experience by state — full state name
curl "http://localhost:5000/api/experience-by-state?state=Pennsylvania"

# Experience by state — threshold with tolerance
curl "http://localhost:5000/api/experience-by-state?state=PA&min_months=3&tolerance_months=0.5"

# Experience by state — BPO only
curl "http://localhost:5000/api/experience-by-state?state=PA&team=BPO"

# Experience by state — date range
curl "http://localhost:5000/api/experience-by-state?state=PA&start_date=2026-01-01&end_date=2026-03-31"

# Experience by state — client filter
curl "http://localhost:5000/api/experience-by-state?state=PA&client=Arch%20Energy%20Management"

# Experience by state — county with state embedded
curl "http://localhost:5000/api/experience-by-state?state=PA&county=Fayette%2C%20PA"

# Experience by state — combined filters
curl "http://localhost:5000/api/experience-by-state?state=PA&team=BPO&client=Arch%20Energy%20Management&min_months=3&tolerance_months=0.5&start_date=2026-03-01&end_date=2026-03-31"

# Dashboard — BPO + specific client
curl "http://localhost:5000/api/dashboard?team=BPO&client=Arch%20Energy%20Management"

# Dashboard — county with state embedded
curl "http://localhost:5000/api/dashboard?county=Fayette%2C%20PA"

# Dashboard — custom date range
curl "http://localhost:5000/api/dashboard?start_date=2025-01-01&end_date=2025-03-31"

# Verify individual endpoints still work
curl "http://localhost:5000/api/availability?team=BPO"
curl "http://localhost:5000/api/worklogs/summary?group_by=client&team=TLS"
curl "http://localhost:5000/api/aoi-hours?team=BPO"
curl "http://localhost:5000/api/warnings?team=TLS"
```
