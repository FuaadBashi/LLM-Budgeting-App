from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost/budgetapp"
    test_database_url: str = "postgresql+psycopg://localhost/budgetapp_test"
    echo_sql: bool = False


settings = Settings()
