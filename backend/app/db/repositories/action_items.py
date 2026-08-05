"""Action item persistence. Every query is organization-scoped."""

from __future__ import annotations

import builtins
from typing import Any

from app.db.session import execute, fetch_all, fetch_one, fetch_value, get_pool

ITEM_COLUMNS = """
    id, org_id, analysis_id, document_id, finding_id, title, description, category,
    obligation_type, evidence_quote, doc_start_offset, doc_end_offset, duration_days,
    duration_text, ai_due_date, ai_priority, due_date, date_status, assignee_id,
    priority, status, reviewer_note, dedupe_key, completed_at, completed_by,
    created_at, updated_at
"""

# Same columns, table-qualified for the joined list query.
ITEM_COLUMNS_I = ", ".join(
    f"i.{c.strip()}" for c in ITEM_COLUMNS.replace("\n", " ").split(",") if c.strip()
)

EDITABLE = {"due_date", "date_status", "assignee_id", "priority", "status", "reviewer_note"}


class ActionItemRepository:
    async def bulk_upsert(
        self,
        *,
        org_id: str,
        analysis_id: str,
        document_id: str,
        items: builtins.list[dict[str, Any]],
    ) -> int:
        """Insert derived items, skipping any that already exist.

        ON CONFLICT DO NOTHING against the (analysis_id, dedupe_key) unique
        constraint means regeneration is idempotent and never clobbers a human
        edit made since the last run.
        """
        if not items:
            return 0
        inserted = 0
        async with get_pool().acquire() as connection, connection.transaction():
            for item in items:
                row = await connection.fetchrow(
                    """
                    INSERT INTO action_items
                        (org_id, analysis_id, document_id, finding_id, title, description,
                         category, obligation_type, evidence_quote, doc_start_offset,
                         doc_end_offset, duration_days, duration_text, ai_priority,
                         priority, date_status, dedupe_key)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$14,$15,$16)
                    ON CONFLICT (analysis_id, dedupe_key) DO NOTHING
                    RETURNING id
                    """,
                    org_id,
                    analysis_id,
                    document_id,
                    item["finding_id"],
                    item["title"],
                    item["description"],
                    item["category"],
                    item["obligation_type"],
                    item["evidence_quote"],
                    item.get("doc_start_offset"),
                    item.get("doc_end_offset"),
                    item.get("duration_days"),
                    item.get("duration_text"),
                    item.get("priority", "medium"),
                    item.get("date_status", "unresolved"),
                    item["dedupe_key"],
                )
                if row is not None:
                    inserted += 1
        return inserted

    async def get(self, org_id: str, item_id: str) -> dict | None:
        return await fetch_one(
            f"SELECT {ITEM_COLUMNS} FROM action_items WHERE id = $1 AND org_id = $2",
            item_id,
            org_id,
        )

    async def list(
        self,
        org_id: str,
        *,
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        assignee_id: str | None = None,
        document_id: str | None = None,
        analysis_id: str | None = None,
        due: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "due_date",
    ) -> dict[str, Any]:
        conditions = ["i.org_id = $1"]
        params: list[Any] = [org_id]

        def add(clause: str, value: Any) -> None:
            params.append(value)
            conditions.append(clause.format(n=len(params)))

        if status:
            add("i.status = ${n}", status)
        if category:
            add("i.category = ${n}", category)
        if priority:
            add("i.priority = ${n}", priority)
        if assignee_id:
            add("i.assignee_id = ${n}", assignee_id)
        if document_id:
            add("i.document_id = ${n}", document_id)
        if analysis_id:
            add("i.analysis_id = ${n}", analysis_id)

        if due == "overdue":
            conditions.append("i.due_date < CURRENT_DATE AND i.status IN ('open','in_progress')")
        elif due == "soon":
            conditions.append(
                "i.due_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days' "
                "AND i.status IN ('open','in_progress')"
            )
        elif due == "unresolved":
            conditions.append("i.date_status = 'unresolved'")

        where = " AND ".join(conditions)
        order = {
            "due_date": "i.due_date ASC NULLS LAST, i.created_at DESC",
            "priority": (
                "CASE i.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'medium' THEN 2 ELSE 3 END, i.due_date ASC NULLS LAST"
            ),
            "created_at": "i.created_at DESC",
        }.get(sort, "i.due_date ASC NULLS LAST, i.created_at DESC")

        total = await fetch_value(f"SELECT COUNT(*) FROM action_items i WHERE {where}", *params)
        params.extend([limit, offset])
        rows = await fetch_all(
            f"""
            SELECT {ITEM_COLUMNS_I},
                   d.title AS document_title, d.vendor_name
            FROM action_items i
            JOIN documents d ON d.id = i.document_id
            WHERE {where}
            ORDER BY {order}
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return {"items": rows, "total": total or 0, "limit": limit, "offset": offset}

    async def summary(self, org_id: str) -> dict[str, Any]:
        """Counts for the dashboard widget. One query, no N+1."""
        row = await fetch_one(
            """
            SELECT
              COUNT(*) FILTER (WHERE status IN ('open','in_progress'))        AS open_count,
              COUNT(*) FILTER (WHERE status = 'completed')                    AS completed_count,
              COUNT(*) FILTER (WHERE status IN ('open','in_progress')
                               AND due_date < CURRENT_DATE)                   AS overdue_count,
              COUNT(*) FILTER (WHERE status IN ('open','in_progress')
                               AND due_date BETWEEN CURRENT_DATE
                                                AND CURRENT_DATE + INTERVAL '30 days')
                                                                              AS due_soon_count,
              COUNT(*) FILTER (WHERE status IN ('open','in_progress')
                               AND priority = 'urgent')                       AS urgent_count,
              COUNT(*) FILTER (WHERE date_status = 'unresolved'
                               AND status IN ('open','in_progress'))          AS unresolved_date_count
            FROM action_items WHERE org_id = $1
            """,
            org_id,
        )
        return {k: int(v or 0) for k, v in (row or {}).items()}

    async def update(
        self, *, org_id: str, item_id: str, reviewer_id: str | None, changes: dict[str, Any]
    ) -> dict | None:
        """Apply human edits and append an immutable review row for each change.

        The machine's original extraction (`ai_priority`, `ai_due_date`,
        `evidence_quote`, `duration_*`) is never touched.
        """
        updates = {k: v for k, v in changes.items() if k in EDITABLE}
        if not updates:
            return await self.get(org_id, item_id)

        async with get_pool().acquire() as connection, connection.transaction():
            current = await connection.fetchrow(
                f"SELECT {ITEM_COLUMNS} FROM action_items WHERE id = $1 AND org_id = $2 FOR UPDATE",
                item_id,
                org_id,
            )
            if current is None:
                return None

            # A human-supplied date is explicitly marked as such.
            if "due_date" in updates and updates["due_date"] is not None:
                updates.setdefault("date_status", "human_set")

            if updates.get("status") == "completed":
                updates["completed_at"] = "NOW()"

            assignments, values = [], []
            for key, value in updates.items():
                if key == "completed_at":
                    assignments.append("completed_at = NOW()")
                    continue
                values.append(value)
                assignments.append(f"{key} = ${len(values) + 2}")
            if updates.get("status") == "completed":
                values.append(reviewer_id)
                assignments.append(f"completed_by = ${len(values) + 2}")

            row = await connection.fetchrow(
                f"UPDATE action_items SET {', '.join(assignments)} "
                f"WHERE id = $1 AND org_id = $2 RETURNING {ITEM_COLUMNS}",
                item_id,
                org_id,
                *values,
            )

            action_for = {
                "status": "status_change",
                "due_date": "due_date_set",
                "assignee_id": "assign",
                "priority": "priority_change",
                "reviewer_note": "note",
            }
            for key, value in updates.items():
                if key in ("completed_at", "date_status"):
                    continue
                await connection.execute(
                    """
                    INSERT INTO action_item_reviews
                        (org_id, action_item_id, reviewer_id, action, field_changed,
                         previous_value, new_value)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    """,
                    org_id,
                    item_id,
                    reviewer_id,
                    action_for.get(key, "note"),
                    key,
                    str(current[key]) if current[key] is not None else None,
                    str(value) if value is not None else None,
                )
        return dict(row) if row else None

    async def list_reviews(self, org_id: str, item_id: str) -> builtins.list[dict[str, Any]]:
        return await fetch_all(
            """
            SELECT r.id, r.action, r.field_changed, r.previous_value, r.new_value,
                   r.note, r.created_at, p.full_name AS reviewer_name
            FROM action_item_reviews r
            LEFT JOIN profiles p ON p.id = r.reviewer_id
            WHERE r.action_item_id = $1 AND r.org_id = $2
            ORDER BY r.created_at DESC
            """,
            item_id,
            org_id,
        )

    async def delete_for_analysis(self, org_id: str, analysis_id: str) -> None:
        await execute(
            "DELETE FROM action_items WHERE analysis_id = $1 AND org_id = $2",
            analysis_id,
            org_id,
        )
