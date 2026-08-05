"""JWT verification: asymmetric (ES256/RS256) via JWKS, plus legacy HS256.

Regression context: Supabase moved to asymmetric access tokens, and the backend
verified only HS256 with SUPABASE_JWT_SECRET. Every valid login was rejected
with InvalidAlgorithmError -> 401.

Keys here are generated per test run; nothing is hard-coded or fetched.
"""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.core.errors import Unauthenticated
from app.core.jwks import JWKSCache, JWKSError, get_jwks_cache, reset_jwks_caches
from app.core.security import (
    ASYMMETRIC_ALGORITHMS,
    SUPPORTED_ALGORITHMS,
    decode_supabase_jwt,
)

SUPABASE_URL = "https://testproj.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"
AUDIENCE = "authenticated"
SECRET = "legacy-super-secret-value-for-hs256"
SUBJECT = "11111111-1111-1111-1111-111111111111"


def jwk_from_public_key(public_key, kid: str, alg: str) -> dict:
    """Serialise a public key into a JWKS entry, as Supabase publishes it."""
    return json.loads(jwt.algorithms.get_default_algorithms()[alg].to_jwk(public_key)) | {
        "kid": kid,
        "alg": alg,
        "use": "sig",
    }


@pytest.fixture(autouse=True)
def _clear_caches():
    reset_jwks_caches()
    yield
    reset_jwks_caches()


@pytest.fixture(scope="module")
def es256_key():
    private = ec.generate_private_key(ec.SECP256R1())
    return private, jwk_from_public_key(private.public_key(), "es256-key-1", "ES256")


@pytest.fixture(scope="module")
def rs256_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, jwk_from_public_key(private.public_key(), "rs256-key-1", "RS256")


@pytest.fixture
def seeded_cache(es256_key, rs256_key):
    """Seed the JWKS cache so no network call is made."""
    cache = get_jwks_cache(SUPABASE_URL)
    cache.seed([es256_key[1], rs256_key[1]])
    return cache


def make_token(private_key, kid, alg, **overrides):
    now = int(time.time())
    claims = {
        "sub": SUBJECT,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "exp": now + 3600,
        "iat": now,
        "email": "user@example.com",
        "role": "authenticated",
    }
    claims.update(overrides)
    for key in [k for k, v in claims.items() if v is None]:
        del claims[key]
    return jwt.encode(claims, private_key, algorithm=alg, headers={"kid": kid})


def decode(token, **kwargs):
    params = {"secret": SECRET, "supabase_url": SUPABASE_URL}
    params.update(kwargs)
    return decode_supabase_jwt(token, **params)


class TestAsymmetricTokens:
    def test_valid_es256_token_is_accepted(self, es256_key, seeded_cache):
        private, jwk = es256_key
        claims = decode(make_token(private, jwk["kid"], "ES256"))
        assert claims["sub"] == SUBJECT
        assert claims["iss"] == ISSUER

    def test_valid_rs256_token_is_accepted(self, rs256_key, seeded_cache):
        private, jwk = rs256_key
        assert decode(make_token(private, jwk["kid"], "RS256"))["sub"] == SUBJECT

    def test_es256_works_without_the_legacy_secret(self, es256_key, seeded_cache):
        """Asymmetric verification must not depend on SUPABASE_JWT_SECRET."""
        private, jwk = es256_key
        token = make_token(private, jwk["kid"], "ES256")
        assert decode(token, secret="")["sub"] == SUBJECT

    def test_correct_key_is_selected_by_kid(self, es256_key, rs256_key, seeded_cache):
        es_private, es_jwk = es256_key
        rs_private, rs_jwk = rs256_key
        assert decode(make_token(es_private, es_jwk["kid"], "ES256"))["sub"] == SUBJECT
        assert decode(make_token(rs_private, rs_jwk["kid"], "RS256"))["sub"] == SUBJECT

    def test_token_signed_by_an_unknown_key_is_rejected(self, es256_key, seeded_cache):
        """Right kid, wrong private key - the signature must not verify."""
        _, jwk = es256_key
        impostor = ec.generate_private_key(ec.SECP256R1())
        with pytest.raises(Unauthenticated):
            decode(make_token(impostor, jwk["kid"], "ES256"))


