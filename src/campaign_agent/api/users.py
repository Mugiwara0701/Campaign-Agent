"""Who is asking: the signed-in dashboard user, proven by their own Supabase login token (audit 2026-10-17, F-18).

The shared API_TOKEN proves the caller is the dashboard's server. It cannot prove which person clicked, and before this
the gateway took `requested_by` (the approval record) on trust. Now the dashboard's server also forwards the person's
Supabase access token in `X-User-Token`; the gateway checks its signature, expiry and audience, takes the person's email
from it as `requested_by`, and applies the dashboard's own roles: only an admin may send a campaign request or resume the
agent; anyone signed in may pause it (the safety brake).

Off unless API_SUPABASE_URL (or the legacy API_SUPABASE_JWT_SECRET) is set. With API_REQUIRE_USER=true a request without a
valid user token is refused; without it, a request with no user token is accepted as before (for the rollout), but a
token that is present must be valid.
"""

from dataclasses import dataclass
from typing import Any

import jwt

ROLES = ("admin", "employee")
ASYMMETRIC_ALGS = ["ES256", "RS256", "EdDSA"]
USER_HEADER = "x-user-token"


class UserTokenError(Exception):
    """The user token is missing, invalid, expired, or belongs to an account without a dashboard role."""


@dataclass(frozen=True)
class User:
    email: str
    role: str

    @property
    def admin(self) -> bool:
        return self.role == "admin"


class UserVerifier:
    def __init__(self, supabase_url: str | None = None, jwt_secret: str | None = None) -> None:
        if not supabase_url and not jwt_secret:
            raise ValueError("a Supabase URL (for its signing keys) or a JWT secret is needed")
        self._secret = jwt_secret or None
        self._jwks = (
            jwt.PyJWKClient(
                f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True
            )
            if supabase_url
            else None
        )

    def verify(self, token: str) -> User:
        options: dict[str, Any] = {"require": ["exp", "sub"]}
        try:
            alg = jwt.get_unverified_header(token).get("alg")
            if alg == "HS256":
                if not self._secret:
                    raise UserTokenError("HS256 token, but no JWT secret is configured")
                claims = jwt.decode(
                    token,
                    self._secret,
                    algorithms=["HS256"],
                    audience="authenticated",
                    options=options,
                )
            else:
                if self._jwks is None:
                    raise UserTokenError("signed token, but no Supabase URL is configured")
                key = self._jwks.get_signing_key_from_jwt(token).key
                claims = jwt.decode(
                    token,
                    key,
                    algorithms=ASYMMETRIC_ALGS,
                    audience="authenticated",
                    options=options,
                )
        except jwt.PyJWTError as exc:
            raise UserTokenError(f"invalid user token: {type(exc).__name__}") from exc
        meta = claims.get("app_metadata") or {}
        if meta.get("removed"):
            raise UserTokenError("the account was removed")
        role = meta.get("role")
        if role not in ROLES:
            raise UserTokenError("the account has no dashboard role")
        email = str(claims.get("email") or claims["sub"]).strip().lower()[:100]
        return User(email=email, role=str(role))
