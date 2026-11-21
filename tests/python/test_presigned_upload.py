import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from conftest import post

from services.api import main
from services.api.storage import S3Storage
from test_workflow import create_case


def test_presigned_policy_limits_size_type_key_and_time():
    backend = S3Storage()
    asset = SimpleNamespace(id="asset-id", sha256="a"*64, mime_type="image/png", byte_size=1234)
    ticket = backend.upload_ticket(asset)
    policy = json.loads(base64.b64decode(ticket["fields"]["policy"]))
    assert ticket["expires_in_seconds"] == 300
    assert ["content-length-range", 1234, 1234] in policy["conditions"]
    assert {"Content-Type": "image/png"} in policy["conditions"]
    assert {"key": "staging/asset-id/"+"a"*64} in policy["conditions"]


def test_presigned_completion_checks_original_and_ignores_later_staging_replays(clients, monkeypatch):
    merchant, buyer, outsider = clients
    shipment = create_case(merchant, buyer)
    sid = shipment["id"]
    cap = post(merchant, f"/v1/shipments/{sid}/captures", {"stage": "DISPATCH"}).json()
    raw = Path("tests/fixtures/images/dispatch.png").read_bytes()
    intent = post(merchant, f"/v1/shipments/{sid}/uploads", {"capture_session_id": cap["id"], "filename": "box.png",
        "mime_type": "image/png", "byte_size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}).json()
    aid = intent["asset_id"]
    assert post(outsider, f"/v1/assets/{aid}/upload-ticket").status_code == 404
    assert post(buyer, f"/v1/assets/{aid}/upload-ticket").status_code == 404
    assert post(merchant, f"/v1/assets/{aid}/upload-ticket").json()["transport"] == "API_PUT"
    assert post(merchant, f"/v1/assets/{aid}/complete").status_code == 409
    cfg = main.settings()
    monkeypatch.setattr(cfg, "presigned_uploads_enabled", True)
    monkeypatch.setattr(cfg, "storage_backend", "s3")
    storage = main.storage()
    monkeypatch.setattr(storage, "staged_content", lambda asset: b"tampered", raising=False)
    assert post(merchant, f"/v1/assets/{aid}/complete").status_code == 422
    monkeypatch.setattr(storage, "staged_content", lambda asset: raw, raising=False)
    assert post(merchant, f"/v1/assets/{aid}/complete").status_code == 200
    assert post(merchant, f"/v1/shipments/{sid}/captures/{cap['id']}/seal", {"expected_version": 1}).status_code == 200
    monkeypatch.setattr(storage, "staged_content", lambda asset: b"replayed", raising=False)
    assert post(merchant, f"/v1/assets/{aid}/complete").status_code == 200
    assert merchant.get(f"/v1/assets/{aid}/content?variant=original").content == raw
    assert post(merchant, f"/v1/assets/{aid}/upload-ticket").status_code == 409
