"""Фабрика AI-провайдера: протокол по имени модели и защита от неподдерживаемых."""

from __future__ import annotations

import base64

import pytest

from jarvis.ai import create_ai_provider
from jarvis.ai.opencode_go import OpenCodeGoProvider
from jarvis.config.settings import Settings

TEST_KEY_B64 = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
TEST_SECRETS_KEY_B64 = base64.urlsafe_b64encode(bytes(range(32, 64))).decode("ascii")


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "dev",
        "telegram_allowed_user_ids": [111],
        "document_encryption_key": TEST_KEY_B64,
        "secrets_encryption_key": TEST_SECRETS_KEY_B64,
        "ai_provider": "opencode_go",
        "ai_model": "deepseek-v4.1-flash",
        "opencode_go_api_key": "test-key",
        "opencode_go_base_url": "https://go.invalid/v1",
    }
    values.update(overrides)
    return Settings(**values)


def test_responses_protocol_selected_for_luna():
    provider = create_ai_provider(make_settings(ai_model="gpt-6-luna"))
    assert isinstance(provider, OpenCodeGoProvider)
    assert provider.protocol == "responses"


def test_chat_protocol_default_for_deepseek():
    provider = create_ai_provider(make_settings(ai_model="deepseek-v4.1-flash"))
    assert provider.protocol == "chat"


def test_anthropic_model_gives_clear_error():
    with pytest.raises(ValueError, match="Anthropic"):
        create_ai_provider(make_settings(ai_model="claude-haiku-5-5"))
