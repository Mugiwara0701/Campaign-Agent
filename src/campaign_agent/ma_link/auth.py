"""The bearer token of the link. Constant-time comparison; the token is never logged."""

import hmac

MIN_TOKEN_CHARS = 32


def token_ok(header: str | None, current: str, previous: str | None = None) -> bool:
    """Does the Authorization header carry the current token, or the previous one (during a rollover)?"""
    if not header:
        return False
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return False
    given = value.strip().encode("utf-8")
    ok_current = hmac.compare_digest(given, current.encode("utf-8"))
    # Both comparisons always run, so the answer does not depend on which one matched.
    ok_previous = bool(previous) and hmac.compare_digest(given, (previous or "").encode("utf-8"))
    return ok_current or ok_previous
