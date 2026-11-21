"""Explicit production migration after live dependency checks; never seeds demo users."""
import json

from alembic import command
from alembic.config import Config

from scripts.deployment_preflight import check


if __name__ == "__main__":
    try:
        check()
        command.upgrade(Config("alembic.ini"), "head")
        print(json.dumps({"status": "MIGRATED", "demo_users_seeded": False}))
    except Exception as exc:
        print(json.dumps({"status": "INITIALIZATION_FAILED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
