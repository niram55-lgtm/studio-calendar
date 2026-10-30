"""Fernet encrypt/decrypt helpers using FERNET_KEY env var."""
from __future__ import annotations

import os
from cryptography.fernet import Fernet

_FERNET: Fernet | None = None


def _fernet() -> Fernet:
    global _FERNET
    if _FERNET is None:
        key = os.environ.get("FERNET_KEY")
        if not key:
            raise RuntimeError("Missing FERNET_KEY env var")
        _FERNET = Fernet(key.encode() if isinstance(key, str) else key)
    return _FERNET


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode("utf-8")
