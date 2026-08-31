from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost/budgetapp"
    test_database_url: str = "postgresql+psycopg://localhost/budgetapp_test"
    echo_sql: bool = False

    #: Empty means authentication is off. Set it with scripts/set_password.py.
    #: The password itself is never stored -- only this hash.
    auth_password_hash: str = ""
    #: Send the session cookie only over HTTPS. Leave false for localhost.
    cookie_secure: bool = False


settings = Settings()
