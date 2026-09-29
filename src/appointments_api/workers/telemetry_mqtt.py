"""MQTT telemetry ingestion worker — the primary ingestion path (spec §5).

The worker subscribes to the broker and funnels every message through the *same*
``TelemetryIngestionService`` the HTTP batch endpoint uses, so one test suite covers both paths.
Run it as its own process::

    python -m appointments_api.workers.telemetry_mqtt

Message handling (``handle_message``) is deliberately separate from the broker loop (``run``): it
takes a topic and a raw payload and does the parse/validate/ingest, so it can be tested against a
real database (testcontainers) with no broker at all. The device id comes from the *topic*, not the
envelope body — the broker's ACLs bind a device to its own topic, so the topic is authoritative.

Robustness: a malformed payload or a contract violation is logged and dropped (one bad device must
not stall the stream); an unknown or disabled device is logged and dropped; only unexpected errors
propagate. Ingestion itself is idempotent, so at-least-once redelivery from the broker is safe.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointments_api.config import Settings, get_settings
from appointments_api.db import create_engine, create_redis, create_sessionmaker
from appointments_api.services.clock import Clock, SystemClock
from appointments_api.services.telemetry.contract import ContractError, parse_batch
from appointments_api.services.telemetry.errors import DeviceInactiveError, DeviceNotFoundError
from appointments_api.telemetry_wiring import build_ingestion_service

logger = structlog.get_logger(__name__)


def device_id_from_topic(topic: str) -> str | None:
    """Extract the device id from ``aurora/v1/clinic/{clinic}/device/{device}/telemetry``."""
    parts = topic.split("/")
    if len(parts) == 7 and parts[4] == "device" and parts[6] == "telemetry":
        return parts[5]
    return None


async def handle_message(
    topic: str,
    payload: bytes | bytearray | str,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    settings: Settings,
    clock: Clock,
) -> None:
    """Parse, validate against the contract, and ingest one telemetry message."""
    device_id_str = device_id_from_topic(topic)
    if device_id_str is None:
        logger.warning("telemetry.mqtt.bad_topic", topic=topic)
        return
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("telemetry.mqtt.bad_json", topic=topic, error=str(exc))
        return
    try:
        device_id, readings = parse_batch(data, device_id_override=device_id_str)
    except ContractError as exc:
        logger.warning("telemetry.mqtt.contract_violation", topic=topic, error=str(exc))
        return
    try:
        device_uuid = uuid.UUID(device_id)
    except ValueError:
        logger.warning("telemetry.mqtt.bad_device_id", topic=topic, device_id=device_id)
        return

    async with sessionmaker() as session:
        service = build_ingestion_service(
            session=session, redis=redis, settings=settings, clock=clock
        )
        try:
            outcome = await service.ingest(device_uuid, readings)
            await session.commit()
        except (DeviceNotFoundError, DeviceInactiveError) as exc:
            await session.rollback()
            logger.warning("telemetry.mqtt.device_rejected", device_id=device_id, error=str(exc))
            return
        except Exception:
            await session.rollback()
            raise
    logger.info(
        "telemetry.mqtt.ingested",
        device_id=device_id,
        accepted=outcome.accepted,
        duplicates=outcome.duplicates,
        rejected=outcome.rejected,
        open_excursions=outcome.open_excursions,
    )


async def run(
    stop: asyncio.Event,
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    clock: Clock | None = None,
) -> None:
    """Connect to the broker, subscribe, and ingest until ``stop`` is set."""
    import aiomqtt

    clock = clock or SystemClock()
    async with aiomqtt.Client(
        hostname=settings.mqtt_broker_host,
        port=settings.mqtt_broker_port,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        identifier=settings.mqtt_client_id,
    ) as client:
        await client.subscribe(settings.mqtt_topic_filter)
        logger.info("telemetry.mqtt.subscribed", topic_filter=settings.mqtt_topic_filter)
        async for message in client.messages:
            if stop.is_set():
                break
            await handle_message(
                str(message.topic),
                message.payload,
                sessionmaker=sessionmaker,
                redis=redis,
                settings=settings,
                clock=clock,
            )


def main() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    redis = create_redis(settings)
    stop = asyncio.Event()

    async def _amain() -> None:
        try:
            await run(stop, settings=settings, sessionmaker=sessionmaker, redis=redis)
        finally:
            await engine.dispose()
            await redis.aclose()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    main()
