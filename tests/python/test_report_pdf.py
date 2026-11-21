import hashlib

from conftest import post
from test_workflow import ready_case

from services.worker.runner import run_once


def test_pdf_export_checks_access_and_distinguishes_hashes(clients):
    merchant, buyer, outsider = clients
    sid, _, query_id = ready_case(clients)
    post(buyer, f"/v1/shipments/{sid}/analyses", {"query_asset_id": query_id})
    assert run_once()
    report = merchant.get(f"/v1/shipments/{sid}/reports").json()[0]
    assert outsider.get(f"/v1/shipments/{sid}/reports/1/pdf").status_code == 404
    response = buyer.get(f"/v1/shipments/{sid}/reports/1/pdf")
    assert response.status_code == 200, response.text[:200] if response.status_code != 200 else ""
    assert response.content.startswith(b"%PDF-")
    assert response.headers["x-content-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert response.headers["x-report-sha256"] == report["canonical_hash"]
    assert response.headers["content-disposition"] == 'attachment; filename="bien-ban-v1.pdf"'

def test_signatures_are_explicit_and_bound_to_one_report_version(clients):
    shop, buyer, outsider = clients
    sid, _, query = ready_case(clients)
    post(buyer, f"/v1/shipments/{sid}/analyses", {"query_asset_id": query})
    assert run_once()
    reports = f"/v1/shipments/{sid}/reports"
    first = shop.get(reports).json()[0]
    payload = {"expected_version": 1, "report_hash": first["canonical_hash"]}
    original = buyer.get(f"{reports}/1/export").content
    assert post(shop, reports + "/confirm", payload).json()["confirmations"][0]["signature"] is None
    draw = {"method": "DRAWN", "name": "Người nhận mẫu", "strokes": [[[150, 100], [2212, 1333]]]}
    sign = {**payload, "signature": draw}
    assert post(outsider, reports + "/confirm", sign).status_code == 404
    assert post(buyer, reports + "/confirm", {**sign, "report_hash": "0"*64}).status_code == 409
    assert post(buyer, reports + "/confirm", {**payload, "signature": {**draw, "strokes": []}}).status_code == 422
    first_signature = post(buyer, reports + "/confirm", sign).json()
    signed = next(c["signature"] for c in first_signature["confirmations"] if c["role"] == "BUYER")
    assert signed["strokes"] == draw["strokes"] and signed["report_hash"] == first["canonical_hash"]
    again = post(buyer, reports + "/confirm", sign).json()
    assert next(c["signature"] for c in again["confirmations"] if c["role"] == "BUYER") == signed
    typed = {**payload, "signature": {"method": "TYPED", "name": "Bên gửi mẫu"}}
    assert post(buyer, reports + "/confirm", typed).status_code == 409
    signed_report = post(shop, reports + "/confirm", typed).json()
    assert len(signed_report["confirmations"]) == 2 and all(c["signature"] for c in signed_report["confirmations"])
    assert buyer.get(f"{reports}/1/pdf").content.startswith(b"%PDF")
    assert buyer.get(f"{reports}/1/export").content == original
    revised = post(shop, reports, {"expected_version": 1, "observations": first["document"]["observations"],
                                   "user_notes": "Cập nhật sau khi ký."}).json()
    assert revised["version"] == 2 and revised["confirmations"] == []
    assert revised["document"]["observations"] == first["document"]["observations"]
    assert post(buyer, reports + "/confirm", sign).status_code == 409
    assert all(c["signature"] for c in buyer.get(reports).json()[1]["confirmations"])
