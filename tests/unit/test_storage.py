"""File storage: relative paths in, files on disk."""

import uuid

from app.core.config import Settings
from app.services.storage import FileStorage


def _storage(tmp_path) -> FileStorage:
    return FileStorage(Settings(storage_dir=tmp_path, fa_api_token="t"))


def test_a_saved_pdf_is_stored_by_relative_path(tmp_path):
    storage = _storage(tmp_path)
    document_id = uuid.uuid4()

    relative = storage.save_pdf(document_id, b"%PDF-1.4 x")

    # Relative, so moving the volume does not invalidate rows.
    assert relative == f"pdfs/{document_id}.pdf"
    assert storage.exists(relative)
    assert storage.read(relative) == b"%PDF-1.4 x"


def test_page_images_are_numbered_so_they_sort_in_page_order(tmp_path):
    storage = _storage(tmp_path)
    document_id = uuid.uuid4()

    first = storage.save_page_image(document_id, 1, b"a")
    tenth = storage.save_page_image(document_id, 10, b"b")

    assert first.endswith("0001.jpg") and tenth.endswith("0010.jpg")
    assert first < tenth


def test_path_for_points_at_the_file_on_disk(tmp_path):
    storage = _storage(tmp_path)
    relative = storage.save_page_image(uuid.uuid4(), 1, b"jpeg")

    assert storage.path_for(relative).read_bytes() == b"jpeg"
    assert storage.path_for(relative).is_relative_to(tmp_path)


def test_a_file_that_was_never_written_does_not_exist(tmp_path):
    assert not _storage(tmp_path).exists("images/nothing/0001.jpg")
