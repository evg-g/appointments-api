"""The served OpenAPI declares bearer auth as a security scheme, not as a plain header parameter.

Why this matters: Swagger UI only shows its *Authorize* button, and generated clients only know an
operation needs a token, when the spec carries a ``securitySchemes`` entry and per-operation
``security``. A plain ``authorization`` header parameter looks the same on the wire but gives
neither.
"""

from __future__ import annotations

from typing import Any

from appointments_api.main import create_app

PUBLIC_OPERATIONS = {
    ("post", "/api/v1/auth/login"),
    ("post", "/api/v1/auth/refresh"),
    ("post", "/api/v1/auth/logout"),
    ("post", "/api/v1/devices/{device_id}/telemetry:batch"),  # X-Device-Secret, not a user JWT
}


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if path.startswith("/api/v1/")
    ]


def test_bearer_scheme_is_declared() -> None:
    spec = create_app().openapi()

    assert spec["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Access token from POST /api/v1/auth/login.",
    }


def test_protected_operations_use_the_scheme_and_no_authorization_header_param() -> None:
    spec = create_app().openapi()

    for method, path, op in _operations(spec):
        header_params = {p["name"].lower() for p in op.get("parameters", []) if p["in"] == "header"}
        assert "authorization" not in header_params, f"{method.upper()} {path}"
        if (method, path) in PUBLIC_OPERATIONS:
            assert "security" not in op, f"{method.upper()} {path} should be public"
        else:
            assert op.get("security") == [{"BearerAuth": []}], f"{method.upper()} {path}"
