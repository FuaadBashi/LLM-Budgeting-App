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

    #: Anthropic API key. Empty means every LLM feature is simply off -- the app
    #: works exactly as it does without one, with no suggestions. A separate
    #: product from a Claude.ai subscription; get one at console.anthropic.com.
    anthropic_api_key: str = ""
    #: Classification is a small-model job. Reserve larger models for prose.
    llm_model: str = "claude-haiku-4-5-20251001"
    #: Hard ceiling on a single reply. Categorisation answers are a few tokens;
    #: this is a runaway guard, not a target.
    llm_max_tokens: int = 256
    #: Reading a receipt is a vision task and needs a more capable model than
    #: classification does. Still the cheapest one that reads crumpled paper.
    llm_vision_model: str = "claude-sonnet-5"


settings = Settings()
