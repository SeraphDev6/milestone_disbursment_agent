"""Encryption at rest for stored files (StoredFile.data): AES-256-GCM, applied by the column type itself, so every
read and write of a file's bytes goes through it and no call site can forget.

Stored form: MAGIC + key id (4 bytes) + nonce (12) + ciphertext and tag. Rows written before encryption was on
have no MAGIC and are read as they are (`python -m app.jobs encrypt-files` converts them). Several keys can be
configured: the first encrypts, all of them decrypt (rotation). A blob that fails authentication reads as empty
bytes and is logged; every download checks the file's SHA-256, so it is refused as a mismatch rather than served.

The key is FILE_ENCRYPTION_KEY (32 bytes, base64), or derived from SECRET_KEY when that isn't set.
"""

import base64
import hashlib
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

MAGIC = b"\x00MDEenc1"
log = logging.getLogger("mde.files")


class FileCipher:
    def __init__(self, keys: list[bytes]):
        if not keys or any(len(k) != 32 for k in keys):
            raise ValueError("file encryption keys must be 32 bytes")
        self._keys = {hashlib.sha256(k).digest()[:4]: AESGCM(k) for k in keys}
        self._primary = hashlib.sha256(keys[0]).digest()[:4]

    @classmethod
    def from_settings(cls, settings) -> "FileCipher":
        raw = settings.file_encryption_key.get_secret_value() if settings.file_encryption_key else None
        old = settings.file_encryption_old_keys.get_secret_value() if settings.file_encryption_old_keys else ""
        keys = (
            [_decode(raw)]
            if raw
            else [hashlib.sha256(b"mde-files:" + settings.secret_key.get_secret_value().encode()).digest()]
        )
        return cls(keys + [_decode(k) for k in old.split(",") if k.strip()])

    def encrypt(self, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return MAGIC + self._primary + nonce + self._keys[self._primary].encrypt(nonce, data, MAGIC)

    def current(self, blob: bytes) -> bool:
        """Encrypted with the primary key (nothing for encrypt-files to do)."""
        return blob.startswith(MAGIC) and blob[8:12] == self._primary

    def decrypt(self, blob: bytes) -> bytes:
        if not blob.startswith(MAGIC):
            return blob  # stored before encryption was on
        kid, nonce, ct = blob[8:12], blob[12:24], blob[24:]
        aes = self._keys.get(kid)
        if aes is None:
            log.error("a stored file was encrypted with a key that isn't configured (FILE_ENCRYPTION_OLD_KEYS?)")
            return b""
        try:
            return aes.decrypt(nonce, ct, MAGIC)
        except InvalidTag:
            log.error("a stored file failed authentication; it was altered or the key is wrong")
            return b""


def _decode(k: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(k.strip() + "=" * (-len(k.strip()) % 4))
    except ValueError as e:
        raise ValueError("FILE_ENCRYPTION_KEY must be 32 bytes, base64") from e
    if len(key) != 32:
        raise ValueError("FILE_ENCRYPTION_KEY must be 32 bytes, base64")
    return key


_cipher: FileCipher | None = None


def configure(cipher: FileCipher | None) -> None:
    global _cipher
    _cipher = cipher


def is_encrypted(blob: bytes | None) -> bool:
    return bool(blob) and blob.startswith(MAGIC)  # type: ignore[union-attr]


class EncryptedBytes(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or _cipher is None or is_encrypted(value):
            return value
        return _cipher.encrypt(bytes(value))

    def process_result_value(self, value, dialect):
        if value is None or not is_encrypted(bytes(value)):
            return value
        if _cipher is None:
            log.error("a stored file is encrypted but no file encryption key is configured")
            return b""
        return _cipher.decrypt(bytes(value))


def cipher() -> FileCipher | None:
    return _cipher
