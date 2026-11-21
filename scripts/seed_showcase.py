"""Populate the local judge accounts using the same authenticated workflow as the UI."""
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import requests

API = "http://127.0.0.1:8000"
PHOTOS = Path(__file__).resolve().parents[1] / "assets/showcase"
LAMP_PHOTOS = Path(__file__).resolve().parents[1] / "apps/web/src/assets"
CASES = [
    ("mailer", "Áo sơ mi linen · Túi chuyển phát", "túi chuyển phát có vết rách", True),
    ("books", "Bộ sách mỹ thuật · Hộp giấy", "hộp sách còn nguyên nếp gấp", True),
    ("tube", "Tranh in · Ống đựng có nắp", "ống đựng tranh có mép bị móp", False),
    ("lamp", "Đèn gốm · Thùng carton", "thùng đèn gốm có vùng bị móp", True),
]


def login(role):
    session = requests.Session()
    response = session.post(API + "/v1/dev/session", json={"role": role}, timeout=15)
    response.raise_for_status()
    session.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return session


def post(session, route, body, key):
    response = session.post(API + route, json=body, headers={"Idempotency-Key": key}, timeout=30)
    response.raise_for_status()
    return response.json()


def upload_set(session, route, parcel, stage, shots):
    capture = post(session, route + "/captures", {"stage": stage}, f"showcase-{parcel}-{stage}")
    overview = None
    for shot in shots:
        file = (LAMP_PHOTOS / {"dispatch": "truoc-khi-gui.webp", "receipt": "luc-nhan.webp"}[shot]
                if parcel == "lamp" else PHOTOS / f"{parcel}-{shot}.webp")
        data = file.read_bytes()
        key = f"showcase-{parcel}-{shot}"
        intent = post(session, route + "/uploads", {
            "capture_session_id": capture["id"], "filename": file.name, "byte_size": len(data),
            "sha256": sha256(data).hexdigest(), "mime_type": "image/webp", "parent_overview_id": overview,
        }, key)
        session.put(API + intent["upload_url"], data=data, headers={"Content-Type": "image/webp"}, timeout=30).raise_for_status()
        asset = post(session, f"/v1/assets/{intent['asset_id']}/complete", {}, key)
        if overview is None:
            overview = asset["id"]
    post(session, f"{route}/captures/{capture['id']}/seal", {"expected_version": capture["version"]}, f"showcase-{parcel}-{stage}")
    return overview


def main():
    shop, buyer = login("MERCHANT"), login("BUYER")
    for parcel, title, scene, analyze in CASES:
        key = f"showcase-{parcel}-v1"
        shipment = post(shop, "/v1/shipments", {
            "reference": f"DEMO-PL-{parcel.upper()}", "title": title, "recipient_name": "Minh",
            "notes": f"Dữ liệu mô phỏng do AI tạo để minh họa {scene}.",
        }, key)
        route = "/v1/shipments/" + shipment["id"]
        upload_set(shop, route, parcel, "DISPATCH", ["dispatch"])
        current = shop.get(API + route, timeout=15)
        current.raise_for_status()
        if not current.json()["recipient_linked"]:
            invitation = post(shop, route + "/invitations", {}, str(uuid4()))
            post(buyer, "/v1/invitations/claim", {"token": invitation["token"]}, key)
        query = upload_set(buyer, route, parcel, "RECEIPT", ["receipt"] if parcel == "lamp" else ["receipt", "detail"])
        if analyze:
            post(buyer, route + "/analyses", {"query_asset_id": query}, key)
        print(shipment["reference"], title)


if __name__ == "__main__":
    main()
