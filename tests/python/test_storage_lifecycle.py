from types import SimpleNamespace

import boto3
import pytest
from botocore.stub import Stubber

from scripts.configure_storage import RULE, RULE_ID, configure


def test_lifecycle_preserves_unrelated_rules_and_verifies_exact_prefix():
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture")
    backend = SimpleNamespace(client=client, bucket="test-bucket")
    other = {"ID":"other", "Status":"Disabled", "Filter":{"Prefix":"exports/"}, "Expiration":{"Days":90}}
    with Stubber(client) as stub:
        stub.add_response("get_bucket_lifecycle_configuration", {"Rules":[other]}, {"Bucket":backend.bucket})
        stub.add_response("put_bucket_lifecycle_configuration", {}, {"Bucket":backend.bucket,"LifecycleConfiguration":{"Rules":[other,RULE]}})
        stub.add_response("get_bucket_lifecycle_configuration", {"Rules":[other,RULE]}, {"Bucket":backend.bucket})
        assert configure(backend, True)["status"] == "CONFIGURED"
        stub.assert_no_pending_responses()
    with Stubber(client) as stub:
        stub.add_response("get_bucket_lifecycle_configuration", {"Rules":[{**RULE,"Filter":{"Prefix":"originals/"}}]}, {"Bucket":backend.bucket})
        with pytest.raises(ValueError, match="CONFLICT"):
            configure(backend, True)


def test_lifecycle_dry_run_and_initial_missing_policy_never_delete_objects():
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture")
    with Stubber(client) as stub:
        stub.add_client_error("get_bucket_lifecycle_configuration", service_error_code="NoSuchLifecycleConfiguration", expected_params={"Bucket":"test-bucket"})
        result = configure(SimpleNamespace(client=client,bucket="test-bucket"))
        assert result["status"] == "DRY_RUN" and result["managed_rule"]["ID"] == RULE_ID
        stub.assert_no_pending_responses()
