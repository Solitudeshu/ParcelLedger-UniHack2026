import os
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

# A dedicated PostgreSQL test database is required; production/local data is never truncated.
base_url = make_url(
    os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://kienchung:kienchung_local@127.0.0.1:55432/kienchung"
    )
)
test_url = base_url.set(database="kienchung_test")
os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
os.environ["APP_ENV"] = "local"
os.environ["ALLOW_LOCAL_DEMO_LOGIN"] = "true"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_PATH"] = ".local/test-evidence"
os.environ["SHADOW_RANKER_PATH"] = ""  # Image-model runtime has its own parity/integration check.
os.environ["SHADOW_CHANGE_PATH"] = ""
os.environ["RESEARCH_TRANSFORMER_PATH"] = ""

from scripts.bootstrap import seed_demo  # noqa: E402
from services.api.database import Base, SessionLocal, engine  # noqa: E402
from services.api.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def test_database():
    connect_url = base_url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(connect_url, autocommit=True) as conn:
        if not conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", ("kienchung_test",)
        ).fetchone():
            conn.execute("CREATE DATABASE kienchung_test")
    assert engine.url.database == "kienchung_test"
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture
def clean_db(test_database):
    assert engine.url.database == "kienchung_test"
    names = ", ".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    with SessionLocal() as db:
        credentials = seed_demo(db)
    return credentials


@pytest.fixture
def clients(clean_db):
    with TestClient(app) as merchant, TestClient(app) as buyer, TestClient(app) as outsider:
        for client, role in [(merchant, "MERCHANT"), (buyer, "BUYER")]:
            result = client.post("/v1/dev/session", json={"role": role})
            assert result.status_code == 200, result.text
            client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        result = outsider.post(
            "/v1/auth/login",
            json={
                "email": "outside@kienchung.local",
                "password": clean_db["outside@kienchung.local"],
            },
        )
        assert result.status_code == 200
        outsider.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        yield merchant, buyer, outsider


def post(client, url, body=None, key=None):
    return client.post(url, json=body or {}, headers={"Idempotency-Key": key or str(uuid4())})
