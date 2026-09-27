"""Signed outbound webhooks: events, signing, delivery queue, and the retrying worker.

State changes on an appointment (spec §4 rule 8) are published as events, fanned out to matching
subscriptions, and delivered by a background worker that signs each request with HMAC-SHA256 and
retries with exponential backoff, dead-lettering a delivery that keeps failing.
"""
