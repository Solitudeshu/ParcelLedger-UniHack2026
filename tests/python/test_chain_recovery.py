import pytest
from sqlalchemy import select

from conftest import post
from services.api import chain_rpc
from services.api.chain import chain_case
from services.api.config import settings
from services.api.database import ChainCase, SessionLocal, Shipment
from services.api.errors import DomainError


@pytest.mark.parametrize("raw",["0x","0x12","0xzz",None,{}])
def test_empty_or_malformed_contract_return_is_recoverable_domain_error(monkeypatch,raw):
    monkeypatch.setattr(chain_rpc,"rpc",lambda *args:raw)
    with pytest.raises(DomainError) as failure:
        chain_rpc.call("0x"+"11"*20,"state()",["uint256"])
    assert failure.value.code == "CHAIN_DATA_INVALID"


def test_recorded_case_cannot_be_silently_recreated_when_chain_is_reset(clients,monkeypatch):
    merchant,_,_ = clients
    shipment = post(merchant,"/v1/shipments",{"reference":"RESET-TEST","title":"Hộp","recipient_name":"An"}).json()
    address = "0x"+"12"*20
    with SessionLocal() as db,db.begin():
        db.add(ChainCase(shipment_id=shipment["id"],chain_id=settings().chain_id,escrow_address=address,terms={},snapshot={"kept":True}))
    monkeypatch.setattr(chain_rpc,"deployment",lambda:{"contracts":{"EscrowFactory":address}})
    monkeypatch.setattr(chain_rpc,"network_block",lambda:("0x1",1,"0x"+"00"*32))
    for response in (chain_rpc.ZERO_ADDRESS,"0x"+"13"*20):
        monkeypatch.setattr(chain_rpc,"call",lambda *args,**kwargs:response)
        with SessionLocal() as db:
            with pytest.raises(DomainError) as failure:
                chain_case(db,db.get(Shipment,shipment["id"]))
            assert failure.value.code == "CHAIN_HISTORY_UNAVAILABLE"
            assert db.get(ChainCase,shipment["id"]).snapshot == {"kept":True}
