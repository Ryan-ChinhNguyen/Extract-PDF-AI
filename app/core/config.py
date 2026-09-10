"""Application settings, loaded once from the environment / .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PDF Receipt Extraction"
    debug: bool = False

    # --- Database -------------------------------------------------------
    database_url: str
    db_echo: bool = False

    # --- FastAccounting API ---------------------------------------------
    fa_base_url: str = "https://api-gt01-sandbox.fastaccounting.jp/fa"
    fa_api_version: str = "v1.5"
    fa_api_token: str = ""
    fa_timeout_seconds: float = 60.0

    # --- Retention ------------------------------------------------------
    # A document stays re-runnable for this many days. Once it lapses the
    # content hash is released so the same file can be uploaded again.
    document_ttl_days: int = Field(default=7, ge=1)

    # --- OCR presentation -----------------------------------------------
    # Below this score a field is flagged for a human to check. The engine
    # reports 0-1; this is a display threshold only -- nothing is rejected,
    # retried or hidden because of it, and the stored value is untouched.
    ocr_low_confidence_threshold: float = Field(default=0.80, ge=0, le=1)

    # --- Background worker ----------------------------------------------
    # The pipeline runs out of band: endpoints only move rows to `pending`
    # and this worker picks them up, so a restart resumes instead of losing
    # the job.
    worker_enabled: bool = True
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    worker_batch_size: int = Field(default=5, ge=1)
    # A row claimed longer ago than this is assumed to belong to a worker
    # that died, and is released back to `pending`.
    worker_claim_timeout_seconds: int = Field(default=300, ge=30)

    # --- Storage --------------------------------------------------------
    storage_dir: Path = Path("storage")

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
    return Settings()  # type: ignore[call-arg]
