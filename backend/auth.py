"""Who is signed in: a Supabase Auth token checked against the project's public keys.

The dashboard signs a handler in with Supabase and sends the access token with each
action. Supabase signs it with a private key it keeps and publishes the public key,
so the backend needs no secret.
"""

import os
from functools import cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import Connection, select

from backend import tables as t
from backend.models import Handler

AUDIENCE = "authenticated"
ALGORITHMS = ["ES256", "RS256"]

bearer = HTTPBearer(auto_error=False)


def supabase_url() -> str:
    """Return the Supabase project's address, read from the environment."""
    return os.getenv("VITE_SUPABASE_URL", "").rstrip("/")


@cache
def keys() -> PyJWKClient:
    """Return the client that fetches and remembers the project's public keys."""
    url = supabase_url()
    if not url:
        raise HTTPException(503, "Sign-in is not set up: VITE_SUPABASE_URL is missing")
    return PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json")


def signing_key(token: str) -> Any:
    """Return the public key that checks a token. Tests replace it."""
    return keys().get_signing_key_from_jwt(token).key


def token_email(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    """Return the email address in a valid access token, or refuse with a 401."""
    if credentials is None:
        raise HTTPException(401, "Sign in to do that")
    try:
        claims = jwt.decode(
            credentials.credentials,
            signing_key(credentials.credentials),
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "Your sign-in is not valid. Sign in again") from None
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise HTTPException(401, "Your sign-in is not valid. Sign in again")
    return email.lower()


SignedIn = Annotated[str, Depends(token_email)]


def handler_for(conn: Connection, email: str) -> Handler:
    """Return the handler with this email address, or refuse with a 403."""
    row = (
        conn.execute(
            select(
                t.handlers.c.handler_id,
                t.handlers.c.name,
                t.handlers.c.role,
                t.handlers.c.can_approve_over_limit,
            ).where(t.handlers.c.email == email)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(403, f"{email} is not one of UnderCover's claims handlers")
    return Handler.model_validate(dict(row))


def handler_by_id(conn: Connection, handler_id: str) -> Handler:
    """Return the handler with this id, or raise a 404."""
    row = (
        conn.execute(
            select(
                t.handlers.c.handler_id,
                t.handlers.c.name,
                t.handlers.c.role,
                t.handlers.c.can_approve_over_limit,
            ).where(t.handlers.c.handler_id == handler_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, f"Handler {handler_id} not found")
    return Handler.model_validate(dict(row))


def senior_handler_for(conn: Connection, email: str, action: str) -> Handler:
    """Return the handler, who must be able to sign off payouts over the limit."""
    handler = handler_for(conn, email)
    if not handler.can_approve_over_limit:
        raise HTTPException(
            403,
            f"{handler.name} cannot {action}. Only a handler who can sign off"
            " payouts over the limit can",
        )
    return handler
