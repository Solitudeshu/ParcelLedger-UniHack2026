from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: Literal["local", "production"] = "local"
    allow_local_demo_login: bool = False
    database_url: str = "postgresql+psycopg://kienchung:kienchung_local@127.0.0.1:55432/kienchung"
    web_origin: str = "http://127.0.0.1:3000"
    api_base_url: str = "http://127.0.0.1:8000"
    storage_backend: str = "s3"
    local_storage_path: str = ".local/evidence"
    s3_endpoint_url: str = "http://127.0.0.1:59000"
    s3_access_key: str = "kienchung_local"
    s3_secret_key: str = "kienchung_local_storage"
    s3_bucket: str = "kienchung-evidence"
    s3_region: str = "us-east-1"
    presigned_uploads_enabled: bool = False
    s3_public_endpoint_url: str = ""
    session_hours: int = 24
    max_upload_bytes: int = 20 * 1024 * 1024
    max_image_pixels: int = 40_000_000
    max_active_jobs_per_organization: int = 10
    chain_id: int = 43113
    chain_rpc_url: str = "https://api.avax-test.network/ext/bc/C/rpc"
    chain_deployment_path: str = "artifacts/deployments/fuji.json"
    fuji_rpc_url: str = "https://api.avax-test.network/ext/bc/C/rpc"
    escrow_factory_address: str = ""
    demo_token_address: str = ""
    model_manifest_path: str = "artifacts/manifests/active-model.json"
    shadow_ranker_path: str = ""
    shadow_change_path: str = ""
    research_transformer_path: str = ""
    research_vision_policy_path: str = "artifacts/models/candidates/transformer-v2/correspondence-policy.json"
    research_vision_url: str = "http://vision-research:8010"
    research_vision_token_file: str = ".local/vision-token"
    dino_cache_path: str = ".local/dino-cache"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""

    @model_validator(mode="after")
    def production_configuration(self):
        if self.app_env != "production":
            return self
        invalid = []
        origin = urlsplit(self.web_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment):
            invalid.append("WEB_ORIGIN_HTTPS_ORIGIN_REQUIRED")
        if self.allow_local_demo_login:
            invalid.append("DEMO_LOGIN_MUST_BE_DISABLED")
        if self.chain_id != 43113:
            invalid.append("FUJI_TESTNET_REQUIRED")
        if self.shadow_ranker_path or self.shadow_change_path or self.research_transformer_path:
            invalid.append("RESEARCH_MODELS_LOCAL_ONLY")
        if self.storage_backend != "s3":
            invalid.append("MANAGED_S3_REQUIRED")
        for value, field in ((self.s3_endpoint_url, "S3_ENDPOINT"),
                             (self.chain_rpc_url, "CHAIN_RPC")):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname:
                invalid.append(field + "_HTTPS_REQUIRED")
        if self.presigned_uploads_enabled:
            parsed = urlsplit(self.s3_public_endpoint_url or self.s3_endpoint_url)
            if parsed.scheme != "https" or not parsed.hostname:
                invalid.append("S3_PUBLIC_HTTPS_REQUIRED")
        if (not self.s3_access_key or self.s3_access_key == "kienchung_local"
                or len(self.s3_secret_key) < 16 or self.s3_secret_key == "kienchung_local_storage"):
            invalid.append("DEDICATED_STORAGE_CREDENTIALS_REQUIRED")
        try:
            database = make_url(self.database_url)
            if (database.get_backend_name() != "postgresql" or not database.host or not database.database
                    or not database.username or not database.password or len(database.password) < 16
                    or database.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}):
                invalid.append("DEDICATED_POSTGRES_TLS_REQUIRED")
        except Exception:
            invalid.append("DATABASE_URL_INVALID")
        if invalid:
            # Only stable codes: never include URLs, passwords or input dictionaries.
            raise ValueError(",".join(invalid))
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
