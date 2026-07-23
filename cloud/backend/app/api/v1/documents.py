"""Upload and extraction. The file is read IN MEMORY, extracted, and discarded.
It is never written to disk or object storage; only the extraction record and
the AI JSON (exactly as returned) are kept."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Document, User
from app.schemas.document import DocumentDetail, DocumentOut
from app.services.extraction.extractor import extract_invoice
from app.services.extraction.file_reader import detect_kind

log = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentDetail)
def upload_document(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    filename = file.filename or "upload"
    try:
        detect_kind(filename)  # PDF and image only (further formats planned)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {settings.max_upload_mb} MB limit.",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file.")

    doc = Document(uploaded_by=user.id, filename=filename,
                   mime=file.content_type, status="failed")
    try:
        # Extraction runs on the in-memory bytes; stored exactly as returned.
        doc.extracted_json = extract_invoice(data, filename)
        doc.status = "extracted"
    except Exception as e:
        log.exception("Extraction failed for %s", filename)
        doc.error = str(e)
    finally:
        del data  # the raw bytes are discarded here; nothing was stored

    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Per-user isolation (docs/12): non-admins see only their own uploads.
    query = select(Document)
    if user.role != "admin":
        query = query.where(Document.uploaded_by == user.id)
    return db.execute(
        query.order_by(Document.created_at.desc()).limit(200)
    ).scalars().all()


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if user.role != "admin" and doc.uploaded_by != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc
