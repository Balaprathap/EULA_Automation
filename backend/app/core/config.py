"""Application configuration.

Every secret, provider, model name, URL, and limit is read from the environment.
Startup fails loudly and specifically when a required variable is missing, so a
misconfigured deployment can never silently fall back to an insecure default.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # cors_origins is the only complex field, and it is parsed by the
        # validator below. Without this, pydantic-settings would try to
        # JSON-decode it first and reject a plain comma-separated list.
        enable_decoding=False,
    )

    # --- Environment ---------------------------------------------------------
    environment: Environment = "development"
    log_level: str = "INFO"
    sentry_dsn: str | None = None

    # --- Supabase ------------------------------------------------------------
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    # Legacy only. Supabase now signs with ES256/RS256, verified via the project
    # JWKS endpoint. Set this ONLY if the project still issues HS256 tokens;
    # leaving it empty disables symmetric verification entirely, which is the
    # safer configuration for a project that has migrated to asymmetric keys.
    supabase_jwt_secret: str = ""
    supabase_storage_bucket: str = "documents"
    supabase_reports_bucket: str = "reports"

    # --- Database / queue ----------------------------------------------------
    database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"

    # --- Anthropic (text generation ONLY - there is no Anthropic embeddings
    #     API, so the embedding provider below is deliberately independent) ---
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_max_tokens: int = 4096
    anthropic_timeout_seconds: float = 120.0
    anthropic_max_retries: int = 4
    anthropic_input_cost_per_mtok: float = 3.00
    anthropic_cached_input_cost_per_mtok: float = 0.30
    anthropic_output_cost_per_mtok: float = 15.00

    # --- Groq chatbot ---------------------------------------------------------
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"
    groq_max_completion_tokens: int = 900
    groq_timeout_seconds: float = 45.0
    groq_reasoning_effort: Literal["low", "medium", "high"] = "low"

    # --- Embeddings ----------------------------------------------------------
    embedding_provider: Literal["openai", "voyage", "deterministic"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str | None = None
    embedding_dimensions: int = 1536
    embedding_batch_size: int = 64
    embedding_timeout_seconds: float = 60.0
    embedding_cost_per_mtok: float = 0.02

    # --- URLs / CORS ---------------------------------------------------------
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Limits --------------------------------------------------------------
    max_upload_mb: int = 10
    max_document_pages: int = 150
    max_tool_calls_per_category: int = 5
    retrieval_top_k: int = 8
    rate_limit_analyses_per_hour: int = 20
    rate_limit_requests_per_minute: int = 200

    # --- Report delivery by email --------------------------------------------
    # "console" writes a redacted line to the log and sends nothing. It is the
    # default so the feature degrades safely when no provider is configured.
    email_provider: Literal["console", "smtp", "resend"] = "console"
    email_from: str = "ClauseGuard <noreply@example.com>"
    email_reply_to: str | None = None
    email_max_attachment_mb: float = 8.0
    email_max_attempts: int = 3

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    resend_api_key: str | None = None

    report_signed_url_ttl_seconds: int = 900
    rate_limit_report_emails_per_hour: int = 10

    # --- AWS (optional, additive) --------------------------------------------
    # Both flags default to False: ClauseGuard runs entirely on Supabase storage
    # and the existing email providers until AWS is verified. Nothing about the
    # analysis pipeline depends on either.
    aws_region: str = "us-east-1"
    aws_s3_report_bucket: str | None = None
    aws_report_storage_enabled: bool = False
    aws_kms_key_id: str | None = None
    aws_ses_enabled: bool = False
    aws_ses_from_email: str | None = None
    aws_ses_configuration_set: str | None = None
    aws_report_url_ttl_seconds: int = 900
    aws_report_attachment_max_bytes: int = 8 * 1024 * 1024

    # -------------------------------------------------------------------------
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept a comma-separated string or a JSON array.

        Both forms appear in the wild - .env files favour the former, container
        orchestrators often emit the latter - so support both rather than
        failing on a formatting detail.
        """
        if isinstance(v, str):
            text = v.strip()
            if text.startswith("["):
                import json

                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "CORS_ORIGINS looks like a JSON array but could not be parsed. "
                        'Use either http://a.com,http://b.com or ["http://a.com"].'
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS JSON must be an array of strings.")
                return [str(o).strip() for o in parsed if str(o).strip()]
            return [o.strip() for o in text.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment in ("production", "staging")

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        missing: list[str] = []

        # Tests run entirely on fakes and must not require live infrastructure.
        if not self.is_test:
            required = {
                "DATABASE_URL": self.database_url,
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
                # SUPABASE_JWT_SECRET is deliberately NOT required. Supabase
                # signs access tokens asymmetrically (ES256/RS256) and those are
                # verified against the project JWKS, which needs no shared
                # secret. The secret is only consulted for legacy HS256 tokens;
                # leaving it unset simply means such tokens are refused.
                "REDIS_URL": self.redis_url,
            }
            missing += [name for name, value in required.items() if not value]

        if self.is_production:
            if not self.anthropic_api_key:
                missing.append("ANTHROPIC_API_KEY")
            if self.embedding_provider != "deterministic" and not self.embedding_api_key:
                missing.append("EMBEDDING_API_KEY")
            if self.embedding_provider == "deterministic":
                raise ValueError(
                    "EMBEDDING_PROVIDER=deterministic is a test-only provider and must "
                    "never be used in staging or production."
                )
            if "*" in self.cors_origins:
                raise ValueError(
                    "CORS_ORIGINS must be an explicit allowlist in production; "
                    "the '*' wildcard is not permitted."
                )
            if not self.cors_origins:
                missing.append("CORS_ORIGINS")

        if self.aws_report_storage_enabled and not self.aws_s3_report_bucket:
            missing.append("AWS_S3_REPORT_BUCKET (required when AWS_REPORT_STORAGE_ENABLED=true)")
        if self.aws_ses_enabled and not self.aws_ses_from_email:
            missing.append("AWS_SES_FROM_EMAIL (required when AWS_SES_ENABLED=true)")

        if missing:
            raise ValueError(
                "Missing required environment variables: "
                + ", ".join(sorted(set(missing)))
                + ". Copy .env.example to .env and fill these in."
            )

        if self.embedding_dimensions <= 0:
            raise ValueError("EMBEDDING_DIMENSIONS must be a positive integer.")
        return self


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
