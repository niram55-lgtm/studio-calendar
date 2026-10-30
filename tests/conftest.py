"""Shared pytest fixtures and env setup."""
from __future__ import annotations

import os
import pytest
from cryptography.fernet import Fernet


# Set env vars BEFORE any imports from the app that might read them.
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")


@pytest.fixture
def fernet_key() -> str:
    return os.environ["FERNET_KEY"]
