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

    # Storage
    parquet_dir: str = "/data/parquet"
    model_dir: str = "/data/models"


@lru_cache
def get_settings() -> Settings:
    return Settings()
