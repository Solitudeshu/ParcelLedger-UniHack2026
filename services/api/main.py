import secrets
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select, text

from services.api.arbitration import router as arbitration_router
from services.api.canonical import bytes_hash, canonical_bytes, document_hash
from services.api.chain import assert_report_editable
from services.api.chain import router as chain_router
from services.api.chain_rpc import deployment as chain_deployment
from services.api.config import settings
from services.api.database import (
    Approval,
    Asset,
    AuditEvent,
    AuthSession,
    CaptureSession,
    ChainCase,
    Invitation,
    Job,
    Organization,
    Report,
    Shipment,
    User,
    now,
)
from services.api.errors import DomainError, install_handlers
from services.api.images import decode_image, image_quality, preview_bytes
from services.api.inbox import router as inbox_router
from services.api.schemas import (
    AnalysisRequest,
    AssetView,
    CaptureCreate,
    DemoLogin,
    InvitationClaim,
    JobView,
    LoginRequest,
    ReportEdit,
    ReviewConfirmation,
    SealRequest,
    SessionView,
    ShipmentCreate,
    ShipmentView,
    UploadIntent,
)
from services.api.security import (
    COOKIE,
    DUMMY_HASH,
    Actor,
    Db,
    aware,
    issue_session,
    passwords,
    require_role,
    role,
    shipment_access,
    token_hash,
)
from services.api.storage import storage
from services.api.telemetry import router as telemetry_router
from services.api.transactions import event, idempotent
from services.api.wallet import router as wallet_router

