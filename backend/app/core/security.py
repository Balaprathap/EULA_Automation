"""Supabase JWT verification and security headers.

The API trusts nothing from the client but a signature it can verify itself.
Tokens are checked for signature, expiry, and audience before any handler runs;
the organization is then resolved from the database, never from a client-supplied
header or a claim the user could set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.errors import Unauthenticated
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str
    org_id: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role in ("owner", "admin")

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


# Explicit allowlist. The algorithm is NEVER taken from the token header alone -
# `alg` in an unverified header is attacker-controlled, and honouring it enables
# both the alg=none bypass and the RS256->HS256 key-confusion attack.
ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")
LEGACY_SYMMETRIC_ALGORITHMS = ("HS256",)
SUPPORTED_ALGORITHMS = ASYMMETRIC_ALGORITHMS + LEGACY_SYMMETRIC_ALGORITHMS

DEFAULT_AUDIENCE = "authenticated"


def _reject(exc: Exception, reason: str) -> Unauthenticated:
    """Log the precise cause, return a deliberately vague message.

    A specific message would tell an attacker which part of a forged token to
    fix next.
    """
    logger.warning("jwt rejected", extra={"error_type": type(exc).__name__, "reason": reason})
    return Unauthenticated("The access token is invalid.")


def decode_supabase_jwt(
    token: str,
    secret: str = "",
    *,
    audience: str = DEFAULT_AUDIENCE,
    supabase_url: str = "",
    allow_legacy_hs256: bool = True,
) -> dict[str, Any]:
    """Verify and decode a Supabase access token.

    Supabase issues asymmetric tokens (ES256, sometimes RS256) whose public keys
    are published as a JWKS. Legacy projects still issue HS256 tokens signed with
    the project JWT secret, so both are supported.

    Verified on every path: signature, expiry, audience, issuer, and the presence
    of `exp` and `sub`.

    Args:
        token: The raw bearer token.
        secret: SUPABASE_JWT_SECRET. Only used for legacy HS256 tokens.
        audience: Expected `aud` claim.
        supabase_url: Project URL. Required for asymmetric tokens; the issuer is
            derived from it as ``{supabase_url}/auth/v1``.
        allow_legacy_hs256: Set False to refuse symmetric tokens entirely once a
            project has fully migrated to asymmetric keys.
    """
    import jwt

    if not token:
        raise Unauthenticated("An access token is required.")

    # Read the header WITHOUT verifying, purely to discover which key to use.
    # Nothing here is trusted: the algorithm is checked against our allowlist
    # below, and the signature is verified with a key we choose.
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise _reject(exc, "malformed header") from exc

    algorithm = header.get("alg")

    # Explicitly refuse unsigned tokens before anything else.
    if not algorithm or str(algorithm).lower() == "none":
        raise _reject(ValueError("alg=none"), "unsigned token")

    if algorithm not in SUPPORTED_ALGORITHMS:
        raise _reject(ValueError(algorithm), f"unsupported algorithm {algorithm}")

    issuer = f"{supabase_url.rstrip('/')}/auth/v1" if supabase_url else None

    # Pin verification to exactly the one algorithm the chosen key supports, so
    # a key of one type can never be used to validate a token of another.
    if algorithm in ASYMMETRIC_ALGORITHMS:
        if not supabase_url:
            raise _reject(
                ValueError("SUPABASE_URL missing"),
                "asymmetric token but SUPABASE_URL is not configured",
            )
        from app.core.jwks import JWKSError, get_jwks_cache

        try:
            jwk = get_jwks_cache(supabase_url).get_key(header.get("kid", ""))
        except JWKSError as exc:
            raise _reject(exc, "no matching signing key") from exc

        # Guard against a JWKS entry whose own `alg` disagrees with the token.
        key_algorithm = jwk.get("alg")
        if key_algorithm and key_algorithm != algorithm:
            raise _reject(
                ValueError(key_algorithm), "token algorithm does not match the signing key"
            )
        try:
            key: Any = jwt.PyJWK(jwk).key
        except Exception as exc:  # noqa: BLE001 - malformed JWK entry
            raise _reject(exc, "unusable signing key") from exc
        algorithms = [algorithm]
    else:
        if not allow_legacy_hs256:
            raise _reject(ValueError("HS256"), "legacy symmetric tokens are disabled")
        if not secret:
            raise Unauthenticated("The server is not configured to verify access tokens.")
        key = secret
        algorithms = list(LEGACY_SYMMETRIC_ALGORITHMS)

    try:
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer,
            options={
                "require": ["exp", "sub"],
                "verify_exp": True,
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": bool(issuer),
            },
        )
    except jwt.ExpiredSignatureError as exc:
        logger.info("jwt expired")
        raise Unauthenticated("Your session has expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise _reject(exc, "verification failed") from exc


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise Unauthenticated("An Authorization header is required.")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise Unauthenticated("The Authorization header must use the Bearer scheme.")
    return parts[1].strip()


# Paths that render the interactive API documentation. Swagger UI and ReDoc are
# loaded from a CDN by FastAPI's default templates, so the strict application CSP
# blocks them. These are the only routes that ever receive a relaxed policy.
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})

# CDN origins used by FastAPI's bundled documentation templates.
SWAGGER_CDN = "https://cdn.jsdelivr.net"
DOCS_FAVICON = "https://fastapi.tiangolo.com"
GOOGLE_FONTS_CSS = "https://fonts.googleapis.com"
GOOGLE_FONTS_FILES = "https://fonts.gstatic.com"


def _base_headers(*, production: bool) -> dict[str, str]:
    """Security headers that apply to every response, documentation or not."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "X-Permitted-Cross-Domain-Policies": "none",
    }
    if production:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return headers


