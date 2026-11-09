"""Private storage helpers for HR document uploads (PDF, DOCX, TXT).

Separate from CSV import storage. Never use user filenames as storage paths.
Do not log file contents or raw bytes.
"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final
from uuid import uuid4

from smarthris.core.exceptions import PayloadTooLargeError, StorageError, ValidationError

CHUNK_SIZE: Final[int] = 64 * 1024
DEFAULT_MAX_UPLOAD_BYTES: Final[int] = 52_428_800  # 50 MiB

ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({".pdf", ".docx", ".txt"})

_PDF_MAGIC: Final[bytes] = b"%PDF"
_ZIP_MAGIC: Final[bytes] = b"PK"
_DOCX_REQUIRED_MEMBERS: Final[frozenset[str]] = frozenset(
    {
        "[Content_Types].xml",
        "word/document.xml",
    }
)

_CONTENT_TYPES: Final[dict[str, str]] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


@dataclass(frozen=True)
class DocumentStorageResult:
    """Metadata returned after a successful private document store."""

    document_id: str
    original_filename: str
    storage_path: Path
    size_bytes: int
    checksum_sha256: str
    content_type: str


def new_document_id() -> str:
    return str(uuid4())


def sanitize_original_filename(filename: str | None) -> str:
    """
    Return a safe basename for DB display only.

    Never use the result to build storage paths.
    """
    if filename is None or not str(filename).strip():
        raise ValidationError("Uploaded file must include a filename")

    normalized = str(filename).replace("\\", "/").strip()
    name = Path(normalized).name.strip()
    if not name or name in {".", ".."}:
        raise ValidationError("Uploaded file must include a valid filename")

    if len(name) > 255:
        name = name[:255]
    return name


def assert_allowed_document_extension(filename: str) -> str:
    """
    Require an allowlisted document extension.

    Returns the normalized lowercase suffix (e.g. ``.pdf``).
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "Unsupported document type; only PDF, DOCX, and TXT uploads are supported"
        )
    return suffix


def ensure_document_dir(rag_document_dir: str | Path) -> Path:
    """Create and return the resolved private document directory."""
    base = Path(rag_document_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def document_storage_path(
    rag_document_dir: str | Path,
    document_id: str,
    extension: str,
) -> Path:
    """
    Server-controlled path: ``{rag_document_dir}/{document_id}{extension}``.

    ``extension`` must be an allowlisted suffix (including the leading dot).
    """
    suffix = extension.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "Unsupported document type; only PDF, DOCX, and TXT uploads are supported"
        )
    if not document_id or "/" in document_id or "\\" in document_id or ".." in document_id:
        raise ValidationError("Invalid document id")

    base = Path(rag_document_dir).expanduser().resolve()
    destination = (base / f"{document_id}{suffix}").resolve()
    _assert_path_inside(base, destination)
    return destination


def delete_document_file(path: Path) -> None:
    """Best-effort delete; callers preserve the original error."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def save_document_upload(
    *,
    source: BinaryIO,
    rag_document_dir: str | Path,
    original_filename: str | None,
    document_id: str | None = None,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    chunk_size: int = CHUNK_SIZE,
) -> DocumentStorageResult:
    """
    Stream ``source`` into a private UUID-named file under ``rag_document_dir``.

    Validates extension and content signatures, enforces size limits, computes
    SHA-256 while writing, and uses exclusive create (no overwrite).
    Deletes any partial file on failure.
    """
    display_name = sanitize_original_filename(original_filename)
    extension = assert_allowed_document_extension(display_name)
    content_type = _CONTENT_TYPES[extension]

    doc_id = document_id or new_document_id()
    base = ensure_document_dir(rag_document_dir)
    destination = document_storage_path(base, doc_id, extension)

    digest = hashlib.sha256()
    bytes_written = 0
    first_chunk: bytes | None = None

    try:
        # Exclusive create: fail if the destination already exists.
        with destination.open("xb") as target:
            while True:
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                if first_chunk is None:
                    first_chunk = chunk
                    _validate_magic(extension, chunk)
                if extension == ".txt" and b"\x00" in chunk:
                    raise ValidationError("TXT upload contains binary (NUL) content")
                bytes_written += len(chunk)
                if bytes_written > max_upload_bytes:
                    raise PayloadTooLargeError(
                        f"Uploaded file exceeds the maximum allowed size "
                        f"of {max_upload_bytes} bytes"
                    )
                digest.update(chunk)
                target.write(chunk)
    except FileExistsError as exc:
        raise StorageError("Destination document file already exists") from exc
    except (PayloadTooLargeError, ValidationError):
        delete_document_file(destination)
        raise
    except OSError as exc:
        delete_document_file(destination)
        raise StorageError("Failed to store uploaded document") from exc
    except Exception:
        delete_document_file(destination)
        raise

    if bytes_written == 0:
        delete_document_file(destination)
        raise ValidationError("Uploaded file is empty")

    try:
        if extension == ".docx":
            _validate_docx_archive(destination)
        elif extension == ".pdf":
            # Re-check leading signature after write (defense in depth).
            with destination.open("rb") as stored:
                head = stored.read(5)
            if not head.startswith(_PDF_MAGIC):
                raise ValidationError("File content is not a valid PDF")
    except (ValidationError, StorageError):
        delete_document_file(destination)
        raise
    except Exception:
        delete_document_file(destination)
        raise

    _assert_path_inside(base, destination.resolve())

    return DocumentStorageResult(
        document_id=doc_id,
        original_filename=display_name,
        storage_path=destination,
        size_bytes=bytes_written,
        checksum_sha256=digest.hexdigest(),
        content_type=content_type,
    )


def _assert_path_inside(base: Path, target: Path) -> None:
    base_resolved = base.resolve()
    target_resolved = target.resolve()
    if not target_resolved.is_relative_to(base_resolved):
        raise StorageError("Document storage path escaped the document directory")


def _validate_magic(extension: str, first_chunk: bytes) -> None:
    if extension == ".pdf":
        if not first_chunk.startswith(_PDF_MAGIC):
            raise ValidationError("File content is not a valid PDF")
        return
    if extension == ".docx":
        if not first_chunk.startswith(_ZIP_MAGIC):
            raise ValidationError("File content is not a valid DOCX archive")
        return
    if extension == ".txt":
        if first_chunk.startswith(_PDF_MAGIC) or first_chunk.startswith(_ZIP_MAGIC):
            raise ValidationError("TXT upload content does not match a plain-text document")
        if b"\x00" in first_chunk:
            raise ValidationError("TXT upload contains binary (NUL) content")
        return
    raise ValidationError(
        "Unsupported document type; only PDF, DOCX, and TXT uploads are supported"
    )


def _validate_docx_archive(path: Path) -> None:
    """Require ZIP structure with standard DOCX members (not extension-only)."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            if archive.testzip() is not None:
                raise ValidationError("DOCX archive is corrupted")
            names = set(archive.namelist())
    except ValidationError:
        raise
    except zipfile.BadZipFile as exc:
        raise ValidationError("File content is not a valid DOCX archive") from exc
    except OSError as exc:
        raise StorageError("Failed to validate DOCX archive") from exc

    missing = _DOCX_REQUIRED_MEMBERS - names
    if missing:
        raise ValidationError("DOCX archive is missing required document entries")
