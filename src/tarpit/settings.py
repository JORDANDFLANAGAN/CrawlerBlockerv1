import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="TARPIT_", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    log_level: str = "info"

    corpus_path: Path = Path("src/tarpit/corpus.txt")
    log_path: Path = Path("logs/access.log")

    delay_min_s: float = 2.0
    delay_max_s: float = 5.0


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
