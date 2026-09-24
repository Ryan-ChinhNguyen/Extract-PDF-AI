"""Configuration loading."""

import pytest

from app.core import config
from app.core.config import Settings


def test_the_dev_file_supplies_every_setting():
    """No field has a code default, so a gap in config/dev.env fails here."""
    settings = Settings()

    assert settings.app_env == "dev"
    assert settings.document_ttl_days == 7
    assert settings.ocr_low_confidence_threshold == 0.80


def test_an_unknown_environment_fails_with_its_name(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "does-not-exist")
    config.get_settings.cache_clear()

    try:
        with pytest.raises(RuntimeError, match="does-not-exist"):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()


def test_derived_urls_and_directories_follow_the_settings(tmp_path):
    settings = Settings(
        storage_dir=tmp_path,
        fa_base_url="https://api.example.test/fa",
        fa_api_version="v9",
        fa_api_token="t",
    )

    assert settings.fa_convert_url == "https://api.example.test/fa/v9/convert_to_jpg"
    assert settings.fa_receipt_url == "https://api.example.test/fa/v9/receipt"
    assert settings.images_dir == tmp_path / "images"


def test_get_settings_is_cached():
    config.get_settings.cache_clear()
    try:
        assert config.get_settings() is config.get_settings()
    finally:
        config.get_settings.cache_clear()
