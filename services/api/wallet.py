import secrets
from datetime import timedelta

from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select, text

from services.api.config import settings
from services.api.database import User, WalletChallenge, now
from services.api.errors import DomainError
from services.api.schemas import Schema
from services.api.security import Actor, Db
from services.api.transactions import idempotent

router = APIRouter(prefix="/v1/wallet", tags=["wallet"])


class WalletAddress(Schema):
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")


class WalletProof(Schema):
    challenge_id: str
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


def recover(typed_data, signature):
    try:
        return Account.recover_message(encode_typed_data(full_message=typed_data), signature=signature).lower()
    except (ValueError, TypeError) as exc:
        raise DomainError("SIGNATURE_INVALID", "Chữ ký ví không hợp lệ.", 400) from exc


@router.get("")
def wallet(actor: Actor):
    return {"address": actor.wallet_address, "chain_id": settings().chain_id}


@router.post("/challenge")
def challenge(body: WalletAddress, request: Request, db: Db, actor: Actor):
    def execute():
        address = body.address.lower()
        if actor.wallet_address and actor.wallet_address != address:
            raise DomainError("WALLET_ALREADY_BOUND", "Tài khoản đã liên kết với ví khác.", 409)
        expires = now() + timedelta(minutes=5)
        typed_data = {"domain": {"name": "KienChungAccount", "version": "1", "chainId": settings().chain_id},
            "primaryType": "WalletBinding", "types": {
                "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"}, {"name": "chainId", "type": "uint256"}],
                "WalletBinding": [{"name": "userId", "type": "string"}, {"name": "wallet", "type": "address"},
                    {"name": "nonce", "type": "bytes32"}, {"name": "expiresAt", "type": "uint64"}, {"name": "origin", "type": "string"}]},
            "message": {"userId": actor.id, "wallet": address, "nonce": "0x"+secrets.token_hex(32),
                        "expiresAt": int(expires.timestamp()), "origin": settings().web_origin}}
        item = WalletChallenge(user_id=actor.id, session_id=request.state.auth_session.id,
                               wallet_address=address, typed_data=typed_data, expires_at=expires)
        db.add(item)
        db.flush()
        return {"id": item.id, "typed_data": typed_data, "expires_at": expires.isoformat()}
    return idempotent(db, actor, request, body.model_dump(), execute)


@router.post("/verify")
def verify(body: WalletProof, request: Request, db: Db, actor: Actor):
    def execute():
        item = db.scalar(select(WalletChallenge).where(WalletChallenge.id == body.challenge_id).with_for_update())
        if not item or item.user_id != actor.id or item.session_id != request.state.auth_session.id:
            raise DomainError("CHALLENGE_INVALID", "Yêu cầu liên kết không thuộc phiên này.", 403)
        if item.used_at or item.expires_at < now():
            raise DomainError("CHALLENGE_EXPIRED", "Yêu cầu liên kết đã hết hạn hoặc đã dùng.", 409)
        recovered = recover(item.typed_data, body.signature)
        if recovered != item.wallet_address:
            raise DomainError("SIGNATURE_INVALID", "Chữ ký không khớp ví đã chọn.", 400)
        # Serialize concurrent claims of the same wallet across different users.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:wallet, 0))"), {"wallet": recovered})
        other = db.scalar(select(User).where(User.wallet_address == recovered, User.id != actor.id))
        if other or (actor.wallet_address and actor.wallet_address != recovered):
            raise DomainError("WALLET_ALREADY_BOUND", "Ví đã được liên kết với tài khoản.", 409)
        actor.wallet_address, item.used_at = recovered, now()
        return {"address": recovered, "chain_id": settings().chain_id}
    return idempotent(db, actor, request, body.model_dump(), execute)
