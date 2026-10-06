# Changelog

## [1.1.0](https://github.com/evg-g/appointments-api/compare/v1.0.1...v1.1.0) (2026-10-06)


### Features

* **appointments:** AURORA-2 AC1.data AC3.data record audit entry on cancel ([c22043a](https://github.com/evg-g/appointments-api/commit/c22043a9254ee3754c76bde9bef18ed1c2dfb3cb))
* **appointments:** AURORA-3 AC2.api free the idempotency key when the booking commit fails ([d2e0c1d](https://github.com/evg-g/appointments-api/commit/d2e0c1db06dca3cb77bf66f056278e78cfa01357))
* **webhooks:** AURORA-3 AC4.data publish webhook events after commit ([1433fb9](https://github.com/evg-g/appointments-api/commit/1433fb98cded71698a3b883cba61667d8295310c))


### Bug Fixes

* **ci:** ignore the rebased fake secret; unlink localhost URLs in README ([cc6d414](https://github.com/evg-g/appointments-api/commit/cc6d4149b938f82924803bbfde1d6cd854ce88cc))
* **db:** AURORA-3 AC3.data AC1.api commit before the response ([103c04e](https://github.com/evg-g/appointments-api/commit/103c04e8cd787fe0a2bc6c646562082989820287))


### Documentation

* **audit:** ADRs for AURORA-2 ([95d100e](https://github.com/evg-g/appointments-api/commit/95d100ed7c7401b6bea8b8ae7ec1d4fa543519fa))
* AURORA-3 catalog internal-error and mark ADR 0007/0010 timing superseded by ADR 0017 ([8b55668](https://github.com/evg-g/appointments-api/commit/8b5566801ffb39510893da8ed627f5503bb95a5d))
* **db:** ADRs for AURORA-3 ([9119ae5](https://github.com/evg-g/appointments-api/commit/9119ae58932c86590fb83636f6ae98ba8906b7db))
* stop the CI/CD gap note reading as "no pipeline" ([bf37443](https://github.com/evg-g/appointments-api/commit/bf37443ab2e74b4940bc0dfc1b94a0cddd7f9d17))

## [1.0.1](https://github.com/evg-g/appointments-api/compare/v1.0.0...v1.0.1) (2026-10-01)


### Bug Fixes

* **api:** do not fail the contract gate on a release version bump ([bdb9f75](https://github.com/evg-g/appointments-api/commit/bdb9f750cccf23589f5a13a81fabd68563c7f50d))
* **api:** upgrade base-image OS packages so Trivy passes ([0cccd43](https://github.com/evg-g/appointments-api/commit/0cccd43453bd295f9c700b744af16e07fa513f38))


### Documentation

* **api:** describe the repo as a showcase, not a course ([9631d64](https://github.com/evg-g/appointments-api/commit/9631d643e7d2abe3a8afe1abe301ffa3a5509404))

## 1.0.0 (2026-09-29)


### Features

* add app factory and health endpoints with unit tests ([231659d](https://github.com/evg-g/appointments-api/commit/231659d9fa46d37b606e66e4199b65bd86744010))
* **api:** add admin-only audit-log read endpoint ([b4c3178](https://github.com/evg-g/appointments-api/commit/b4c317871d546bf2b0685dab21597dcc7fd7d9ed))
* **api:** declare bearer auth as an OpenAPI security scheme ([5b957e1](https://github.com/evg-g/appointments-api/commit/5b957e1b191648628616bb7f66590b554400c19b))
* **api:** Idempotency-Key on create and ETag/If-Match concurrency ([ea86642](https://github.com/evg-g/appointments-api/commit/ea8664253918ccd315815ad9e8f84f266312cd95))
* **api:** per-principal rate limiting with RateLimit headers ([7b8bea2](https://github.com/evg-g/appointments-api/commit/7b8bea2549a6e62561615164d1c5fb29a7dbc675))
* **api:** routers, schemas, deps, problem+json, pagination, and app wiring ([7624083](https://github.com/evg-g/appointments-api/commit/762408369cec9c988cf98a91b173363a865ea901))
* **api:** signed webhooks with a retrying delivery worker ([02e6382](https://github.com/evg-g/appointments-api/commit/02e6382a1e041023f26b7246948f6ee2f35a6dc8))
* **auth:** Argon2 passwords, JWT, and refresh-token rotation with reuse detection ([cd99dcc](https://github.com/evg-g/appointments-api/commit/cd99dcc9bde01d2dc1cfc885c5533a6e435c03da))
* **ci:** backend CI/CD — build, scan, sign, deploy, smoke, nightly ([cd50ea2](https://github.com/evg-g/appointments-api/commit/cd50ea220cbaa3de49b614c5ac3969f0beb84ed9))
* **contract:** publish OpenAPI + drift and oasdiff breaking gates ([5a636b9](https://github.com/evg-g/appointments-api/commit/5a636b9f9e9017c4df20c7eb5409ad4e2928c602))
* **db:** add Alembic and the domain-core migration ([659705e](https://github.com/evg-g/appointments-api/commit/659705e995808194951b4c800d0d14e1cc09fe12))
* **db:** async SQLAlchemy engine/session and Redis wiring ([2098ab8](https://github.com/evg-g/appointments-api/commit/2098ab8368772f4ea0659c72e669e81b8006da14))
* **models:** add domain ORM models and shared enums ([8ef8a56](https://github.com/evg-g/appointments-api/commit/8ef8a56f8fbe5ad3957a2ce439b02a522150a7cc))
* **repositories:** SQLAlchemy repositories with keyset pagination ([7b6e8dd](https://github.com/evg-g/appointments-api/commit/7b6e8dd89f4105eb6b5e4f6c7b1aa9f70ebcefd5))
* **services:** add clock, state machine, cancellation and availability ([f7aab91](https://github.com/evg-g/appointments-api/commit/f7aab91273444e3e717875a4ba6e1f3179115e52))
* **telemetry:** ingest device telemetry (MQTT + HTTP), excursions, SSE, contract ([d9f54ad](https://github.com/evg-g/appointments-api/commit/d9f54ad36bb9ef08612203396eda98f57d920fda))


### Bug Fixes

* **api:** upgrade FastAPI 0.141 / Starlette 1.7 to clear HIGH CVEs ([25d1c77](https://github.com/evg-g/appointments-api/commit/25d1c77eb834e9971f78728fc5695fe960e01a66))


### Documentation

* add "Try the API by hand" (Swagger, ReDoc, .http, Postman) ([409da19](https://github.com/evg-g/appointments-api/commit/409da19706be43a7922adb98b6109df2c9746d73))
* add ADRs for exclusion constraint, time handling and optimistic locking ([64adab7](https://github.com/evg-g/appointments-api/commit/64adab76d31e481a9fb563a61478989a5228d72a))
* add README, contributing guide, conventions, first ADR, Dockerfile ([aa9a6fa](https://github.com/evg-g/appointments-api/commit/aa9a6fa0b9a8c10b5ba5594a8f0d32770175253d))
* ADRs, error catalog, env, and README for milestone 4 ([0a55844](https://github.com/evg-g/appointments-api/commit/0a558447c8cfa7b5ca09426235c92ceac9eadf97))
* **api:** CI badge and links to the Aurora repo ([3d8733c](https://github.com/evg-g/appointments-api/commit/3d8733ce9d924592b7e79354f13a8bf495dcc304))
* **api:** explain the Swagger Authorize flow ([3287a1b](https://github.com/evg-g/appointments-api/commit/3287a1bc6276887a96e959dc0921f0381ca8e357))
* **api:** milestone 16 — exercises, API testing guide, diagrams ([dc10c96](https://github.com/evg-g/appointments-api/commit/dc10c96d5a65559f7d9b298539b24d5737596108))
* **api:** note UV_SYSTEM_CERTS for local uv behind a TLS proxy ([b7068c1](https://github.com/evg-g/appointments-api/commit/b7068c1fa259c8914692e7b9c6b81834135e271a))
* error catalog, known gaps, and ADRs for auth, errors, and pagination ([cfa94f9](https://github.com/evg-g/appointments-api/commit/cfa94f96dce7c764f79fb6fbf3d4fe946b4b5df4))


### Miscellaneous Chores

* **api:** release 1.0.0 ([6a8cd07](https://github.com/evg-g/appointments-api/commit/6a8cd073eace97c7bb0c3d0f77474fb9c6d372ab))
