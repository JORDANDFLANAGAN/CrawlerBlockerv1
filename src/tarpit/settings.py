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

    detection_enabled: bool = True
    detection_log_path: Path = Path("logs/detection.log")
    detection_rate_budget: int = 60
    detection_rate_window_s: float = 60.0
    detection_state_max_entries: int = 10_000

    abuse_protection_enabled: bool = True
    abuse_log_path: Path = Path("logs/abuse.log")

    rl_per_ip_budget: int = 120
    rl_per_ip_window_s: float = 60.0
    rl_state_max_entries: int = 10_000

    cc_per_ip_max: int = 10

    gl_inflight_max: int = 500
    gl_rate_budget: int = 1_000
    gl_window_s: float = 60.0


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
