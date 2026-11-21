import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from conftest import post
from sqlalchemy import select

from services.api.canonical import document_hash
from services.api.database import Job, Report, SessionLocal, Shipment, now
from services.worker.runner import claim_job, finish, run_once

ROOT = Path("tests/fixtures/images")


def create_case(merchant, buyer):
    response = post(
        merchant,
        "/v1/shipments",
        {
            "reference": "KC-TEST-01",
            "title": "Đèn gốm",
            "recipient_name": "Minh",
            "reserve_amount": "10000000",
        },
    )
    assert response.status_code == 201, response.text
    shipment = response.json()
    invitation = post(merchant, f"/v1/shipments/{shipment['id']}/invitations").json()
    claimed = post(buyer, "/v1/invitations/claim", {"token": invitation["token"]})
    assert claimed.status_code == 200, claimed.text
    return shipment


def upload(client, sid, stage, filename):
    capture = post(client, f"/v1/shipments/{sid}/captures", {"stage": stage}).json()
    raw = (ROOT / filename).read_bytes()
    intent = post(
        client,
        f"/v1/shipments/{sid}/uploads",
        {
            "capture_session_id": capture["id"],
            "filename": filename,
            "mime_type": "image/png",
            "byte_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert intent.status_code == 201, intent.text
    aid = intent.json()["asset_id"]
    result = client.put(
        f"/v1/assets/{aid}/content",
        content=raw,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert result.status_code == 200, result.text
    complete = post(client, f"/v1/assets/{aid}/complete")
    assert complete.status_code == 200, complete.text
    return capture, aid


def seal(client, sid, capture):
    result = post(
        client, f"/v1/shipments/{sid}/captures/{capture['id']}/seal", {"expected_version": 1}
    )
    assert result.status_code == 200, result.text
    assert result.json()["manifest_hash"] == document_hash(result.json()["manifest"])
    return result.json()


def ready_case(clients):
    merchant, buyer, _ = clients
    shipment = create_case(merchant, buyer)
    sid = shipment["id"]
    dispatch, ref_id = upload(merchant, sid, "DISPATCH", "dispatch.png")
    seal(merchant, sid, dispatch)
    receipt, query_id = upload(buyer, sid, "RECEIPT", "changed.png")
    seal(buyer, sid, receipt)
    return sid, ref_id, query_id


def test_analysis_backpressure_preserves_existing_job_and_retry(clients, monkeypatch):
    from services.api.config import settings
    merchant, buyer, _ = clients
    sid, ref_id, query_id = ready_case(clients)
    monkeypatch.setattr(settings(), "max_active_jobs_per_organization", 1)
    first = post(buyer, f"/v1/shipments/{sid}/analyses", {"query_asset_id": query_id})
    assert first.status_code == 202
    repeated = post(buyer, f"/v1/shipments/{sid}/analyses", {"query_asset_id": query_id})
    assert repeated.status_code == 202 and repeated.json()["id"] == first.json()["id"]
    different = {"query_asset_id": query_id, "query_region": [[100,100],[9900,100],[9900,9900],[100,9900]]}
    blocked = post(buyer, f"/v1/shipments/{sid}/analyses", different)
    assert blocked.status_code == 429 and blocked.json()["error"]["retryable"] is True
    assert run_once()
    assert post(buyer, f"/v1/shipments/{sid}/analyses", different).status_code == 202


def test_full_workflow_report_revision_and_stale_confirmation(clients):
    merchant, buyer, outsider = clients
    sid, ref_id, query_id = ready_case(clients)
    response = post(buyer, f"/v1/shipments/{sid}/analyses", {"query_asset_id": query_id})
    assert response.status_code == 202, response.text
    assert run_once()
    job = buyer.get(f"/v1/jobs/{response.json()['id']}").json()
    assert job["status"] == "SUCCEEDED", job
    report = buyer.get(f"/v1/shipments/{sid}/reports").json()[0]
    assert report["document"]["match"]["reference_asset_id"] == ref_id
    assert report["canonical_hash"] == document_hash(report["document"])
    assert report["document"]["inference_kind"] == "UNTRAINED_BASELINE"
    approval = {"expected_version": 1, "report_hash": report["canonical_hash"]}
    assert post(buyer, f"/v1/shipments/{sid}/reports/confirm", approval).status_code == 200
    inbox = "/v1/shipment-inbox"
    assert merchant.get(inbox).json()["counts"] == {"REVIEW": 1}
    assert post(merchant, f"/v1/shipments/{sid}/reports/confirm", approval).status_code == 200
    assert buyer.get(inbox, params={"stage": "CONFIRMED"}).json()["items"][0]["id"] == sid
    assert outsider.get(inbox).json()["counts"] == {}
    revised = post(
        merchant,
        f"/v1/shipments/{sid}/reports",
        {
            "expected_version": 1,
            "observations": report["document"]["observations"],
            "user_notes": "Hai bên xem lại ảnh góc hộp.",
        },
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["version"] == 2 and revised.json()["confirmations"] == []
    assert revised.json()["canonical_hash"] != report["canonical_hash"]
    assert merchant.get(inbox).json()["counts"] == {"REVIEW": 1}
    stale = post(buyer, f"/v1/shipments/{sid}/reports/confirm", approval)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REPORT_VERSION_CONFLICT"
    assert outsider.get(f"/v1/shipments/{sid}").status_code == 404
    assert outsider.get(f"/v1/assets/{ref_id}/content").status_code == 404
    assert outsider.get(f"/v1/jobs/{job['id']}").status_code == 404
    assert outsider.get(f"/v1/shipments/{sid}/reports/1/export").status_code == 404
    export = buyer.get(f"/v1/shipments/{sid}/reports/1/export")
    assert hashlib.sha256(export.content).hexdigest() == report["canonical_hash"]


def test_inbox_finds_older_orders_and_separates_generated_examples(clients):
    merchant, buyer, outsider = clients
    actor = merchant.get("/v1/auth/session").json()["user"]
    with SessionLocal() as db:
        for index in range(103):
            db.add(Shipment(
                reference=f"DEMO-{index:03}", title=f"Kiện hàng {index}", recipient_name="Minh",
                case_id=f"0x{index:064x}", merchant_id=actor["id"], organization_id=actor["organization_id"],
                notes="Dữ liệu mô phỏng do AI tạo" if index == 102 else "",
                updated_at=now() + timedelta(seconds=index),
            ))
        db.commit()
    path = "/v1/shipment-inbox"
    assert merchant.get(path, params={"q": "demo-000"}).json()["items"][0]["reference"] == "DEMO-000"
    last_page = merchant.get(path, params={"page": 20, "page_size": 10}).json()
    assert last_page["counts"] == {"PREPARING": 102}
    earliest = merchant.get(path, params={"stage": "PREPARING", "sort": "oldest", "page_size": 2, "page": 2}).json()
    assert [row["reference"] for row in earliest["items"]] == ["DEMO-002", "DEMO-003"]
    assert last_page["page"] == 11 and len(last_page["items"]) == 2
    assert merchant.get(path, params={"source": "sample"}).json()["counts"] == {"PREPARING": 1}
    assert merchant.get(path, params={"q": "%"}).json()["items"] == []
    for viewer in (buyer, outsider):
        assert viewer.get(path).json()["counts"] == {}
        assert viewer.get(path, params={"source": "sample"}).json()["items"] == []


def test_concurrent_idempotency_and_changed_payload(clients):
    merchant, _, _ = clients
    payload = {"reference": "IDEMPOTENT-1", "title": "Test", "recipient_name": "Minh"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: post(merchant, "/v1/shipments", payload, "same-key-123"), range(2))
        )
    assert all(r.status_code == 201 for r in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    conflict = post(merchant, "/v1/shipments", {**payload, "title": "Changed"}, "same-key-123")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_wrong_role_checksum_and_sealed_mutation(clients):
    merchant, buyer, _ = clients
    sid = create_case(merchant, buyer)["id"]
    assert post(buyer, f"/v1/shipments/{sid}/captures", {"stage": "DISPATCH"}).status_code == 403
    capture, aid = upload(merchant, sid, "DISPATCH", "dispatch.png")
    bad = merchant.put(f"/v1/assets/{aid}/content", content=b"not the original image")
    assert bad.status_code == 422
    seal(merchant, sid, capture)
    raw = (ROOT / "changed.png").read_bytes()
    rejected = post(
        merchant,
        f"/v1/shipments/{sid}/uploads",
        {
            "capture_session_id": capture["id"],
            "filename": "new.png",
            "mime_type": "image/png",
            "byte_size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "CAPTURE_SEALED"


def test_worker_lease_reclaim_and_no_duplicate_reports(clients):
    _, buyer, _ = clients
    sid, _, query_id = ready_case(clients)
    job = post(buyer, f"/v1/shipments/{sid}/analyses", {"query_asset_id": query_id}).json()
    old = claim_job()
    assert old[0] == job["id"]
    assert claim_job() is None  # Active lease must not be stolen.
    with SessionLocal() as db, db.begin():
        db.get(Job, job["id"]).lease_until = now() - timedelta(seconds=1)
    assert not finish(job["id"], old[1], {})  # Expired holder cannot publish.
    assert run_once()
    assert not run_once()
    with SessionLocal() as db:
        assert db.get(Job, job["id"]).attempts == 2
        assert len(db.scalars(select(Report).where(Report.job_id == job["id"])).all()) == 1


def test_csrf_and_cross_report_reference(clients):
    merchant, buyer, _ = clients
    sid, _, query_id = ready_case(clients)
    old = merchant.headers.pop("X-CSRF-Token")
    denied = post(merchant, f"/v1/shipments/{sid}/invitations")
    assert denied.status_code == 403
    merchant.headers["X-CSRF-Token"] = old
    post(buyer, f"/v1/shipments/{sid}/analyses", {"query_asset_id": query_id})
    run_once()
    report = merchant.get(f"/v1/shipments/{sid}/reports").json()[0]
    observations = report["document"]["observations"]
    observations[0]["evidence_asset_ids"] = ["foreign-asset"]
    denied = post(
        merchant,
        f"/v1/shipments/{sid}/reports",
        {"expected_version": 1, "observations": observations},
    )
    assert denied.status_code == 422

def test_supplement_ownership_and_pending_upload_recovery(clients):
    merchant, buyer, _ = clients
    sid, _, query = ready_case(clients)
    root = f"/v1/shipments/{sid}"
    post(buyer, root + "/analyses", {"query_asset_id": query})
    assert run_once()
    sender_capture, sender_asset = upload(merchant, sid, "SUPPLEMENT", "changed.png")
    receiver_capture, receiver_asset = upload(buyer, sid, "SUPPLEMENT", "unchanged.png")
    assert sender_capture["id"] != receiver_capture["id"]
    assert post(buyer, root + "/analyses", {"query_asset_id": receiver_asset}).status_code == 409
    seal(merchant, sid, sender_capture)
    assert post(buyer, root + "/analyses", {"query_asset_id": sender_asset}).status_code == 422
    pending = post(buyer, root + "/uploads", {"capture_session_id": receiver_capture["id"],
        "filename": "bad.png", "mime_type": "image/png", "byte_size": 8, "sha256": "1"*64}).json()["asset_id"]
    cancel = f"/v1/assets/{pending}/cancel-upload"
    assert post(merchant, cancel).status_code == 404
    assert post(buyer, cancel).status_code == 200
    assert post(buyer, f"/v1/assets/{pending}/complete").status_code == 409
    sealed = seal(buyer, sid, receiver_capture)
    assert [a["id"] for a in sealed["manifest"]["assets"]] == [receiver_asset]
    assert buyer.get(root).json()["status"] == "REVIEW"
    assert post(buyer, root + "/analyses", {"query_asset_id": receiver_asset}).status_code == 202


def test_failed_job_can_be_retried_with_same_photos(clients):
    _, buyer, _ = clients
    sid, _, query = ready_case(clients)
    path = f"/v1/shipments/{sid}/analyses"
    body = {"query_asset_id": query}
    original = post(buyer, path, body).json()
    with SessionLocal.begin() as db:
        job = db.get(Job, original["id"])
        job.status, job.attempts = "FAILED", 3
    assert claim_job() is None
    assert buyer.get(f"/v1/shipments/{sid}").json()["status"] == "RECEIVED"
    retry = post(buyer, path, body).json()
    assert retry["id"] == original["id"] and retry["status"] == "QUEUED" and retry["attempts"] == 0
    assert run_once()
    assert buyer.get(f"/v1/jobs/{job.id}").json()["status"] == "SUCCEEDED"
