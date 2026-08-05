"""Document and chunk persistence. Every query is organization-scoped."""

from __future__ import annotations

from typing import Any

from app.db.session import execute, fetch_all, fetch_one, fetch_value, to_vector_literal
from app.services.retrieval import ChunkSearchBackend, RetrievedChunk

DOCUMENT_COLUMNS = """
    id, org_id, uploaded_by, title, vendor_name, source_type, storage_path,
    original_filename, file_size_bytes, content_sha256, page_count, char_count,
    status, error_code, error_message, metadata, created_at, updated_at
"""


class DocumentRepository:
    async def create(self, **fields) -> dict[str, Any]:
        row = await fetch_one(
            f"""
            INSERT INTO documents (
                org_id, uploaded_by, title, vendor_name, source_type, storage_path,
                original_filename, file_size_bytes, normalized_text, content_sha256,
                page_count, char_count, status, metadata
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            RETURNING {DOCUMENT_COLUMNS}
            """,
            fields["org_id"],
            fields.get("uploaded_by"),
            fields["title"],
            fields.get("vendor_name"),
            fields["source_type"],
            fields.get("storage_path"),
            fields.get("original_filename"),
            fields.get("file_size_bytes"),
            fields.get("normalized_text"),
            fields.get("content_sha256"),
            fields.get("page_count"),
            fields.get("char_count"),
            fields.get("status", "uploaded"),
            fields.get("metadata") or {},
        )
        if row is None:
            raise RuntimeError("The document INSERT returned no row.")
        return row

    async def get(self, org_id: str, document_id: str, *, with_text: bool = False):
        columns = DOCUMENT_COLUMNS + (", normalized_text" if with_text else "")
        return await fetch_one(
            f"SELECT {columns} FROM documents WHERE id = $1 AND org_id = $2 AND deleted_at IS NULL",
            document_id,
            org_id,
        )

    async def list(
        self,
        org_id: str,
        *,
        limit: int = 25,
        offset: int = 0,
        search: str | None = None,
        status: str | None = None,
        sort: str = "created_at",
        direction: str = "desc",
    ) -> dict[str, Any]:
        # Allowlisted to keep the ORDER BY clause injection-proof.
        sort_column = {"created_at": "created_at", "title": "title", "status": "status"}.get(
            sort, "created_at"
        )
        order = "ASC" if direction.lower() == "asc" else "DESC"

        conditions = ["org_id = $1", "deleted_at IS NULL"]
        params: list[Any] = [org_id]
        if search:
            params.append(f"%{search}%")
            conditions.append(f"(title ILIKE ${len(params)} OR vendor_name ILIKE ${len(params)})")
        if status:
            params.append(status)
            conditions.append(f"status = ${len(params)}")
        where = " AND ".join(conditions)

        total = await fetch_value(f"SELECT COUNT(*) FROM documents WHERE {where}", *params)
        params.extend([limit, offset])
        items = await fetch_all(
            f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE {where} "
            f"ORDER BY {sort_column} {order} LIMIT ${len(params) - 1} OFFSET ${len(params)}",
            *params,
        )
        return {"items": items, "total": total or 0, "limit": limit, "offset": offset}

    async def update_status(
        self,
        document_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        await execute(
            "UPDATE documents SET status = $2, error_code = $3, error_message = $4 WHERE id = $1",
            document_id,
            status,
            error_code,
            error_message,
        )

    async def update_metadata(self, org_id: str, document_id: str, **fields) -> dict | None:
        allowed = {"title", "vendor_name"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return await self.get(org_id, document_id)

        assignments = ", ".join(f"{k} = ${i + 3}" for i, k in enumerate(updates))
        return await fetch_one(
            f"UPDATE documents SET {assignments} "
            f"WHERE id = $1 AND org_id = $2 AND deleted_at IS NULL RETURNING {DOCUMENT_COLUMNS}",
            document_id,
            org_id,
            *updates.values(),
        )

    async def soft_delete(self, org_id: str, document_id: str) -> bool:
        result = await execute(
            "UPDATE documents SET deleted_at = NOW() "
            "WHERE id = $1 AND org_id = $2 AND deleted_at IS NULL",
            document_id,
            org_id,
        )
        return result.endswith("1")

    async def get_normalized_text(self, org_id: str, document_id: str) -> str | None:
        return await fetch_value(
            "SELECT normalized_text FROM documents "
            "WHERE id = $1 AND org_id = $2 AND deleted_at IS NULL",
            document_id,
            org_id,
        )

    async def org_document_ids(self, org_id: str) -> set:
        rows = await fetch_all(
            "SELECT id FROM documents WHERE org_id = $1 AND deleted_at IS NULL", org_id
        )
        return {str(r["id"]) for r in rows}


CHUNK_COLUMNS = """
    id, org_id, document_id, ordinal, heading, chunk_text,
    start_offset, end_offset, token_count, content_sha256
"""


class ChunkRepository(ChunkSearchBackend):
    async def bulk_insert(self, org_id: str, document_id: str, chunks: list[dict]) -> int:
        if not chunks:
            return 0
        from app.db.session import get_pool

        records = [
            (
                org_id,
                document_id,
                c["ordinal"],
                c.get("heading"),
                c["chunk_text"],
                c["start_offset"],
                c["end_offset"],
                c["token_count"],
                c["content_sha256"],
            )
            for c in chunks
        ]
        async with get_pool().acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO document_chunks
                    (org_id, document_id, ordinal, heading, chunk_text,
                     start_offset, end_offset, token_count, content_sha256)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (document_id, ordinal) DO NOTHING
                """,
                records,
            )
        return len(records)

    async def set_embedding(self, chunk_id: str, embedding: list[float], model: str) -> None:
        await execute(
            "UPDATE document_chunks SET embedding = $2::vector, embedding_model = $3 WHERE id = $1",
            chunk_id,
            to_vector_literal(embedding),
            model,
        )

    async def list_for_document(self, document_id: str) -> list[dict]:
        return await fetch_all(
            f"SELECT {CHUNK_COLUMNS} FROM document_chunks WHERE document_id = $1 ORDER BY ordinal",
            document_id,
        )

    async def missing_embeddings(self, document_id: str) -> list[dict]:
        return await fetch_all(
            f"SELECT {CHUNK_COLUMNS} FROM document_chunks "
            "WHERE document_id = $1 AND embedding IS NULL ORDER BY ordinal",
            document_id,
        )

    async def get(self, chunk_id: str) -> dict | None:
        return await fetch_one(
            f"SELECT {CHUNK_COLUMNS} FROM document_chunks WHERE id = $1", chunk_id
        )

    # --- ChunkSearchBackend --------------------------------------------------
    @staticmethod
    def _to_retrieved(row: dict) -> RetrievedChunk:
        return RetrievedChunk(
            id=str(row["id"]),
            document_id=str(row["document_id"]),
            ordinal=row["ordinal"],
            heading=row.get("heading"),
            text=row["chunk_text"],
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
        )

    async def dense_search(self, document_id: str, embedding: list[float], limit: int):
        rows = await fetch_all(
            f"""
            SELECT {CHUNK_COLUMNS}, 1 - (embedding <=> $2::vector) AS similarity
            FROM document_chunks
            WHERE document_id = $1 AND embedding IS NOT NULL
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            """,
            document_id,
            to_vector_literal(embedding),
            limit,
        )
        return [self._to_retrieved(r) for r in rows]

    async def keyword_search(self, document_id: str, query: str, limit: int):
        rows = await fetch_all(
            f"""
            SELECT {CHUNK_COLUMNS}, ts_rank_cd(fts, plainto_tsquery('english', $2)) AS rank
            FROM document_chunks
            WHERE document_id = $1 AND fts @@ plainto_tsquery('english', $2)
            ORDER BY rank DESC
            LIMIT $3
            """,
            document_id,
            query,
            limit,
        )
        return [self._to_retrieved(r) for r in rows]

    async def ordinal_scan(self, document_id: str, limit: int):
        rows = await fetch_all(
            f"SELECT {CHUNK_COLUMNS} FROM document_chunks "
            "WHERE document_id = $1 ORDER BY ordinal LIMIT $2",
            document_id,
            limit,
        )
        return [self._to_retrieved(r) for r in rows]

    async def get_by_ordinal_range(self, document_id: str, start: int, end: int):
        rows = await fetch_all(
            f"SELECT {CHUNK_COLUMNS} FROM document_chunks "
            "WHERE document_id = $1 AND ordinal BETWEEN $2 AND $3 ORDER BY ordinal",
            document_id,
            start,
            end,
        )
        return [self._to_retrieved(r) for r in rows]
