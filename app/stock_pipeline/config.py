from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_port: int = 8910
    app_secret_key: str = "change-me"
    app_auth_enabled: bool = True
    admin_username: str = "admin"
    admin_password: str = ""

    database_url: str = "sqlite:///./stock-pipeline.db"
    stock_root: Path = Path("./stock-data")
    inbox_path: Path = Path("./stock-data/inbox")
    scan_interval_seconds: int = 300
    worker_poll_seconds: int = 2
    max_assets_per_day: int = 50
    max_uploads_total_per_day: int = 50

    ai_provider: str = "mock"
    ai_model: str = ""
    ai_confidence_threshold: float = 0.75
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    ai_base_url: str = ""
    ai_daily_budget_usd: float = 5.0

    mock_provider_enabled: bool = True
    shutterstock_enabled: bool = False
    shutterstock_ftps_host: str = "ftps.shutterstock.com"
    shutterstock_ftps_user: str = ""
    shutterstock_ftps_password: str = ""
    shutterstock_ftps_remote_dir: str = ""
    pond5_enabled: bool = False
    adobe_enabled: bool = False
    alamy_enabled: bool = False
    storyblocks_enabled: bool = False

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def ensure_storage(self) -> None:
        for path in (
            self.stock_root,
            self.inbox_path,
            self.stock_root / "library",
            self.stock_root / "derivatives",
            self.stock_root / "releases",
            self.stock_root / "exports",
            self.stock_root / "backups",
            self.stock_root / "logs",
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_storage()
    return settings
