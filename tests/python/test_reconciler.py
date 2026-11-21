import pytest
from conftest import post
from eth_abi import encode
from sqlalchemy import select

from services.api import chain_rpc
from services.api.chain_events import TOPICS, decode_log
from services.api.config import settings
from services.api.database import ChainCase, ChainLog, SessionLocal
from services.worker.reconciler import reconcile_case

ADDRESS = "0x"+"12"*20


def test_chain_index_replay_failure_and_reorg(clients, monkeypatch):
    merchant, _, _ = clients
    shipment = post(merchant, "/v1/shipments", {"reference": "INDEX", "title": "Hộp", "recipient_name": "An"}).json()
    with SessionLocal() as db, db.begin():
        db.add(ChainCase(shipment_id=shipment["id"], chain_id=settings().chain_id, escrow_address=ADDRESS, terms={}, snapshot={}))
    state = {"branch": "a", "fail": True}

    def rpc(method, params):
        if method == "eth_getBlockByNumber":
            return {"hash": "0x"+state["branch"]*64}
        if method == "eth_getLogs":
            return [{"address": ADDRESS, "blockNumber": "0x5", "blockHash": "0x"+state["branch"]*64,
                "transactionHash": "0x"+state["branch"]*64, "logIndex": "0x0",
                "topics": [next(topic for topic,name in TOPICS.items() if name == "Funded"), "0x"+encode(["address"],[ADDRESS]).hex()],
                "data": "0x"+encode(["uint256"],[10000000]).hex()}]
        raise AssertionError(method)

    def snapshot(address):
        if state["fail"]:
            raise ValueError("simulate interrupted RPC after log writes")
        return {"accounting_consistent": True, "terms":{"caseId":shipment["case_id"]}}

    monkeypatch.setattr(chain_rpc, "deployment", lambda: {"start_block": "0"})
    monkeypatch.setattr(chain_rpc, "network_block", lambda: ("0x5", 1, "0x"+state["branch"]*64))
    monkeypatch.setattr(chain_rpc, "rpc", rpc)
    monkeypatch.setattr(chain_rpc, "snapshot", snapshot)
    with pytest.raises(ValueError):
        reconcile_case(shipment["id"])
    with SessionLocal() as db:
        assert db.get(ChainCase, shipment["id"]).indexed_block is None
        assert not db.scalars(select(ChainLog)).all()
    state["fail"] = False
    assert reconcile_case(shipment["id"])
    assert reconcile_case(shipment["id"])
    with SessionLocal() as db:
        logs = db.scalars(select(ChainLog)).all()
        assert len(logs) == 1 and logs[0].arguments["amount"] == "10000000"
    state["branch"] = "b"
    assert reconcile_case(shipment["id"])
    with SessionLocal() as db:
        logs = db.scalars(select(ChainLog)).all()
        assert len(logs) == 2
        assert sum(log.canonical for log in logs) == 1
        assert db.get(ChainCase, shipment["id"]).indexed_block_hash == "0x"+"b"*64
    # A fresh local network may reuse an address. Never attach another case's
    # balance/logs to the old application's shipment, even with a valid ABI.
    state["branch"] = "c"
    monkeypatch.setattr(chain_rpc,"snapshot",lambda address:{"accounting_consistent":True,"terms":{"caseId":"0x"+"99"*32}})
    with pytest.raises(ValueError,match="CHAIN_CASE_IDENTITY_MISMATCH"):
        reconcile_case(shipment["id"])
    with SessionLocal() as db:
        assert db.get(ChainCase, shipment["id"]).indexed_block_hash == "0x"+"b"*64
        assert len(db.scalars(select(ChainLog)).all()) == 2


def test_unknown_chain_topic_is_not_invented():
    assert decode_log({"topics": ["0x"+"00"*32], "data": "0x"}) is None
