from collections.abc import Callable

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.api.canonical import document_hash
from services.api.database import AuditEvent, IdempotencyRecord, OutboxEvent, User
from services.api.errors import DomainError


def idempotent(
    db: Session, actor: User, request: Request, payload: dict, execute: Callable[[], dict]
) -> dict:
    key = request.headers.get("Idempotency-Key", "")
    if not 8 <= len(key) <= 100:
        raise DomainError("IDEMPOTENCY_KEY_REQUIRED", "Thiếu mã thao tác để gửi lại an toàn.", 400)
    # Lock once per actor before any business locks to serialize concurrent retries.
    db.scalar(select(User).where(User.id == actor.id).with_for_update())
    operation = f"{request.method} {request.url.path}"
    digest = document_hash(payload)
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.actor_id == actor.id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if record:
        if record.request_hash != digest:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT", "Mã thao tác đã được dùng cho nội dung khác.", 409
            )
        return record.response
    result = execute()
    db.add(
        IdempotencyRecord(
            actor_id=actor.id, operation=operation, key=key, request_hash=digest, response=result
        )
    )
    db.commit()
    return result


def event(db: Session, shipment_id: str, actor_id: str, kind: str, detail: dict):
    db.add(AuditEvent(shipment_id=shipment_id, actor_id=actor_id, kind=kind, detail=detail))
    db.add(OutboxEvent(aggregate_id=shipment_id, kind=kind, payload=detail))
