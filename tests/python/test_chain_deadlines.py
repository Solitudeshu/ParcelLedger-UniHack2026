from datetime import timedelta
from types import SimpleNamespace

from services.api.chain import intent_valid
from services.api.database import now


def test_local_idle_block_does_not_create_expired_new_terms(clients, monkeypatch):
    from conftest import post
    from services.api import chain, chain_rpc
    from services.api.config import settings
    from test_workflow import create_case
    merchant, buyer, _ = clients
    shipment = create_case(merchant,buyer)
    wall = now()
    epoch = int(wall.timestamp())
    monkeypatch.setattr(chain,'now',lambda:wall)
    monkeypatch.setattr(settings(),'chain_id',31337)
    monkeypatch.setattr(chain_rpc,'network_block',lambda:('0x1',epoch-3*86400,'0x'+'00'*32))
    contracts = {name:'0x'+'11'*20 for name in ('DemoToken','ArbitrationSafe','EscrowFactory')}
    monkeypatch.setattr(chain_rpc,'deployment',lambda:{'contracts':contracts})
    monkeypatch.setattr(chain,'parties',lambda *args:(SimpleNamespace(wallet_address='0x'+'22'*20),SimpleNamespace(wallet_address='0x'+'33'*20)))
    response = post(merchant,f"/v1/shipments/{shipment['id']}/chain/terms")
    assert response.status_code == 200, response.text
    assert int(response.json()['terms']['acceptBy']) == epoch+86400
    assert int(response.json()['terms']['reviewDeadline']) == epoch+7*86400
    # Local time travel is preserved; this helper never moves a clock backward.
    assert chain.effective_chain_time(epoch+100) == epoch+100
    monkeypatch.setattr(settings(),'chain_id',43113)
    assert chain.effective_chain_time(epoch-3*86400) == epoch-3*86400


def test_idle_local_existing_deadlines_are_not_extended(clients,monkeypatch):
    from conftest import post
    from services.api import chain
    from services.api.config import settings
    from test_workflow import create_case
    merchant, buyer, _ = clients
    shipment = create_case(merchant,buyer)
    epoch = int(now().timestamp())
    monkeypatch.setattr(settings(),'chain_id',31337)
    current = {'state':'1','chain_timestamp':epoch-7200,
               'terms':{'acceptBy':str(epoch-3600),'reviewDeadline':str(epoch+86400)}}
    monkeypatch.setattr(chain,'chain_case',lambda *args:SimpleNamespace(snapshot=current))
    response = post(merchant,f"/v1/shipments/{shipment['id']}/chain/intents",{'kind':'TERMS'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'CHAIN_DEADLINE_PASSED'


def test_chain_deadlines_invalidate_signatures_except_open_dispute():
    current = {"state": "1", "chain_timestamp": 101, "terms": {"acceptBy": "100", "reviewDeadline": "200"},
        "dispatchHash": "dispatch", "reportNonce": "2", "settlementNonce": "3", "evidenceRoot": "root", "acceptedReportHash": "report"}
    shipment = SimpleNamespace(report_version=1)
    intent = SimpleNamespace(kind="TERMS", expires_at=now()+timedelta(minutes=10), typed_data={"message": {"dispatchHash": "dispatch"}})
    assert not intent_valid(intent, shipment, current)
    current["chain_timestamp"] = 100
    assert intent_valid(intent, shipment, current)
    intent.kind, intent.report_version = "REPORT", 1
    intent.typed_data["message"] = {"evidenceRoot": "root", "nonce": 2, "reportHash": "report"}
    current.update(state="2", chain_timestamp=201)
    assert not intent_valid(intent, shipment, current)
    current["state"] = "3"
    assert intent_valid(intent, shipment, current)
