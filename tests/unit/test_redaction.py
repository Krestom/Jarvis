from jarvis.security.redaction import redact

TELEGRAM_TOKEN = "123456789:AAFakeTokenValueForTests_0123456789abc"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop"


def test_redacts_telegram_token() -> None:
    result = redact(f"token is {TELEGRAM_TOKEN} here")
    assert TELEGRAM_TOKEN not in result
    assert "REDACTED_TELEGRAM_TOKEN" in result


def test_redacts_jwt() -> None:
    assert JWT not in redact(f"Authorization: {JWT}")


def test_redacts_bearer_header() -> None:
    result = redact("Authorization: Bearer abcdef1234567890abcdef1234567890")
    assert "abcdef1234567890abcdef1234567890" not in result
    assert "REDACTED" in result


def test_redacts_password_assignment() -> None:
    result = redact("password=hunter2-super-secret")
    assert "hunter2-super-secret" not in result
    assert "password=[REDACTED]" in result


def test_redacts_private_key_block() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"
    result = redact(text)
    assert "MIIEow" not in result
    assert "REDACTED_PRIVATE_KEY" in result


def test_keeps_normal_text_intact() -> None:
    text = "Напомни купить токен для метро и настроить сервер"
    assert redact(text) == text


def test_empty_text() -> None:
    assert redact("") == ""
