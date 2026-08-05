"""Apply SQL migrations in order against DATABASE_URL.

Builds the entire schema from an empty database. No manual step in the Supabase
dashboard is required. Every migration is recorded in ``schema_migrations`` with
a checksum, so a file edited after being applied is detected rather than
silently ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
LOCAL_SHIM = MIGRATIONS_DIR / "local" / "0000_supabase_shim.sql"

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum   TEXT
);
"""


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def looks_like_supabase(database_url: str) -> bool:
    return "supabase.co" in database_url or "supabase.com" in database_url


async def run(local_shim: bool = False) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set. Copy .env.example to .env first.", file=sys.stderr)
        return 1

    if local_shim and looks_like_supabase(database_url):
        print(
            "ERROR: --local-shim was requested but DATABASE_URL points at Supabase.\n"
            "Supabase already provides the auth and storage schemas. Re-run without "
            "--local-shim.",
            file=sys.stderr,
        )
        return 1

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"ERROR: no migrations found in {MIGRATIONS_DIR}", file=sys.stderr)
        return 1

    import asyncpg

    connection = await asyncpg.connect(database_url)
    try:
        if local_shim:
            print("Applying the local Supabase shim (auth + storage schemas)...")
            await connection.execute(LOCAL_SHIM.read_text(encoding="utf-8"))
            print("  + shim applied\n")
        else:
            # Fail early and clearly rather than part-way through migration 0002.
            has_auth = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.schemata "
                "WHERE schema_name = 'auth')"
            )
            if not has_auth:
                print(
                    "ERROR: this database has no `auth` schema.\n\n"
                    "The migrations reference auth.users, auth.uid(), and storage.*, "
                    "which Supabase provides.\n"
                    "  - Against Supabase: check DATABASE_URL points at your project.\n"
                    "  - Against a local/docker Postgres: re-run with --local-shim.\n",
                    file=sys.stderr,
                )
                return 1

        await connection.execute(BOOTSTRAP)
        applied = {
            row["version"]: row["checksum"]
            for row in await connection.fetch("SELECT version, checksum FROM schema_migrations")
        }

        pending = 0
        for path in files:
            version = path.stem
            sql = path.read_text(encoding="utf-8")
            digest = checksum(sql)

            if version in applied:
                if applied[version] and applied[version] != digest:
                    print(
                        f"  ! {version} was applied previously but the file has since changed. "
                        "Add a new migration instead of editing an applied one."
                    )
                else:
                    print(f"  = {version} (already applied)")
                continue

            print(f"  + {version} ...", end=" ", flush=True)
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2) "
                    "ON CONFLICT (version) DO UPDATE SET checksum = EXCLUDED.checksum",
                    version,
                    digest,
                )
            print("ok")
            pending += 1

        print(f"\nMigrations complete. {pending} applied, {len(files) - pending} already present.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\nMIGRATION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apply ClauseGuard SQL migrations.")
    parser.add_argument(
        "--local-shim",
        action="store_true",
        help="First create minimal auth/storage schemas. For local or docker Postgres "
        "only - refused against a Supabase host.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(local_shim=args.local_shim)))
