from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CRAWLERBLOCKER_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = True
    log_level: str = "info"


@lru_cache
def get_settings() -> Settings:
    return Settings()
