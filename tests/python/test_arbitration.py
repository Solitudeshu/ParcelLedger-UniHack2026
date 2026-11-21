from types import SimpleNamespace

from conftest import post
from eth_account import Account
from eth_account.messages import encode_typed_data
from test_workflow import ready_case

from services.api import arbitration
from services.api.canonical import document_hash
from services.api.database import now
from services.worker.runner import run_once


def test_safe_owner_quorum_replay_stale_and_tenant_access(clients, monkeypatch):
    merchant, buyer, outsider = clients
    sid, _, query = ready_case(clients)
    post(buyer, f"/v1/shipments/{sid}/analyses", {"query_asset_id": query})
    assert run_once()
    owners = [Account.create() for _ in range(3)]
    safe = "0x"+"ab"*20
    current = {"state": "3", "chain_timestamp": int(now().timestamp()), "block_number": 1,
        "evidenceRoot": "0x"+"cd"*32, "disputeNonce": "2", "terms": {"arbitrationSafe": safe}}
    monkeypatch.setattr(arbitration, "chain_case", lambda *_: SimpleNamespace(snapshot=current, escrow_address="0x"+"ef"*20))

    def call(address, signature, *args, **kwargs):
        if signature == "getOwners()":
            return [owner.address for owner in owners]
        if signature == "getThreshold()":
            return 2
        return b"d"*32

    monkeypatch.setattr(arbitration.chain_rpc, "call", call)
    route = f"/v1/shipments/{sid}/arbitration"
    body = {"buyer_amount": "4000000", "decision_text": "Hai bên đã xem ảnh gốc và thống nhất cách xử lý thử nghiệm."}
    assert post(outsider, route, body).status_code == 404
    assert post(merchant, route, {**body, "buyer_amount": "10000001"}).status_code == 400
    response = post(merchant, route, body)
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["resolution"]["decisionHash"] == "0x"+document_hash(proposal["decision"])
    signature_route = route+f"/{proposal['id']}/signatures"

    def sign(owner):
        return "0x"+owner.sign_message(encode_typed_data(full_message=proposal["typed_data"])).signature.hex()

    assert post(merchant, signature_route, {"signature": sign(Account.create())}).status_code == 403
    for _ in range(2):
        first = post(merchant, signature_route, {"signature": sign(owners[0])}).json()
        assert len(first["signatures"]) == 1 and not first["ready"]
    second = post(buyer, signature_route, {"signature": sign(owners[1])}).json()
    assert second["ready"] and len(second["signatures"]) == 2
    addresses = [row["wallet_address"] for row in second["signatures"]]
    assert addresses == sorted(addresses)
    assert len(second["safe_signature"]) == 2+130*2
    current["evidenceRoot"] = "0x"+"99"*32
    assert post(merchant, signature_route, {"signature": sign(owners[2])}).status_code == 409
    assert merchant.get(route).json()["proposals"][0]["valid"] is False
