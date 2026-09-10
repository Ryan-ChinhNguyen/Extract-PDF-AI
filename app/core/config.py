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