class TestLegacyHs256:
    def test_valid_hs256_token_is_accepted(self):
        now = int(time.time())
        token = jwt.encode(
            {"sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},
            SECRET,
            algorithm="HS256",
        )
        assert decode(token)["sub"] == SUBJECT

    def test_hs256_with_the_wrong_secret_is_rejected(self):
        now = int(time.time())
        token = jwt.encode(
            {"sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},
            "a-completely-different-secret",
            algorithm="HS256",
        )
        with pytest.raises(Unauthenticated):
            decode(token)

    def test_hs256_can_be_disabled_once_migration_completes(self):
        now = int(time.time())
        token = jwt.encode(
            {"sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},
            SECRET,
            algorithm="HS256",
        )
        assert decode(token)["sub"] == SUBJECT  # allowed by default
        with pytest.raises(Unauthenticated):
            decode(token, allow_legacy_hs256=False)


class TestAlgorithmConfusion:
    """The algorithm must never be chosen by the untrusted token header."""

    def test_alg_none_is_rejected(self):
        now = int(time.time())
        token = jwt.encode(
            {"sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},
            key="",
            algorithm="none",
        )
        with pytest.raises(Unauthenticated):
            decode(token)

    @pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
    def test_unsupported_algorithm_is_rejected(self, algorithm, seeded_cache):
        """Correctly signed, but the algorithm is not on the allowlist."""
        now = int(time.time())
        token = jwt.encode(
            {"sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},
            SECRET,
            algorithm=algorithm,
        )
        with pytest.raises(Unauthenticated):
            decode(token)

    def test_allowlist_contents(self):
        assert set(SUPPORTED_ALGORITHMS) == {"ES256", "RS256", "HS256"}
        assert set(ASYMMETRIC_ALGORITHMS) == {"ES256", "RS256"}
        assert "none" not in SUPPORTED_ALGORITHMS

    def test_key_confusion_is_blocked(self, rs256_key, seeded_cache):
        """An RS256 public key must not be usable as an HS256 shared secret."""
        _, jwk = rs256_key
        now = int(time.time())
        forged = jwt.encode(
            {"sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},
            SECRET,
            algorithm="HS256",
            headers={"kid": jwk["kid"]},
        )
        # Signed with the real legacy secret, so it verifies as HS256 - the point
        # is that the RS256 JWKS entry was NOT consulted for an HS256 token.
        assert decode(forged)["sub"] == SUBJECT
        with pytest.raises(Unauthenticated):
            decode(forged, secret="wrong")

    def test_malformed_token_is_rejected(self):
        for bad in ("", "not-a-token", "a.b", "a.b.c.d"):
            with pytest.raises(Unauthenticated):
                decode(bad)


class TestClaimValidation:
    def test_expired_token_is_rejected(self, es256_key, seeded_cache):
        private, jwk = es256_key
        now = int(time.time())
        token = make_token(private, jwk["kid"], "ES256", exp=now - 10, iat=now - 3600)
        with pytest.raises(Unauthenticated, match="expired"):
            decode(token)

    def test_invalid_issuer_is_rejected(self, es256_key, seeded_cache):
        private, jwk = es256_key
        token = make_token(private, jwk["kid"], "ES256", iss="https://evil.example/auth/v1")
        with pytest.raises(Unauthenticated):
            decode(token)

    def test_issuer_is_derived_from_supabase_url(self, es256_key, seeded_cache):
        private, jwk = es256_key
        token = make_token(private, jwk["kid"], "ES256")
        assert decode(token)["iss"] == f"{SUPABASE_URL}/auth/v1"
        # A token for a different project must not be accepted here.
        other = make_token(private, jwk["kid"], "ES256", iss="https://other.supabase.co/auth/v1")
        with pytest.raises(Unauthenticated):
            decode(other)

    def test_trailing_slash_in_supabase_url_is_tolerated(self, es256_key, seeded_cache):
        private, jwk = es256_key
        token = make_token(private, jwk["kid"], "ES256")
        assert decode(token, supabase_url=SUPABASE_URL + "/")["sub"] == SUBJECT

    def test_invalid_audience_is_rejected(self, es256_key, seeded_cache):
        private, jwk = es256_key
        token = make_token(private, jwk["kid"], "ES256", aud="some-other-audience")
        with pytest.raises(Unauthenticated):
            decode(token)

    def test_missing_sub_is_rejected(self, es256_key, seeded_cache):
        private, jwk = es256_key
        token = make_token(private, jwk["kid"], "ES256", sub=None)
        with pytest.raises(Unauthenticated):
            decode(token)

    def test_missing_exp_is_rejected(self, es256_key, seeded_cache):
        private, jwk = es256_key
        token = make_token(private, jwk["kid"], "ES256", exp=None)
        with pytest.raises(Unauthenticated):
            decode(token)


class TestKidHandling:
    def test_unknown_kid_is_rejected(self, es256_key, seeded_cache):
        private, _ = es256_key
        with pytest.raises(Unauthenticated):
            decode(make_token(private, "a-kid-that-does-not-exist", "ES256"))

    def test_missing_kid_is_rejected(self, es256_key, seeded_cache):
        private, _ = es256_key
        now = int(time.time())
        token = jwt.encode(
            {"sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},
            private,
            algorithm="ES256",
        )
        with pytest.raises(Unauthenticated):
            decode(token)

    def test_asymmetric_token_without_supabase_url_is_rejected(self, es256_key, seeded_cache):
        private, jwk = es256_key
        with pytest.raises(Unauthenticated):
            decode(make_token(private, jwk["kid"], "ES256"), supabase_url="")


class TestJwksCache:
    def test_jwks_url_and_issuer_are_derived_from_the_project_url(self):
        cache = JWKSCache("https://abc.supabase.co/")
        assert cache.jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
        assert cache.issuer == "https://abc.supabase.co/auth/v1"

    def test_cache_is_reused_per_project(self):
        assert get_jwks_cache(SUPABASE_URL) is get_jwks_cache(SUPABASE_URL + "/")
        assert get_jwks_cache(SUPABASE_URL) is not get_jwks_cache("https://other.supabase.co")

    def test_seeded_keys_are_served_without_network_access(self, es256_key, monkeypatch):
        _, jwk = es256_key
        cache = JWKSCache(SUPABASE_URL)
        cache.seed([jwk])

        def explode(*_a, **_kw):
            raise AssertionError("the cache must not fetch when it holds a fresh key")

        monkeypatch.setattr(cache, "_fetch", explode)
        assert cache.get_key(jwk["kid"])["kid"] == jwk["kid"]

    def test_unknown_kid_refresh_is_rate_limited(self, es256_key):
        """A forged kid must not cause an outbound fetch on every request."""
        _, jwk = es256_key
        cache = JWKSCache(SUPABASE_URL)
        cache.seed([jwk])
        calls = {"n": 0}

        def counting_fetch():
            calls["n"] += 1
            return {jwk["kid"]: jwk}

        cache._fetch = counting_fetch  # type: ignore[method-assign]
        for _ in range(10):
            with pytest.raises(JWKSError):
                cache.get_key("unknown-kid")
        assert calls["n"] == 0, "seeded keys are fresh, so no refetch should occur"

    def test_rotated_key_is_picked_up_after_the_refresh_interval(self, es256_key, rs256_key):
        """Rate limiting must not permanently block legitimate key rotation."""
        import app.core.jwks as jwks_module

        _, old_jwk = es256_key
        _, new_jwk = rs256_key
        cache = JWKSCache(SUPABASE_URL)
        cache.seed([old_jwk])
        cache._fetch = lambda: {new_jwk["kid"]: new_jwk}  # type: ignore[method-assign]

        # Immediately after seeding, the limiter suppresses the refresh.
        with pytest.raises(JWKSError):
            cache.get_key(new_jwk["kid"])

        # Once the interval has elapsed, the new key is fetched and served.
        cache._last_attempt -= jwks_module.MIN_REFRESH_INTERVAL_SECONDS + 1
        assert cache.get_key(new_jwk["kid"])["kid"] == new_jwk["kid"]

    def test_any_fetch_failure_becomes_a_clean_jwks_error(self, monkeypatch):
        """Not just HTTP errors - a proxy or TLS misconfiguration must not 500."""
        import httpx

        for boom in (
            httpx.ConnectError("dns failure"),
            ImportError("missing socks extra"),
            RuntimeError("unexpected"),
        ):
            cache = JWKSCache(SUPABASE_URL)

            def raise_it(*_args, _error=boom, **_kwargs):
                raise _error

            monkeypatch.setattr(httpx, "get", raise_it)
            with pytest.raises(JWKSError):
                cache.get_key("any-kid")

    def test_empty_kid_is_rejected(self):
        with pytest.raises(JWKSError):
            JWKSCache(SUPABASE_URL).get_key("")

    def test_fetch_failure_surfaces_as_jwks_error(self, monkeypatch):
        import httpx

        cache = JWKSCache(SUPABASE_URL)

        def boom(*_a, **_kw):
            raise httpx.ConnectError("dns failure")

        monkeypatch.setattr(httpx, "get", boom)
        with pytest.raises(JWKSError):
            cache.get_key("any-kid")

    def test_malformed_jwks_document_is_rejected(self, monkeypatch):
        import httpx

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"keys": []}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse())
        with pytest.raises(JWKSError):
            JWKSCache(SUPABASE_URL).get_key("any-kid")


class TestSafeErrorResponses:
    def test_rejection_message_is_generic(self, es256_key, seeded_cache):
        private, _ = es256_key
        with pytest.raises(Unauthenticated) as excinfo:
            decode(make_token(private, "unknown-kid", "ES256"))
        message = str(excinfo.value)
        assert message == "The access token is invalid."
        for leak in ("kid", "JWKS", "ES256", "signature"):
            assert leak not in message

    def test_expiry_message_is_actionable_but_still_safe(self, es256_key, seeded_cache):
        private, jwk = es256_key
        now = int(time.time())
        token = make_token(private, jwk["kid"], "ES256", exp=now - 10, iat=now - 100)
        with pytest.raises(Unauthenticated) as excinfo:
            decode(token)
        assert "expired" in str(excinfo.value).lower()

    def test_rejections_use_the_401_error_type(self, es256_key, seeded_cache):
        private, _ = es256_key
        with pytest.raises(Unauthenticated) as excinfo:
            decode(make_token(private, "nope", "ES256"))
        assert excinfo.value.status_code == 401
        assert excinfo.value.code == "AUTHENTICATION_REQUIRED"
