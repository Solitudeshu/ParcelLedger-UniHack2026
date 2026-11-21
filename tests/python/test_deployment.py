from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from scripts import deployment_preflight
from scripts.provision_user import provision
from services.api.config import Settings
from services.api.database import SessionLocal, User
from services.api.security import passwords


def valid_config(**overrides):
    values = dict(app_env="production", allow_local_demo_login=False,
                  web_origin="https://evidence.example", chain_id=43113, storage_backend="s3",
                  chain_rpc_url="https://api.avax-test.network/ext/bc/C/rpc",
                  s3_endpoint_url="https://s3.example", s3_access_key="dedicated-account",
                  s3_secret_key="test-storage-secret-123456", presigned_uploads_enabled=False,
                  database_url="postgresql+psycopg://app:test-db-password-12345@db.example/app?sslmode=verify-full")
    return Settings(_env_file=None, **(values | overrides))


def test_production_config_accepts_explicit_tls_dependencies():
    assert valid_config().app_env == "production"


@pytest.mark.parametrize("overrides,code", [
    ({"web_origin": "http://evidence.example"}, "WEB_ORIGIN_HTTPS_ORIGIN_REQUIRED"),
    ({"web_origin": "https://evidence.example/path"}, "WEB_ORIGIN_HTTPS_ORIGIN_REQUIRED"),
    ({"allow_local_demo_login": True}, "DEMO_LOGIN_MUST_BE_DISABLED"),
    ({"chain_id": 43114}, "FUJI_TESTNET_REQUIRED"),
    ({"shadow_ranker_path": "/models/ranker"}, "RESEARCH_MODELS_LOCAL_ONLY"),
    ({"shadow_change_path": "/models/change"}, "RESEARCH_MODELS_LOCAL_ONLY"),
    ({"storage_backend": "local"}, "MANAGED_S3_REQUIRED"),
    ({"s3_secret_key": "kienchung_local_storage"}, "DEDICATED_STORAGE_CREDENTIALS_REQUIRED"),
    ({"s3_endpoint_url": "http://127.0.0.1:59000"}, "S3_ENDPOINT_HTTPS_REQUIRED"),
    ({"database_url": "postgresql+psycopg://app:secret@db.example/app"}, "DEDICATED_POSTGRES_TLS_REQUIRED"),
])
def test_production_rejects_local_defaults_without_leaking_secrets(overrides, code):
    with pytest.raises(ValidationError) as error:
        valid_config(**overrides)
    assert code in str(error.value)
    assert "test-storage-secret" not in str(error.value)
    assert "test-db-password" not in str(error.value)


def test_unknown_environment_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prod")


def test_provisioning_keeps_accounts_separate_and_cannot_reset_existing(clean_db):
    first = {"email": "ONE@example.com", "name": "Một", "organization": "Shop A", "password": "test-password-first-12345"}
    second = first | {"email": "two@example.com", "organization": "Shop B"}
    with SessionLocal() as db, db.begin():
        a, b = provision(db, first), provision(db, second)
        assert a["organization_id"] != b["organization_id"]
        user = db.scalar(select(User).where(User.email == "one@example.com"))
        original_hash = user.password_hash
        assert passwords.verify(first["password"], original_hash)
        with pytest.raises(ValueError, match="USER_ALREADY_EXISTS"):
            provision(db, first | {"password": "another-password-12345"})
        assert user.password_hash == original_hash
    with SessionLocal() as db, db.begin():
        with pytest.raises(ValueError, match="INVALID_PROVISION_VALUES"):
            provision(db, second | {"email": "three@example.com", "password": "short"})


def test_fuji_preflight_requires_live_code_and_real_safe_configuration(monkeypatch):
    addresses = ["0x" + str(n)*40 for n in range(1, 7)]
    manifest = {"contracts":dict(zip(("EscrowFactory", "DemoToken", "ArbitrationSafe"), addresses[:3])),
                "safe_owners":addresses[3:]}
    monkeypatch.setattr(deployment_preflight, "settings", lambda: SimpleNamespace(chain_id=43113))
    monkeypatch.setattr(deployment_preflight.chain_rpc, "deployment", lambda: manifest)
    monkeypatch.setattr(deployment_preflight.chain_rpc, "network_block", lambda: ("0x1", 1, "0x1"))
    monkeypatch.setattr(deployment_preflight.chain_rpc, "rpc", lambda *args: "0x6000")
    threshold = [2]
    def call(address, signature, *args):
        if signature == "getOwners()":
            return addresses[3:]
        if signature == "getThreshold()":
            return threshold[0]
        return True
    monkeypatch.setattr(deployment_preflight.chain_rpc, "call", call)
    assert deployment_preflight.verify_chain()["safe_threshold"] == 2
    threshold[0] = 1
    with pytest.raises(ValueError, match="FUJI_SAFE_CONFIGURATION_MISMATCH"):
        deployment_preflight.verify_chain()
    monkeypatch.setattr(deployment_preflight.chain_rpc, "rpc", lambda *args: "0x")
    with pytest.raises(ValueError, match="FUJI_CONTRACT_CODE_MISSING"):
        deployment_preflight.verify_chain()
