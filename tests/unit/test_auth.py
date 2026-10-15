import pytest

from jarvis.security.auth import AccessDenied, Authorizer


def test_allowed_user() -> None:
    authorizer = Authorizer([111, 222])
    assert authorizer.is_allowed(111)
    assert authorizer.is_allowed(222)
    authorizer.ensure_allowed(111)


def test_denied_user() -> None:
    authorizer = Authorizer([111])
    assert not authorizer.is_allowed(333)
    with pytest.raises(AccessDenied):
        authorizer.ensure_allowed(333)


def test_empty_allowlist_denies_everyone() -> None:
    authorizer = Authorizer([])
    assert not authorizer.is_allowed(111)
    with pytest.raises(AccessDenied):
        authorizer.ensure_allowed(111)


def test_none_user_is_denied() -> None:
    authorizer = Authorizer([111])
    assert not authorizer.is_allowed(None)
    with pytest.raises(AccessDenied):
        authorizer.ensure_allowed(None)
