from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from conftest import post
from sqlalchemy import func, select

from services.api.database import OutboxEvent, SessionLocal, WorkflowEvent, now
from services.worker.outbox import dispatch_once, project


def test_outbox_concurrent_delivery_and_replay(clients):
    merchant, _, outsider = clients
    result = post(merchant, "/v1/shipments", {"reference": "OUTBOX", "title": "Hộp", "recipient_name": "Minh"})
    assert result.status_code == 201
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: dispatch_once(), range(2)))
    assert sum(results) == 1
    with SessionLocal() as db, db.begin():
        event = db.scalar(select(OutboxEvent))
        assert event.delivered_at is not None
        event.delivered_at = None  # Intentional replay after an external maintenance retry.
    assert dispatch_once()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowEvent)) == 1
    metrics = merchant.get("/v1/telemetry/workflow").json()
    assert metrics["case_count"] == metrics["event_count"] == 1
    assert metrics["measurement_scopes"] == ["LOCAL_TEST"]
    assert outsider.get("/v1/telemetry/workflow").json()["event_count"] == 0


def test_projection_failure_rolls_back_and_dead_letters(clients):
    merchant, _, _ = clients
    post(merchant, "/v1/shipments", {"reference": "FAIL", "title": "Hộp", "recipient_name": "Minh"})

    def broken(db, event):
        project(db, event)
        raise RuntimeError("simulated failure after write")

    for attempt in range(1, 6):
        assert dispatch_once(broken)
        with SessionLocal() as db, db.begin():
            event = db.scalar(select(OutboxEvent))
            assert event.attempts == attempt and event.delivered_at is None
            assert db.scalar(select(func.count()).select_from(WorkflowEvent)) == 0
            event.available_at = now() - timedelta(seconds=1)
    assert not dispatch_once()
    metrics = merchant.get("/v1/telemetry/workflow").json()
    assert metrics["outbox_dead_letters"] == 1
