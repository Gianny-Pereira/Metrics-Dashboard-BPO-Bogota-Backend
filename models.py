from datetime import date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Landman(db.Model):
    __tablename__ = "landmen"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    email = db.Column(db.String(150), unique=True, nullable=True)  # not present in Excel exports
    role = db.Column(db.String(100))
    status = db.Column(db.String(50), default="active")

    worklogs = db.relationship("WorkLog", backref="landman", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "status": self.status,
        }


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    industry = db.Column(db.String(100))

    projects = db.relationship("Project", backref="client", lazy=True)
    prospects = db.relationship("Prospect", backref="client", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "industry": self.industry,
        }


class Prospect(db.Model):
    """
    Named development area / AOI within a client.
    Corresponds to the 'Prospect' column in the Excel time sheet.
    """
    __tablename__ = "prospects"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)

    worklogs = db.relationship("WorkLog", backref="prospect", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "client_id": self.client_id,
            "client_name": self.client.name if self.client else None,
            "name": self.name,
        }


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)

    worklogs = db.relationship("WorkLog", backref="project", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "client_id": self.client_id,
            "client_name": self.client.name if self.client else None,
            "name": self.name,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
        }


class WorkLog(db.Model):
    __tablename__ = "worklogs"

    id = db.Column(db.Integer, primary_key=True)

    # Deduplication fingerprint — SHA-256 hash of the source row's key fields.
    # Prevents the same row from being inserted on re-import.
    row_hash = db.Column(db.String(64), unique=True, nullable=True, index=True)

    # Core relations
    landman_id = db.Column(db.Integer, db.ForeignKey("landmen.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    prospect_id = db.Column(db.Integer, db.ForeignKey("prospects.id"), nullable=True)

    # Time & billing
    hours = db.Column(db.Numeric(6, 2), nullable=False)
    expense_total = db.Column(db.Numeric(10, 2), default=0)
    date = db.Column(db.Date, nullable=False, default=date.today)

    # Classification (open string — Excel has "Training - BPO", "Holiday", etc.)
    work_type = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(50), default="Unapproved")  # Approved / Unapproved

    # Location & legal
    county = db.Column(db.String(150))   # e.g. "Abbeville"
    state = db.Column(db.String(50))     # e.g. "SC"
    legal_description = db.Column(db.String(200))  # e.g. "01-01N-01W"

    # Well info
    well_name = db.Column(db.String(500))  # comma-separated well names

    # Free-text notes
    work_performed_detail = db.Column(db.Text)

    def to_dict(self):
        return {
            "id": self.id,
            "landman_id": self.landman_id,
            "landman_name": self.landman.name if self.landman else None,
            "project_id": self.project_id,
            "project_name": self.project.name if self.project else None,
            "prospect_id": self.prospect_id,
            "prospect_name": self.prospect.name if self.prospect else None,
            "hours": float(self.hours),
            "expense_total": float(self.expense_total) if self.expense_total is not None else 0,
            "date": self.date.isoformat(),
            "work_type": self.work_type,
            "status": self.status,
            "county": self.county,
            "state": self.state,
            "legal_description": self.legal_description,
            "well_name": self.well_name,
            "work_performed_detail": self.work_performed_detail,
        }
