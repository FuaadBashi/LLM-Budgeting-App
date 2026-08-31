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
    #: Signs session cookies. Kept separate from the password hash so the hash
    #: can only verify a password, never mint a session.
    session_secret: str = ""
    #: Send the session cookie only over HTTPS. Leave false for localhost.
    cookie_secure: bool = False

    #: Where scheduled backups are written. Relative paths resolve against the
    #: process working directory, which for the usual `uvicorn` invocation is
    #: `backend/`.
    backup_dir: str = "backups"
    backup_enabled: bool = True
    #: How often the in-process timer writes one. The timer only runs while the
    #: API does; see docs/HANDOFF.md for wiring it to cron instead.
    backup_interval_hours: int = 24
    #: How many to retain. Zero or less keeps everything -- a misread config
    #: must not be a data-loss event.
    backup_keep: int = 14


settings = Settings()
