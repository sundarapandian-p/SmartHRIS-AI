"""Secure temporary storage for bulk import uploads."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from smarthris.core.exceptions import PayloadTooLargeError, StorageError, ValidationError

CHUNK_SIZE = 64 * 1024
_ZIP_MAGIC = b"PK"


def sanitize_original_filename(filename: str | None) -> str:
    """
    Return a safe basename for DB display only.

    Never use the result to build storage paths.
    """
    if filename is None or not str(filename).strip():
        raise ValidationError("Uploaded file must include a filename")

    # Normalize Windows and Unix separators before taking the basename.
    normalized = str(filename).replace("\\", "/").strip()
    name = Path(normalized).name.strip()
    if not name or name in {".", ".."}:
        raise ValidationError("Uploaded file must include a valid filename")

    if len(name) > 255:
        name = name[:255]
    return name


def assert_csv_filename(filename: str) -> None:
    """Require a case-insensitive .csv extension."""
    if Path(filename).suffix.lower() != ".csv":
        raise ValidationError("Only CSV uploads are supported")


def import_storage_path(import_temp_dir: str | Path, job_id: str) -> Path:
    """Server-controlled path: ``{import_temp_dir}/{job_id}.csv``."""
    base = Path(import_temp_dir).expanduser().resolve()
    return base / f"{job_id}.csv"


def ensure_import_temp_dir(import_temp_dir: str | Path) -> Path:
    base = Path(import_temp_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def delete_import_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Best-effort cleanup; original error is more important to the caller.
        pass


def copy_import_file(
    source: Path,
    destination: Path,
    *,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """
    Stream-copy ``source`` to ``destination`` with exclusive create.

    Never moves or overwrites. Deletes a partial destination on failure.
    Returns bytes written.
    """
    if not source.is_file():
        raise StorageError("Source import file not found")

    destination.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    try:
        # Exclusive create: fail if the destination already exists.
        with source.open("rb") as src, destination.open("xb") as dest:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                dest.write(chunk)
                bytes_written += len(chunk)
    except FileExistsError as exc:
        raise StorageError("Destination import file already exists") from exc
    except OSError as exc:
        delete_import_file(destination)
        raise StorageError("Failed to copy import file") from exc
    except Exception:
        delete_import_file(destination)
        raise

    if bytes_written == 0:
        delete_import_file(destination)
        raise StorageError("Source import file is empty")

    return bytes_written


def save_upload_stream(
    *,
    source: BinaryIO,
    destination: Path,
    max_upload_bytes: int,
    chunk_size: int = CHUNK_SIZE,
) -> int:
    """
    Copy ``source`` to ``destination`` in bounded chunks.

    Enforces ``max_upload_bytes``. Deletes a partial destination on failure.
    Returns the number of bytes written.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    first_chunk: bytes | None = None

    try:
        with destination.open("wb") as target:
            while True:
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                if first_chunk is None:
                    first_chunk = chunk
                    if chunk.startswith(_ZIP_MAGIC):
                        raise ValidationError(
                            "Uploaded file looks like a ZIP/XLSX archive; only CSV is supported"
                        )
                bytes_written += len(chunk)
                if bytes_written > max_upload_bytes:
                    raise PayloadTooLargeError(
                        f"Uploaded file exceeds the maximum allowed size "
                        f"of {max_upload_bytes} bytes"
                    )
                target.write(chunk)
    except (PayloadTooLargeError, ValidationError):
        delete_import_file(destination)
        raise
    except OSError as exc:
        delete_import_file(destination)
        raise StorageError("Failed to store uploaded file") from exc
    except Exception:
        delete_import_file(destination)
        raise

    if bytes_written == 0:
        delete_import_file(destination)
        raise ValidationError("Uploaded file is empty")

    return bytes_written


def new_job_id() -> str:
    return str(uuid4())
