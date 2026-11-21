"""A party can relay signatures; only current Safe owners authorize a decision."""
import secrets

from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from services.api import chain_rpc
from services.api.canonical import document_hash
from services.api.chain import IntentSignature, chain_case, latest_report
from services.api.config import settings
from services.api.database import ArbitrationProposal, ArbitrationSignature, now
from services.api.errors import DomainError
from services.api.schemas import Amount, Schema
from services.api.security import Actor, Db, shipment_access
from services.api.transactions import event, idempotent
from services.api.wallet import recover

router = APIRouter(prefix="/v1/shipments/{shipment_id}/arbitration", tags=["arbitration"])
RESOLUTION_FIELDS = ["evidenceRoot", "decisionHash", "merchantAmount", "buyerAmount", "nonce", "expiresAt"]


class ProposalRequest(Schema):
    buyer_amount: Amount
    decision_text: str = Field(min_length=20, max_length=5000)


def safe_state(current):
    address = current["terms"]["arbitrationSafe"]
    block = hex(current["block_number"])
    owners = [owner.lower() for owner in chain_rpc.call(address, "getOwners()", ["address[]"], block=block)]
    threshold = int(chain_rpc.call(address, "getThreshold()", ["uint256"], block=block))
    if threshold < 2 or threshold > len(owners):
        raise DomainError("SAFE_QUORUM_INVALID", "Cấu hình Safe không đạt ngưỡng nhiều người xác nhận.", 409)
    return {"address": address, "owners": owners, "threshold": threshold}


def valid(proposal, current):
    payload = proposal.resolution
    return (current["state"] == "3" and current["evidenceRoot"] == payload["evidenceRoot"]
            and current["disputeNonce"] == str(payload["nonce"])
            and current["chain_timestamp"] <= int(payload["expiresAt"])
            and now().timestamp() <= int(payload["expiresAt"])
            and proposal.typed_data["domain"]["chainId"] == settings().chain_id
            and proposal.typed_data["domain"]["verifyingContract"].lower() == current["terms"]["arbitrationSafe"].lower())


def view(db, proposal, current, safe):
    rows = db.scalars(select(ArbitrationSignature).where(ArbitrationSignature.proposal_id == proposal.id)).all()
    signatures = sorted([{"wallet_address": row.wallet_address, "signature": row.signature}
                         for row in rows if row.wallet_address in safe["owners"]], key=lambda row: row["wallet_address"])
    is_valid = valid(proposal, current)
    return {"id": proposal.id, "decision": proposal.decision, "resolution": proposal.resolution,
            "typed_data": proposal.typed_data, "valid": is_valid, "signatures": signatures,
            "ready": is_valid and len(signatures) >= safe["threshold"],
            "safe_signature": "0x"+"".join(row["signature"][2:] for row in signatures[:safe["threshold"]])}


@router.get("")
def list_proposals(shipment_id: str, db: Db, actor: Actor):
    shipment = shipment_access(db, shipment_id, actor)
    case = chain_case(db, shipment)
    if case is None:
        return {"safe": None, "proposals": []}
    safe = safe_state(case.snapshot)
    rows = db.scalars(select(ArbitrationProposal).where(ArbitrationProposal.shipment_id == shipment_id)
                      .order_by(ArbitrationProposal.created_at.desc()).limit(30)).all()
    result = {"safe": safe, "proposals": [view(db, row, case.snapshot, safe) for row in rows]}
    db.commit()
    return result


