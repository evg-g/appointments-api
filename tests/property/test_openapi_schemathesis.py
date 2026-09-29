"""Schemathesis: fuzz every operation in the served OpenAPI schema.

The headline property of a REST API is *no unhandled 500s* — for any input the schema says is
possible, the server must answer with a handled status, never crash. Schemathesis reads the live
``/openapi.json``, generates conforming (and, where asked, deliberately invalid) requests for every
operation, and calls them through the ASGI transport against real Postgres + Redis.

We fuzz as a PLATFORM_ADMIN so generation reaches the real handlers rather than bouncing off the
auth guard. Deep, authenticated *behavioural* correctness is covered by the integration and
security tiers; here we assert the crash-safety and schema-conformance laws that must hold for the
whole surface at once.
"""

from __future__ import annotations

from typing import Any

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import response_schema_conformance

schema = schemathesis.pytest.from_fixture("api_schema")


_SUPPRESSED = [
    # The app/DB/Redis fixtures are function-scoped by design (one clean world per test), some
    # operations legitimately filter many inputs (tight schemas), and container-backed calls are
    # not microsecond-fast. None of these are defects in the API under test.
    HealthCheck.function_scoped_fixture,
    HealthCheck.too_slow,
    HealthCheck.filter_too_much,
]


def _skip_unfuzzable(case: Any) -> None:
    # The SSE endpoint returns an unbounded text/event-stream; case.call would read it forever, so
    # it cannot be fuzzed through the ASGI transport (the integration SSE tests cover it instead).
    if case.operation.path.endswith("/streams/telemetry"):
        pytest.skip("SSE stream endpoint is not fuzzable (unbounded response).")


@schema.parametrize()
@settings(max_examples=20, deadline=None, suppress_health_check=_SUPPRESSED)
def test_no_operation_returns_a_server_error(case: Any, admin_token: str) -> None:
    _skip_unfuzzable(case)
    response = case.call(headers={"Authorization": f"Bearer {admin_token}"})
    # Only the crash-safety check here: any input the schema allows must not 500.
    case.validate_response(response, checks=[not_a_server_error])


@schema.parametrize()
@settings(max_examples=20, deadline=None, suppress_health_check=_SUPPRESSED)
def test_successful_responses_conform_to_the_schema(case: Any, admin_token: str) -> None:
    _skip_unfuzzable(case)
    response = case.call(headers={"Authorization": f"Bearer {admin_token}"})
    # When the API returns a 2xx that the spec documents with a body, the body must match it.
    if 200 <= response.status_code < 300:
        case.validate_response(response, checks=[response_schema_conformance])
