"""Typed settings loaded from env — shared across all services."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # Database
    database_url: str = Field(
        default="postgresql+psycopg2://stockai:stockai_dev@postgres:5432/stockai"
    )

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Auth
    jwt_secret: str = "stockai-change-me-in-production-secret-key"
    jwt_expire_days: int = 7
    admin_password: str = ""   # required in production; seed skipped if empty

    # CORS — comma-separated allowed origins; defaults to * in dev, must be set in prod
    cors_origins: str = ""

    def __post_init__(self) -> None:
        pass  # kept for subclass compatibility

    def model_post_init(self, __context) -> None:  # pydantic v2 hook
        _WEAK = "stockai-change-me-in-production-secret-key"
        if self.env != "development" and self.jwt_secret == _WEAK:
            raise RuntimeError(
                "JWT_SECRET must be set to a strong random value in non-development envs. "
                "The default placeholder is not safe."
            )
        if self.env != "development" and (not self.cors_origins or "*" in self.cors_origins):
            import warnings
            warnings.warn(
                "CORS_ORIGINS is not set or uses wildcard '*' in non-development env. "
                "Set CORS_ORIGINS=https://yourdomain.com in .env.production to lock down CORS.",
                stacklevel=2,
            )

    # Providers
    alpha_vantage_api_key: str = ""
    polygon_api_key: str = ""
    itick_api_key: str = ""

    # Service URLs
    market_data_url: str = "http://market-data:8001"
    technical_analysis_url: str = "http://technical-analysis:8002"
    ml_prediction_url: str = "http://ml-prediction:8003"
    ranking_engine_url: str = "http://ranking-engine:8004"
    signal_engine_url: str = "http://signal-engine:8005"
    strategy_engine_url: str = "http://strategy-engine:8006"
    portfolio_optimizer_url: str = "http://portfolio-optimizer:8007"
    research_engine_url: str = "http://research-engine:8008"
    decision_engine_url: str = "http://decision-engine:8009"
    event_intelligence_url: str = "http://event-intelligence:8010"

    # Event Intelligence
    fred_api_key: str = ""      # https://fred.stlouisfed.org/docs/api/api_key.html (free)
    fmp_api_key: str = ""       # https://site.financialmodelingprep.com (free tier)

    # Paper trading — disabled by default in development; set ENABLE_PAPER_TRADING=true in production .env
    enable_paper_trading: bool = False

    # Storage
    parquet_dir: str = "/data/parquet"
    model_dir: str = "/data/models"

    # Email — set EMAIL_PROVIDER=ses or EMAIL_PROVIDER=smtp
    email_provider: str = ""          # "ses" | "smtp" | "" (disabled)
    email_from: str = ""              # sender address shown in From:
    # SMTP (Gmail or any SMTP relay)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""           # Gmail: use an App Password
    # AWS SES
    ses_region: str = "us-east-1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
