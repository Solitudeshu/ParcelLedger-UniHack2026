from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from services.api.config import settings


class LocalStorage:
    def __init__(self):
        self.root = Path(settings().local_storage_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise ValueError("Invalid object key")
        return target

    def put(self, key: str, data: bytes, mime: str):
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # API holds the asset row lock. Never overwrite previously accepted evidence.
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("Immutable object conflict")
            return
        temporary = target.with_suffix(".partial")
        temporary.write_bytes(data)
        temporary.replace(target)

    def get(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def ready(self):
        return self.root.is_dir()


class S3Storage:
    def __init__(self):
        cfg = settings()
        self.bucket = cfg.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint_url,
            aws_access_key_id=cfg.s3_access_key,
            aws_secret_access_key=cfg.s3_secret_key,
            region_name=cfg.s3_region,
        )
        self.public_client = boto3.client("s3", endpoint_url=cfg.s3_public_endpoint_url or cfg.s3_endpoint_url,
            aws_access_key_id=cfg.s3_access_key, aws_secret_access_key=cfg.s3_secret_key,
            region_name=cfg.s3_region, config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))

    @staticmethod
    def staging_key(asset):
        # Presigned clients can only replace temporary bytes, never sealed evidence.
        return f"staging/{asset.id}/{asset.sha256}"

    def upload_ticket(self, asset):
        form = self.public_client.generate_presigned_post(Bucket=self.bucket, Key=self.staging_key(asset),
            Fields={"Content-Type": asset.mime_type, "success_action_status": "204"},
            Conditions=[{"Content-Type": asset.mime_type}, {"success_action_status": "204"},
                        ["content-length-range", asset.byte_size, asset.byte_size]], ExpiresIn=300)
        return {"transport": "S3_POST", "url": form["url"], "fields": form["fields"], "expires_in_seconds": 300}

    def staged_content(self, asset):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.staging_key(asset))
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey"}:
                return None
            raise
        stream = response["Body"]
        try:
            if response["ContentLength"] != asset.byte_size:
                raise ValueError("STAGED_SIZE_MISMATCH")
            # Bound reads even if object metadata or a provider is faulty.
            raw = stream.read(asset.byte_size+1)
            if len(raw) != asset.byte_size:
                raise ValueError("STAGED_SIZE_MISMATCH")
            return raw
        finally:
            stream.close()

    def initialize(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes, mime: str):
        # Object key contains the asset UUID and SHA256. The API enforces immutability.
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=mime)

    def get(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def ready(self):
        self.client.head_bucket(Bucket=self.bucket)
        return True


@lru_cache
def storage():
    return S3Storage() if settings().storage_backend == "s3" else LocalStorage()
