"""Read-only production preflight with sanitized output and live Fuji checks."""
import json

from sqlalchemy import text

from services.api import chain_rpc
from services.api.config import settings
from services.api.database import engine
from services.api.storage import storage


def verify_chain():
    cfg = settings()
    manifest = chain_rpc.deployment()
    if not manifest or cfg.chain_id != 43113:
        raise ValueError("FUJI_MANIFEST_REQUIRED")
    chain_rpc.network_block()
    required = ("EscrowFactory", "DemoToken", "ArbitrationSafe")
    for name in required:
        address = manifest["contracts"][name]
        code = chain_rpc.rpc("eth_getCode", [address, "latest"])
        if not isinstance(code, str) or not code.startswith("0x") or len(code) < 4:
            raise ValueError("FUJI_CONTRACT_CODE_MISSING")
    factory, token, safe = (manifest["contracts"][name] for name in required)
    for signature, address in (("allowedTokens(address)", token), ("allowedArbitrators(address)", safe)):
        if not chain_rpc.call(factory, signature, ["bool"], ["address"], [address]):
            raise ValueError("FUJI_FACTORY_ALLOWLIST_MISMATCH")
    owners = chain_rpc.call(safe, "getOwners()", ["address[]"])
    threshold = chain_rpc.call(safe, "getThreshold()", ["uint256"])
    expected = manifest.get("safe_owners", [])
    if (threshold != 2 or len(expected) != 3 or len(set(a.lower() for a in expected)) != 3
            or sorted(a.lower() for a in owners) != sorted(a.lower() for a in expected)):
        raise ValueError("FUJI_SAFE_CONFIGURATION_MISMATCH")
    return {"chain_id": 43113, "contracts_with_code": list(required), "safe_threshold": 2}


def check():
    if settings().app_env != "production":
        raise ValueError("PRODUCTION_ENV_REQUIRED")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    storage().ready()
    return {"status": "DEPENDENCIES_VERIFIED", "chain": verify_chain(),
            "limits": "Not a security audit, real-image benchmark or permission to accept real funds."}


if __name__ == "__main__":
    try:
        print(json.dumps(check()))
    except Exception as exc:
        # Provider errors can embed signed URLs; keep only type in console.
        print(json.dumps({"status": "PREFLIGHT_FAILED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
