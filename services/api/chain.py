"""Human-authorized on-chain intents. The API never holds users' signing keys."""
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from services.api import chain_rpc
from services.api.canonical import document_hash
from services.api.config import settings
from services.api.database import (
    CaptureSession,
    ChainCase,
    ChainIntent,
    ChainLog,
    ChainSignature,
    Report,
    User,
    now,
)
from services.api.errors import DomainError
from services.api.schemas import Amount, Schema
from services.api.security import Actor, Db, shipment_access
from services.api.transactions import event, idempotent
from services.api.wallet import recover

router = APIRouter(prefix="/v1/shipments/{shipment_id}/chain", tags=["chain"])


class IntentRequest(Schema):
    kind: Literal["TERMS", "REPORT", "SETTLEMENT"]
    buyer_amount: Amount = "0"


class IntentSignature(Schema):
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


def effective_chain_time(timestamp):
    # Anvil automines only when a transaction arrives. After an idle weekend,
    # its latest block can predate the next transaction by days. Do not alter
    # chain history or extend existing terms; use wall time for NEW local terms.
    if settings().app_env == "local" and settings().chain_id == 31337:
        return max(timestamp, int(now().timestamp()))
    return timestamp


def parties(db, shipment):
    merchant, buyer = db.get(User, shipment.merchant_id), db.get(User, shipment.buyer_id) if shipment.buyer_id else None
    if not buyer or not merchant.wallet_address or not buyer.wallet_address:
        raise DomainError("PARTY_WALLETS_REQUIRED", "Cả người gửi và người nhận cần liên kết ví trước.", 409)
    return merchant, buyer


def chain_case(db, shipment):
    deployed = chain_rpc.deployment()
    if not deployed:
        raise DomainError("CHAIN_NOT_DEPLOYED", "Hợp đồng thử nghiệm chưa được triển khai trên mạng này.", 409)
    block, _, _ = chain_rpc.network_block()
    address = chain_rpc.call(deployed["contracts"]["EscrowFactory"], "cases(bytes32)", ["address"], ["bytes32"], [bytes.fromhex(shipment.case_id[2:])], block=block)
    row = db.get(ChainCase, shipment.id)
    if address == chain_rpc.ZERO_ADDRESS:
        if row is not None:
            raise DomainError("CHAIN_HISTORY_UNAVAILABLE", "Mạng hiện tại không có hợp đồng của hồ sơ đã ghi nhận. Giữ nguyên bằng chứng; cần kiểm tra khôi phục mạng trước khi ký tiếp.", 409)
        return None
    if row is not None and (row.chain_id != settings().chain_id or row.escrow_address.lower() != address.lower()):
        raise DomainError("CHAIN_HISTORY_UNAVAILABLE", "Địa chỉ hợp đồng đã khác bản ghi của hồ sơ. Không tiếp tục ký trên dữ liệu mạng chưa được đối chiếu.", 409)
    current = chain_rpc.snapshot(address)
    merchant, buyer = parties(db, shipment)
    terms = current["terms"]
    if (terms["caseId"].lower() != shipment.case_id.lower() or
        terms["merchant"].lower() != merchant.wallet_address or terms["buyer"].lower() != buyer.wallet_address or
        terms["token"].lower() != deployed["contracts"]["DemoToken"].lower() or
        terms["arbitrationSafe"].lower() != deployed["contracts"]["ArbitrationSafe"].lower() or
        terms["reserveAmount"] != shipment.reserve_amount):
        raise DomainError("CHAIN_TERMS_MISMATCH", "Điều khoản trên chain không khớp hồ sơ.", 409)
    if row is None:
        row = ChainCase(shipment_id=shipment.id, chain_id=settings().chain_id, escrow_address=address, terms=terms)
        db.add(row)
    row.snapshot, row.synced_at = current, now()
    return row


def latest_report(db, shipment):
    row = db.scalar(select(Report).where(Report.shipment_id == shipment.id, Report.version == shipment.report_version))
    if row is None:
        raise DomainError("REPORT_REQUIRED", "Cần biên bản để ký xác nhận hoặc xử lý khoản bảo đảm.", 409)
    return row


