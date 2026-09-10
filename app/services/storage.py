"""Filesystem storage for uploaded PDFs and converted page images.

Blobs stay out of the database: a single scanned receipt PDF is already
megabytes, and the page images the convert API returns are larger still.

Paths are stored *relative* to ``STORAGE_DIR`` so the database never records a
machine-specific absolute path -- moving the volume does not invalidate rows.
Swapping this for object storage means reimplementing this one class.
"""

import uuid
from pathlib import Path

from app.core.config import Settings


class FileStorage:
    def __init__(self, settings: Settings) -> None:
        self.root = settings.storage_dir

    def _absolute(self, relative_path: str) -> Path:
        return self.root / relative_path

    def save_pdf(self, document_id: uuid.UUID, data: bytes) -> str:
        relative = f"pdfs/{document_id}.pdf"
        self._write(relative, data)
        return relative

    def save_page_image(self, document_id: uuid.UUID, page_no: int, data: bytes) -> str:
        relative = f"images/{document_id}/{page_no:04d}.jpg"
        self._write(relative, data)
        return relative

    def _write(self, relative_path: str, data: bytes) -> None:
        target = self._absolute(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def read(self, relative_path: str) -> bytes:
        return self._absolute(relative_path).read_bytes()

    def exists(self, relative_path: str) -> bool:
        return self._absolute(relative_path).is_file()

    def path_for(self, relative_path: str) -> Path:
        """Absolute path, for handing a file to a streaming response."""
        return self._absolute(relative_path)
