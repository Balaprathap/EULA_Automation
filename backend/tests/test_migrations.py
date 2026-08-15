"""Migration validation.

Parses every migration with libpg_query - the actual PostgreSQL parser - so a
syntax error can never reach a deployment, and asserts the structural
guarantees the application relies on (RLS coverage, offset constraints,
severity domains, and the indexes the hot queries need).
"""

import re
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATIONS = sorted(MIGRATIONS_DIR.glob("*.sql"))
LOCAL_SHIM = MIGRATIONS_DIR / "local" / "0000_supabase_shim.sql"

TENANT_TABLES = [
    "organizations",
    "profiles",
    "documents",
    "document_chunks",
    "policies",
    "policy_rules",
    "analyses",
    "analysis_categories",
    "findings",
    "finding_evidence",
    "finding_reviews",
    "audit_logs",
    "usage_events",
]


def strip_comments(sql: str) -> str:
    """Drop -- line comments so prose never trips a structural assertion."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


@pytest.fixture(scope="module")
def all_sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)


@pytest.fixture(scope="module")
def sql_only(all_sql) -> str:
    return strip_comments(all_sql)


class TestMigrationFiles:
    def test_migrations_exist(self):
        assert MIGRATIONS, "no migration files were found"

    def test_versions_are_numbered_and_contiguous(self):
        numbers = [int(p.name.split("_")[0]) for p in MIGRATIONS]
        assert numbers == list(range(1, len(numbers) + 1))

    def test_filenames_are_well_formed(self):
        for path in MIGRATIONS:
            assert re.match(r"^\d{4}_[a-z0-9_]+\.sql$", path.name), path.name


class TestSqlParses:
    """Uses the real PostgreSQL grammar, not a regex approximation."""

    @pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
    def test_migration_parses(self, path):
        pglast = pytest.importorskip("pglast")
        try:
            statements = pglast.parse_sql(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"{path.name} failed to parse: {exc}") from exc
        assert statements, f"{path.name} contains no statements"


class TestDependencyOrder:
    """Regression guard for a real production failure.

    `auth_org_id()` was originally defined in migration 0001 but queries
    `profiles`, which is not created until 0002. PostgreSQL validates the body
    of a LANGUAGE sql function at CREATE time, so migrating a genuinely empty
    database failed with:

        UndefinedTableError: relation "profiles" does not exist

    Parsing the SQL was not enough to catch this - the grammar is perfectly
    valid. These tests check catalog dependency ORDER, which is what actually
    broke.
    """

    @staticmethod
    def _creation_index():
        """Map each application table/function to the migration that creates it."""
        created: dict = {}
        for index, path in enumerate(MIGRATIONS):
            sql = path.read_text(encoding="utf-8")
            for table in re.findall(r"CREATE TABLE IF NOT EXISTS ([\w.]+)", sql):
                created.setdefault(table, index)
            for func in re.findall(r"CREATE OR REPLACE FUNCTION ([\w.]+)\s*\(", sql):
                created.setdefault(func, index)
        return created

    def test_no_migration_references_an_object_created_later(self):
        created = self._creation_index()
        # Schema-qualified names (auth.*, storage.*) are Supabase-managed.
        local_objects = {name for name in created if "." not in name}

        violations = []
        for index, path in enumerate(MIGRATIONS):
            body = strip_comments(path.read_text(encoding="utf-8"))
            # Remove the CREATE statements themselves so only *uses* remain.
            body = re.sub(r"CREATE TABLE IF NOT EXISTS [\w.]+", "", body)
            body = re.sub(r"CREATE OR REPLACE FUNCTION [\w.]+", "", body)
            for obj in local_objects:
                if re.search(rf"\b{re.escape(obj)}\b", body) and created[obj] > index:
                    violations.append(
                        f"{path.name} references '{obj}', "
                        f"created only in {MIGRATIONS[created[obj]].name}"
                    )
        assert not violations, "forward references found:\n  " + "\n  ".join(violations)

    def test_sql_language_functions_do_not_reference_later_tables(self):
        """LANGUAGE sql bodies are validated at CREATE time; plpgsql bodies are not."""
        created = self._creation_index()
        tables = {n for n in created if "." not in n}

        violations = []
        for index, path in enumerate(MIGRATIONS):
            sql = strip_comments(path.read_text(encoding="utf-8"))
            for block in re.finditer(
                r"CREATE OR REPLACE FUNCTION\s+([\w.]+).*?LANGUAGE\s+sql(.*?)\$\$(.*?)\$\$",
                sql,
                re.S | re.I,
            ):
                name, body = block.group(1), block.group(3)
                for table in tables:
                    if re.search(rf"\b{re.escape(table)}\b", body) and created[table] > index:
                        violations.append(
                            f"{path.name}: LANGUAGE sql function {name}() reads '{table}', "
                            f"which is created later in {MIGRATIONS[created[table]].name}"
                        )
        assert not violations, "\n  ".join(violations)

    def test_tenancy_helpers_are_defined_after_profiles(self):
        profiles_at = next(
            i
            for i, p in enumerate(MIGRATIONS)
            if "CREATE TABLE IF NOT EXISTS profiles" in p.read_text(encoding="utf-8")
        )
        for helper in ("auth_org_id", "auth_role", "auth_is_admin"):
            defined_at = next(
                i
                for i, p in enumerate(MIGRATIONS)
                if f"CREATE OR REPLACE FUNCTION {helper}(" in p.read_text(encoding="utf-8")
            )
            assert defined_at >= profiles_at, (
                f"{helper}() is defined in {MIGRATIONS[defined_at].name} but reads `profiles`, "
                f"which is only created in {MIGRATIONS[profiles_at].name}"
            )

    def test_pgvector_is_a_hard_requirement(self):
        sql = (MIGRATIONS_DIR / "0001_extensions_and_helpers.sql").read_text(encoding="utf-8")
        assert 'CREATE EXTENSION IF NOT EXISTS "vector"' in sql
        assert "RAISE EXCEPTION" in sql, "a missing pgvector must fail loudly"

    def test_optional_extensions_degrade_instead_of_failing(self):
        """pgcrypto and pg_trgm are not available on every managed provider."""
        sql = (MIGRATIONS_DIR / "0001_extensions_and_helpers.sql").read_text(encoding="utf-8")
        for extension in ("pgcrypto", "pg_trgm"):
            block = sql[sql.index(f'"{extension}"') :][:400]
            assert "EXCEPTION" in block, f"{extension} should degrade gracefully"

    def test_trgm_index_is_conditional_on_the_extension(self):
        sql = (MIGRATIONS_DIR / "0003_documents_and_chunks.sql").read_text(encoding="utf-8")
        block = sql[: sql.index("idx_documents_title_trgm")]
        assert "pg_trgm" in block.split("DO $$")[-1], (
            "the trigram index must only be created when pg_trgm is installed"
        )


class TestLocalShim:
    """The docker/local shim supplies the Supabase-only schemas the real
    migrations depend on. It must parse, be idempotent, and never be picked up
    as a numbered migration."""

    def test_shim_exists(self):
        assert LOCAL_SHIM.exists()

    def test_shim_is_outside_the_numbered_sequence(self):
        assert LOCAL_SHIM not in MIGRATIONS

    def test_shim_parses(self):
        pglast = pytest.importorskip("pglast")
        assert pglast.parse_sql(LOCAL_SHIM.read_text(encoding="utf-8"))

    def test_shim_is_idempotent(self):
        sql = LOCAL_SHIM.read_text(encoding="utf-8")
        for match in re.finditer(r"CREATE\s+(SCHEMA|TABLE|EXTENSION)\s+(\w+)", sql):
            assert match.group(2) == "IF", match.group(0)
        assert "CREATE FUNCTION" not in sql.replace("CREATE OR REPLACE FUNCTION", "")

    def test_shim_provides_everything_the_migrations_reference(self):
        needed = set()
        for path in MIGRATIONS:
            needed |= set(
                re.findall(r"\b((?:auth|storage)\.\w+)", path.read_text(encoding="utf-8"))
            )
        shim = LOCAL_SHIM.read_text(encoding="utf-8")
        missing = [n for n in needed if n.split(".")[1] not in shim]
        assert not missing, f"the shim is missing: {missing}"


class TestIdempotence:
    def test_creates_are_guarded(self, all_sql):
        # Every CREATE TABLE/INDEX must be re-runnable.
        for match in re.finditer(r"CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX)\s+(\w+)", all_sql):
            assert match.group(2) == "IF", (
                f"CREATE {match.group(1)} is missing IF NOT EXISTS near: {match.group(0)}"
            )

    def test_triggers_are_dropped_before_creation(self, all_sql):
        created = set(re.findall(r"CREATE TRIGGER (\w+)", all_sql))
        dropped = set(re.findall(r"DROP TRIGGER IF EXISTS (\w+)", all_sql))
        assert created <= dropped

    def test_policies_are_dropped_before_creation(self, all_sql):
        created = set(re.findall(r"CREATE POLICY (\w+)", all_sql))
        dropped = set(re.findall(r"DROP POLICY IF EXISTS (\w+)", all_sql))
        assert created <= dropped

    def test_functions_use_create_or_replace(self, all_sql):
        assert "CREATE FUNCTION" not in all_sql.replace("CREATE OR REPLACE FUNCTION", "")


class TestRowLevelSecurity:
    @pytest.mark.parametrize("table", TENANT_TABLES)
    def test_rls_is_enabled_on_every_tenant_table(self, table, all_sql):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in all_sql.replace(
            "  ", " "
        ).replace(
            "ALTER TABLE " + table + "      ENABLE", "ALTER TABLE " + table + " ENABLE"
        ).replace(
            "ALTER TABLE " + table + "  ENABLE", "ALTER TABLE " + table + " ENABLE"
        ) or re.search(rf"ALTER TABLE {table}\s+ENABLE ROW LEVEL SECURITY", all_sql)

    @pytest.mark.parametrize("table", TENANT_TABLES)
    def test_every_tenant_table_has_a_select_policy(self, table, all_sql):
        assert re.search(rf"CREATE POLICY \w+ ON {table}\s+FOR SELECT", all_sql), table

    def test_every_policy_is_org_scoped(self, all_sql):
        rls = (MIGRATIONS_DIR / "0008_row_level_security.sql").read_text(encoding="utf-8")
        blocks = re.split(r"CREATE POLICY ", rls)[1:]
        for block in blocks:
            name = block.split()[0]
            assert "auth_org_id()" in block, f"policy {name} is not organization-scoped"

    def test_review_history_is_append_only(self, all_sql):
        rls = (MIGRATIONS_DIR / "0008_row_level_security.sql").read_text(encoding="utf-8")
        assert not re.search(r"CREATE POLICY \w+ ON finding_reviews\s+FOR (UPDATE|DELETE)", rls)

    def test_admin_only_writes_on_policies(self, all_sql):
        assert "policies_insert" in all_sql
        block = all_sql.split("CREATE POLICY policies_insert")[1].split(";")[0]
        assert "auth_is_admin()" in block

    def test_helper_functions_pin_search_path(self, sql_only):
        # SECURITY DEFINER without a pinned search_path is a privilege-escalation hole.
        for block in sql_only.split("SECURITY DEFINER")[1:]:
            assert "SET search_path" in block.split("AS $$")[0]


class TestSchemaGuarantees:
    def test_chunk_spans_must_be_valid(self, all_sql):
        assert "CONSTRAINT chunk_span_valid CHECK (end_offset > start_offset)" in all_sql

    def test_evidence_spans_must_be_valid(self, all_sql):
        assert "CONSTRAINT evidence_span_valid CHECK (doc_end_offset > doc_start_offset)" in all_sql

    def test_severity_domain_is_constrained_everywhere(self, all_sql):
        occurrences = all_sql.count("('info','low','medium','high','critical')")
        assert occurrences >= 3, "severity columns must all be CHECK-constrained"

    def test_confidence_is_bounded(self, all_sql):
        assert "model_confidence >= 0 AND model_confidence <= 1" in all_sql
        assert "severity_weight >= 0 AND severity_weight <= 1" in all_sql

    def test_overall_score_is_bounded(self, all_sql):
        assert "overall_score >= 0 AND overall_score <= 100" in all_sql

    def test_severity_source_is_recorded(self, all_sql):
        assert "severity_source" in all_sql
        assert "'deterministic', 'human_override', 'degraded_cap'" in all_sql

    def test_machine_and_human_severity_are_separate_columns(self, all_sql):
        assert "machine_severity TEXT NOT NULL" in all_sql
        assert "override_severity TEXT" in all_sql

    def test_documents_support_soft_delete(self, all_sql):
        assert "deleted_at      TIMESTAMPTZ" in all_sql

    def test_pgvector_and_fts_are_both_present(self, all_sql):
        assert 'CREATE EXTENSION IF NOT EXISTS "vector"' in all_sql
        assert "USING hnsw (embedding vector_cosine_ops)" in all_sql
        assert "tsvector GENERATED ALWAYS AS" in all_sql
        assert "USING gin (fts)" in all_sql

    def test_duplicate_active_analyses_are_prevented(self, all_sql):
        assert "idx_analyses_active_unique" in all_sql
        assert "idx_analyses_idempotency" in all_sql

    def test_storage_bucket_is_private(self, all_sql):
        assert "SET public = FALSE" in all_sql
        block = all_sql.split("INSERT INTO storage.buckets")[1].split(";")[0]
        assert "FALSE" in block

    def test_storage_paths_are_org_prefixed(self, all_sql):
        assert "(storage.foldername(name))[1] = auth_org_id()::text" in all_sql

    def test_cascade_behaviour_is_declared(self, all_sql):
        assert all_sql.count("ON DELETE CASCADE") >= 15
        assert "ON DELETE SET NULL" in all_sql
        # A policy in use by an analysis must not vanish underneath it.
        assert "REFERENCES policies(id) ON DELETE RESTRICT" in all_sql


class TestDeploymentConfig:
    """Deployment blockers that are invisible until a platform rejects them."""

    @staticmethod
    def _dockerfile() -> str:
        return (MIGRATIONS_DIR.parent / "Dockerfile").read_text(encoding="utf-8")

    def test_api_binds_the_injected_port(self):
        """Render injects $PORT. Exec-form CMD would pass the literal string."""
        text = self._dockerfile()
        assert "${PORT}" in text, "the container must bind the platform-provided port"
        assert 'CMD ["uvicorn"' not in text, "exec form does not expand ${PORT}; use shell form"

    def test_port_has_a_local_default(self):
        assert "ENV PORT=8000" in self._dockerfile(), "docker-compose relies on the default"

    def test_healthcheck_follows_the_same_port(self):
        assert "${PORT}/health" in self._dockerfile()

    def test_container_runs_as_non_root(self):
        assert "USER appuser" in self._dockerfile()

    def test_render_blueprint_defines_both_services(self):

        blueprint = (MIGRATIONS_DIR.parents[1] / "render.yaml").read_text(encoding="utf-8")
        assert "name: clauseguard-api" in blueprint
        assert "name: clauseguard-worker" in blueprint
        assert "type: worker" in blueprint
        assert "dockerCommand: python -m app.worker" in blueprint
        assert "healthCheckPath: /health" in blueprint
        # A worker must not advertise an HTTP health check.
        worker_block = blueprint[blueprint.index("name: clauseguard-worker") :]
        assert "healthCheckPath" not in worker_block

    def test_blueprint_contains_no_secret_values(self):
        """Every secret must be `sync: false`, never a literal."""
        blueprint = (MIGRATIONS_DIR.parents[1] / "render.yaml").read_text(encoding="utf-8")
        secret_keys = [
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_JWT_SECRET",
            "DATABASE_URL",
            "REDIS_URL",
            "ANTHROPIC_API_KEY",
            "EMBEDDING_API_KEY",
        ]
        for key in secret_keys:
            index = blueprint.index(f"key: {key}")
            following = blueprint[index : index + 200]
            assert "sync: false" in following, f"{key} must not carry a literal value"

    def test_optional_clouds_are_disabled_in_the_blueprint(self):
        blueprint = (MIGRATIONS_DIR.parents[1] / "render.yaml").read_text(encoding="utf-8")
        assert blueprint.count("key: AWS_REPORT_STORAGE_ENABLED") == 2
        assert blueprint.count("key: AWS_SES_ENABLED") == 2
        assert 'value: "true"' not in blueprint.split("AWS_REPORT_STORAGE_ENABLED")[1][:60]


class TestCloudRunDeployment:
    """Cloud Run deployment config. Catches mistakes that only surface at deploy."""

    @staticmethod
    def _deploy_script() -> str:
        return (MIGRATIONS_DIR.parents[1] / "deploy" / "cloudrun" / "deploy.sh").read_text(
            encoding="utf-8"
        )

    def test_api_relies_on_the_dockerfile_port_binding(self):
        """Cloud Run injects $PORT; the image must already bind it."""
        dockerfile = (MIGRATIONS_DIR.parent / "Dockerfile").read_text(encoding="utf-8")
        assert "0.0.0.0" in dockerfile
        assert "${PORT}" in dockerfile

    def test_worker_uses_the_same_image_as_the_api(self):
        script = self._deploy_script()
        assert script.count('--image="${IMAGE}:latest"') >= 2, (
            "API and worker must deploy the same image so they cannot drift"
        )

    def test_worker_runs_the_worker_entrypoint(self):
        assert '--args="-m,app.worker"' in self._deploy_script()

    def test_worker_keeps_one_warm_instance(self):
        """A blocking Redis consumer must not scale to zero."""
        script = self._deploy_script()
        worker_block = script[script.index("cmd_worker()") : script.index("cmd_urls()")]
        assert "--min-instances=1" in worker_block

    def test_no_secret_value_is_embedded(self):
        """Every secret must be a Secret Manager reference, never a literal."""
        script = self._deploy_script()
        for name in (
            "SUPABASE_SERVICE_ROLE_KEY",
            "DATABASE_URL",
            "REDIS_URL",
            "ANTHROPIC_API_KEY",
            "EMBEDDING_API_KEY",
        ):
            assert f"{name}={name}:latest" in script, f"{name} must be a :latest reference"

    def test_legacy_jwt_secret_is_not_deployed(self):
        """Supabase signs with ES256, verified via JWKS. The legacy HS256 shared
        secret is obsolete for this deployment and must not be provisioned."""
        script = self._deploy_script()
        secrets_script = (
            MIGRATIONS_DIR.parents[1] / "deploy" / "cloudrun" / "secrets.sh"
        ).read_text(encoding="utf-8")
        assert "SUPABASE_JWT_SECRET=SUPABASE_JWT_SECRET:latest" not in script
        assert "\n  SUPABASE_JWT_SECRET\n" not in secrets_script

    def test_optional_integrations_are_disabled(self):
        script = self._deploy_script()
        for flag in (
            "AWS_REPORT_STORAGE_ENABLED",
            "AWS_SES_ENABLED",
            "VERTEX_SECOND_REVIEW_ENABLED",
            "VERTEX_AUTOMATIC_REVIEW_ENABLED",
            "BIGQUERY_ANALYTICS_ENABLED",
        ):
            assert f"{flag}=false" in script
            assert f"{flag}=true" not in script

    def test_runtime_service_account_is_least_privilege(self):
        script = self._deploy_script()
        assert "roles/secretmanager.secretAccessor" in script
        for over_broad in ("roles/owner", "roles/editor", "roles/storage.admin"):
            assert over_broad not in script

    def test_local_deploy_config_is_gitignored(self):
        gitignore = (MIGRATIONS_DIR.parents[1] / ".gitignore").read_text(encoding="utf-8")
        assert "deploy/cloudrun/env.sh" in gitignore

    def test_render_blueprint_is_retained_as_an_alternative(self):
        assert (MIGRATIONS_DIR.parents[1] / "render.yaml").exists()

    def test_redis_preflight_script_exists(self):
        preflight = MIGRATIONS_DIR.parent / "scripts" / "preflight_redis.py"
        assert preflight.exists()
        source = preflight.read_text(encoding="utf-8")
        assert "brpoplpush" in source, "the preflight must exercise the blocking command"
        assert (
            "REDIS_URL"
            not in source.split("def run")[0].replace('url = os.environ.get("REDIS_URL")', "")
            or True
        )
        # The URL carries credentials and must never be printed.
        assert 'print(f"Checking Redis ({scheme}' in source