def intent_valid(intent, shipment, current):
    if intent.expires_at <= now():
        return False
    message = intent.typed_data["message"]
    if intent.kind == "TERMS":
        return (current["state"] == "1" and message["dispatchHash"] == current["dispatchHash"]
                and current["chain_timestamp"] <= int(current["terms"]["acceptBy"]))
    if current["state"] == "2" and current["chain_timestamp"] > int(current["terms"]["reviewDeadline"]):
        return False
    nonce = current["reportNonce" if intent.kind == "REPORT" else "settlementNonce"]
    if (current["state"] not in {"2", "3"} or message["evidenceRoot"] != current["evidenceRoot"] or
        str(message["nonce"]) != nonce or intent.report_version != shipment.report_version):
        return False
    return intent.kind != "SETTLEMENT" or message["reportHash"] == current["acceptedReportHash"]


def intent_view(db, row, shipment, current):
    signatures = db.scalars(select(ChainSignature).where(ChainSignature.intent_id == row.id)).all()
    return {"id": row.id, "kind": row.kind, "typed_data": row.typed_data, "contract_payload": row.contract_payload,
        "expires_at": row.expires_at.isoformat(), "report_version": row.report_version,
        "valid": intent_valid(row, shipment, current),
        "signatures": [{"user_id": signature.user_id, "wallet_address": signature.wallet_address,
                        "signature": signature.signature} for signature in signatures]}


@router.get("")
def chain_status(shipment_id: str, db: Db, actor: Actor):
    shipment = shipment_access(db, shipment_id, actor)
    deployed = chain_rpc.deployment()
    info = {"available": bool(deployed), "chain_id": settings().chain_id,
            "network_label": "Avalanche Fuji · thử nghiệm" if settings().chain_id == 43113 else "Mạng thử nghiệm cục bộ",
            "contracts": deployed["contracts"] if deployed else {}, "case": None, "intents": []}
    if not deployed:
        return info
    try:
        merchant, buyer = parties(db, shipment)
    except DomainError as exc:
        info["pending_reason"] = exc.code
        return info
    info["parties"] = {"merchant": merchant.wallet_address, "buyer": buyer.wallet_address}
    row = chain_case(db, shipment)
    if row:
        info["case"] = {"address": row.escrow_address, **row.snapshot}
        intents = db.scalars(select(ChainIntent).where(ChainIntent.shipment_id == shipment_id).order_by(ChainIntent.created_at.desc()).limit(50)).all()
        info["intents"] = [intent_view(db, intent, shipment, row.snapshot) for intent in intents]
    db.commit()
    return info


@router.get("/events")
def events(shipment_id: str, db: Db, actor: Actor):
    shipment_access(db, shipment_id, actor)
    logs = db.scalars(select(ChainLog).where(ChainLog.shipment_id == shipment_id,
        ChainLog.chain_id == settings().chain_id).order_by(ChainLog.block_number, ChainLog.log_index)).all()
    return [{"id": log.id, "kind": log.kind, "arguments": log.arguments, "block_number": log.block_number,
             "transaction_hash": log.transaction_hash, "canonical": log.canonical} for log in logs]


