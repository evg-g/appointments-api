#!/usr/bin/env python
"""Seed a realistic demo dataset directly into the database.

Why this exists:
  - There is no public registration and the first PLATFORM_ADMIN cannot be created through the API
    (POST /users itself needs a PLATFORM_ADMIN token). So the first admin must be inserted directly.
  - The smoke test and the load test both need a logged-in patient and a bookable clinic graph.
  - It gives a `make seed` demo: several clinics across timezones (including a DST-crossing set),
    each with a clinician open all week and a service, plus an admin and a patient.

It is idempotent: if the admin already exists it does nothing and exits 0, so it is safe to run on
every deploy or `docker compose up`.

Safety: it refuses to run against APP_ENV=production unless --force is passed, because the demo
passwords are weak by design.

Usage:
    uv run python scripts/seed.py [--force]

Credentials (override via env): SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD,
SEED_PATIENT_EMAIL / SEED_PATIENT_PASSWORD.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import time

from sqlalchemy import select

from appointments_api.config import Settings
from appointments_api.db import create_engine, create_sessionmaker
from appointments_api.enums import UserRole
from appointments_api.models import Clinic, Clinician, ClinicianWorkingHours, Service, User
from appointments_api.security import hash_password

# Note: emails must pass Pydantic EmailStr at the login endpoint, which rejects reserved/special-use
# TLDs like `.local`. Use an ordinary domain for the demo accounts.
ADMIN_EMAIL = os.environ.get("SEED_ADMIN_EMAIL", "admin@aurora-clinic.com")
ADMIN_PASSWORD = os.environ.get("SEED_ADMIN_PASSWORD", "password123")
PATIENT_EMAIL = os.environ.get("SEED_PATIENT_EMAIL", "patient@aurora-clinic.com")
PATIENT_PASSWORD = os.environ.get("SEED_PATIENT_PASSWORD", "password123")

# Open every day, wide hours, so a bookable slot always exists regardless of the day the smoke or
# load test happens to run.
_ALL_WEEK = [(wd, "06:00", "22:00") for wd in range(7)]

# Several clinics in different timezones. Two of them observe DST on opposite hemispheres, which is
# the "DST-crossing" variety the project is built to exercise.
_CLINICS = [
    ("Aurora Central", "UTC", "1 Center Plaza"),
    ("Aurora East", "America/New_York", "200 Harbor Ave"),
    ("Aurora West", "Europe/London", "5 Kingsway"),
]


async def main(force: bool) -> int:
    settings = Settings()
    if settings.app_env == "production" and not force:
        print("Refusing to seed demo data into production without --force.")
        return 1

    engine = create_engine(settings)
    maker = create_sessionmaker(engine)
    try:
        async with maker() as session:
            existing = await session.scalar(select(User).where(User.email == ADMIN_EMAIL))
            if existing is not None:
                print(f"Admin {ADMIN_EMAIL} already present; nothing to seed.")
                return 0

            admin = User(
                email=ADMIN_EMAIL,
                full_name="Platform Admin",
                role=UserRole.PLATFORM_ADMIN,
                hashed_password=hash_password(ADMIN_PASSWORD),
                is_active=True,
            )
            patient = User(
                email=PATIENT_EMAIL,
                full_name="Demo Patient",
                role=UserRole.PATIENT,
                hashed_password=hash_password(PATIENT_PASSWORD),
                is_active=True,
            )
            session.add_all([admin, patient])
            await session.flush()

            for index, (name, tz, address) in enumerate(_CLINICS):
                clinic = Clinic(
                    name=name,
                    timezone=tz,
                    address=address,
                    cancellation_cutoff_hours=24,
                )
                session.add(clinic)
                await session.flush()

                doc = User(
                    email=f"doc{index}@aurora-clinic.com",
                    full_name=f"Dr. Demo {index}",
                    role=UserRole.CLINICIAN,
                    hashed_password=hash_password("password123"),
                    is_active=True,
                )
                session.add(doc)
                await session.flush()

                session.add(
                    Clinician(
                        clinic_id=clinic.id,
                        user_id=doc.id,
                        specialty="General Practice",
                        buffer_minutes=0,
                        working_hours=[
                            ClinicianWorkingHours(
                                weekday=wd,
                                start_time=time.fromisoformat(s),
                                end_time=time.fromisoformat(e),
                            )
                            for (wd, s, e) in _ALL_WEEK
                        ],
                    )
                )
                session.add(
                    Service(
                        clinic_id=clinic.id,
                        name="Consultation",
                        duration_minutes=30,
                        price_cents=5000,
                        currency="USD",
                        is_active=True,
                    )
                )

            await session.commit()
    finally:
        await engine.dispose()

    print(
        f"Seeded {len(_CLINICS)} clinics (each with a clinician + service), "
        f"admin {ADMIN_EMAIL}, and patient {PATIENT_EMAIL}."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the demo dataset.")
    parser.add_argument("--force", action="store_true", help="Allow seeding in production.")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.force)))
