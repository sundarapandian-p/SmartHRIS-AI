"""Phase 5.4 — HR document upload and metadata API tests."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.core.exceptions import ConflictError
from smarthris.main import create_app
from smarthris.models.hr_document import HrDocument, HrDocumentStatus, HrDocumentType
from smarthris.repositories.hr_document_repository import HrDocumentRepository
from smarthris.schemas.hr_document import HrDocumentRead
from smarthris.services.hr_document_service import HrDocumentService


@pytest.fixture
def documents_api_client(
    db_session: Session,
    integration_database_url: str,
    tmp_path: Path,
) -> TestClient:
    """API client with isolated private document directory."""
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-Documents",
        database_url=integration_database_url,
        rag_document_dir=str(tmp_path / "documents"),
        max_upload_bytes=1024,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _minimal_pdf_bytes() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _minimal_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr("word/document.xml", "<document></document>")
    return buffer.getvalue()


def _assert_no_storage_path(payload: dict) -> None:
    assert "storage_path" not in payload
    dumped = str(payload)
    assert "storage_path" not in dumped


# --- Repository -----------------------------------------------------------------


def test_repository_create_get_list_and_checksum(db_session: Session) -> None:
    repo = HrDocumentRepository(db_session)
    document = HrDocument(
        original_filename="policy.pdf",
        content_type="application/pdf",
        checksum="a" * 64,
        storage_path="private/a.pdf",
        title="Leave",
        doc_type=HrDocumentType.POLICY,
        status=HrDocumentStatus.PENDING,
    )
    repo.add(document)
    db_session.flush()

    assert repo.get_by_id(document.id) is document
    assert repo.exists_by_checksum("a" * 64) is True
    assert repo.exists_by_checksum("b" * 64) is False
    assert repo.get_by_checksum("a" * 64) is document

    rows = repo.list_page(offset=0, limit=10, doc_type=HrDocumentType.POLICY)
    assert len(rows) == 1
    assert repo.count(status=HrDocumentStatus.PENDING) == 1
    assert repo.count(doc_type=HrDocumentType.HANDBOOK) == 0


# --- Service --------------------------------------------------------------------


def test_service_upload_persists_pending_and_rejects_duplicate(
    db_session: Session,
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://unused:unused@127.0.0.1:3306/smarthris_test",
        rag_document_dir=str(tmp_path / "documents"),
        max_upload_bytes=1024,
    )
    service = HrDocumentService(db_session, settings)

    class _Upload:
        def __init__(self, name: str, data: bytes) -> None:
            self.filename = name
            self.file = io.BytesIO(data)

    payload = _minimal_pdf_bytes()
    first = service.create_from_upload(
        upload=_Upload("leave.pdf", payload),  # type: ignore[arg-type]
        title="Leave Policy",
        doc_type=HrDocumentType.POLICY,
    )
    assert first.status == HrDocumentStatus.PENDING
    assert first.title == "Leave Policy"
    assert first.doc_type == HrDocumentType.POLICY
    assert "storage_path" not in first.model_dump()

    stored = list((tmp_path / "documents").iterdir())
    assert len(stored) == 1

    with pytest.raises(ConflictError, match="same content"):
        service.create_from_upload(
            upload=_Upload("leave-copy.pdf", payload),  # type: ignore[arg-type]
        )
    assert len(list((tmp_path / "documents").iterdir())) == 1


def test_service_db_failure_removes_uploaded_file(
    db_session: Session,
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://unused:unused@127.0.0.1:3306/smarthris_test",
        rag_document_dir=str(tmp_path / "documents"),
        max_upload_bytes=1024,
    )
    service = HrDocumentService(db_session, settings)

    class _Upload:
        filename = "policy.pdf"
        file = io.BytesIO(_minimal_pdf_bytes())

    with patch.object(db_session, "commit", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError, match="db down"):
            service.create_from_upload(upload=_Upload())  # type: ignore[arg-type]

    assert list((tmp_path / "documents").iterdir()) == []


def test_service_checksum_lookup_failure_removes_uploaded_file(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """If exists_by_checksum fails after storage, delete the new file and leave DB empty."""
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://unused:unused@127.0.0.1:3306/smarthris_test",
        rag_document_dir=str(tmp_path / "documents"),
        max_upload_bytes=1024,
    )
    service = HrDocumentService(db_session, settings)
    docs_dir = tmp_path / "documents"

    class _Upload:
        filename = "policy.pdf"
        file = io.BytesIO(_minimal_pdf_bytes())

    with patch.object(
        HrDocumentRepository,
        "exists_by_checksum",
        side_effect=RuntimeError("checksum lookup failed"),
    ):
        with pytest.raises(RuntimeError, match="checksum lookup failed"):
            service.create_from_upload(upload=_Upload())  # type: ignore[arg-type]

    assert list(docs_dir.iterdir()) == []
    assert HrDocumentRepository(db_session).count() == 0


# --- API ------------------------------------------------------------------------


def test_upload_pdf_success(documents_api_client: TestClient, tmp_path: Path) -> None:
    response = documents_api_client.post(
        "/documents",
        files={"file": ("policy.pdf", _minimal_pdf_bytes(), "application/pdf")},
        data={"title": "Leave Policy", "doc_type": "policy"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    _assert_no_storage_path(body)
    assert body["original_filename"] == "policy.pdf"
    assert body["title"] == "Leave Policy"
    assert body["doc_type"] == "policy"
    assert body["status"] == "pending"
    assert body["checksum"] == hashlib.sha256(_minimal_pdf_bytes()).hexdigest()
    assert body["indexed_at"] is None
    assert (tmp_path / "documents" / f"{body['id']}.pdf").is_file()


def test_upload_docx_success(documents_api_client: TestClient) -> None:
    response = documents_api_client.post(
        "/documents",
        files={
            "file": (
                "handbook.docx",
                _minimal_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    _assert_no_storage_path(body)
    assert body["original_filename"] == "handbook.docx"
    assert body["doc_type"] == "other"
    assert body["status"] == "pending"
    assert body["content_type"].startswith("application/vnd.openxmlformats")


def test_upload_txt_success(documents_api_client: TestClient) -> None:
    payload = b"Employees may request unpaid leave with manager approval.\n"
    response = documents_api_client.post(
        "/documents",
        files={"file": ("notes.txt", payload, "text/plain")},
        data={"doc_type": "handbook"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    _assert_no_storage_path(body)
    assert body["doc_type"] == "handbook"
    assert body["title"] is None
    assert body["checksum"] == hashlib.sha256(payload).hexdigest()


def test_upload_default_doc_type_is_other(documents_api_client: TestClient) -> None:
    response = documents_api_client.post(
        "/documents",
        files={"file": ("plain.txt", b"hello policy\n", "text/plain")},
    )
    assert response.status_code == 201
    assert response.json()["doc_type"] == "other"


def test_upload_rejects_invalid_file_type(documents_api_client: TestClient) -> None:
    response = documents_api_client.post(
        "/documents",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_upload_rejects_oversized_file(documents_api_client: TestClient, tmp_path: Path) -> None:
    payload = b"x" * 2048
    response = documents_api_client.post(
        "/documents",
        files={"file": ("big.txt", payload, "text/plain")},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"
    assert list((tmp_path / "documents").glob("*")) == []


def test_upload_rejects_duplicate_checksum(documents_api_client: TestClient, tmp_path: Path) -> None:
    files = {"file": ("a.pdf", _minimal_pdf_bytes(), "application/pdf")}
    first = documents_api_client.post("/documents", files=files)
    assert first.status_code == 201
    second = documents_api_client.post(
        "/documents",
        files={"file": ("b.pdf", _minimal_pdf_bytes(), "application/pdf")},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"
    assert len(list((tmp_path / "documents").iterdir())) == 1


def test_list_pagination_and_filters(documents_api_client: TestClient) -> None:
    documents_api_client.post(
        "/documents",
        files={"file": ("one.txt", b"alpha document text\n", "text/plain")},
        data={"doc_type": "policy"},
    )
    documents_api_client.post(
        "/documents",
        files={"file": ("two.txt", b"beta document text\n", "text/plain")},
        data={"doc_type": "handbook"},
    )
    documents_api_client.post(
        "/documents",
        files={"file": ("three.txt", b"gamma document text\n", "text/plain")},
        data={"doc_type": "policy"},
    )

    page1 = documents_api_client.get("/documents", params={"page": 1, "page_size": 2})
    assert page1.status_code == 200
    body = page1.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["pages"] == 2
    assert len(body["items"]) == 2
    for item in body["items"]:
        _assert_no_storage_path(item)

    filtered = documents_api_client.get(
        "/documents",
        params={"doc_type": "policy", "status": "pending"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert all(item["doc_type"] == "policy" for item in filtered.json()["items"])


def test_get_document_by_id_and_unknown(
    documents_api_client: TestClient,
) -> None:
    created = documents_api_client.post(
        "/documents",
        files={"file": ("get-me.txt", b"lookup target\n", "text/plain")},
    ).json()
    response = documents_api_client.get(f"/documents/{created['id']}")
    assert response.status_code == 200
    body = response.json()
    _assert_no_storage_path(body)
    assert body["id"] == created["id"]
    # Round-trip through schema to ensure storage_path stays excluded.
    assert "storage_path" not in HrDocumentRead.model_validate(body).model_dump()

    missing = documents_api_client.get(
        "/documents/00000000-0000-4000-8000-000000000000"
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_api_db_failure_does_not_leave_orphan(
    db_session: Session,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-Documents-Orphan",
        database_url=integration_database_url,
        rag_document_dir=str(tmp_path / "documents"),
        max_upload_bytes=1024,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch.object(db_session, "commit", side_effect=RuntimeError("db down")):
            response = client.post(
                "/documents",
                files={"file": ("orphan.pdf", _minimal_pdf_bytes(), "application/pdf")},
            )
        assert response.status_code == 500
    assert list((tmp_path / "documents").iterdir()) == []
