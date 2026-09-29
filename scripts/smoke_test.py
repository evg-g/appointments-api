#!/usr/bin/env python
"""Post-deploy smoke test: prove a freshly deployed instance is actually working.

It does two things the spec asks for:
  1. hits /health/ready and checks every dependency reports ok, and
  2. exercises one real business flow end to end:
        login (patient) -> discover a clinic/clinician/service -> read availability
        -> book an appointment -> read it back -> cancel it (cleanup).

cd.yml runs this after each deploy; a non-zero exit triggers an automatic rollback. It needs the
demo data from scripts/seed.py to be present in the target environment.

Usage:
    uv run python scripts/smoke_test.py --base-url https://staging.example.com

Credentials default to the seeded patient; override with --email / --password or the env vars
SMOKE_EMAIL / SMOKE_PASSWORD.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import httpx

API = "/api/v1"


def _fail(message: str) -> None:
    print(f"SMOKE FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(base_url: str, email: str, password: str, timeout: float) -> None:
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    # 1. Readiness: the process and every backing dependency.
    r = client.get("/health/ready")
    if r.status_code != 200:
        _fail(f"/health/ready returned {r.status_code}: {r.text}")
    checks = r.json().get("checks", {})
    if not all(v == "ok" for v in checks.values()):
        _fail(f"/health/ready reports a degraded dependency: {checks}")
    print(f"OK  /health/ready  checks={checks}")

    # 2. The OpenAPI document is served (the web app generates its client from this).
    r = client.get("/openapi.json")
    if r.status_code != 200:
        _fail(f"/openapi.json returned {r.status_code}")
    print("OK  /openapi.json")

    # 3. Log in as the seeded patient.
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        _fail(f"login failed ({r.status_code}): {r.text}")
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    print(f"OK  login as {email}")

    # 4. Discover a clinic, a clinician, and a service.
    clinics = client.get(f"{API}/clinics", headers=auth).json().get("data", [])
    if not clinics:
        _fail("no clinics found — was scripts/seed.py run against this environment?")
    clinic_id = clinics[0]["id"]

    clinicians = (
        client.get(f"{API}/clinicians", headers=auth, params={"clinic_id": clinic_id})
        .json()
        .get("data", [])
    )
    services = (
        client.get(f"{API}/services", headers=auth, params={"clinic_id": clinic_id})
        .json()
        .get("data", [])
    )
    if not clinicians or not services:
        _fail("clinic has no clinician or no service to book")
    clinician_id = clinicians[0]["id"]
    service_id = services[0]["id"]
    print(f"OK  discovered clinic/clinician/service under clinic {clinic_id}")

    # 5. Find a bookable slot in the next two weeks. Start two days out so the slot is safely
    #    outside the clinic's cancellation cutoff (default 24h) and the patient can cancel it below.
    slot_start: str | None = None
    for offset in range(2, 16):
        day = date.today() + timedelta(days=offset)
        slots = client.get(
            f"{API}/availability",
            headers=auth,
            params={
                "clinician_id": clinician_id,
                "service_id": service_id,
                "day": day.isoformat(),
            },
        ).json()
        if slots:
            slot_start = slots[0]["start"]
            break
    if slot_start is None:
        _fail("no availability found in the next 14 days")
    print(f"OK  found slot at {slot_start}")

    # 6. Book it.
    r = client.post(
        f"{API}/appointments",
        headers=auth,
        json={
            "clinic_id": clinic_id,
            "clinician_id": clinician_id,
            "service_id": service_id,
            "starts_at": slot_start,
        },
    )
    if r.status_code != 201:
        _fail(f"booking failed ({r.status_code}): {r.text}")
    appointment = r.json()
    appointment_id = appointment["id"]
    if appointment["status"] != "REQUESTED":
        _fail(f"unexpected status after booking: {appointment['status']}")
    etag = r.headers.get("ETag")
    print(f"OK  booked appointment {appointment_id} (status REQUESTED, ETag {etag})")

    # 7. Read it back.
    r = client.get(f"{API}/appointments/{appointment_id}", headers=auth)
    if r.status_code != 200:
        _fail(f"could not read the new appointment ({r.status_code})")
    print("OK  read appointment back")

    # 8. Cancel it, so the smoke test leaves no residue and can run again on the same slot.
    r = client.post(
        f"{API}/appointments/{appointment_id}/cancel",
        headers={**auth, "If-Match": etag or ""},
        json={"reason": "smoke test cleanup"},
    )
    if r.status_code not in (200, 204):
        _fail(f"cleanup cancel failed ({r.status_code}): {r.text}")
    print("OK  cancelled appointment (cleanup)")

    print("SMOKE PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-deploy smoke test.")
    parser.add_argument("--base-url", required=True, help="Base URL of the deployed API.")
    parser.add_argument(
        "--email", default=os.environ.get("SMOKE_EMAIL", "patient@aurora-clinic.com")
    )
    parser.add_argument("--password", default=os.environ.get("SMOKE_PASSWORD", "password123"))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    run(args.base_url, args.email, args.password, args.timeout)
