from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import case, func, or_, select

from services.api.database import Approval, Asset, CaptureSession, Report, Shipment
from services.api.schemas import Schema
from services.api.security import Actor, Db

router = APIRouter()


class InboxItem(Schema):
    id: str
    reference: str
    title: str
    recipient_name: str
    updated_at: datetime
    work_stage: str
    cover_asset_id: str | None
    photo_count: int


class InboxPage(Schema):
    items: list[InboxItem]
    counts: dict[str, int]
    page: int


@router.get("/v1/shipment-inbox", response_model=InboxPage)
def shipment_inbox(
    db: Db,
    actor: Actor,
    source: Literal["work", "sample"] = "work",
    role: Literal["MERCHANT", "BUYER"] | None = None,
    q: str = Query("", max_length=200),
    stage: str = Query("", max_length=40),
    sort: Literal["recent", "oldest"] = "recent",
    page: int = Query(1, ge=1),
    page_size: int = Query(8, ge=1, le=100),
):
    visible = or_(Shipment.organization_id == actor.organization_id, Shipment.buyer_id == actor.id)
    if role == "BUYER":
        visible = Shipment.buyer_id == actor.id
    elif role == "MERCHANT":
        visible = Shipment.organization_id == actor.organization_id
    # Existing generated examples carry both markers. Ordinary order codes may also start with DEMO-.
    sample = Shipment.reference.startswith("DEMO-") & Shipment.notes.startswith("Dữ liệu mô phỏng")
    filters = [visible, sample if source == "sample" else ~sample]
    term = q.strip()
    if term:
        fields = (Shipment.reference, Shipment.title, Shipment.recipient_name)
        filters.append(or_(*(field.icontains(term, autoescape=True) for field in fields)))

    confirmations = (
        select(func.count(Approval.id))
        .select_from(Report)
        .join(Approval, Approval.report_id == Report.id)
        .where(Report.shipment_id == Shipment.id, Report.version == Shipment.report_version)
        .correlate(Shipment)
        .scalar_subquery()
    )
    work_stage = case(
        ((Shipment.status == "REVIEW") & (confirmations >= 2), "CONFIRMED"),
        else_=Shipment.status,
    )
    counts = dict(db.execute(
        select(work_stage, func.count(Shipment.id)).where(*filters).group_by(work_stage)
    ).all())
    total = counts.get(stage, 0) if stage else sum(counts.values())
    page = min(page, max(1, (total + page_size - 1) // page_size))
    if stage:
        filters.append(work_stage == stage)
    order = (Shipment.updated_at.asc(), Shipment.id.asc()) if sort == "oldest" else (Shipment.updated_at.desc(), Shipment.id.desc())
    photos = (select().select_from(Asset).join(CaptureSession, CaptureSession.id == Asset.capture_session_id)
              .where(Asset.shipment_id == Shipment.id, Asset.status == "READY", CaptureSession.status == "SEALED")
              .correlate(Shipment))
    cover = (photos.add_columns(Asset.id)
             .order_by((Asset.stage == "DISPATCH").desc(), Asset.parent_overview_id.is_not(None), Asset.created_at, Asset.id)
             .limit(1).scalar_subquery())
    rows = db.execute(
        select(Shipment.id, Shipment.reference, Shipment.title, Shipment.recipient_name,
               Shipment.updated_at, work_stage.label("work_stage"), cover.label("cover_asset_id"),
               photos.add_columns(func.count(Asset.id)).scalar_subquery().label("photo_count")).where(*filters)
        .order_by(*order)
        .offset((page - 1) * page_size).limit(page_size)
    )
    return InboxPage(items=[InboxItem(**row) for row in rows.mappings()], counts=counts, page=page)
