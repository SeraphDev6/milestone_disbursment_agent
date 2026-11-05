"""Keeping and reading stored files' bytes, wherever they live (app/integrations/filestore.py).

Every write of a file's bytes goes through `save` and every read through `read`, so the bucket and the database
column behave the same: encrypted at rest, deduplicated by SHA-256, and gone once retention purges them.
"""

import hashlib
from datetime import datetime

from sqlmodel import Session, select

from app.integrations import filecrypt, filestore
from app.integrations.filestore import FileStoreError
from app.models import StoredFile


def save(session: Session, data: bytes, *, filename: str, media_type: str) -> StoredFile:
    """The live row holding these bytes, created (and its bytes written) if there isn't one. Flushed."""
    digest = hashlib.sha256(data).hexdigest()
    row = session.exec(
        select(StoredFile).where(StoredFile.sha256 == digest, StoredFile.purged_at.is_(None))  # type: ignore[union-attr]
    ).first()
    if row is not None:
        return row
    row = StoredFile(filename=filename[:255], media_type=media_type, sha256=digest, size=len(data), data=data)
    bucket = filestore.store()
    if bucket is not None:
        row.storage_key = filestore.key_for(row.id)
        bucket.put(row.storage_key, _seal(data))
        row.data = b""
    session.add(row)
    session.flush()
    return row


def read(f: StoredFile) -> bytes:
    """The file's original bytes. Raises FileStoreError (503) when the bucket can't be reached."""
    if not f.storage_key:
        return f.data
    bucket = filestore.store()
    if bucket is None:
        raise FileStoreError("this file is in object storage, but FILE_STORAGE isn't s3 on this deployment")
    blob = bucket.get(f.storage_key)
    if not filecrypt.is_encrypted(blob):
        return blob
    c = filecrypt.cipher()
    if c is None:
        raise FileStoreError("this file is encrypted, but no file encryption key is configured")
    return c.decrypt(blob)  # b"" if it fails authentication; callers check the hash


def purge(f: StoredFile, now: datetime) -> None:
    """Remove the bytes (retention). The row, its hash and size stay."""
    if f.storage_key:
        bucket = filestore.store()
        if bucket is None:
            raise FileStoreError("this file is in object storage, but FILE_STORAGE isn't s3 on this deployment")
        bucket.delete(f.storage_key)
    f.data, f.purged_at = b"", now


def _seal(data: bytes) -> bytes:
    c = filecrypt.cipher()
    return c.encrypt(data) if c is not None else data
