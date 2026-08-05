"""Policy and policy-rule persistence."""

from __future__ import annotations

import builtins
from typing import Any

from app.db.session import execute, fetch_all, fetch_one, get_pool

POLICY_COLUMNS = "id, org_id, created_by, name, description, version, is_default, is_active, created_at, updated_at"
RULE_COLUMNS = """
    id, org_id, policy_id, category, display_name, description, retrieval_guidance,
    keywords, severity_weight, confidence_threshold, escalate, is_enabled, sort_order
"""


class PolicyRepository:
    async def list(self, org_id: str, *, active_only: bool = False) -> builtins.list[dict]:
        clause = " AND is_active" if active_only else ""
        return await fetch_all(
            f"SELECT {POLICY_COLUMNS} FROM policies WHERE org_id = $1{clause} "
            "ORDER BY is_default DESC, name, version DESC",
            org_id,
        )

    async def get(self, org_id: str, policy_id: str) -> dict | None:
        return await fetch_one(
            f"SELECT {POLICY_COLUMNS} FROM policies WHERE id = $1 AND org_id = $2",
            policy_id,
            org_id,
        )

    async def get_default(self, org_id: str) -> dict | None:
        return await fetch_one(
            f"SELECT {POLICY_COLUMNS} FROM policies "
            "WHERE org_id = $1 AND is_default AND is_active LIMIT 1",
            org_id,
        )

    async def create(
        self,
        org_id: str,
        *,
        name: str,
        description: str | None,
        created_by: str | None,
        rules: builtins.list[dict] | None = None,
    ) -> dict[str, Any]:
        async with get_pool().acquire() as connection, connection.transaction():
            policy = await connection.fetchrow(
                f"INSERT INTO policies (org_id, name, description, created_by) "
                f"VALUES ($1,$2,$3,$4) RETURNING {POLICY_COLUMNS}",
                org_id,
                name,
                description,
                created_by,
            )
            for index, rule in enumerate(rules or []):
                await connection.execute(
                    """
                    INSERT INTO policy_rules
                        (org_id, policy_id, category, display_name, description,
                         retrieval_guidance, keywords, severity_weight,
                         confidence_threshold, escalate, sort_order)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    """,
                    org_id,
                    policy["id"],
                    rule["category"],
                    rule["display_name"],
                    rule["description"],
                    rule.get("retrieval_guidance"),
                    rule.get("keywords") or [],
                    rule.get("severity_weight", 0.5),
                    rule.get("confidence_threshold", 0.35),
                    rule.get("escalate", False),
                    rule.get("sort_order", index),
                )
        return dict(policy)

    async def create_version(self, org_id: str, policy_id: str, created_by: str | None):
        """Clone a policy and its rules at version+1, leaving history intact."""
        async with get_pool().acquire() as connection, connection.transaction():
            current = await connection.fetchrow(
                "SELECT * FROM policies WHERE id = $1 AND org_id = $2", policy_id, org_id
            )
            if current is None:
                return None
            new_version = await connection.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM policies WHERE org_id = $1 AND name = $2",
                org_id,
                current["name"],
            )
            created = await connection.fetchrow(
                f"INSERT INTO policies (org_id, name, description, version, created_by, is_active) "
                f"VALUES ($1,$2,$3,$4,$5,TRUE) RETURNING {POLICY_COLUMNS}",
                org_id,
                current["name"],
                current["description"],
                new_version,
                created_by,
            )
            await connection.execute(
                """
                INSERT INTO policy_rules
                    (org_id, policy_id, category, display_name, description,
                     retrieval_guidance, keywords, severity_weight,
                     confidence_threshold, escalate, is_enabled, sort_order)
                SELECT org_id, $2, category, display_name, description,
                       retrieval_guidance, keywords, severity_weight,
                       confidence_threshold, escalate, is_enabled, sort_order
                FROM policy_rules WHERE policy_id = $1
                """,
                policy_id,
                created["id"],
            )
        return dict(created)

    async def update(self, org_id: str, policy_id: str, **fields) -> dict | None:
        allowed = {"name", "description", "is_active", "is_default"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return await self.get(org_id, policy_id)

        async with get_pool().acquire() as connection, connection.transaction():
            if updates.get("is_default"):
                # At most one default per organization.
                await connection.execute(
                    "UPDATE policies SET is_default = FALSE WHERE org_id = $1 AND id <> $2",
                    org_id,
                    policy_id,
                )
            assignments = ", ".join(f"{k} = ${i + 3}" for i, k in enumerate(updates))
            row = await connection.fetchrow(
                f"UPDATE policies SET {assignments} WHERE id = $1 AND org_id = $2 "
                f"RETURNING {POLICY_COLUMNS}",
                policy_id,
                org_id,
                *updates.values(),
            )
        return dict(row) if row else None

    async def list_rules(self, org_id: str, policy_id: str, *, enabled_only: bool = False):
        clause = " AND is_enabled" if enabled_only else ""
        return await fetch_all(
            f"SELECT {RULE_COLUMNS} FROM policy_rules "
            f"WHERE policy_id = $1 AND org_id = $2{clause} ORDER BY sort_order, category",
            policy_id,
            org_id,
        )

    async def replace_rules(
        self, org_id: str, policy_id: str, rules: builtins.list[dict]
    ) -> builtins.list[dict]:
        """Atomically replace a policy's full rule set."""
        async with get_pool().acquire() as connection, connection.transaction():
            await connection.execute(
                "DELETE FROM policy_rules WHERE policy_id = $1 AND org_id = $2",
                policy_id,
                org_id,
            )
            for index, rule in enumerate(rules):
                await connection.execute(
                    """
                    INSERT INTO policy_rules
                        (org_id, policy_id, category, display_name, description,
                         retrieval_guidance, keywords, severity_weight,
                         confidence_threshold, escalate, is_enabled, sort_order)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    """,
                    org_id,
                    policy_id,
                    rule["category"],
                    rule["display_name"],
                    rule["description"],
                    rule.get("retrieval_guidance"),
                    rule.get("keywords") or [],
                    rule.get("severity_weight", 0.5),
                    rule.get("confidence_threshold", 0.35),
                    rule.get("escalate", False),
                    rule.get("is_enabled", True),
                    rule.get("sort_order", index),
                )
        return await self.list_rules(org_id, policy_id)

    async def delete(self, org_id: str, policy_id: str) -> bool:
        result = await execute(
            "DELETE FROM policies WHERE id = $1 AND org_id = $2 AND NOT is_default",
            policy_id,
            org_id,
        )
        return result.endswith("1")
