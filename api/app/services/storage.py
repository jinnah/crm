"""Object storage for document binaries — PostgreSQL keeps metadata only.

Two backends behind one interface:

- LocalDiskStorage: development/Docker default. Objects live under
  DOCUMENTS_LOCAL_PATH (a named volume in compose), keys are sanitized
  relative paths, and writes are atomic (temp file + rename).
- S3Storage: any S3-compatible store for production. Configure
  DOCUMENTS_STORAGE_BACKEND=s3 plus bucket/endpoint/region/credentials in
  environment secrets. Credentials never appear in settings rows, API
  responses or the browser.

Keys grant no authority: every read goes through an authorized CRM route.
Uploads land under the quarantine/ prefix and are promoted only after
validation and malware scanning succeed, so an object whose database
transaction failed is never exposed and gets swept by reconciliation.
"""

import contextlib
import os
import re
import tempfile
import uuid
from pathlib import Path

from app.config import Settings

_KEY_PATTERN = re.compile(r"^[a-z0-9/_.-]{1,300}$")

QUARANTINE_PREFIX = "quarantine/"
DOCUMENTS_PREFIX = "documents/"
PREVIEWS_PREFIX = "previews/"
COMMERCIAL_PREFIX = "commercial/"


class StorageError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def new_object_key(prefix: str, suffix: str) -> str:
    """Random, non-guessable key. Never derived from customer data."""
    return f"{prefix}{uuid.uuid4().hex}{suffix}"


def _validate_key(key: str) -> str:
    if not _KEY_PATTERN.match(key) or ".." in key or key.startswith("/"):
        raise StorageError("Invalid storage key.")
    return key


class LocalDiskStorage:
    backend_name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _validate_key(key)).resolve()
        if not str(path).startswith(str(self.root)):
            raise StorageError("Invalid storage key.")
        return path

    def put_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic on POSIX and NTFS: write a sibling temp file, then replace.
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp_name, path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("Stored object not found.", status_code=404)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    def move(self, source_key: str, target_key: str) -> None:
        source = self._path(source_key)
        target = self._path(target_key)
        if not source.is_file():
            raise StorageError("Stored object not found.", status_code=404)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)

    def list_keys(self, prefix: str) -> list[str]:
        base = self._path(prefix.rstrip("/")) if prefix else self.root
        if not base.is_dir():
            return []
        return [
            str(path.relative_to(self.root)).replace(os.sep, "/")
            for path in base.rglob("*")
            if path.is_file()
        ]

    def health(self) -> dict[str, str]:
        probe = f"{QUARANTINE_PREFIX}health-{uuid.uuid4().hex}.probe"
        try:
            self.put_bytes(probe, b"ok")
            self.delete(probe)
            return {"backend": self.backend_name, "status": "ok"}
        except OSError as error:
            return {"backend": self.backend_name, "status": f"error: {type(error).__name__}"}


class S3Storage:
    """S3-compatible backend. boto3 is imported lazily so installations using
    local storage never load it."""

    backend_name = "s3"

    def __init__(self, settings: Settings) -> None:
        import boto3  # noqa: PLC0415 - deliberate lazy import
        from botocore.config import Config  # noqa: PLC0415 - deliberate lazy import

        self.bucket = settings.documents_s3_bucket
        if not self.bucket:
            raise StorageError("DOCUMENTS_S3_BUCKET is not configured.")
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.documents_s3_endpoint_url or None,
            region_name=settings.documents_s3_region or None,
            aws_access_key_id=settings.documents_s3_access_key_id or None,
            aws_secret_access_key=settings.documents_s3_secret_access_key or None,
            config=Config(s3={"addressing_style": settings.documents_s3_addressing_style}),
        )

    def put_bytes(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=_validate_key(key), Body=data)

    def get_bytes(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=_validate_key(key))
        except self.client.exceptions.NoSuchKey as error:
            raise StorageError("Stored object not found.", status_code=404) from error
        return response["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_validate_key(key))
            return True
        except Exception:  # noqa: BLE001 - head_object raises a generic ClientError
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_validate_key(key))

    def move(self, source_key: str, target_key: str) -> None:
        self.client.copy_object(
            Bucket=self.bucket,
            Key=_validate_key(target_key),
            CopySource={"Bucket": self.bucket, "Key": _validate_key(source_key)},
        )
        self.delete(source_key)

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return keys

    def health(self) -> dict[str, str]:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return {"backend": self.backend_name, "status": "ok"}
        except Exception as error:  # noqa: BLE001 - report, never raise, from health
            return {"backend": self.backend_name, "status": f"error: {type(error).__name__}"}


def build_storage(settings: Settings) -> LocalDiskStorage | S3Storage:
    if settings.documents_storage_backend == "s3":
        return S3Storage(settings)
    return LocalDiskStorage(settings.documents_local_path)