app = FastAPI(
    title="ParcelLedger API",
    version="0.1.0",
    description="Biên bản giao nhận có bằng chứng. AI hỗ trợ người xác nhận.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
    expose_headers=["X-Correlation-ID"],
)
install_handlers(app)
app.include_router(telemetry_router)
app.include_router(inbox_router)
app.include_router(wallet_router)
app.include_router(chain_router)
app.include_router(arbitration_router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.correlation_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = request.state.correlation_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


def encoded(value):
    return jsonable_encoder(value)


def shipment_view(shipment, actor):
    fields = {
        name: getattr(shipment, name)
        for name in ShipmentView.model_fields
        if name not in {"current_role", "can_confirm", "recipient_linked"}
    }
    return ShipmentView(**fields, current_role=role(shipment, actor),
                        can_confirm=actor.id in {shipment.merchant_id, shipment.buyer_id},
                        recipient_linked=shipment.buyer_id is not None).model_dump(mode="json")


def report_view(db, report):
    approvals = db.scalars(select(Approval).where(Approval.report_id == report.id)).all()
    shipment = db.get(Shipment, report.shipment_id)
    return {
        "id": report.id,
        "version": report.version,
        "document": report.document,
        "sender_name": db.get(Organization, shipment.organization_id).name,
        "canonical_hash": report.canonical_hash,
        "created_at": encoded(report.created_at),
        "confirmations": [
            {"user_id": a.user_id, "kind": a.payload["kind"], "created_at": encoded(a.created_at),
             "role": "BUYER" if a.user_id == shipment.buyer_id else "MERCHANT",
             "signature": a.payload.get("signature")}
            for a in approvals
        ],
    }


@app.get("/health/live")
def live():
    return {"status": "ok", "service": "kien-chung-api"}


@app.get("/health/ready")
def ready(db: Db):
    try:
        db.execute(text("SELECT 1"))
        storage().ready()
    except Exception as exc:
        raise DomainError("DEPENDENCY_UNAVAILABLE", "Dịch vụ lưu trữ chưa sẵn sàng.", 503) from exc
    return {"status": "ok", "database": "ready", "storage": settings().storage_backend}


@app.get("/v1/config")
def public_config():
    deployed = chain_deployment()
    return {
        "app_env": settings().app_env,
        "demo_login_enabled": settings().app_env == "local" and settings().allow_local_demo_login,
        "chain_id": settings().chain_id,
        "escrow_factory_address": deployed["contracts"]["EscrowFactory"] if deployed else None,
        "demo_token_address": deployed["contracts"]["DemoToken"] if deployed else None,
        "network_label": "Avalanche Fuji · thử nghiệm" if settings().chain_id == 43113 else "Mạng thử nghiệm cục bộ",
        "analysis_mode": "GEOMETRY_BASELINE",
        "model_trained": False,
    }


@app.post("/v1/auth/login", response_model=SessionView)
def login(body: LoginRequest, request: Request, response: Response, db: Db):
    if request.headers.get("origin") not in {None, settings().web_origin}:
        raise DomainError("ORIGIN_INVALID", "Nguồn yêu cầu không hợp lệ.", 403)
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    valid = passwords.verify(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid:
        raise DomainError("LOGIN_FAILED", "Email hoặc mật khẩu chưa đúng.", 401)
    return issue_session(db, user, response)


@app.post("/v1/dev/session", response_model=SessionView)
def demo_session(body: DemoLogin, request: Request, response: Response, db: Db):
    if settings().app_env != "local" or not settings().allow_local_demo_login:
        raise DomainError("NOT_FOUND", "Không tìm thấy chức năng này.", 404)
    if request.headers.get("origin") not in {None, settings().web_origin}:
        raise DomainError("ORIGIN_INVALID", "Nguồn yêu cầu không hợp lệ.", 403)
    email = "shop@kienchung.local" if body.role == "MERCHANT" else "recipient@kienchung.local"
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        raise DomainError("DEMO_NOT_SEEDED", "Chưa khởi tạo tài khoản thử nghiệm.", 503)
    return issue_session(db, user, response)


@app.get("/v1/dev/fixtures/{filename}")
def dev_fixture(filename: str, actor: Actor):
    allowed = {
        "dispatch.png",
        "unchanged.png",
        "lighting.png",
        "changed.png",
        "low-detail.png",
        "no-reference.png",
    }
    if (
        settings().app_env != "local"
        or not settings().allow_local_demo_login
        or filename not in allowed
    ):
        raise DomainError("NOT_FOUND", "Không tìm thấy ảnh mẫu.", 404)
    return Response((Path("tests/fixtures/images") / filename).read_bytes(), media_type="image/png")


@app.get("/v1/auth/session", response_model=SessionView)
def session(request: Request, actor: Actor):
    return {"user": actor, "csrf_token": request.state.auth_session.csrf_token}


@app.post("/v1/auth/logout")
def logout(request: Request, response: Response, db: Db, actor: Actor):
    db.delete(db.get(AuthSession, request.state.auth_session.id))
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/v1/shipments", response_model=list[ShipmentView])
def shipments(db: Db, actor: Actor):
    items = db.scalars(
        select(Shipment)
        .where(
            or_(Shipment.organization_id == actor.organization_id, Shipment.buyer_id == actor.id)
        )
        .order_by(Shipment.created_at.desc())
        .limit(100)
    ).all()
    return [shipment_view(item, actor) for item in items]


@app.post("/v1/shipments", status_code=201, response_model=ShipmentView)
def create_shipment(body: ShipmentCreate, request: Request, db: Db, actor: Actor):
    def execute():
        if db.scalar(select(Shipment.id).where(Shipment.organization_id == actor.organization_id,
                                               Shipment.reference == body.reference)):
            raise DomainError("REFERENCE_EXISTS", "Mã kiện này đã có trong cửa hàng. Hãy nhập mã khác.", 409)
        item = Shipment(
            **body.model_dump(),
            organization_id=actor.organization_id,
            merchant_id=actor.id,
            case_id="0x" + secrets.token_hex(32),
        )
        db.add(item)
        db.flush()
        event(db, item.id, actor.id, "SHIPMENT_CREATED", {"reference": item.reference})
        return shipment_view(item, actor)

    return idempotent(db, actor, request, body.model_dump(mode="json"), execute)


@app.get("/v1/shipments/{shipment_id}", response_model=ShipmentView)
def get_shipment(shipment_id: str, db: Db, actor: Actor):
    return shipment_view(shipment_access(db, shipment_id, actor), actor)


@app.post("/v1/shipments/{shipment_id}/invitations")
def invite(shipment_id: str, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        require_role(shipment, actor, "MERCHANT")
        if shipment.buyer_id:
            raise DomainError("RECIPIENT_ALREADY_LINKED", "Người nhận đã được liên kết.", 409)
        token = secrets.token_urlsafe(32)
        expires = now() + timedelta(days=3)
        db.add(
            Invitation(token_hash=token_hash(token), shipment_id=shipment.id, expires_at=expires)
        )
        event(db, shipment.id, actor.id, "INVITATION_CREATED", {})
        return {
            "url": f"{settings().web_origin}/join#token={token}",
            "token": token,
            "expires_at": encoded(expires),
        }

    return idempotent(db, actor, request, {}, execute)


@app.post("/v1/invitations/claim")
def claim(body: InvitationClaim, request: Request, db: Db, actor: Actor):
    def execute():
        invitation = db.get(Invitation, token_hash(body.token))
        if not invitation or aware(invitation.expires_at) <= now():
            raise DomainError(
                "INVITATION_INVALID", "Liên kết nhận kiện không hợp lệ hoặc đã hết hạn.", 404
            )
        shipment = db.scalar(
            select(Shipment).where(Shipment.id == invitation.shipment_id).with_for_update()
        )
        if shipment.organization_id == actor.organization_id:
            raise DomainError(
                "PARTY_CONFLICT", "Người gửi và người nhận cần hai tài khoản độc lập.", 409
            )
        if shipment.buyer_id and shipment.buyer_id != actor.id:
            raise DomainError(
                "INVITATION_USED", "Kiện đã được liên kết với một người nhận khác.", 409
            )
        if invitation.accepted_by and invitation.accepted_by != actor.id:
            raise DomainError("INVITATION_USED", "Liên kết này đã được sử dụng.", 409)
        shipment.buyer_id = actor.id
        shipment.version += 1
        invitation.accepted_by = actor.id
        event(db, shipment.id, actor.id, "RECIPIENT_LINKED", {})
        return shipment_view(shipment, actor)

    # Store only the token hash in the idempotency request digest.
    return idempotent(db, actor, request, {"token_hash": token_hash(body.token)}, execute)


@app.get("/v1/shipments/{shipment_id}/captures")
def list_captures(shipment_id: str, db: Db, actor: Actor):
    shipment_access(db, shipment_id, actor)
    return encoded(
        db.scalars(
            select(CaptureSession)
            .where(CaptureSession.shipment_id == shipment_id)
            .order_by(CaptureSession.created_at)
        ).all()
    )


@app.post("/v1/shipments/{shipment_id}/captures", status_code=201)
def create_capture(shipment_id: str, body: CaptureCreate, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        if body.stage == "DISPATCH":
            require_role(shipment, actor, "MERCHANT")
        elif body.stage == "RECEIPT":
            require_role(shipment, actor, "BUYER")
        existing = db.scalars(
            select(CaptureSession).where(
                CaptureSession.shipment_id == shipment_id, CaptureSession.stage == body.stage
            )
        ).all()
        for item in existing:
            if item.status == "OPEN" and item.actor_id == actor.id:
                return encoded(item)
        if body.stage != "SUPPLEMENT" and any(c.status == "SEALED" for c in existing):
            raise DomainError(
                "STAGE_ALREADY_SEALED", "Mốc này đã khóa. Hãy tạo lần chụp bổ sung.", 409
            )
        capture = CaptureSession(shipment_id=shipment_id, actor_id=actor.id, stage=body.stage)
        db.add(capture)
        db.flush()
        event(
            db,
            shipment_id,
            actor.id,
            "CAPTURE_OPENED",
            {"capture_id": capture.id, "stage": body.stage},
        )
        return encoded(capture)

    return idempotent(db, actor, request, body.model_dump(mode="json"), execute)


def owned_capture(db, capture_id, shipment_id, actor, *, open_required=True):
    capture = db.scalar(
        select(CaptureSession).where(CaptureSession.id == capture_id).with_for_update()
    )
    if not capture or capture.shipment_id != shipment_id or capture.actor_id != actor.id:
        raise DomainError("NOT_FOUND", "Không tìm thấy lần chụp thuộc quyền của bạn.", 404)
    if open_required and capture.status != "OPEN":
        raise DomainError("CAPTURE_SEALED", "Bộ ảnh đã khóa và không thể thay đổi.", 409)
    return capture


@app.post("/v1/shipments/{shipment_id}/uploads", status_code=201)
def upload_intent(shipment_id: str, body: UploadIntent, request: Request, db: Db, actor: Actor):
    def execute():
        shipment_access(db, shipment_id, actor, lock=True)
        capture = owned_capture(db, str(body.capture_session_id), shipment_id, actor)
        assets = db.scalars(select(Asset).where(Asset.capture_session_id == capture.id,
                                               Asset.status != "CANCELLED")).all()
        for asset in assets:
            if asset.sha256 == body.sha256:
                return {
                    "asset_id": asset.id,
                    "upload_url": f"/v1/assets/{asset.id}/content",
                    "status": asset.status,
                }
        if len(assets) >= 40:
            raise DomainError("CAPTURE_LIMIT", "Mỗi lần chụp nhận tối đa 40 ảnh.", 422)
        if body.parent_overview_id:
            parent = db.get(Asset, str(body.parent_overview_id))
            if not parent or parent.capture_session_id != capture.id or parent.status != "READY":
                raise DomainError(
                    "INVALID_OVERVIEW",
                    "Ảnh tổng quan phải thuộc cùng lần chụp và đã tải xong.",
                    422,
                )
        asset_id = str(uuid4())
        asset = Asset(
            id=asset_id,
            shipment_id=shipment_id,
            capture_session_id=capture.id,
            stage=capture.stage,
            filename=Path(body.filename).name,
            mime_type=body.mime_type,
            byte_size=body.byte_size,
            sha256=body.sha256,
            source=body.source,
            parent_overview_id=str(body.parent_overview_id) if body.parent_overview_id else None,
            captured_at_claimed=body.captured_at_claimed,
            object_key=f"{shipment_id}/{capture.id}/{asset_id}/{body.sha256}",
        )
        db.add(asset)
        db.flush()
        return {
            "asset_id": asset.id,
            "upload_url": f"/v1/assets/{asset.id}/content",
            "status": asset.status,
        }

    return idempotent(db, actor, request, body.model_dump(mode="json"), execute)


@app.post("/v1/assets/{asset_id}/cancel-upload")
def cancel_pending_upload(asset_id: str, request: Request, db: Db, actor: Actor):
    def execute():
        asset = db.get(Asset, asset_id)
        if not asset:
            raise DomainError("NOT_FOUND", "Không tìm thấy ảnh.", 404)
        shipment_access(db, asset.shipment_id, actor, lock=True)
        owned_capture(db, asset.capture_session_id, asset.shipment_id, actor)
        db.refresh(asset)
        if asset.status not in {"PENDING", "CANCELLED"}:
            raise DomainError("UPLOAD_COMPLETE", "Ảnh đã tải lên thành công.", 409)
        asset.status = "CANCELLED"
        return {"status": asset.status}

    return idempotent(db, actor, request, {}, execute)


@app.post("/v1/assets/{asset_id}/upload-ticket")
def upload_ticket(asset_id: str, db: Db, actor: Actor):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise DomainError("NOT_FOUND", "Không tìm thấy ảnh.", 404)
    shipment_access(db, asset.shipment_id, actor)
    owned_capture(db, asset.capture_session_id, asset.shipment_id, actor)
    db.refresh(asset)
    if asset.status == "CANCELLED":
        raise DomainError("UPLOAD_CANCELLED", "Ảnh chưa tải đã được bỏ.", 409)
    if asset.status != "PENDING":
        return {"transport": "ALREADY_UPLOADED", "status": asset.status}
    if settings().presigned_uploads_enabled and settings().storage_backend == "s3":
        return storage().upload_ticket(asset)
    return {"transport": "API_PUT", "url": f"/v1/assets/{asset.id}/content"}


@app.put("/v1/assets/{asset_id}/content")
async def upload_content(asset_id: str, request: Request, db: Db, actor: Actor):
    # Authenticate before reading. Bound memory independently of Content-Length.
    asset = db.get(Asset, asset_id)
    if not asset:
        raise DomainError("NOT_FOUND", "Không tìm thấy ảnh.", 404)
    shipment_access(db, asset.shipment_id, actor)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > min(settings().max_upload_bytes, asset.byte_size):
            raise DomainError("UPLOAD_TOO_LARGE", "Ảnh vượt kích thước đã đăng ký.", 413)
    raw = bytes(data)
    if len(raw) != asset.byte_size or bytes_hash(raw) != asset.sha256:
        raise DomainError(
            "CHECKSUM_MISMATCH", "Ảnh nhận được khác ảnh đã đăng ký. Hãy thử lại.", 422
        )
    shipment_access(db, asset.shipment_id, actor, lock=True)
    capture = owned_capture(
        db, asset.capture_session_id, asset.shipment_id, actor, open_required=False
    )
    db.refresh(asset)
    if asset.status == "CANCELLED":
        raise DomainError("UPLOAD_CANCELLED", "Ảnh chưa tải đã được bỏ.", 409)
    if asset.status in {"UPLOADED", "READY"}:
        return {"asset_id": asset.id, "status": asset.status}
    if capture.status != "OPEN":
        raise DomainError("CAPTURE_SEALED", "Bộ ảnh đã khóa.", 409)
    image = decode_image(raw, asset.mime_type)
    storage().put(asset.object_key, raw, asset.mime_type)
    asset.width, asset.height = image.size
    asset.quality = image_quality(image)
    asset.status = "UPLOADED"
    db.commit()
    return {"asset_id": asset.id, "status": asset.status, "quality": asset.quality}


@app.post("/v1/assets/{asset_id}/complete", response_model=AssetView)
def complete_upload(asset_id: str, request: Request, db: Db, actor: Actor):
    def execute():
        asset = db.get(Asset, asset_id)
        if not asset:
            raise DomainError("NOT_FOUND", "Không tìm thấy ảnh.", 404)
        shipment_access(db, asset.shipment_id, actor, lock=True)
        capture = owned_capture(db, asset.capture_session_id, asset.shipment_id, actor, open_required=False)
        db.refresh(asset)
        if asset.status == "CANCELLED":
            raise DomainError("UPLOAD_CANCELLED", "Ảnh chưa tải đã được bỏ.", 409)
        if asset.status == "PENDING":
            if capture.status != "OPEN":
                raise DomainError("CAPTURE_SEALED", "Bộ ảnh đã khóa.", 409)
            raw = None
            if settings().presigned_uploads_enabled and settings().storage_backend == "s3":
                try:
                    raw = storage().staged_content(asset)
                except ValueError:
                    raise DomainError("CHECKSUM_MISMATCH", "Ảnh tải lên không đúng kích thước đã đăng ký.", 422)
            if raw is None:
                raise DomainError("ASSET_NOT_READY", "Ảnh chưa tải lên hoàn tất.", 409)
            if bytes_hash(raw) != asset.sha256:
                raise DomainError("CHECKSUM_MISMATCH", "Ảnh tải lên khác ảnh đã đăng ký. Hãy thử lại.", 422)
            image = decode_image(raw, asset.mime_type)
            storage().put(asset.object_key, raw, asset.mime_type)
            asset.width, asset.height = image.size
            asset.quality = image_quality(image)
            asset.status = "UPLOADED"
        if asset.status != "READY":
            raw = storage().get(asset.object_key)
            if bytes_hash(raw) != asset.sha256:
                raise DomainError(
                    "STORAGE_CORRUPTION", "Kiểm tra toàn vẹn ảnh không thành công.", 503
                )
            asset.status = "READY"
            event(
                db,
                asset.shipment_id,
                actor.id,
                "ASSET_READY",
                {"asset_id": asset.id, "sha256": asset.sha256},
            )
        return AssetView.model_validate(asset).model_dump(mode="json")

    return idempotent(db, actor, request, {}, execute)


@app.get("/v1/shipments/{shipment_id}/assets", response_model=list[AssetView])
def list_assets(shipment_id: str, db: Db, actor: Actor):
    shipment_access(db, shipment_id, actor)
    return db.scalars(
        select(Asset).where(Asset.shipment_id == shipment_id, Asset.status != "CANCELLED").order_by(Asset.created_at)
    ).all()


@app.get("/v1/assets/{asset_id}/content")
def get_image(asset_id: str, db: Db, actor: Actor, variant: str = "preview"):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise DomainError("NOT_FOUND", "Không tìm thấy ảnh.", 404)
    shipment_access(db, asset.shipment_id, actor)
    if asset.status != "READY":
        raise DomainError("ASSET_NOT_READY", "Ảnh chưa sẵn sàng.", 409)
    data = storage().get(asset.object_key)
    if bytes_hash(data) != asset.sha256:
        raise DomainError("STORAGE_CORRUPTION", "Ảnh không khớp mã toàn vẹn đã lưu.", 503)
    if variant == "original":
        return Response(
            data, media_type=asset.mime_type, headers={"Content-Disposition": "attachment"}
        )
    return Response(preview_bytes(data, 360 if variant == "thumbnail" else 1400), media_type="image/jpeg")


@app.post("/v1/shipments/{shipment_id}/captures/{capture_id}/seal")
def seal(
    shipment_id: str, capture_id: str, body: SealRequest, request: Request, db: Db, actor: Actor
):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        capture = owned_capture(db, capture_id, shipment_id, actor, open_required=False)
        if capture.version != body.expected_version:
            raise DomainError(
                "VERSION_CONFLICT",
                "Bộ ảnh đã thay đổi. Hãy tải lại.",
                409,
                current_version=capture.version,
            )
        if capture.status == "SEALED":
            return encoded(capture)
        assets = db.scalars(
            select(Asset).where(Asset.capture_session_id == capture_id, Asset.status != "CANCELLED").order_by(Asset.id)
        ).all()
        if not assets or any(a.status != "READY" for a in assets):
            raise DomainError(
                "ASSET_NOT_READY", "Cần tải hoàn tất mọi ảnh trước khi khóa bộ ảnh.", 409
            )
        manifest = {
            "schema_version": "evidence-v1",
            "shipment_id": shipment_id,
            "case_id": shipment.case_id,
            "capture_session_id": capture_id,
            "stage": capture.stage,
            "actor_id": actor.id,
            "actor_role": role(shipment, actor),
            "salt": secrets.token_hex(32),
            "sealed_at": now().isoformat(),
            "assets": [
                {
                    "id": a.id,
                    "sha256": a.sha256,
                    "byte_size": a.byte_size,
                    "width": a.width,
                    "height": a.height,
                    "source": a.source,
                    "captured_at_claimed": encoded(a.captured_at_claimed),
                    "parent_overview_id": a.parent_overview_id,
                }
                for a in assets
            ],
        }
        capture.manifest, capture.manifest_hash = manifest, document_hash(manifest)
        capture.status, capture.version = "SEALED", capture.version + 1
        if capture.stage == "DISPATCH" and shipment.status == "PREPARING":
            shipment.status = "DISPATCHED"
        elif capture.stage == "RECEIPT" and shipment.status in {"PREPARING", "DISPATCHED"}:
            shipment.status = "RECEIVED"
        shipment.version += 1
        event(
            db,
            shipment_id,
            actor.id,
            "CAPTURE_SEALED",
            {
                "capture_id": capture.id,
                "stage": capture.stage,
                "manifest_hash": capture.manifest_hash,
            },
        )
        return encoded(capture)

    return idempotent(db, actor, request, body.model_dump(mode="json"), execute)


@app.post("/v1/shipments/{shipment_id}/analyses", status_code=202, response_model=JobView)
def analyze(shipment_id: str, body: AnalysisRequest, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        assert_report_editable(db, shipment)
        query = db.get(Asset, str(body.query_asset_id))
        if not query or query.shipment_id != shipment_id or query.status != "READY":
            raise DomainError("ASSET_NOT_READY", "Ảnh cần kiểm tra chưa sẵn sàng.", 422)
        capture = db.get(CaptureSession, query.capture_session_id)
        if capture.status != "SEALED" or capture.stage == "DISPATCH":
            raise DomainError(
                "CAPTURE_NOT_SEALED", "Hãy khóa bộ ảnh nhận hàng trước khi phân tích.", 409
            )
        if capture.actor_id != shipment.buyer_id:
            raise DomainError("RECEIPT_REQUIRED", "Chọn ảnh đã khóa của người nhận.", 422)
        references = db.scalars(
            select(CaptureSession).where(
                CaptureSession.shipment_id == shipment_id,
                CaptureSession.stage == "DISPATCH",
                CaptureSession.status == "SEALED",
            )
        ).all()
        reference_ids = sorted(a["id"] for c in references for a in c.manifest["assets"])
        if body.reference_asset_id:
            if str(body.reference_asset_id) not in reference_ids:
                raise DomainError(
                    "INVALID_REFERENCE",
                    "Ảnh tham chiếu phải thuộc bộ ảnh gửi đã khóa của kiện này.",
                    422,
                )
            reference_ids = [str(body.reference_asset_id)]
        from services.worker.baseline import BASELINE_CONFIG, BASELINE_VERSION, SOURCE_SHA256
        from services.worker.change_shadow import identity as change_identity
        from services.worker.report_composer import config_identity as composition_identity
        from services.worker.shadow import bundle_identity as ranker_identity
        from services.worker.transformer_shadow import identity as transformer_identity

        ranker_bundle = ranker_identity()
        try:
            transformer_bundle = transformer_identity()
        except Exception:
            # Optional GPU research must never prevent the primary analysis.
            transformer_bundle = None
        inputs = {
            "shipment_id": shipment_id,
            "case_id": shipment.case_id,
            "query_asset_id": query.id,
            "query_region": body.model_dump(mode="json")["query_region"],
            "query_manifest_hash": capture.manifest_hash,
            "reference_manifest_hashes": sorted(c.manifest_hash for c in references),
            "reference_asset_ids": reference_ids,
            "selection_method": "USER_SELECTED" if body.reference_asset_id else "AUTOMATIC",
            "model_version": BASELINE_VERSION,
            "config_sha256": document_hash(BASELINE_CONFIG),
            "baseline_source_sha256": SOURCE_SHA256,
            "shadow_checkpoint_sha256": ranker_bundle["checkpoint_sha256"] if ranker_bundle else None,
            "shadow_bundle_identity": ranker_bundle,
            "shadow_change_identity": change_identity(),
            "research_transformer_identity": transformer_bundle,
            "research_transformer_requested": bool(settings().research_transformer_path),
            "composition_config_sha256": composition_identity(),
        }
        digest = document_hash(inputs)
        job = db.scalar(select(Job).where(Job.input_hash == digest))
        if not job or job.status == "FAILED":
            # Serialize intake across this merchant's shipments. Counting without
            # this lock would allow concurrent requests to bypass the queue limit.
            db.scalar(select(Organization).where(Organization.id == shipment.organization_id).with_for_update())
            active = db.scalar(select(func.count()).select_from(Job).join(Shipment).where(
                Shipment.organization_id == shipment.organization_id, Job.status.in_(["QUEUED", "RUNNING"])))
            if active >= settings().max_active_jobs_per_organization:
                raise DomainError("ANALYSIS_QUEUE_FULL", "Đã có nhiều hồ sơ đang xử lý. Hãy đợi một hồ sơ hoàn tất rồi thử lại.", 429, retryable=True)
            if job is None:
                job = Job(shipment_id=shipment_id, input_hash=digest, inputs=inputs)
                db.add(job)
            else:
                job.status, job.attempts, job.error = "QUEUED", 0, None
                job.progress = "Đang chờ đối chiếu lại"
            db.flush()
            shipment.status = "ANALYZING"
            shipment.version += 1
            event(
                db,
                shipment_id,
                actor.id,
                "ANALYSIS_QUEUED",
                {"job_id": job.id, "input_hash": digest},
            )
        return JobView.model_validate(job).model_dump(mode="json")

    return idempotent(db, actor, request, body.model_dump(mode="json"), execute)


@app.get("/v1/shipments/{shipment_id}/jobs", response_model=list[JobView])
def list_jobs(shipment_id: str, db: Db, actor: Actor):
    shipment_access(db, shipment_id, actor)
    return db.scalars(
        select(Job).where(Job.shipment_id == shipment_id).order_by(Job.created_at.desc())
    ).all()


@app.get("/v1/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, db: Db, actor: Actor):
    job = db.get(Job, job_id)
    if not job:
        raise DomainError("NOT_FOUND", "Không tìm thấy lượt phân tích.", 404)
    shipment_access(db, job.shipment_id, actor)
    return job


@app.get("/v1/shipments/{shipment_id}/reports")
def list_reports(shipment_id: str, db: Db, actor: Actor):
    shipment_access(db, shipment_id, actor)
    return [
        report_view(db, r)
        for r in db.scalars(
            select(Report).where(Report.shipment_id == shipment_id).order_by(Report.version.desc())
        ).all()
    ]


@app.post("/v1/shipments/{shipment_id}/reports", status_code=201)
def edit_report(shipment_id: str, body: ReportEdit, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        assert_report_editable(db, shipment)
        if shipment.report_version != body.expected_version:
            raise DomainError(
                "REPORT_VERSION_CONFLICT",
                "Biên bản đã có phiên bản mới. Hãy kiểm tra lại.",
                409,
                current_version=shipment.report_version,
            )
        previous = db.scalar(
            select(Report).where(
                Report.shipment_id == shipment_id, Report.version == shipment.report_version
            )
        )
        if not previous:
            raise DomainError("NOT_FOUND", "Chưa có biên bản để chỉnh sửa.", 404)
        allowed = set(previous.document["evidence_asset_ids"])
        ids = [o.id for o in body.observations]
        if len(ids) != len(set(ids)):
            raise DomainError("DUPLICATE_OBSERVATION", "Mã nhận xét phải khác nhau.", 422)
        observations = []
        previous_items = {item["id"]: item for item in previous.document["observations"]}
        for observation in body.observations:
            if not set(observation.evidence_asset_ids) <= allowed:
                raise DomainError(
                    "INVALID_EVIDENCE_REFERENCE", "Nhận xét trỏ đến ảnh ngoài hồ sơ đã khóa.", 422
                )
            item = observation.model_dump(mode="json")
            original = previous_items.get(item["id"])
            if original and all(original.get(key) == value for key, value in item.items()
                                if key not in {"author_type", "review_status"}):
                observations.append(original)
            else:
                observations.append({**item, "author_type": "HUMAN", "review_status": "EDITED"})
        document = {
            **previous.document,
            "version": shipment.report_version + 1,
            "parent_report_hash": previous.canonical_hash,
            "observations": observations,
            "user_notes": body.user_notes,
            "created_by": actor.id,
            "created_at": now().isoformat(),
        }
        report = Report(
            shipment_id=shipment_id,
            version=document["version"],
            parent_id=previous.id,
            document=document,
            canonical_hash=document_hash(document),
            created_by=actor.id,
        )
        db.add(report)
        shipment.report_version += 1
        shipment.version += 1
        shipment.status = "REVIEW"
        db.flush()
        event(
            db,
            shipment_id,
            actor.id,
            "REPORT_REVISED",
            {"version": report.version, "report_hash": report.canonical_hash},
        )
        return report_view(db, report)

    return idempotent(db, actor, request, body.model_dump(mode="json"), execute)


@app.post("/v1/shipments/{shipment_id}/reports/confirm")
def confirm_report(
    shipment_id: str, body: ReviewConfirmation, request: Request, db: Db, actor: Actor
):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        if actor.id not in {shipment.merchant_id, shipment.buyer_id}:
            raise DomainError("FORBIDDEN", "Chỉ hai bên được chỉ định mới xác nhận biên bản.", 403)
        if shipment.report_version != body.expected_version:
            raise DomainError(
                "REPORT_VERSION_CONFLICT",
                "Có bản mới hơn. Hãy đọc lại trước khi xác nhận.",
                409,
                current_version=shipment.report_version,
            )
        report = db.scalar(
            select(Report).where(
                Report.shipment_id == shipment_id, Report.version == body.expected_version
            )
        )
        if not report or report.canonical_hash != body.report_hash:
            raise DomainError("REPORT_HASH_MISMATCH", "Nội dung xác nhận không khớp biên bản.", 409)
        existing = db.scalar(
            select(Approval).where(Approval.report_id == report.id, Approval.user_id == actor.id)
        )
        if not existing:
            # Account confirmation is deliberately NOT a blockchain settlement signature.
            existing = Approval(
                report_id=report.id,
                user_id=actor.id,
                wallet_address="",
                signature="",
                expires_at=0,
                payload={"kind": "ACCOUNT_REVIEW", "report_hash": report.canonical_hash},
            )
            db.add(existing)
            db.flush()
            shipment.updated_at = now()
            event(db, shipment_id, actor.id, "REPORT_REVIEW_CONFIRMED", {"version": report.version})
        if body.signature:
            mark = body.signature.model_dump(mode="json")
            prior = existing.payload.get("signature")
            if prior and any(prior[key] != value for key, value in mark.items()):
                raise DomainError("ALREADY_SIGNED", "Bạn đã ký phiên bản này.", 409)
            if not prior:
                signed_at = now()
                existing.payload = {**existing.payload, "signature": {
                    **mark, "signed_at": signed_at.isoformat(), "account_name": actor.name,
                    "report_hash": report.canonical_hash, "version": report.version,
                }}
                shipment.updated_at = signed_at
                event(db, shipment_id, actor.id, "REPORT_SIGNED", {
                    "version": report.version, "report_hash": report.canonical_hash,
                })
        return report_view(db, report)

    return idempotent(db, actor, request, body.model_dump(mode="json"), execute)


@app.get("/v1/shipments/{shipment_id}/reports/{version}/export")
def export_report(shipment_id: str, version: int, db: Db, actor: Actor):
    shipment_access(db, shipment_id, actor)
    report = db.scalar(
        select(Report).where(Report.shipment_id == shipment_id, Report.version == version)
    )
    if not report:
        raise DomainError("NOT_FOUND", "Không tìm thấy phiên bản biên bản.", 404)
    return Response(
        canonical_bytes(report.document),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="bien-ban-v{version}.json"',
            "X-Content-SHA256": report.canonical_hash,
        },
    )


@app.get("/v1/shipments/{shipment_id}/reports/{version}/pdf")
def export_report_pdf(shipment_id: str, version: int, db: Db, actor: Actor):
    from services.api.report_pdf import render_report
    shipment = shipment_access(db, shipment_id, actor)
    report = db.scalar(select(Report).where(Report.shipment_id == shipment_id, Report.version == version))
    if not report:
        raise DomainError("NOT_FOUND", "Không tìm thấy phiên bản biên bản.", 404)
    match = report.document["match"]
    pictures = {}
    for asset_id in {match.get("query_asset_id"), match.get("reference_asset_id")} - {None}:
        asset = db.get(Asset, asset_id)
        if asset is None or asset.shipment_id != shipment_id or asset_id not in report.document["evidence_asset_ids"]:
            raise DomainError("REPORT_EVIDENCE_INVALID", "Ảnh tham chiếu không khớp hồ sơ.", 409)
        raw = storage().get(asset.object_key)
        if bytes_hash(raw) != asset.sha256:
            raise DomainError("EVIDENCE_INTEGRITY_ERROR", "Ảnh gốc không vượt qua kiểm tra toàn vẹn.", 503)
        pictures[asset_id] = decode_image(raw, asset.mime_type)
    view = report_view(db, report)
    chain = db.get(ChainCase, shipment_id)
    chain_summary = {"chain_id": chain.chain_id, "address": chain.escrow_address, "synced_at": chain.synced_at,
                     "state": chain.snapshot.get("state")} if chain else None
    content = render_report(
        report.document, report.canonical_hash, pictures, view["confirmations"], chain_summary,
        sender=view["sender_name"],
        recipient=shipment.recipient_name,
        sample=shipment.notes if shipment.reference.startswith("DEMO-") and shipment.notes.startswith("Dữ liệu mô phỏng") else "",
    )
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="bien-ban-v{version}.pdf"',
        "X-Content-SHA256": bytes_hash(content), "X-Report-SHA256": report.canonical_hash})


@app.get("/v1/shipments/{shipment_id}/timeline")
def timeline(shipment_id: str, db: Db, actor: Actor):
    shipment_access(db, shipment_id, actor)
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.shipment_id == shipment_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    return encoded(events)