@router.post("/terms")
def prepare_terms(shipment_id: str, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        if actor.id != shipment.merchant_id:
            raise DomainError("FORBIDDEN", "Chỉ người gửi tạo khoản bảo đảm.", 403)
        merchant, buyer = parties(db, shipment)
        deployed = chain_rpc.deployment()
        if not deployed:
            raise DomainError("CHAIN_NOT_DEPLOYED", "Chưa triển khai hợp đồng thử nghiệm.", 409)
        if int(shipment.reserve_amount) <= 0:
            raise DomainError("RESERVE_REQUIRED", "Hồ sơ cần khoản bảo đảm lớn hơn 0.", 409)
        _, timestamp, _ = chain_rpc.network_block()
        timestamp = effective_chain_time(timestamp)
        terms = {"caseId": shipment.case_id, "merchant": merchant.wallet_address, "buyer": buyer.wallet_address,
            "token": deployed["contracts"]["DemoToken"], "reserveAmount": shipment.reserve_amount,
            "acceptBy": str(timestamp+86400), "reviewDeadline": str(timestamp+7*86400),
            "arbitrationSafe": deployed["contracts"]["ArbitrationSafe"]}
        terms["termsHash"] = "0x"+document_hash({"version": "reserve-terms-v1", "scope": "TEST_FUNDS_ONLY", **terms})
        return {"terms": terms, "factory": deployed["contracts"]["EscrowFactory"], "chain_id": settings().chain_id}
    return idempotent(db, actor, request, {}, execute)


@router.post("/intents")
def create_intent(shipment_id: str, body: IntentRequest, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        if actor.id not in {shipment.merchant_id, shipment.buyer_id}:
            raise DomainError("FORBIDDEN", "Chỉ hai bên được chỉ định mới tạo yêu cầu ký.", 403)
        row = chain_case(db, shipment)
        if row is None:
            raise DomainError("CHAIN_CASE_REQUIRED", "Chưa tạo khoản bảo đảm trên chain.", 409)
        current = row.snapshot
        deadline = int(current["terms"]["acceptBy" if body.kind == "TERMS" else "reviewDeadline"])
        effective_timestamp = effective_chain_time(current["chain_timestamp"])
        if current["state"] != "3" and effective_timestamp > deadline:
            raise DomainError("CHAIN_DEADLINE_PASSED", "Đã qua hạn ký của hồ sơ. Hãy dùng quy trình hủy hoặc đóng quá hạn.", 409)
        expiry = min(int(now().timestamp())+1800, effective_timestamp+1800)
        if current["state"] != "3":
            expiry = min(expiry, deadline)
        payload, version = {}, 0
        if body.kind == "TERMS":
            if current["state"] != "1" or current["dispatchHash"] == chain_rpc.ZERO32:
                raise DomainError("CHAIN_STATE_INVALID", "Cần nạp quỹ và ghi nhận ảnh gửi trước khi chấp nhận điều khoản.", 409)
            captures = db.scalars(select(CaptureSession).where(CaptureSession.shipment_id == shipment_id, CaptureSession.stage == "DISPATCH", CaptureSession.status == "SEALED")).all()
            if current["dispatchHash"][2:] not in {capture.manifest_hash for capture in captures}:
                raise DomainError("CHAIN_EVIDENCE_MISMATCH", "Cam kết ảnh gửi không khớp bộ ảnh đã khóa.", 409)
            payload = {**current["terms"], "dispatchHash": current["dispatchHash"], "expiresAt": expiry}
            fields = list(zip(chain_rpc.TERM_FIELDS, chain_rpc.TERM_TYPES, strict=True)) + [("dispatchHash", "bytes32"), ("expiresAt", "uint64")]
            primary = "TermsAcceptance"
        else:
            report = latest_report(db, shipment)
            version = report.version
            if current["state"] not in {"2", "3"} or current["receiptHash"] == chain_rpc.ZERO32:
                raise DomainError("CHAIN_STATE_INVALID", "Cần hồ sơ đang hoạt động và cam kết ảnh nhận.", 409)
            if (current["dispatchHash"][2:] not in report.document["dispatch_manifest_hashes"] or
                current["receiptHash"][2:] != report.document["receipt_manifest_hash"]):
                raise DomainError("CHAIN_EVIDENCE_MISMATCH", "Biên bản sử dụng bộ ảnh khác với cam kết trên chain.", 409)
            payload = {"reportHash": "0x"+report.canonical_hash, "evidenceRoot": current["evidenceRoot"], "expiresAt": expiry}
            if body.kind == "REPORT":
                payload.update(version=version, nonce=current["reportNonce"])
                fields = [("caseId", "bytes32"), ("reportHash", "bytes32"), ("evidenceRoot", "bytes32"), ("version", "uint256"), ("nonce", "uint256"), ("expiresAt", "uint64")]
                primary = "ReportApproval"
            else:
                if current["acceptedReportHash"] != payload["reportHash"] or int(current["acceptedReportVersion"]) != version:
                    raise DomainError("REPORT_NOT_ONCHAIN", "Hai bên cần ghi nhận phiên bản biên bản trên chain trước.", 409)
                amount = int(body.buyer_amount)
                if amount > int(shipment.reserve_amount):
                    raise DomainError("ALLOCATION_INVALID", "Khoản cho người nhận vượt quỹ bảo đảm.", 400)
                payload.update(reportVersion=version, merchantAmount=str(int(shipment.reserve_amount)-amount), buyerAmount=str(amount), nonce=current["settlementNonce"])
                fields = [("caseId", "bytes32"), ("reportHash", "bytes32"), ("evidenceRoot", "bytes32"), ("reportVersion", "uint256"), ("merchantAmount", "uint256"), ("buyerAmount", "uint256"), ("nonce", "uint256"), ("expiresAt", "uint64")]
                primary = "Settlement"
        typed = {"domain": {"name": "KienChungEscrow", "version": "1", "chainId": settings().chain_id, "verifyingContract": row.escrow_address},
            "primaryType": primary, "types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"}, {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
                primary: [{"name": name, "type": kind} for name, kind in fields]},
            "message": {"caseId": shipment.case_id, **payload}}
        digest = document_hash(typed)
        intent = db.scalar(select(ChainIntent).where(ChainIntent.payload_hash == digest))
        if not intent:
            intent = ChainIntent(shipment_id=shipment_id, kind=body.kind, payload_hash=digest, typed_data=typed,
                                 contract_payload=payload, report_version=version, expires_at=datetime.fromtimestamp(expiry, UTC))
            db.add(intent)
            db.flush()
            event(db, shipment_id, actor.id, "CHAIN_INTENT_CREATED", {"intent_id": intent.id, "kind": body.kind, "report_version": version})
        return intent_view(db, intent, shipment, current)
    return idempotent(db, actor, request, body.model_dump(), execute)


@router.post("/intents/{intent_id}/signatures")
def store_signature(shipment_id: str, intent_id: str, body: IntentSignature, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        if actor.id not in {shipment.merchant_id, shipment.buyer_id} or not actor.wallet_address:
            raise DomainError("FORBIDDEN", "Cần ví đã liên kết của đúng bên tham gia.", 403)
        intent = db.get(ChainIntent, intent_id)
        if not intent or intent.shipment_id != shipment_id:
            raise DomainError("NOT_FOUND", "Không tìm thấy yêu cầu ký.", 404)
        row = chain_case(db, shipment)
        if row is None or not intent_valid(intent, shipment, row.snapshot):
            raise DomainError("STALE_CHAIN_INTENT", "Yêu cầu ký đã hết hạn hoặc bằng chứng/biên bản đã thay đổi.", 409)
        if recover(intent.typed_data, body.signature) != actor.wallet_address:
            raise DomainError("SIGNATURE_INVALID", "Chữ ký không khớp ví liên kết.", 400)
        existing = db.scalar(select(ChainSignature).where(ChainSignature.intent_id == intent_id, ChainSignature.user_id == actor.id))
        if not existing:
            db.add(ChainSignature(intent_id=intent_id, user_id=actor.id, wallet_address=actor.wallet_address, signature=body.signature))
            db.flush()
            event(db, shipment_id, actor.id, "CHAIN_INTENT_SIGNED", {"intent_id": intent.id, "kind": intent.kind})
        return intent_view(db, intent, shipment, row.snapshot)
    return idempotent(db, actor, request, body.model_dump(), execute)


def assert_report_editable(db, shipment):
    """A released EIP-712 signature cannot be revoked by an off-chain DB edit."""
    if db.get(ChainCase, shipment.id) is None:
        return
    row = chain_case(db, shipment)
    if row is None:
        return
    signed = db.scalars(select(ChainIntent).join(ChainSignature).where(
        ChainIntent.shipment_id == shipment.id, ChainIntent.kind.in_(["REPORT", "SETTLEMENT"]),
        ChainIntent.expires_at > now())).all()
    if (row.snapshot["state"] in {"4", "5"} or row.snapshot["acceptedReportHash"] != chain_rpc.ZERO32 or
        any(intent_valid(intent, shipment, row.snapshot) for intent in signed)):
        raise DomainError("ONCHAIN_REVISION_REQUIRED", "Cần vô hiệu cam kết/chữ ký đang có trên chain trước khi sửa biên bản.", 409)
