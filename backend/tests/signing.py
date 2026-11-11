"""Sign-in for the tests: tokens signed with a key of their own, never Supabase's."""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from backend import auth

PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())

DANA = "dana.whitfield@undercover.example"
MARCUS = "marcus.oyelaran@undercover.example"
PRIYA = "priya.raman@undercover.example"


def token(
    email: str = DANA,
    key: Any = PRIVATE_KEY,
    audience: str = "authenticated",
    **extra: Any,
) -> str:
    """Sign an access token for an email address."""
    claims = {"email": email, "aud": audience, "exp": int(time.time()) + 3600}
    return jwt.encode({**claims, **extra}, key, algorithm="ES256")


def sign_in_as(client: TestClient, email: str) -> None:
    """Make the client's requests come from a handler."""
    client.headers["Authorization"] = f"Bearer {token(email)}"


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check tokens against the tests' key instead of fetching Supabase's."""
    monkeypatch.setattr(auth, "signing_key", lambda _: PRIVATE_KEY.public_key())