@router.post("")
def propose(shipment_id: str, body: ProposalRequest, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        case = chain_case(db, shipment)
        if case is None or case.snapshot["state"] != "3":
            raise DomainError("NOT_DISPUTED", "Hồ sơ cần ở trạng thái tranh chấp.", 409)
        current, report = case.snapshot, latest_report(db, shipment)
        safe = safe_state(current)
        amount = int(body.buyer_amount)
        if amount > int(shipment.reserve_amount) or len(body.decision_text.strip()) < 20:
            raise DomainError("DECISION_INVALID", "Kiểm tra số tiền và ghi rõ căn cứ quyết định của người phân xử.", 400)
        decision = {"schema_version": "arbitration-decision-v1", "case_id": shipment.case_id,
            "chain_id": settings().chain_id, "escrow": case.escrow_address,
            "evidence_root": current["evidenceRoot"], "report_hash": report.canonical_hash,
            "text": body.decision_text.strip(), "author_type": "HUMAN", "nonce_salt": secrets.token_hex(32),
            "merchant_amount": str(int(shipment.reserve_amount)-amount), "buyer_amount": str(amount)}
        payload = {"evidenceRoot": current["evidenceRoot"], "decisionHash": "0x"+document_hash(decision),
            "merchantAmount": decision["merchant_amount"], "buyerAmount": decision["buyer_amount"],
            "nonce": current["disputeNonce"], "expiresAt": min(int(now().timestamp()), current["chain_timestamp"])+1800}
        arguments = tuple(bytes.fromhex(payload[key][2:]) if key.endswith("Hash") or key == "evidenceRoot" else int(payload[key]) for key in RESOLUTION_FIELDS)
        digest = chain_rpc.call(case.escrow_address, "resolutionDigest((bytes32,bytes32,uint256,uint256,uint256,uint64))",
            ["bytes32"], ["(bytes32,bytes32,uint256,uint256,uint256,uint64)"], [arguments], block=hex(current["block_number"]))
        typed = {"domain": {"chainId": settings().chain_id, "verifyingContract": safe["address"]},
            "primaryType": "SafeMessage", "types": {
                "EIP712Domain": [{"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
                "SafeMessage": [{"name": "message", "type": "bytes"}]}, "message": {"message": "0x"+digest.hex()}}
        proposal = ArbitrationProposal(shipment_id=shipment_id, decision=decision, resolution=payload, typed_data=typed, created_by=actor.id)
        db.add(proposal)
        db.flush()
        event(db, shipment_id, actor.id, "ARBITRATION_PROPOSED", {"proposal_id": proposal.id, "decision_hash": payload["decisionHash"]})
        return view(db, proposal, current, safe)
    return idempotent(db, actor, request, body.model_dump(), execute)


@router.post("/{proposal_id}/signatures")
def add_signature(shipment_id: str, proposal_id: str, body: IntentSignature, request: Request, db: Db, actor: Actor):
    def execute():
        shipment = shipment_access(db, shipment_id, actor, lock=True)
        proposal = db.get(ArbitrationProposal, proposal_id)
        if proposal is None or proposal.shipment_id != shipment_id:
            raise DomainError("NOT_FOUND", "Không tìm thấy quyết định trong hồ sơ này.", 404)
        case = chain_case(db, shipment)
        if case is None or not valid(proposal, case.snapshot):
            raise DomainError("STALE_ARBITRATION", "Bằng chứng, trạng thái hoặc thời hạn quyết định đã thay đổi.", 409)
        safe = safe_state(case.snapshot)
        owner = recover(proposal.typed_data, body.signature)
        if owner not in safe["owners"]:
            raise DomainError("NOT_SAFE_OWNER", "Chữ ký không thuộc thành viên Safe được chỉ định.", 403)
        if not db.scalar(select(ArbitrationSignature).where(ArbitrationSignature.proposal_id == proposal_id, ArbitrationSignature.wallet_address == owner)):
            db.add(ArbitrationSignature(proposal_id=proposal_id, wallet_address=owner, signature=body.signature, submitted_by=actor.id))
            db.flush()
            event(db, shipment_id, actor.id, "ARBITRATION_SIGNED", {"proposal_id": proposal_id, "wallet": owner})
        return view(db, proposal, case.snapshot, safe)
    return idempotent(db, actor, request, body.model_dump(), execute)
