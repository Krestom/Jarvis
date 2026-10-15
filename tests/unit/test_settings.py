import base64

import pytest
from pydantic import ValidationError

from jarvis.config.settings import Settings


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "telegram_allowed_user_ids": [111],
        "document_encryption_key": base64.urlsafe_b64encode(bytes(range(32))).decode(),
    }
    values.update(overrides)
    return Settings(**values)


def test_allowed_ids_from_comma_string() -> None:
    settings = make_settings(telegram_allowed_user_ids="111, 222 ,333")
    assert settings.telegram_allowed_user_ids == [111, 222, 333]


def test_allowed_ids_from_json_string() -> None:
    settings = make_settings(telegram_allowed_user_ids="[111, 222]")
    assert settings.telegram_allowed_user_ids == [111, 222]


def test_allowed_ids_empty_string() -> None:
    settings = make_settings(telegram_allowed_user_ids="")
    assert settings.telegram_allowed_user_ids == []


def test_unknown_timezone_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown timezone"):
        make_settings(app_timezone="Mars/Olympus")


def test_document_keyring_requires_key() -> None:
    settings = make_settings(document_encryption_key="")
    with pytest.raises(ValueError, match="not configured"):
        settings.document_keyring()


def test_document_keyring_rejects_bad_key() -> None:
    short = base64.urlsafe_b64encode(b"123").decode()
    settings = make_settings(document_encryption_key=short)
    with pytest.raises(ValueError, match="32 bytes"):
        settings.document_keyring()


def test_document_keyring_rejects_non_base64() -> None:
    settings = make_settings(document_encryption_key="not base64!!!")
    with pytest.raises(ValueError, match="base64"):
        settings.document_keyring()


def test_secrets_keyring_falls_back_to_document_key() -> None:
    settings = make_settings(secrets_encryption_key="")
    assert settings.secrets_keyring().keys == settings.document_keyring().keys


def test_secrets_keyring_dedicated_key() -> None:
    other = base64.urlsafe_b64encode(bytes(range(32, 64))).decode()
    settings = make_settings(secrets_encryption_key=other)
    assert settings.secrets_keyring().keys != settings.document_keyring().keys


def test_keyring_includes_old_keys() -> None:
    old = base64.urlsafe_b64encode(bytes(range(32, 64))).decode()
    settings = make_settings(
        document_encryption_key_version=2,
        document_encryption_old_keys=f"1:{old}",
    )
    ring = settings.document_keyring()
    assert ring.active_version == 2
    assert set(ring.keys) == {1, 2}


def test_prod_requires_token_and_allowlist() -> None:
    settings = make_settings(
        app_env="prod",
        telegram_bot_token="",
        telegram_allowed_user_ids=[],
    )
    problems = settings.bot_config_problems()
    assert any("TELEGRAM_BOT_TOKEN" in p for p in problems)
    assert any("ALLOWED_USER_IDS" in p for p in problems)


def test_dev_with_token_and_empty_allowlist_warns() -> None:
    settings = make_settings(telegram_bot_token="123:fake", telegram_allowed_user_ids=[])
    problems = settings.bot_config_problems()
    assert any("deny everyone" in p for p in problems)


def test_mcp_requires_token() -> None:
    settings = make_settings()
    assert any("MCP_AUTH_TOKEN" in p for p in settings.mcp_config_problems())


def test_dev_with_valid_config_has_no_problems() -> None:
    settings = make_settings(telegram_bot_token="123:fake")
    assert settings.bot_config_problems() == []


def test_bot_proxy_default_empty() -> None:
    assert make_settings().bot_proxy == ""


def test_create_bot_uses_proxy_from_settings() -> None:
    from jarvis.bot.app import create_bot

    settings = make_settings(
        telegram_bot_token="123456:AAFakeTokenForTests_0123456789abc",
        bot_proxy="socks5://127.0.0.1:1080",
    )
    bot = create_bot(settings)
    assert bot.session.proxy == "socks5://127.0.0.1:1080"
