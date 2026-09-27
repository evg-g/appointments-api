"""Smoke-level load scenario (Locust).

This is not a benchmark. It puts a modest, realistic read load on the running API so the nightly
pipeline catches gross performance regressions — a query that went O(n), an accidental N+1, a
missing index — before they reach production. The p95 budget is enforced by scripts/check_load.py.

It logs in once per simulated user (as the seeded patient) and then exercises authenticated read
paths plus the readiness probe. Run against a stack seeded by scripts/seed.py.

Note: run the target with rate limiting disabled (RATE_LIMIT_ENABLED=false); otherwise every virtual
user shares one principal and the fixed-window limiter turns the run into a wall of 429s, which
measures the limiter, not the app. The nightly workflow sets this.

    uv run locust -f tests/load/locustfile.py --headless -u 20 -r 5 -t 30s \
        --host http://localhost:8000 --csv load --only-summary
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task

EMAIL = os.environ.get("LOAD_EMAIL", "patient@aurora-clinic.com")
PASSWORD = os.environ.get("LOAD_PASSWORD", "password123")


class PatientUser(HttpUser):
    """A patient browsing the API: reading their appointments and checking health."""

    wait_time = between(0.1, 0.5)

    def on_start(self) -> None:
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
            name="POST /auth/login",
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task(3)
    def list_appointments(self) -> None:
        self.client.get("/api/v1/appointments", name="GET /appointments")

    @task(2)
    def whoami(self) -> None:
        self.client.get("/api/v1/auth/me", name="GET /auth/me")

    @task(1)
    def readiness(self) -> None:
        self.client.get("/health/ready", name="GET /health/ready")
