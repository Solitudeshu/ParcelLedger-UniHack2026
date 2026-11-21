"""Apply a bounded lifecycle for temporary uploads; never expire original evidence."""
import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError

from services.api.storage import S3Storage

RULE_ID = "unihack26-staging-only-v1"
RULE = {"ID": RULE_ID, "Status": "Enabled", "Filter": {"Prefix": "staging/"}, "Expiration": {"Days": 1}}


def lifecycle_md5(params, **kwargs):
    # MinIO's pinned local version requires the legacy wire checksum as well as
    # modern SDK checksum headers. This checksum is not used for authentication.
    body = params["body"]
    if not isinstance(body, bytes):
        raise ValueError("LIFECYCLE_BODY_MUST_BE_BYTES")
    params["headers"]["Content-MD5"] = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()


def configure(backend, apply=False):
    try:
        existing = backend.client.get_bucket_lifecycle_configuration(Bucket=backend.bucket).get("Rules", [])
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchLifecycleConfiguration":
            raise
        existing = []
    for rule in existing:
        if rule.get("ID") == RULE_ID and rule.get("Filter") != {"Prefix": "staging/"}:
            raise ValueError("LIFECYCLE_RULE_ID_CONFLICT")
    desired = [r for r in existing if r.get("ID") != RULE_ID] + [RULE]
    if apply:
        if existing != desired:
            event = "before-call.s3.PutBucketLifecycleConfiguration"
            backend.client.meta.events.register(event, lifecycle_md5, unique_id=RULE_ID)
            try:
                backend.client.put_bucket_lifecycle_configuration(Bucket=backend.bucket, LifecycleConfiguration={"Rules": desired})
            finally:
                backend.client.meta.events.unregister(event, unique_id=RULE_ID)
        actual = backend.client.get_bucket_lifecycle_configuration(Bucket=backend.bucket)["Rules"]
        if not any(r.get("ID") == RULE_ID and r.get("Status") == "Enabled"
                   and r.get("Filter") == RULE["Filter"] and r.get("Expiration") == RULE["Expiration"] for r in actual):
            raise ValueError("STAGING_LIFECYCLE_VERIFICATION_FAILED")
    return {"status": "CONFIGURED" if apply else "DRY_RUN", "checked_at": datetime.now(timezone.utc).isoformat(),
            "bucket": backend.bucket, "managed_rule": RULE, "preserved_rules": len(desired)-1,
            "scope": "Temporary staging/ objects only. No original evidence or DB deletion.",
            "expiration_execution": "PROVIDER_ASYNCHRONOUS_NOT_TIME_ELAPSE_TESTED"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", default="artifacts/evaluation/staging-lifecycle.json")
    args = parser.parse_args()
    result = configure(S3Storage(), args.apply)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
