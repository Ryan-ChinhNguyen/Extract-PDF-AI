"""Application settings.

Values come from ``config/<APP_ENV>.env`` (committed, no secrets), then
``.env`` (not committed, secrets), then real environment variables -- each
overriding the one before. ``APP_ENV`` defaults to ``dev``.

The fields below deliberately carry no default values: a value that also
lived here would be a second place to change it, and the one that silently
wins. Only the validation rules stay in code.
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
APP_ENV = os.getenv("APP_ENV", "dev")


def env_config_file(app_env: str) -> Path:
    return CONFIG_DIR / f"{app_env}.env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later files win: the committed environment file first, local secrets on top.
        env_file=(env_config_file(APP_ENV), PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str
    debug: bool

    # --- Database -------------------------------------------------------
    database_url: str
    db_echo: bool

    # --- FastAccounting API ---------------------------------------------
    fa_base_url: str
    fa_api_version: str
    fa_api_token: str
    fa_timeout_seconds: float = Field(gt=0)

    # --- Retention ------------------------------------------------------
    # A document stays re-runnable for this many days. Once it lapses the
    # content hash is released so the same file can be uploaded again.
    document_ttl_days: int = Field(ge=1)

    # --- OCR presentation -----------------------------------------------
    # Below this score a field is flagged for a human to check. Display only:
    # nothing is rejected, retried or hidden because of it.
    ocr_low_confidence_threshold: float = Field(ge=0, le=1)

    # --- Background worker ----------------------------------------------
    worker_enabled: bool
    worker_poll_interval_seconds: float = Field(gt=0)
    worker_batch_size: int = Field(ge=1)
    # A row claimed longer ago than this is assumed to belong to a worker
    # that died, and is released back to `pending`.
    worker_claim_timeout_seconds: int = Field(ge=30)

    # --- Storage --------------------------------------------------------
    storage_dir: Path

    @property
    def app_env(self) -> str:
        return APP_ENV

    @property
    def images_dir(self) -> Path:
        return self.storage_dir / "images"

    @property
    def fa_convert_url(self) -> str:
        return f"{self.fa_base_url}/{self.fa_api_version}/convert_to_jpg"

    @property
    def fa_receipt_url(self) -> str:
        return f"{self.fa_base_url}/{self.fa_api_version}/receipt"


@lru_cache
def get_settings() -> Settings:
    config_file = env_config_file(APP_ENV)
    if not config_file.is_file():
        # pydantic-settings skips a missing env file silently, which would
        # surface later as a list of "field required" errors that never say
        # the real problem is an unknown environment name.
        raise RuntimeError(f"No configuration for APP_ENV={APP_ENV!r}: expected {config_file}")
    return Settings()  # type: ignore[call-arg]
