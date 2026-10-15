"""Конфигурация приложения: env-переменные + проверка обязательных значений."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from jarvis.security.crypto import KeyRing, decode_key, keyring_from_config


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["dev", "prod"] = "dev"
    app_timezone: str = "Europe/Moscow"
    log_level: str = "INFO"

    # --- Telegram ---
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    # Прокси для исходящих запросов к Telegram API (нужен при работе из РФ/VPN).
    # Формат: http://host:port или socks5://host:port. Пусто — без прокси.
    bot_proxy: str = ""

    # --- База данных ---
    database_url: str = "postgresql+asyncpg://jarvis:jarvis@127.0.0.1:5433/jarvis"
    db_echo: bool = False

    # --- HTTP health ---
    health_host: str = "127.0.0.1"
    health_port: int = 8080

    # --- Зашифрованное хранилище ---
    document_storage_path: Path = Path("var/documents")
    document_encryption_key: SecretStr = SecretStr("")
    document_encryption_key_version: int = 1
    document_encryption_old_keys: str = ""
    secrets_encryption_key: SecretStr = SecretStr("")
    max_document_bytes: int = 50 * 1024 * 1024
    max_voice_bytes: int = 20 * 1024 * 1024

    # --- AI ---
    ai_provider: str = "none"
    opencode_go_api_key: SecretStr = SecretStr("")
    opencode_go_base_url: str = "https://opencode.ai/inference/go/v1"
    ai_model: str = "deepseek-v4.1-flash"
    ai_timeout_seconds: float = 60.0
    ai_max_attempts: int = 2
    ai_max_context_chars: int = 20000

    # --- Транскрибация ---
    transcription_provider: str = "none"
    transcription_base_url: str = ""
    transcription_api_key: SecretStr = SecretStr("")
    transcription_model: str = "whisper-1"
    transcription_language: str = "ru"
    transcription_timeout_seconds: float = 120.0

    # --- MCP ---
    mcp_auth_token: SecretStr = SecretStr("")
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    mcp_include_sensitive: bool = False

    # --- Improvement backlog (Stage 2) ---
    # Гипотезы LLM ниже порога не попадают в backlog (не засоряем список).
    improvement_hypothesis_min_confidence: float = 0.75
    improvement_context_max_chars: int = 4000
    # Каталог для выгрузок improvement backlog (Markdown/JSON).
    export_dir: Path = Path("var/exports")

    # --- Журнал чата (дневной экспорт диалога) ---
    # Тексты сохраняются уже redacted; журнал можно выключить и он чистится.
    chat_log_enabled: bool = True
    chat_log_retention_days: int = 30

    # --- AI-агент (Stage 3) ---
    agent_enabled: bool = True
    # Бюджеты времени (секунды, monotonic) по профилям запроса.
    agent_ordinary_seconds: float = 60.0
    agent_planning_seconds: float = 120.0
    agent_research_seconds: float = 300.0
    # Лимиты шагов оркестрации (LLM-вызов + вызовы инструментов) по профилям.
    agent_ordinary_steps: int = 12
    agent_planning_steps: int = 20
    agent_research_steps: int = 40
    # Абсолютный лимит LLM-вызовов на запрос и повторов одинаковых tool-вызовов.
    agent_max_llm_calls: int = 18
    agent_max_repeat_tool_calls: int = 2
    agent_max_tool_retries: int = 1
    # Ожидание очереди, если для пользователя уже выполняется запрос.
    agent_queue_wait_seconds: float = 90.0
    # Лимиты web-запросов по профилям.
    agent_web_queries_ordinary: int = 1
    agent_web_queries_planning: int = 3
    agent_web_queries_research: int = 8
    # Мягкий/жёсткий лимит токенов (действует, только если провайдер отдаёт usage).
    agent_token_soft_limit: int = 25000
    agent_token_hard_limit: int = 50000
    # Маркеры профилей (нижний регистр, подстрока в тексте запроса).
    agent_planning_markers: str = "спланир,план на,планируем,составь план"
    agent_research_markers: str = "исследуй,глубокий поиск,разберись детально,research"

    # --- Web-поиск (интерфейс research/) ---
    # none | fake (реальный провайдер не подключён: нет настроенного API)
    web_search_provider: str = "none"
    web_search_max_results: int = 5

    # --- Workflow TTL ---
    conversation_state_ttl_hours: int = 24
    pending_operation_ttl_minutes: int = 30
    processed_updates_retention_hours: int = 72

    # --- Валидаторы ---

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _parse_allowed_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("["):
                return json.loads(value)
            return [int(part) for part in value.split(",") if part.strip()]
        return value

    @field_validator("app_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:  # noqa: BLE001 - любая ошибка tz указывает на плохой конфиг
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value

    # --- Производные значения ---

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def bot_token(self) -> str:
        return self.telegram_bot_token.get_secret_value()

    @property
    def mcp_token(self) -> str:
        return self.mcp_auth_token.get_secret_value()

    @property
    def has_document_key(self) -> bool:
        return bool(self.document_encryption_key.get_secret_value())

    def document_keyring(self) -> KeyRing:
        return keyring_from_config(
            active_key=self.document_encryption_key.get_secret_value(),
            active_version=self.document_encryption_key_version,
            old_keys=self.document_encryption_old_keys,
        )

    def secrets_keyring(self) -> KeyRing:
        dedicated = self.secrets_encryption_key.get_secret_value()
        if dedicated:
            return keyring_from_config(
                active_key=dedicated,
                active_version=self.document_encryption_key_version,
                old_keys=self.document_encryption_old_keys,
            )
        return self.document_keyring()

    # --- Проверки перед запуском ---

    def bot_config_problems(self) -> list[str]:
        """Фатальные проблемы конфигурации бота (пустой список = ок)."""
        problems: list[str] = []
        if self.app_env == "prod":
            if not self.bot_token:
                problems.append("TELEGRAM_BOT_TOKEN is not set")
            if not self.telegram_allowed_user_ids:
                problems.append(
                    "TELEGRAM_ALLOWED_USER_IDS is empty: refusing to start in prod"
                )
            if not self.has_document_key:
                problems.append("DOCUMENT_ENCRYPTION_KEY is not set")
        elif self.bot_token and not self.telegram_allowed_user_ids:
            problems.append(
                "TELEGRAM_ALLOWED_USER_IDS is empty: the bot would deny everyone"
            )
        return problems

    def mcp_config_problems(self) -> list[str]:
        problems: list[str] = []
        if not self.mcp_token:
            problems.append("MCP_AUTH_TOKEN is not set")
        if not self.has_document_key and self.mcp_include_sensitive:
            problems.append("DOCUMENT_ENCRYPTION_KEY is required for sensitive MCP access")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings", "decode_key"]
