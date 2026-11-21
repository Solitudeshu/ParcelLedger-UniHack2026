import copy

import pytest

from services.worker import report_composer as composer


def observations():
    return [{"id":"obs-1","state":"INCONCLUSIVE","evidence_asset_ids":["image-q"],"text":"Chưa đủ bằng chứng.",
             "query_polygon":None,"author_type":"MODEL","review_status":"PENDING"}]


def valid_reply():
    return {"items":[{"observation_id":"obs-1","state":"INCONCLUSIVE","evidence_asset_ids":["image-q"],
                       "text":composer.ALTERNATIVES["INCONCLUSIVE"]}]}


@pytest.mark.parametrize("field,value", [("state","VISIBLE_CHANGE"),("evidence_asset_ids",["outside-case"]),
    ("text","Người nhận gây hỏng và phải bồi thường 500.000 đồng."),("observation_id","other-obs")])
def test_provider_cannot_change_facts_references_or_add_compensation(field,value):
    reply=valid_reply();reply["items"][0][field]=value
    with pytest.raises(ValueError):composer.validate_wording(observations(),reply)


def test_template_fallback_is_exact_and_provider_validation_preserves_metadata(monkeypatch):
    source=observations();before=copy.deepcopy(source)
    cfg=composer.settings()
    monkeypatch.setattr(cfg,"llm_base_url","https://example.invalid/v1")
    monkeypatch.setattr(cfg,"llm_model","configured-test-model")
    monkeypatch.setattr(composer,"request_wording",lambda _:{"items":[]})
    fallback,meta=composer.compose(source)
    assert fallback==before and meta["backend"]=="TEMPLATE"
    monkeypatch.setattr(composer,"request_wording",lambda _:valid_reply())
    valid,meta=composer.compose(source)
    assert valid[0]["text"]==composer.ALTERNATIVES["INCONCLUSIVE"]
    assert valid[0]["review_status"]=="PENDING" and source==before
    assert meta["backend"]=="LLM_CONSTRAINED_WORDING" and meta["facts_preserved"]


def test_config_change_does_not_send_old_job_to_a_new_provider(monkeypatch):
    cfg=composer.settings()
    monkeypatch.setattr(cfg,"llm_base_url","https://example.invalid/v1")
    monkeypatch.setattr(cfg,"llm_model","configured-test-model")
    def unexpected(_):raise AssertionError("Must not call a changed provider")
    monkeypatch.setattr(composer,"request_wording",unexpected)
    result,meta=composer.compose(observations(),"0"*64)
    assert result==observations() and meta["status"]=="CONFIG_CHANGED_TEMPLATE_FALLBACK"
