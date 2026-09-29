"""Telemetry domain services: the excursion engine and the ingestion pipeline.

Pure logic here has no FastAPI or SQLAlchemy imports, matching the repo's layering rule — the
excursion engine is a faithful port of the device's ``logic.excursion`` so the two agree on the
shared fixture set (spec §4 rule 11).
"""
