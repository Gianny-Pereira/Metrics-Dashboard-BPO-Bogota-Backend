"""
Seed the database with sample data for development / demo purposes.
Run once after `flask db upgrade`:

    python seed.py
"""
import os
import random
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from app import app
from models import WORK_TYPES, Client, Landman, Project, WorkLog, db

LANDMEN = [
    {"name": "Alice Johnson",  "email": "alice@bpo.com",   "role": "Senior Landman",  "status": "active"},
    {"name": "Bob Martinez",   "email": "bob@bpo.com",     "role": "Landman",         "status": "active"},
    {"name": "Carol Williams", "email": "carol@bpo.com",   "role": "Junior Landman",  "status": "active"},
    {"name": "David Brown",    "email": "david@bpo.com",   "role": "Senior Landman",  "status": "active"},
    {"name": "Eva Davis",      "email": "eva@bpo.com",     "role": "Landman",         "status": "active"},
]

CLIENTS_DATA = [
    {"name": "Permian Basin Energy",   "industry": "Oil & Gas"},
    {"name": "Eagle Ford Resources",   "industry": "Oil & Gas"},
    {"name": "Appalachian Minerals",   "industry": "Mining"},
    {"name": "Gulf Coast Exploration", "industry": "Oil & Gas"},
    {"name": "Rocky Mountain Realty",  "industry": "Real Estate"},
]

PROJECTS_DATA = [
    # (client_index, name, start_offset_days, duration_days)
    (0, "Permian Basin AOI Phase 1",   -90, 120),
    (0, "Permian Basin AOI Phase 2",   -30,  90),
    (1, "Eagle Ford Title Search",     -60,  60),
    (2, "Appalachian Lease Review",    -45,  75),
    (3, "Gulf Coast Due Diligence",    -15,  45),
    (4, "Rocky Mountain Acquisition",  -80, 100),
]


def seed():
    with app.app_context():
        db.drop_all()
        db.create_all()

        # Landmen
        landmen = []
        for data in LANDMEN:
            lm = Landman(**data)
            db.session.add(lm)
            landmen.append(lm)

        # Clients
        clients = []
        for data in CLIENTS_DATA:
            c = Client(**data)
            db.session.add(c)
            clients.append(c)

        db.session.flush()  # get IDs

        # Projects
        today = date.today()
        projects = []
        for client_idx, name, start_offset, duration in PROJECTS_DATA:
            p = Project(
                client_id=clients[client_idx].id,
                name=name,
                start_date=today + timedelta(days=start_offset),
                end_date=today + timedelta(days=start_offset + duration),
            )
            db.session.add(p)
            projects.append(p)

        db.session.flush()

        # Work logs — 90 days of history
        rng = random.Random(2024)
        for days_back in range(90, 0, -1):
            log_date = today - timedelta(days=days_back)
            if log_date.weekday() >= 5:  # skip weekends
                continue
            for lm in landmen:
                work_type = rng.choices(
                    WORK_TYPES,
                    weights=[70, 15, 5, 10],
                )[0]
                hours = round(rng.uniform(4, 9), 2)
                project_id = None
                if work_type == "Project":
                    # pick an active project for this date
                    active = [
                        p for p in projects
                        if p.start_date <= log_date <= p.end_date
                    ]
                    if active:
                        project_id = rng.choice(active).id

                log = WorkLog(
                    landman_id=lm.id,
                    project_id=project_id,
                    hours=hours,
                    work_type=work_type,
                    date=log_date,
                )
                db.session.add(log)

        db.session.commit()
        print("Database seeded successfully.")
        print(f"  Landmen : {len(landmen)}")
        print(f"  Clients : {len(clients)}")
        print(f"  Projects: {len(projects)}")
        print(f"  WorkLogs: {WorkLog.query.count()}")


if __name__ == "__main__":
    seed()
