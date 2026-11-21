"""Optional constrained Vietnamese wording with strict fact/evidence preservation.

The provider selects among reviewed formulations. It cannot introduce new visual
claims, change states/asset IDs or choose money amounts. Invalid/failed responses
fall back to the original deterministic report without breaking the workflow.
"""
import copy
import json

import httpx
from pydantic import BaseModel, ConfigDict, Field

from services.api.canonical import document_hash
from services.api.config import settings

VERSION = "evidence-bound-vietnamese-v1"
ALTERNATIVES = {
    "VISIBLE_CHANGE": "Có khác biệt hình ảnh tại vùng đánh dấu sau căn chỉnh. Hai bên cần đối chiếu ảnh gốc để xác nhận phần nhìn thấy; kết quả không xác định nguyên nhân hoặc trách nhiệm.",
    "NO_VISIBLE_CHANGE": "Trong phần ảnh có thể đối chiếu, bộ xử lý chưa thấy khác biệt vượt ngưỡng. Thay đổi nhỏ, phần bị che hoặc tình trạng bên trong kiện vẫn chưa được đánh giá.",
    "INCONCLUSIVE": "Ảnh hiện có chưa đủ để đối chiếu đáng tin cậy vùng đang kiểm tra. Chưa thể kết luận có thay đổi hay không; cần bổ sung ảnh cùng vùng, rõ nét và đủ sáng.",
}


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    state: str
    evidence_asset_ids: list[str]
    text: str = Field(max_length=3000)


class Wording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Item] = Field(max_length=50)


def config_identity():
    cfg = settings()
    enabled = bool(cfg.llm_base_url and cfg.llm_model)
    return document_hash({"version": VERSION, "enabled": enabled, "model": cfg.llm_model if enabled else None,
                          "endpoint_sha256": document_hash(cfg.llm_base_url) if enabled else None,
                          "alternatives": ALTERNATIVES})


def validate_wording(observations, reply):
    parsed = Wording.model_validate(reply)
    by_id = {o["id"]: o for o in observations}
    if len(parsed.items) != len(by_id) or {o.observation_id for o in parsed.items} != set(by_id):
        raise ValueError("REPORT_REFERENCE_INVALID")
    output = copy.deepcopy(observations)
    proposed = {o.observation_id: o for o in parsed.items}
    for original in output:
        item = proposed[original["id"]]
        if item.state != original["state"] or item.evidence_asset_ids != original["evidence_asset_ids"]:
            raise ValueError("REPORT_FACT_CHANGED")
        if item.text not in {original["text"], ALTERNATIVES[original["state"]]}:
            raise ValueError("REPORT_UNSUPPORTED_CLAIM")
        original["text"] = item.text
    return output


def request_wording(observations):
    cfg = settings()
    facts = [{"observation_id": o["id"], "state": o["state"], "evidence_asset_ids": o["evidence_asset_ids"],
              "allowed_texts": [o["text"], ALTERNATIVES[o["state"]]]} for o in observations]
    schema = Wording.model_json_schema()
    # JSON schema restricts structure; local validation additionally checks the
    # exact evidence/state and approved text for EACH observation.
    body = {"model": cfg.llm_model, "temperature": 0, "max_tokens": 2500,
            "messages": [{"role": "system", "content": "Select one allowed Vietnamese text per observation. Preserve all IDs, order of evidence_asset_ids and states exactly. Return only JSON matching the schema; never invent facts or money amounts."},
                         {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "evidence_report", "strict": True, "schema": schema}}}
    headers = {"Authorization": f"Bearer {cfg.llm_api_key}"} if cfg.llm_api_key else {}
    with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=False) as client:
        with client.stream("POST", cfg.llm_base_url.rstrip("/")+"/chat/completions", json=body, headers=headers) as response:
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 100_000:
                    raise ValueError("REPORT_REPLY_TOO_LARGE")
    return json.loads(json.loads(raw)["choices"][0]["message"]["content"])


def compose(observations, expected_config=None):
    identity = config_identity()
    metadata = {"version": VERSION, "config_sha256": identity, "input_sha256": document_hash(observations),
                "backend": "TEMPLATE", "status": "PROVIDER_NOT_CONFIGURED", "facts_preserved": True}
    output = copy.deepcopy(observations)
    if expected_config and expected_config != identity:
        metadata["status"] = "CONFIG_CHANGED_TEMPLATE_FALLBACK"
    elif settings().llm_base_url and settings().llm_model:
        try:
            output = validate_wording(observations, request_wording(observations))
            metadata.update(backend="LLM_CONSTRAINED_WORDING", status="VALIDATED", model=settings().llm_model)
        except Exception:
            metadata["status"] = "PROVIDER_OR_VALIDATION_FAILED_TEMPLATE_FALLBACK"
    metadata["output_sha256"] = document_hash(output)
    return output, metadata
