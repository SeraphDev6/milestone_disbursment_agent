"""Encryption for secrets we must be able to read back (e.g. an org's SSO client secret)."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, secret_key: str):
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as e:
            raise ValueError("can't decrypt: SECRET_KEY changed since this secret was stored") from e
