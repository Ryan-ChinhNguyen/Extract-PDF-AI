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
