"""Create a single isolated organization/user from stdin; never print a password.

Input is JSON: email, name, organization, password. Provisioning an existing email
fails without changing it. This operator tool deliberately cannot join an existing
organization or reset someone's password.
"""
import json
import re
import sys

from sqlalchemy import select

from services.api.database import Organization, SessionLocal, User
from services.api.security import passwords


def provision(db, data):
    if set(data) != {"email", "name", "organization", "password"} or not all(isinstance(v, str) for v in data.values()):
        raise ValueError("INVALID_PROVISION_FIELDS")
    email, name, organization = (data[k].strip() for k in ("email", "name", "organization"))
    email = email.lower()
    if (not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254
            or not 1 <= len(name) <= 120 or not 1 <= len(organization) <= 200
            or not 16 <= len(data["password"]) <= 256):
        raise ValueError("INVALID_PROVISION_VALUES")
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("USER_ALREADY_EXISTS")
    org = Organization(name=organization)
    db.add(org)
    db.flush()
    user = User(organization_id=org.id, email=email, name=name, password_hash=passwords.hash(data["password"]))
    db.add(user)
    db.flush()
    return {"status": "CREATED", "user_id": user.id, "organization_id": org.id}


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise ValueError("PROVISION_INPUT_TOO_LARGE")
        with SessionLocal() as db, db.begin():
            result = provision(db, json.loads(raw))
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"status": "PROVISION_FAILED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
