"""Tests for crypto helpers."""
from __future__ import annotations

import os
import pytest
from cryptography.fernet import Fernet, InvalidToken

from crypto import encrypt, decrypt


def test_round_trip():
    plaintext = "שלום"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_decrypt_wrong_key_raises(monkeypatch):
    ciphertext = encrypt("secret")
    # Swap to a different key and attempt decrypt
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    import crypto
    crypto._FERNET = None  # reset lazy cache
    with pytest.raises(InvalidToken):
        decrypt(ciphertext)
