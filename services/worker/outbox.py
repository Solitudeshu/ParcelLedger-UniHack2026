"""Transactional internal consumer. Locks + projection + acknowledgement commit together.

No remote effects are performed while holding a lock. A crash rolls back the
projection and acknowledgement; replay is deduplicated by source event ID.
"""
import argparse
import logging
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from services.api.config import settings
from services.api.database import OutboxEvent, SessionLocal, Shipment, WorkflowEvent, now

MAX_ATTEMPTS = 5
logger = logging.getLogger("kienchung.outbox")


def project(db, event):
    shipment = db.get(Shipment, event.aggregate_id)
    if shipment is None:
        raise ValueError("OUTBOX_AGGREGATE_MISSING")
    db.execute(insert(WorkflowEvent).values(
        event_id=event.id, shipment_id=shipment.id, kind=event.kind,
        occurred_at=event.created_at, projected_at=now(),
        measurement_scope="LOCAL_TEST" if settings().app_env == "local" else "APPLICATION",
    ).on_conflict_do_nothing(index_elements=["event_id"]))


def dispatch_once(consumer=project):
    with SessionLocal() as db, db.begin():
        event = db.scalar(select(OutboxEvent).where(
            OutboxEvent.delivered_at.is_(None), OutboxEvent.dead_at.is_(None),
            OutboxEvent.available_at <= now(),
        ).order_by(OutboxEvent.created_at, OutboxEvent.id).with_for_update(skip_locked=True).limit(1))
        if event is None:
            return False
        event.attempts += 1
        try:
            with db.begin_nested():
                consumer(db, event)
                db.flush()
            event.delivered_at, event.error_code = now(), None
        except Exception:
            event.error_code = "INTERNAL_PROJECTION_FAILED"
            event.available_at = now() + timedelta(seconds=min(300, 2 ** event.attempts))
            if event.attempts >= MAX_ATTEMPTS:
                event.dead_at = now()
            logger.warning("Projection failed for event %s (attempt %s)", event.id, event.attempts)
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.once:
        dispatch_once()
        return
    while True:
        try:
            worked = dispatch_once()
        except Exception:
            logger.warning("Outbox dependencies unavailable; retrying")
            worked = False
        time.sleep(.1 if worked else 2)


if __name__ == "__main__":
    main()
