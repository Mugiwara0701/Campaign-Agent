"""One shared secret between the dashboard's server and this gateway. Constant-time compare, nothing else."""

import hmac

MIN_TOKEN_CHARS = 32


def token_ok(presented: str | None, expected: str) -> bool:
    """False for a missing header, a wrong token, or a configured token that is too short to be a secret."""
    if not presented or len(expected) < MIN_TOKEN_CHARS:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def bearer(header: str | None) -> str | None:
    """The token from "Authorization: Bearer <token>", or None."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None
