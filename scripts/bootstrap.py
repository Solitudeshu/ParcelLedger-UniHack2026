"""Initialize a local installation without committing or printing passwords."""

import json
import secrets
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from services.api.config import settings
from services.api.database import Organization, SessionLocal, User
from services.api.security import passwords
from services.api.storage import storage


def seed_demo(db):
    identities = [
        ("shop@kienchung.local", "An · Shop Mộc", "Shop Mộc"),
        ("recipient@kienchung.local", "Minh · Người nhận", "Người nhận Minh"),
        ("outside@kienchung.local", "Tài khoản kiểm thử cách ly", "Tổ chức kiểm thử khác"),
    ]
    created = {}
    for email, name, organization in identities:
        if db.scalar(select(User).where(User.email == email)):
            continue
        org = Organization(name=organization)
        db.add(org)
        db.flush()
        password = secrets.token_urlsafe(24)
        db.add(
            User(
                organization_id=org.id,
                email=email,
                name=name,
                password_hash=passwords.hash(password),
            )
        )
        created[email] = password
    db.commit()
    return created


def main():
    if settings().app_env != "local":
        raise RuntimeError("Demo bootstrap only supports APP_ENV=local")
    command.upgrade(Config("alembic.ini"), "head")
    store = storage()
    if hasattr(store, "initialize"):
        store.initialize()
    with SessionLocal() as db:
        credentials = seed_demo(db)
    if credentials:
        path = Path(".local/demo-credentials.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps({**existing, **credentials}), encoding="utf-8")
    from services.api.main import app

    path = Path("artifacts/manifests/openapi.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print("Local database, storage, demo users and OpenAPI are ready. No credentials printed.")


if __name__ == "__main__":
    main()
