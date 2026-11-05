"""Where stored files' bytes live: the database (the default) or an S3-compatible bucket (Cloudflare R2).

FILE_STORAGE=s3 puts each new file in the bucket under S3_PREFIX + its id; the row keeps the name, type, size and
SHA-256, and `storage_key` says where the bytes are. Objects hold the same AES-GCM ciphertext the database column
would (app/integrations/filecrypt.py), so the bucket never sees a deliverable in the clear and a leaked bucket key
doesn't expose one. Downloads go through the app, which checks access, logs the read and checks the hash; nothing
hands out a direct bucket link.

Files stored in the database stay readable after switching; `python -m app.jobs encrypt-files` moves them over.
"""

import logging
from typing import Protocol

from app.services import ServiceError

log = logging.getLogger("mde.files")


class FileStoreError(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, 503)


class FileStore(Protocol):
    name: str

    def put(self, key: str, blob: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class MemoryStore:
    """A bucket in a dict, for tests and local runs."""

    name = "memory"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, blob: bytes) -> None:
        self.objects[key] = bytes(blob)

    def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileStoreError("the file is missing from object storage")
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class S3Store:
    """Any S3-compatible bucket. For R2: S3_ENDPOINT_URL=https://<account id>.r2.cloudflarestorage.com, region auto."""

    name = "s3"

    def __init__(self, bucket: str, client) -> None:
        self.bucket, self.client = bucket, client

    @classmethod
    def from_settings(cls, settings) -> "S3Store":
        import boto3
        from botocore.config import Config

        missing = [
            k
            for k in ("s3_bucket", "s3_endpoint_url", "s3_access_key_id", "s3_secret_access_key")
            if not getattr(settings, k)
        ]
        if missing:
            raise ValueError(f"FILE_STORAGE=s3 needs {', '.join(m.upper() for m in missing)}")
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=60,
                retries={"max_attempts": 3, "mode": "standard"},
                # R2 doesn't take every checksum boto3 sends by default; only send them when an API requires one.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        return cls(settings.s3_bucket, client)

    def put(self, key: str, blob: bytes) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=blob, ContentType="application/octet-stream")
        except Exception as e:
            log.exception("couldn't write %s to object storage", key)
            raise FileStoreError("file storage is unavailable; try again shortly") from e

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as e:
            log.exception("couldn't read %s from object storage", key)
            raise FileStoreError("file storage is unavailable; try again shortly") from e

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            log.exception("couldn't delete %s from object storage", key)
            raise FileStoreError("file storage is unavailable; try again shortly") from e


def from_settings(settings) -> FileStore | None:
    """The configured bucket, or None when files go in the database."""
    return S3Store.from_settings(settings) if settings.file_storage == "s3" else None


_store: FileStore | None = None
_prefix = "files/"


def configure(store: FileStore | None, prefix: str = "files/") -> None:
    global _store, _prefix
    _store, _prefix = store, prefix


def store() -> FileStore | None:
    return _store


def key_for(file_id: str) -> str:
    return f"{_prefix}{file_id}"
