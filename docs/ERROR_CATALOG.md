# Error catalog

Every error response uses `application/problem+json` (RFC 9457). The body always has a stable
`type` URI, a `title`, the `status`, and the request `instance` (path). Validation errors add an
`errors[]` array of `{field, message}`. Branch on `type`, not on the human-readable `title`.

`type` is `https://aurora.example/problems/<slug>`. The slugs:

| slug | status | When it happens |
|---|---|---|
| `unauthorized` | 401 | Missing/malformed/expired access token, or bad login credentials. |
| `forbidden` | 403 | Authenticated but not allowed (wrong role, or another user's resource). Also a patient cancelling later than the clinic cutoff. |
| `not-found` | 404 | The addressed resource does not exist (or you may not see it). |
| `conflict` | 409 | A state conflict: an illegal appointment status transition, or a generic integrity conflict. |
| `slot-unavailable` | 409 | The requested time overlaps an existing appointment for that clinician (service-layer check, and the DB exclusion constraint under a race). |
| `idempotency-conflict` | 409 | A request with the same `Idempotency-Key` is still being processed; retry shortly. |
| `precondition-failed` | 412 | The `If-Match` ETag did not match the resource's current version (someone changed it first). |
| `precondition-required` | 428 | A mutating request (`transition`/`cancel`) omitted the required `If-Match` header. |
| `validation-error` | 422 | Request body/query failed validation, or a business rule rejected the input (e.g. time outside working hours). `errors[]` lists the offending fields. |
| `idempotency-key-reused` | 422 | An `Idempotency-Key` was replayed with a different request body. |
| `rate-limited` | 429 | The principal exceeded its rate-limit window. Carries `Retry-After` and `RateLimit-*` headers. |

## Examples

Validation error:

```json
{
  "type": "https://aurora.example/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/api/v1/clinics",
  "errors": [{ "field": "timezone", "message": "unknown IANA timezone: 'Mars/Phobos'" }]
}
```

Double-booking under load:

```json
{
  "type": "https://aurora.example/problems/slot-unavailable",
  "title": "Time slot is not available",
  "status": 409,
  "detail": "That time overlaps an existing appointment for this clinician.",
  "instance": "/api/v1/appointments"
}
```

Stale conditional update (ETag/If-Match):

```json
{
  "type": "https://aurora.example/problems/precondition-failed",
  "title": "Precondition failed",
  "status": 412,
  "detail": "The appointment has changed since you last read it; re-read it and retry.",
  "instance": "/api/v1/appointments/1f.../transition"
}
```

Rate limited (headers: `Retry-After`, `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`):

```json
{
  "type": "https://aurora.example/problems/rate-limited",
  "title": "Too many requests",
  "status": 429,
  "detail": "Rate limit exceeded; slow down and retry after the indicated delay.",
  "instance": "/api/v1/auth/me"
}
```