def _connect_src(app_origin: str, api_origin: str) -> str:
    return " ".join(sorted({"'self'", app_origin, api_origin, "https://*.supabase.co"}))


def application_csp(app_origin: str, api_origin: str) -> str:
    """The strict policy for every normal API route.

    No CDN origins and no inline script execution.
    """
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            f"connect-src {_connect_src(app_origin, api_origin)}",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


def docs_csp(app_origin: str, api_origin: str) -> str:
    """A documentation-specific policy, applied ONLY to DOCS_PATHS and ONLY
    outside production.

    Swagger UI and ReDoc need three things the application policy forbids:
      * the jsDelivr bundle for swagger-ui-bundle.js / redoc.standalone.js,
      * 'unsafe-inline' script execution, because FastAPI's template emits an
        inline initialiser (there is no nonce hook in get_swagger_ui_html),
      * Google Fonts and blob: workers, which ReDoc uses.

    The clickjacking and injection protections that do not affect rendering -
    frame-ancestors, base-uri, form-action, object-src - remain as strict as on
    every other route.
    """
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'unsafe-inline' {SWAGGER_CDN}",
            f"style-src 'self' 'unsafe-inline' {SWAGGER_CDN} {GOOGLE_FONTS_CSS}",
            f"img-src 'self' data: blob: {SWAGGER_CDN} {DOCS_FAVICON}",
            f"font-src 'self' data: {SWAGGER_CDN} {GOOGLE_FONTS_FILES}",
            f"connect-src {_connect_src(app_origin, api_origin)}",
            "worker-src 'self' blob:",
            "child-src 'self' blob:",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


def is_docs_path(path: str) -> bool:
    return path.rstrip("/") in DOCS_PATHS or path in DOCS_PATHS


def security_headers(
    app_origin: str,
    api_origin: str,
    *,
    production: bool,
    path: str = "",
) -> dict[str, str]:
    """Security headers for one response.

    Documentation routes receive a relaxed CSP so Swagger UI and ReDoc can
    render, but only outside production. In production every route - including
    /docs - keeps the strict application policy, so the documentation UI will
    not render there. That is deliberate: a public production deployment should
    either disable the docs routes or self-host the assets rather than allow
    'unsafe-inline' scripts from a CDN.

    Every other security header is identical on both paths.
    """
    headers = _base_headers(production=production)
    relax_for_docs = path and is_docs_path(path) and not production
    headers["Content-Security-Policy"] = (
        docs_csp(app_origin, api_origin)
        if relax_for_docs
        else application_csp(app_origin, api_origin)
    )
    return headers
