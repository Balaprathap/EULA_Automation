"""Document upload, listing, and lifecycle."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status

from app.api.deps import client_ip, enforce_request_rate_limit
from app.core.config import Settings, get_settings
from app.core.errors import FileTooLarge, NotFound
from app.core.logging import get_logger
from app.core.security import AuthenticatedUser
from app.db.repositories.documents import ChunkRepository, DocumentRepository
from app.schemas.api import (
    DocumentListResponse,
    DocumentResponse,
    DocumentUpdateRequest,
    PasteDocumentRequest,
)
from app.services.audit import AuditAction, record_audit
from app.services.chunking import chunk_document
from app.services.normalization import content_hash
from app.services.parsing import parse_plain_text, parse_upload
from app.services.storage import StorageService, build_object_key

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

documents = DocumentRepository()
chunks = ChunkRepository()

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}


def to_response(row: dict) -> DocumentResponse:
    return DocumentResponse(
        id=str(row["id"]),
        title=row["title"],
        vendor_name=row.get("vendor_name"),
        source_type=row["source_type"],
        original_filename=row.get("original_filename"),
        file_size_bytes=row.get("file_size_bytes"),
        page_count=row.get("page_count"),
        char_count=row.get("char_count"),
        status=row["status"],
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _persist_chunks(org_id: str, document_id: str, text: str) -> int:
    produced = chunk_document(text)
    await chunks.bulk_insert(
        org_id,
        document_id,
        [
            {
                "ordinal": c.ordinal,
                "heading": c.heading,
                "chunk_text": c.text,
                "start_offset": c.start_offset,
                "end_offset": c.end_offset,
                "token_count": c.token_count,
                "content_sha256": content_hash(c.text),
            }
            for c in produced
        ],
    )
    return len(produced)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    vendor_name: str | None = Form(default=None),
    title: str | None = Form(default=None),
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
    settings: Settings = Depends(get_settings),
):
    """Upload a PDF, DOCX, or TXT agreement.

    Validation is performed before storage: oversize files, encrypted PDFs,
    scanned PDFs, and unsupported types are rejected with an explanatory
    message rather than being accepted and silently failing later.
    """
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise FileTooLarge(f"The file exceeds the {settings.max_upload_mb} MB limit.")

    parsed = parse_upload(
        data,
        file.filename or "document",
        max_bytes=settings.max_upload_bytes,
        max_pages=settings.max_document_pages,
    )

    record = await documents.create(
        org_id=user.org_id,
        uploaded_by=user.user_id,
        title=(title or parsed.title or file.filename or "Untitled agreement")[:500],
        vendor_name=vendor_name,
        source_type=parsed.source_type,
        original_filename=file.filename,
        file_size_bytes=len(data),
        normalized_text=parsed.normalized_text,
        content_sha256=parsed.content_sha256,
        page_count=parsed.page_count,
        char_count=parsed.char_count,
        status="chunking",
        metadata=parsed.metadata,
    )
    document_id = str(record["id"])

    # Store the original in the private, org-prefixed bucket.
    storage_path = None
    try:
        storage = StorageService(
            settings.supabase_url,
            settings.supabase_service_role_key,
            settings.supabase_storage_bucket,
        )
        key = build_object_key(user.org_id, document_id, file.filename or "document")
        storage_path = await storage.upload(
            key, data, CONTENT_TYPES.get(parsed.source_type, "application/octet-stream")
        )
        from app.db.session import execute

        await execute(
            "UPDATE documents SET storage_path = $2 WHERE id = $1", document_id, storage_path
        )
    except Exception as exc:  # noqa: BLE001 - text is already saved; analysis can proceed
        logger.warning(
            "original file could not be archived",
            extra={"document_id": document_id, "error_type": type(exc).__name__},
        )

    await _persist_chunks(user.org_id, document_id, parsed.normalized_text)
    await documents.update_status(document_id, "ready")

    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.DOCUMENT_UPLOAD,
        resource_type="document",
        resource_id=document_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "source_type": parsed.source_type,
            "pages": parsed.page_count,
            "chars": parsed.char_count,
            "bytes": len(data),
        },
    )

    result = await documents.get(user.org_id, document_id)
    return to_response(result)


@router.post("/paste", status_code=status.HTTP_201_CREATED, response_model=DocumentResponse)
async def paste_document(
    request: Request,
    payload: PasteDocumentRequest,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
    settings: Settings = Depends(get_settings),
):
    """Create a document from pasted text. Same normalization path as uploads."""
    parsed = parse_plain_text(payload.text, settings.max_document_pages, source_type="paste")

    record = await documents.create(
        org_id=user.org_id,
        uploaded_by=user.user_id,
        title=payload.title,
        vendor_name=payload.vendor_name,
        source_type="paste",
        normalized_text=parsed.normalized_text,
        content_sha256=parsed.content_sha256,
        page_count=parsed.page_count,
        char_count=parsed.char_count,
        status="chunking",
    )
    document_id = str(record["id"])

    await _persist_chunks(user.org_id, document_id, parsed.normalized_text)
    await documents.update_status(document_id, "ready")

    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.DOCUMENT_UPLOAD,
        resource_type="document",
        resource_id=document_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request),
        metadata={"source_type": "paste", "chars": parsed.char_count},
    )

    return to_response(await documents.get(user.org_id, document_id))


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=200),
    doc_status: str | None = Query(default=None, alias="status"),
    sort: str = Query(default="created_at"),
    direction: str = Query(default="desc"),
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    page = await documents.list(
        user.org_id,
        limit=limit,
        offset=offset,
        search=search,
        status=doc_status,
        sort=sort,
        direction=direction,
    )
    return DocumentListResponse(
        items=[to_response(row) for row in page["items"]],
        total=page["total"],
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str, user: AuthenticatedUser = Depends(enforce_request_rate_limit)
):
    row = await documents.get(user.org_id, document_id)
    if row is None:
        raise NotFound("That document does not exist, or you do not have access to it.")
    return to_response(row)


@router.get("/{document_id}/text")
async def get_document_text(
    document_id: str, user: AuthenticatedUser = Depends(enforce_request_rate_limit)
):
    """Normalized text for the evidence viewer.

    The frontend highlights findings using absolute offsets into exactly this
    string, so it must be byte-identical to what verification ran against.
    """
    text = await documents.get_normalized_text(user.org_id, document_id)
    if text is None:
        raise NotFound("That document does not exist, or you do not have access to it.")
    return {"document_id": document_id, "normalized_text": text, "char_count": len(text)}


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    request: Request,
    document_id: str,
    payload: DocumentUpdateRequest,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    row = await documents.update_metadata(
        user.org_id, document_id, title=payload.title, vendor_name=payload.vendor_name
    )
    if row is None:
        raise NotFound("That document does not exist, or you do not have access to it.")
    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.DOCUMENT_UPDATE,
        resource_type="document",
        resource_id=document_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return to_response(row)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    request: Request,
    document_id: str,
    user: AuthenticatedUser = Depends(enforce_request_rate_limit),
):
    deleted = await documents.soft_delete(user.org_id, document_id)
    if not deleted:
        raise NotFound("That document does not exist, or you do not have access to it.")
    await record_audit(
        org_id=user.org_id,
        actor_id=user.user_id,
        actor_email=user.email,
        action=AuditAction.DOCUMENT_DELETE,
        resource_type="document",
        resource_id=document_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
