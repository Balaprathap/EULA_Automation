"""JWKS retrieval and caching for Supabase asymmetric access tokens.

Supabase now signs access tokens with asymmetric keys (ES256 by default, RS256
for some projects) and publishes the public keys at

    {SUPABASE_URL}/auth/v1/.well-known/jwks.json

The key set changes only when keys are rotated, so it is cached in memory with a
TTL rather than fetched per request. A `kid` that is not in the cache triggers at
most one refresh, rate-limited so a token with a bogus `kid` cannot be used to
hammer the auth endpoint.

Nothing here selects an algorithm from the token. The caller supplies an explicit
allowlist; this module only maps `kid` to a public key.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Keys rotate rarely; an hour bounds staleness without adding request latency.
DEFAULT_TTL_SECONDS = 3600
# Floor between refreshes triggered by an unknown kid.
MIN_REFRESH_INTERVAL_SECONDS = 30
FETCH_TIMEOUT_SECONDS = 5.0


class JWKSError(RuntimeError):
    """The key set could not be retrieved or did not contain the requested key."""


class JWKSCache:
    """Thread-safe, TTL-bounded cache of a project's JSON Web Key Set."""

    def __init__(
        self,
        supabase_url: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: float = FETCH_TIMEOUT_SECONDS,
    ) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float = 0.0
        self._last_attempt: float = 0.0
        self._lock = threading.Lock()

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def issuer(self) -> str:
        """The issuer Supabase puts in the `iss` claim."""
        return f"{self.supabase_url}/auth/v1"

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._fetched_at) > self.ttl_seconds

    def _fetch(self) -> dict[str, dict[str, Any]]:
        try:
            response = httpx.get(self.jwks_url, timeout=self.timeout)
            response.raise_for_status()
            document = response.json()
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad. Anything at all that goes wrong here - DNS,
            # TLS, a proxy misconfiguration, malformed JSON - must become a
            # JWKSError so the request ends in a clean 401 rather than a 500
            # that leaks a stack trace.
            raise JWKSError(f"Could not retrieve the JWKS from {self.jwks_url}") from exc

        keys = document.get("keys")
        if not isinstance(keys, list) or not keys:
            raise JWKSError("The JWKS document contained no keys.")

        by_kid: dict[str, dict[str, Any]] = {}
        for key in keys:
            if isinstance(key, dict) and key.get("kid"):
                by_kid[str(key["kid"])] = key

        if not by_kid:
            raise JWKSError("The JWKS document contained no keys with a kid.")
        return by_kid

    def _refresh_locked(self) -> None:
        self._last_attempt = time.monotonic()
        keys = self._fetch()
        self._keys = keys
        self._fetched_at = time.monotonic()
        logger.info("jwks refreshed", extra={"key_count": len(keys)})

    def get_key(self, kid: str, *, allow_refresh: bool = True) -> dict[str, Any]:
        """Return the JWK for `kid`, refreshing at most once if it is unknown."""
        if not kid:
            raise JWKSError("The token header did not include a kid.")

        with self._lock:
            if not self._keys or self._is_stale():
                self._refresh_locked()

            key = self._keys.get(kid)
            if key is not None:
                return key

            # Unknown kid: the keys may have just rotated. Refresh once, but not
            # more often than MIN_REFRESH_INTERVAL_SECONDS, so a forged kid
            # cannot be used to generate outbound traffic on every request.
            if (
                allow_refresh
                and (time.monotonic() - self._last_attempt) > MIN_REFRESH_INTERVAL_SECONDS
            ):
                self._refresh_locked()
                key = self._keys.get(kid)
                if key is not None:
                    return key

        raise JWKSError(f"No signing key matches kid {kid!r}.")

    def clear(self) -> None:
        with self._lock:
            self._keys = {}
            self._fetched_at = 0.0
            self._last_attempt = 0.0

    def seed(self, keys: list[dict[str, Any]]) -> None:
        """Populate the cache directly. Used by tests; never by request paths."""
        with self._lock:
            self._keys = {str(k["kid"]): k for k in keys if k.get("kid")}
            now = time.monotonic()
            self._fetched_at = now
            # Arm the rate limiter too: freshly-loaded keys count as a recent
            # attempt, so an unknown kid cannot immediately force a refetch.
            self._last_attempt = now


_caches: dict[str, JWKSCache] = {}
_caches_lock = threading.Lock()


def get_jwks_cache(supabase_url: str) -> JWKSCache:
    """One cache per Supabase project URL, shared across requests."""
    key = supabase_url.rstrip("/")
    with _caches_lock:
        cache = _caches.get(key)
        if cache is None:
            cache = JWKSCache(key)
            _caches[key] = cache
        return cache


def reset_jwks_caches() -> None:
    with _caches_lock:
        _caches.clear()
