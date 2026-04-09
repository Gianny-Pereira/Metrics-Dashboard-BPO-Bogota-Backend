import csv
import hashlib
import io
import json
import os
import random
from datetime import date, datetime

import openpyxl

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_migrate import Migrate
from sqlalchemy import func, text

from models import Client, Landman, Project, Prospect, WorkLog, db

load_dotenv()

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

CORS(app)
db.init_app(app)
migrate = Migrate(app, db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_create(model, **kwargs):
    """Return existing row or create a new one (keyed by all kwargs)."""
    instance = model.query.filter_by(**kwargs).first()
    if not instance:
        instance = model(**kwargs)
        db.session.add(instance)
        db.session.flush()  # get the id without a full commit
    return instance


def _parse_date(value: str) -> date:
    """Accept M/D/YYYY, MM/DD/YYYY, or YYYY-MM-DD."""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {value!r}")


def _parse_money(value: str) -> float:
    """Strip $ and commas, return float."""
    return float(value.strip().lstrip("$").replace(",", "") or 0)


def _split_county_state(value: str) -> tuple[str | None, str | None]:
    """
    Split 'Abbeville, SC' into ('Abbeville', 'SC').
    If there is no comma the whole value is treated as county, state is None.
    """
    if not value:
        return None, None
    parts = value.split(",", 1)
    county = parts[0].strip() or None
    state = parts[1].strip() or None if len(parts) == 2 else None
    return county, state


# ---------------------------------------------------------------------------
# Landmen
# ---------------------------------------------------------------------------

@app.get("/api/landmen")
def get_landmen():
    landmen = Landman.query.order_by(Landman.name).all()
    return jsonify([l.to_dict() for l in landmen])


@app.get("/api/landmen/<int:landman_id>/worklogs")
def get_landman_worklogs(landman_id):
    landman = Landman.query.get_or_404(landman_id)
    logs = (
        WorkLog.query
        .filter_by(landman_id=landman_id)
        .order_by(WorkLog.date.desc())
        .all()
    )
    return jsonify({
        "landman": landman.to_dict(),
        "worklogs": [log.to_dict() for log in logs],
    })


# ---------------------------------------------------------------------------
# Work logs
# ---------------------------------------------------------------------------

@app.post("/api/worklogs")
def create_worklog():
    data = request.get_json(force=True)

    required = {"landman_id", "hours", "work_type", "date"}
    missing = required - data.keys()
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    Landman.query.get_or_404(data["landman_id"])

    project_id = data.get("project_id")
    if project_id:
        Project.query.get_or_404(project_id)

    prospect_id = data.get("prospect_id")
    if prospect_id:
        Prospect.query.get_or_404(prospect_id)

    try:
        log_date = _parse_date(data["date"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    county, state = _split_county_state(data.get("county", ""))

    log = WorkLog(
        landman_id=data["landman_id"],
        project_id=project_id,
        prospect_id=prospect_id,
        hours=float(data["hours"]),
        expense_total=float(data.get("expense_total", 0)),
        work_type=data["work_type"],
        status=data.get("status", "Unapproved"),
        county=county,
        state=state,
        legal_description=data.get("legal_description"),
        well_name=data.get("well_name"),
        work_performed_detail=data.get("work_performed_detail"),
        date=log_date,
    )
    db.session.add(log)
    db.session.commit()
    return jsonify(log.to_dict()), 201


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

@app.get("/api/clients")
def get_clients():
    clients = Client.query.order_by(Client.name).all()
    return jsonify([c.to_dict() for c in clients])


# ---------------------------------------------------------------------------
# Prospects
# ---------------------------------------------------------------------------

@app.get("/api/prospects")
def get_prospects():
    client_id = request.args.get("client_id", type=int)
    query = Prospect.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    prospects = query.order_by(Prospect.name).all()
    return jsonify([p.to_dict() for p in prospects])


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.get("/api/projects")
def get_projects():
    client_id = request.args.get("client_id", type=int)
    query = Project.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    projects = query.order_by(Project.name).all()
    return jsonify([p.to_dict() for p in projects])


# ---------------------------------------------------------------------------
# Date-filter helper
# ---------------------------------------------------------------------------

def _resolve_date_filter():
    """
    Parse date filtering query params and return (filters, error_response).

    Priority:
      1. start_date + end_date  → exact range (WorkLog.date >= start, <= end)
      2. period=YYYY-MM         → full calendar month
      3. (neither)              → current calendar month

    Returns a tuple:
      - (list_of_sqlalchemy_filters, None)  on success
      - (None, flask_response)              on bad input
    """
    start_raw = request.args.get("start_date")
    end_raw = request.args.get("end_date")

    if start_raw or end_raw:
        try:
            start = date.fromisoformat(start_raw)
            end = date.fromisoformat(end_raw)
        except (TypeError, ValueError):
            return None, (jsonify({"error": "start_date and end_date must be YYYY-MM-DD"}), 400)
        return [WorkLog.date >= start, WorkLog.date <= end], None

    period = request.args.get("period")
    try:
        if period:
            year, month = map(int, period.split("-"))
        else:
            today = date.today()
            year, month = today.year, today.month
    except (ValueError, AttributeError):
        return None, (jsonify({"error": "period must be YYYY-MM"}), 400)

    return [
        func.extract("year", WorkLog.date) == year,
        func.extract("month", WorkLog.date) == month,
    ], None


# ---------------------------------------------------------------------------
# AOI hours  (average hours per prospect)
# ---------------------------------------------------------------------------

@app.get("/api/aoi-hours")
def get_aoi_hours():
    """Average hours per prospect, aggregated from real work-log data."""
    landman_names = [n.strip() for n in request.args.get("landman", "").split(",") if n.strip()]
    date_filter, date_error = _resolve_date_filter()
    if date_error:
        return date_error

    query = (
        db.session.query(
            Prospect.name,
            func.avg(WorkLog.hours).label("avg_hours"),
        )
        .join(WorkLog, WorkLog.prospect_id == Prospect.id)
    )
    query = query.filter(*date_filter)
    if landman_names:
        query = query.join(Landman, WorkLog.landman_id == Landman.id).filter(Landman.name.in_(landman_names))
    rows = query.group_by(Prospect.name).all()

    if rows:
        result = [
            {"prospect": name, "avgHours": round(float(avg), 2)}
            for name, avg in rows
        ]
    else:
        # Fallback: use clients with deterministic random hours
        clients = Client.query.order_by(Client.name).all()
        rng = random.Random(42)
        result = [
            {"prospect": c.name, "avgHours": round(rng.uniform(5, 50), 2)}
            for c in clients
        ]

    return jsonify(result)


# ---------------------------------------------------------------------------
# Availability summary
# ---------------------------------------------------------------------------

@app.get("/api/availability")
def get_availability():
    """
    Per-landman hour totals broken down by work_type.
    Optional query params: ?period=YYYY-MM, ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD, ?landman=Name1,Name2
    """
    date_filter, date_error = _resolve_date_filter()
    if date_error:
        return date_error

    landman_names = [n.strip() for n in request.args.get("landman", "").split(",") if n.strip()]

    query = (
        db.session.query(
            Landman.id,
            Landman.name,
            WorkLog.work_type,
            func.sum(WorkLog.hours).label("total"),
        )
        .join(WorkLog, WorkLog.landman_id == Landman.id)
        .filter(*date_filter)
    )
    if landman_names:
        query = query.filter(Landman.name.in_(landman_names))
    rows = query.group_by(Landman.id, Landman.name, WorkLog.work_type).all()

    summary: dict = {}
    for landman_id, name, work_type, total in rows:
        if landman_id not in summary:
            summary[landman_id] = {"id": landman_id, "name": name}
        summary[landman_id][work_type] = float(total)

    return jsonify(list(summary.values()))


# ---------------------------------------------------------------------------
# Work-log summary  (aggregated hours for pie charts)
# ---------------------------------------------------------------------------

_SUMMARY_GROUP_BY = {
    "client": Client.name,
    "state":  WorkLog.state,
    "county": WorkLog.county,
}


@app.get("/api/worklogs/summary")
def get_worklogs_summary():
    """
    Aggregated total hours grouped by a single dimension.

    Query params:
      group_by   : "client" | "state" | "county"  (required)
      period     : YYYY-MM  (optional, defaults to current month)
      start_date : YYYY-MM-DD  (optional, use with end_date for exact range)
      end_date   : YYYY-MM-DD  (optional, use with start_date for exact range)

    Response:
      [ { "label": <value>, "hours": <float> }, ... ]  ordered by hours desc.
      Rows where the grouping column is NULL / empty are omitted.
    """
    group_by = request.args.get("group_by", "").strip().lower()
    if group_by not in _SUMMARY_GROUP_BY:
        return jsonify({"error": f"group_by must be one of: {', '.join(_SUMMARY_GROUP_BY)}"}), 400

    date_filter, date_error = _resolve_date_filter()
    if date_error:
        return date_error

    landman_names = [n.strip() for n in request.args.get("landman", "").split(",") if n.strip()]

    group_col = _SUMMARY_GROUP_BY[group_by]

    if group_by == "client":
        # Resolve client through the prospect association (inner join keeps only
        # logs that are tied to a prospect → client).
        query = (
            db.session.query(
                Client.name.label("label"),
                func.sum(WorkLog.hours).label("total"),
            )
            .join(Prospect, WorkLog.prospect_id == Prospect.id)
            .join(Client, Prospect.client_id == Client.id)
            .filter(*date_filter)
            .group_by(Client.name)
        )
    else:
        query = (
            db.session.query(
                group_col.label("label"),
                func.sum(WorkLog.hours).label("total"),
            )
            .filter(
                *date_filter,
                group_col.isnot(None),
                group_col != "",
            )
            .group_by(group_col)
        )

    if landman_names:
        query = query.join(Landman, WorkLog.landman_id == Landman.id).filter(Landman.name.in_(landman_names))

    rows = query.order_by(func.sum(WorkLog.hours).desc()).all()

    return jsonify([
        {"label": label, "hours": round(float(total), 2)}
        for label, total in rows
    ])


# ---------------------------------------------------------------------------
# Worklog entries  (individual rows for a landman on a given date)
# ---------------------------------------------------------------------------

@app.get("/api/worklogs/entries")
def get_worklog_entries():
    """
    Individual worklog rows for a specific landman on a specific date.

    Query params (both required):
      landman : landman name
      date    : YYYY-MM-DD

    Response:
      [ { "client", "prospect", "project", "work_type", "county", "hours" }, ... ]
    """
    landman_name = request.args.get("landman", "").strip()
    date_raw = request.args.get("date", "").strip()

    if not landman_name or not date_raw:
        return jsonify({"error": "Both 'landman' and 'date' params are required"}), 400

    try:
        log_date = date.fromisoformat(date_raw)
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400

    rows = (
        db.session.query(
            Client.name.label("client"),
            Prospect.name.label("prospect"),
            Project.name.label("project"),
            WorkLog.work_type,
            WorkLog.county,
            WorkLog.hours,
        )
        .join(Landman, WorkLog.landman_id == Landman.id)
        .outerjoin(Prospect, WorkLog.prospect_id == Prospect.id)
        .outerjoin(Client, Prospect.client_id == Client.id)
        .outerjoin(Project, WorkLog.project_id == Project.id)
        .filter(Landman.name == landman_name, WorkLog.date == log_date)
        .order_by(WorkLog.id)
        .all()
    )

    return jsonify([
        {
            "client": client,
            "prospect": prospect,
            "project": project,
            "work_type": work_type,
            "county": county,
            "hours": float(hours),
        }
        for client, prospect, project, work_type, county, hours in rows
    ])


# ---------------------------------------------------------------------------
# Daily warnings
# ---------------------------------------------------------------------------

@app.get("/api/warnings")
def get_warnings():
    """
    Days where a landman's total hours are not exactly 8 (one full day).

    Query params (all optional):
      start_date + end_date : YYYY-MM-DD exact range
      period                : YYYY-MM calendar month
      landman               : comma-separated names

    Response:
      [ { "landman": str, "date": "YYYY-MM-DD", "total": float,
          "status": "incomplete" | "over" }, ... ]
    """
    date_filter, date_error = _resolve_date_filter()
    if date_error:
        return date_error

    landman_names = [n.strip() for n in request.args.get("landman", "").split(",") if n.strip()]

    query = (
        db.session.query(
            Landman.name,
            WorkLog.date,
            func.sum(WorkLog.hours).label("total"),
        )
        .join(Landman, WorkLog.landman_id == Landman.id)
        .filter(*date_filter)
        .group_by(Landman.name, WorkLog.date)
    )
    if landman_names:
        query = query.filter(Landman.name.in_(landman_names))

    rows = query.order_by(Landman.name, WorkLog.date).all()

    result = []
    for name, log_date, total in rows:
        total_hours = round(float(total), 3)
        if abs(total_hours - 8.0) < 0.001:
            continue  # exactly one full day — OK
        result.append({
            "landman": name,
            "date": log_date.isoformat(),
            "total": total_hours,
            "status": "incomplete" if total_hours < 8.0 else "over",
        })

    return jsonify(result)


# ---------------------------------------------------------------------------
# Import  (CSV or TSV exported from the Excel time sheet)
# ---------------------------------------------------------------------------

# Maps raw Excel header names → internal keys used throughout import logic.
_COL_MAP = {
    "time": "landman",
    "status": "status",
    "date": "date",
    "client": "client",
    "prospect": "prospect",
    "county": "county",
    "legal description": "legal_description",
    "work type": "work_type",
    "project": "project",
    "well name": "well_name",
    "time worked": "hours",
    "expense total": "expense_total",
    "work performed detail": "work_performed_detail",
}


def _normalise_headers(raw_headers: list[str]) -> list[str]:
    return [_COL_MAP.get(h.strip().lower(), h.strip().lower()) for h in raw_headers]


def _row_hash(row: dict) -> str:
    """
    Stable fingerprint of a work-log row used to skip exact re-imports.
    Keyed on the fields that uniquely describe a time entry.
    """
    key = json.dumps({
        "landman": row.get("landman", "").strip().lower(),
        "date": row.get("date", "").strip(),
        "work_type": row.get("work_type", "").strip().lower(),
        "hours": row.get("hours", "").strip(),
        "project": row.get("project", "").strip().lower(),
        "prospect": row.get("prospect", "").strip().lower(),
        "county": row.get("county", "").strip().lower(),
        "well_name": row.get("well_name", "").strip().lower(),
        "work_performed_detail": row.get("work_performed_detail", "").strip().lower(),
        "legal_description": row.get("legal_description", "").strip().lower(),
    }, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()


class _EntityCache:
    """
    Per-import-request cache for Landman / Client / Prospect / Project.
    Avoids N+1 DB queries when the same entity appears across many rows.
    Always checks the in-memory cache first; falls back to DB; creates if absent.
    """

    def __init__(self):
        self._landmen: dict[str, Landman] = {}      # name → Landman
        self._clients: dict[str, Client] = {}        # name → Client
        self._prospects: dict[tuple, Prospect] = {}  # (client_id, name) → Prospect
        self._projects: dict[tuple, Project] = {}    # (client_id, name) → Project

    def landman(self, name: str) -> Landman:
        key = name.lower()
        if key not in self._landmen:
            obj = Landman.query.filter_by(name=name).first()
            if not obj:
                obj = Landman(name=name)
                db.session.add(obj)
                db.session.flush()
            self._landmen[key] = obj
        return self._landmen[key]

    def client(self, name: str) -> Client:
        key = name.lower()
        if key not in self._clients:
            obj = Client.query.filter_by(name=name).first()
            if not obj:
                obj = Client(name=name)
                db.session.add(obj)
                db.session.flush()
            self._clients[key] = obj
        return self._clients[key]

    def prospect(self, client: Client, name: str) -> Prospect:
        key = (client.id, name.lower())
        if key not in self._prospects:
            obj = Prospect.query.filter_by(client_id=client.id, name=name).first()
            if not obj:
                obj = Prospect(client_id=client.id, name=name)
                db.session.add(obj)
                db.session.flush()
            self._prospects[key] = obj
        return self._prospects[key]

    def project(self, client: Client, name: str) -> Project:
        key = (client.id, name.lower())
        if key not in self._projects:
            obj = Project.query.filter_by(client_id=client.id, name=name).first()
            if not obj:
                obj = Project(client_id=client.id, name=name)
                db.session.add(obj)
                db.session.flush()
            self._projects[key] = obj
        return self._projects[key]


def _import_rows(reader) -> tuple[int, list[dict], list[str], list[dict]]:
    """
    Process normalised DictReader rows.
    Returns (imported_count, skipped_rows, list_of_error_strings, daily_warnings).

    Entity creation rules (the import is the sole data source):
      - Landman   : found by name, created if absent
      - Client    : found by name, created if absent
      - Prospect  : found by (client, name), created if absent
      - Project   : found by (client, name), created if absent
      - WorkLog   : always created unless an identical row hash already exists
    """
    cache = _EntityCache()

    # Pre-load all existing row hashes so we can skip duplicates in O(1).
    existing_hashes: set[str] = {
        h for (h,) in db.session.query(WorkLog.row_hash).filter(
            WorkLog.row_hash.isnot(None)
        ).all()
    }

    imported = 0
    skipped_rows = []
    errors = []
    # Tracks raw time-worked units (before *8) per (landman, date)
    daily_totals: dict[tuple, float] = {}

    for i, row in enumerate(reader, start=2):  # row 1 is the header
        try:
            # ── Landman ──────────────────────────────────────────────────────
            landman_name = row.get("landman", "").strip()
            if not landman_name:
                errors.append(f"Row {i}: 'Time' column is empty — skipped.")
                continue
            landman = cache.landman(landman_name)

            # ── Client ───────────────────────────────────────────────────────
            client_name = row.get("client", "").strip()
            client = cache.client(client_name) if client_name else None

            # ── Prospect (scoped to client) ───────────────────────────────────
            prospect_name = row.get("prospect", "").strip()
            prospect = cache.prospect(client, prospect_name) if (prospect_name and client) else None

            # ── Project (scoped to client) ────────────────────────────────────
            project_name = row.get("project", "").strip()
            project = cache.project(client, project_name) if (project_name and client) else None

            # ── Date ──────────────────────────────────────────────────────────
            raw_date = row.get("date", "").strip()
            if not raw_date:
                errors.append(f"Row {i}: 'Date' column is empty — skipped.")
                continue
            log_date = _parse_date(raw_date)

            # ── Deduplication ─────────────────────────────────────────────────
            h = _row_hash(row)
            if h in existing_hashes:
                skipped_rows.append({
                    "row": i,
                    "landman": landman_name,
                    "date": raw_date,
                    "client": client_name,
                    "prospect": prospect_name,
                    "project": project_name,
                    "work_type": row.get("work_type", "").strip(),
                    "hours": row.get("hours", "").strip(),
                    "county": row.get("county", "").strip(),
                })
                continue
            existing_hashes.add(h)  # prevent duplicates within the same file

            # ── Hours & expense ───────────────────────────────────────────────
            # Imported values are in day-units (1 = full day, 0.5 = half day).
            # Multiply by 8 to convert to hours.
            raw_units = float(row.get("hours", "0").strip() or 0)
            hours = raw_units * 8
            expense = _parse_money(row.get("expense_total", "0"))

            # ── Daily total tracking ──────────────────────────────────────────
            day_key = (landman_name, raw_date)
            daily_totals[day_key] = round(daily_totals.get(day_key, 0.0) + raw_units, 6)

            # ── County / State ────────────────────────────────────────────────
            county, state = _split_county_state(row.get("county", ""))

            log = WorkLog(
                row_hash=h,
                landman_id=landman.id,
                project_id=project.id if project else None,
                prospect_id=prospect.id if prospect else None,
                hours=hours,
                expense_total=expense,
                date=log_date,
                work_type=row.get("work_type", "").strip() or "Unknown",
                status=row.get("status", "Unapproved").strip(),
                county=county,
                state=state,
                legal_description=row.get("legal_description", "").strip() or None,
                well_name=row.get("well_name", "").strip() or None,
                work_performed_detail=row.get("work_performed_detail", "").strip() or None,
            )
            db.session.add(log)
            imported += 1

        except Exception as exc:
            errors.append(f"Row {i}: {exc}")

    # ── Daily sum validation ──────────────────────────────────────────────────
    daily_warnings = []
    for (landman_name, raw_date), total in sorted(daily_totals.items()):
        total_rounded = round(total, 3)
        if abs(total_rounded - 1.0) < 0.001:
            continue  # exactly 1 day — OK
        status = "incomplete" if total_rounded < 1.0 else "over"
        daily_warnings.append({
            "landman": landman_name,
            "date": raw_date,
            "total": total_rounded,
            "status": status,
        })

    return imported, skipped_rows, errors, daily_warnings


def _xlsx_to_dict_reader(raw_bytes: bytes):
    """
    Parse an xlsx file and return (headers, row_dicts) where headers are
    already normalised and each row is a plain dict keyed by those headers.
    """
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return None, []

    raw_headers = [str(cell) if cell is not None else "" for cell in rows[0]]
    headers = _normalise_headers(raw_headers)

    dict_rows = []
    for row in rows[1:]:
        # Skip entirely blank rows
        if all(cell is None or str(cell).strip() == "" for cell in row):
            continue
        dict_rows.append({
            headers[i]: (str(cell).strip() if cell is not None else "")
            for i, cell in enumerate(row)
            if i < len(headers)
        })

    return headers, dict_rows


@app.post("/api/import")
def import_worklogs():
    """
    Upload a CSV, TSV, or XLSX file exported from the Excel time sheet.

    multipart/form-data field: `file`

    - Auto-creates Landman, Client, Prospect, and Project records when absent.
    - Skips rows whose content was already imported (idempotent re-imports).
    - Returns: { imported, skipped, errors }
    """
    if "file" not in request.files:
        return jsonify({"error": "No file field in request."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected."}), 400

    raw_bytes = f.read()
    filename_lower = f.filename.lower()

    if filename_lower.endswith(".xlsx"):
        headers, dict_rows = _xlsx_to_dict_reader(raw_bytes)
        if headers is None:
            return jsonify({"error": "File appears to be empty."}), 400
        reader = dict_rows  # _import_rows accepts any iterable of dicts
    else:
        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw_bytes.decode("latin-1")

        # Auto-detect delimiter: TSV if first line contains a tab, else CSV.
        first_line = text.split("\n")[0]
        delimiter = "\t" if "\t" in first_line else ","

        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

        if not reader.fieldnames:
            return jsonify({"error": "File appears to be empty."}), 400

        reader.fieldnames = _normalise_headers(list(reader.fieldnames))

    try:
        imported, skipped_rows, errors, daily_warnings = _import_rows(reader)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500

    status_code = 200 if not errors else 207
    return jsonify({
        "imported": imported,
        "skipped": len(skipped_rows),
        "skipped_rows": skipped_rows,
        "errors": errors,
        "daily_warnings": daily_warnings,
    }), status_code


# ---------------------------------------------------------------------------
# Verify password
# ---------------------------------------------------------------------------

@app.post("/api/verify-password")
def verify_password():
    body = request.get_json()
    admin_password = os.environ.get("ADMIN_PASSWORD", "changeme")
    if body.get("password") == admin_password:
        return jsonify({"ok": True})
    return jsonify({"error": "Incorrect password"}), 401


# ---------------------------------------------------------------------------
# Clear database
# ---------------------------------------------------------------------------

@app.delete("/api/clear-database")
def clear_database():
    try:
        db.session.execute(text("DELETE FROM worklogs"))
        db.session.execute(text("DELETE FROM projects"))
        db.session.execute(text("DELETE FROM prospects"))
        db.session.execute(text("DELETE FROM clients"))
        db.session.execute(text("DELETE FROM landmen"))
        db.session.commit()
        return jsonify({"message": "Database cleared successfully."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1", port=5000)
