"""Model-definition checks that need no database.

Importing the models registers them on the metadata; these tests assert the schema-level
guarantees (the double-booking exclusion constraint, optimistic-locking column) are wired, so a
later refactor that drops them fails here, fast, without spinning up Postgres.
"""

from __future__ import annotations

from appointments_api.models import Appointment, Base

EXPECTED_TABLES = {
    "users",
    "clinics",
    "clinicians",
    "clinician_working_hours",
    "services",
    "appointments",
    "audit_log",
}


def test_all_domain_tables_are_registered() -> None:
    assert set(Base.metadata.tables) >= EXPECTED_TABLES


def test_appointments_have_the_no_double_booking_exclusion_constraint() -> None:
    appointments = Base.metadata.tables["appointments"]
    constraint_names = {c.name for c in appointments.constraints}
    assert "ck_appointment_no_double_booking" in constraint_names


def test_appointment_uses_version_column_for_optimistic_locking() -> None:
    version_col = Appointment.__mapper__.version_id_col
    assert version_col is not None
    assert version_col.name == "version"
