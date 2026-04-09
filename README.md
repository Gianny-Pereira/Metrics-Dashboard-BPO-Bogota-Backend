# Backend API

Base URL: `http://localhost:5000`

All responses are JSON. Error responses have the shape `{ "error": "..." }`.

---

## Landmen

### `GET /api/landmen`

Returns all landmen ordered by name.

**Response `200`**
```json
[
  {
    "id": 1,
    "name": "Jane Doe",
    "email": "jane@example.com",
    "role": "Senior Landman",
    "status": "active"
  }
]
```

---

### `GET /api/landmen/<landman_id>/worklogs`

Returns a landman and all their work logs, ordered by date descending.

**Path params**
| Param | Type | Description |
|-------|------|-------------|
| `landman_id` | integer | Landman ID |

**Response `200`**
```json
{
  "landman": { "id": 1, "name": "Jane Doe", "email": null, "role": null, "status": "active" },
  "worklogs": [ { ... } ]
}
```

**Response `404`** — landman not found.

---

## Work Logs

### `POST /api/worklogs`

Creates a single work log entry.

**Request body** (`application/json`)
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `landman_id` | integer | yes | ID of the landman |
| `hours` | number | yes | Hours worked |
| `work_type` | string | yes | Type of work (e.g. `"Training - BPO"`, `"Holiday"`) |
| `date` | string | yes | Date in `M/D/YYYY`, `MM/DD/YYYY`, or `YYYY-MM-DD` |
| `project_id` | integer | no | Associated project ID |
| `prospect_id` | integer | no | Associated prospect ID |
| `expense_total` | number | no | Expense amount (default `0`) |
| `status` | string | no | `"Approved"` or `"Unapproved"` (default `"Unapproved"`) |
| `county` | string | no | County and state in `"County, ST"` format (e.g. `"Abbeville, SC"`). The state is parsed and stored separately. |
| `legal_description` | string | no | Legal description (e.g. `"01-01N-01W"`) |
| `well_name` | string | no | Well name(s), comma-separated |
| `work_performed_detail` | string | no | Free-text notes |

**Response `201`** — the created work log object.

**Response `400`** — missing required fields or invalid date format.

**Response `404`** — `landman_id`, `project_id`, or `prospect_id` not found.

---

## Clients

### `GET /api/clients`

Returns all clients ordered by name.

**Response `200`**
```json
[
  { "id": 1, "name": "Acme Corp", "industry": "Oil & Gas" }
]
```

---

## Prospects

### `GET /api/prospects`

Returns prospects, optionally filtered by client.

**Query params**
| Param | Type | Description |
|-------|------|-------------|
| `client_id` | integer | *(optional)* Filter by client |

**Response `200`**
```json
[
  { "id": 1, "client_id": 1, "client_name": "Acme Corp", "name": "AOI North" }
]
```

---

## Projects

### `GET /api/projects`

Returns projects, optionally filtered by client.

**Query params**
| Param | Type | Description |
|-------|------|-------------|
| `client_id` | integer | *(optional)* Filter by client |

**Response `200`**
```json
[
  {
    "id": 1,
    "client_id": 1,
    "client_name": "Acme Corp",
    "name": "Phase 1",
    "start_date": "2024-01-01",
    "end_date": null
  }
]
```

---

## AOI Hours

### `GET /api/aoi-hours`

Returns average hours worked per prospect, aggregated from real work-log data. Falls back to a deterministic random sample keyed on client names when no work logs exist.

**Response `200`**
```json
[
  { "prospect": "AOI North", "avgHours": 12.5 }
]
```

---

## Availability

### `GET /api/availability`

Returns per-landman hour totals broken down by `work_type` for a given month.

**Query params**
| Param | Type | Description |
|-------|------|-------------|
| `period` | string | *(optional)* `YYYY-MM` format. Defaults to the current month. |

**Response `200`**
```json
[
  {
    "id": 1,
    "name": "Jane Doe",
    "Training - BPO": 8.0,
    "Holiday": 8.0
  }
]
```

**Response `400`** — `period` is not in `YYYY-MM` format.

---

## Work-log Summary

### `GET /api/worklogs/summary`

Returns total hours aggregated by a single dimension for a given month. Used to power pie charts (Project Hours by Client, Hours per State, Hours per County).

**Query params**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `group_by` | string | yes | Dimension to aggregate by: `client`, `state`, or `county` |
| `period` | string | no | `YYYY-MM` format. Defaults to the current month. |

**Notes**
- `group_by=client` — totals hours for work logs that are linked to a prospect (and therefore a client). Logs without a prospect are excluded.
- `group_by=state` / `group_by=county` — uses the `state` / `county` fields parsed from the County column on import. Rows with a null or empty value are omitted.

**Response `200`** — ordered by hours descending.
```json
[
  { "label": "Acme Corp", "hours": 120.0 },
  { "label": "Beta LLC",  "hours": 64.0 }
]
```

**Response `400`** — invalid `group_by` value or malformed `period`.

---

## Import

### `POST /api/import`

Imports work logs from a CSV, TSV, or XLSX file exported from the Excel time sheet.

**Request** — `multipart/form-data`
| Field | Description |
|-------|-------------|
| `file` | The file to import (`.csv`, `.tsv`, or `.xlsx`) |

The file must contain a header row. Column names are mapped as follows:

| Excel column | Internal key |
|--------------|--------------|
| Time | landman |
| Status | status |
| Date | date |
| Client | client |
| Prospect | prospect |
| County | county / state (split on `,` → `"Abbeville, SC"` becomes county `"Abbeville"`, state `"SC"`) |
| Legal Description | legal_description |
| Work Type | work_type |
| Project | project |
| Well Name | well_name |
| Time Worked | hours (day-units × 8 → e.g. `1` = 8 h, `0.5` = 4 h) |
| Expense Total | expense_total |
| Work Performed Detail | work_performed_detail |

**Behaviour**
- Landman, Client, Prospect, and Project records are created automatically if they do not exist.
- Rows with identical content (same landman, date, work type, hours, project, prospect, county, and well name) are skipped — re-importing the same file is safe.

**Response `200`** — all rows imported without errors.
```json
{ "imported": 42, "skipped": 3, "errors": [] }
```

**Response `207`** — partial success; some rows had errors.
```json
{ "imported": 40, "skipped": 3, "errors": ["Row 5: Unrecognised date format: '99/99/9999'"] }
```

**Response `400`** — no file provided or file is empty.

**Response `500`** — unexpected server error; transaction is rolled back.

---

## Health Check

### `GET /api/health`

**Response `200`**
```json
{ "status": "ok" }
```
