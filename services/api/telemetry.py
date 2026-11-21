"""Aggregate process timings from the durable internal projection."""
from collections import Counter, defaultdict

import numpy as np
from fastapi import APIRouter
from sqlalchemy import select

from services.api.database import OutboxEvent, Shipment, WorkflowEvent
from services.api.security import Actor, Db

router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])


@router.get("/workflow")
def workflow_metrics(db: Db, actor: Actor):
    # Only the actor's merchant organization. No cross-tenant recipient activity.
    events = db.scalars(select(WorkflowEvent).join(Shipment).where(
        Shipment.organization_id == actor.organization_id,
    ).order_by(WorkflowEvent.occurred_at)).all()
    grouped = defaultdict(dict)
    for event in events:
        grouped[event.shipment_id].setdefault(event.kind, event.occurred_at)
    timings = {}
    for name, first, last in [
        ("analysis_seconds", "ANALYSIS_QUEUED", "REPORT_DRAFTED"),
        ("first_review_seconds", "REPORT_DRAFTED", "REPORT_REVIEW_CONFIRMED"),
        ("draft_from_creation_seconds", "SHIPMENT_CREATED", "REPORT_DRAFTED"),
    ]:
        values = [(items[last]-items[first]).total_seconds() for items in grouped.values()
                  if first in items and last in items and items[last] >= items[first]]
        timings[name] = {"sample_count": len(values), "median": float(np.median(values)) if values else None,
                         "p95": float(np.percentile(values, 95)) if values else None}
    pending = db.scalars(select(OutboxEvent).join(Shipment, Shipment.id == OutboxEvent.aggregate_id).where(
        Shipment.organization_id == actor.organization_id, OutboxEvent.delivered_at.is_(None),
    )).all()
    return {"case_count": len(grouped), "event_count": len(events),
            "event_counts": dict(Counter(event.kind for event in events)), "timings": timings,
            "measurement_scopes": sorted({event.measurement_scope for event in events}),
            "outbox_pending": sum(event.dead_at is None for event in pending),
            "outbox_dead_letters": sum(event.dead_at is not None for event in pending),
            "scope_note": "Số đo vận hành nội bộ; không phải bằng chứng về nhu cầu thị trường hoặc độ chính xác AI."}
