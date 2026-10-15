import base64

import pytest

from jarvis.security.crypto import (
    MAGIC,
    DecryptionError,
    FormatError,
    KeyRing,
    UnknownKeyVersion,
    decode_key,
    decrypt,
    encrypt,
    generate_key,
    keyring_from_config,
)

K1 = bytes(range(32))
K2 = bytes(range(32, 64))


def ring_v1() -> KeyRing:
    return KeyRing(keys={1: K1}, active_version=1)


def test_roundtrip() -> None:
    blob = encrypt(b"hello jarvis", ring_v1())
    assert blob[:4] == MAGIC
    assert decrypt(blob, ring_v1()) == b"hello jarvis"


def test_empty_payload_roundtrip() -> None:
    assert decrypt(encrypt(b"", ring_v1()), ring_v1()) == b""


def test_generate_key_is_valid() -> None:
    key = generate_key()
    assert len(decode_key(key)) == 32


def test_decode_key_accepts_standard_base64() -> None:
    standard = base64.b64encode(K1).decode("ascii")
    assert decode_key(standard) == K1


def test_decode_key_rejects_wrong_length() -> None:
    short = base64.b64encode(b"123").decode("ascii")
    with pytest.raises(ValueError, match="32 bytes"):
        decode_key(short)


def test_decode_key_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="base64"):
        decode_key("not-base64!!!")


def test_wrong_key_fails() -> None:
    blob = encrypt(b"secret", KeyRing(keys={1: K1}, active_version=1))
    with pytest.raises(DecryptionError):
        decrypt(blob, KeyRing(keys={1: K2}, active_version=1))


def test_tampered_ciphertext_fails() -> None:
    blob = bytearray(encrypt(b"secret", ring_v1()))
    blob[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        decrypt(bytes(blob), ring_v1())


def test_tampered_nonce_fails() -> None:
    blob = bytearray(encrypt(b"secret", ring_v1()))
    blob[9] ^= 0x01  # nonce начинается с 9-го байта (magic=4, fmt=1, version=4)
    with pytest.raises(DecryptionError):
        decrypt(bytes(blob), ring_v1())


def test_tampered_key_version_is_part_of_aad() -> None:
    blob = bytearray(encrypt(b"secret", ring_v1()))
    blob[5:9] = (2).to_bytes(4, "big")  # объявляем версию 2
    ring = KeyRing(keys={1: K1, 2: K1}, active_version=1)
    with pytest.raises(DecryptionError):
        decrypt(bytes(blob), ring)


def test_unknown_key_version() -> None:
    blob = encrypt(b"secret", KeyRing(keys={2: K2}, active_version=2))
    with pytest.raises(UnknownKeyVersion):
        decrypt(blob, ring_v1())


def test_bad_magic() -> None:
    blob = bytearray(encrypt(b"x", ring_v1()))
    blob[0:4] = b"XXXX"
    with pytest.raises(FormatError, match="magic"):
        decrypt(bytes(blob), ring_v1())


def test_unsupported_format_version() -> None:
    blob = bytearray(encrypt(b"x", ring_v1()))
    blob[4] = 99
    with pytest.raises(FormatError, match="format version"):
        decrypt(bytes(blob), ring_v1())


def test_truncated_blob() -> None:
    with pytest.raises(FormatError, match="too short"):
        decrypt(MAGIC, ring_v1())


def test_blob_key_version() -> None:
    from jarvis.security.crypto import blob_key_version

    blob = encrypt(b"x", KeyRing(keys={2: K2}, active_version=2))
    assert blob_key_version(blob) == 2
    with pytest.raises(FormatError):
        blob_key_version(b"short")


def test_keyring_requires_active_version() -> None:
    with pytest.raises(ValueError, match="active key version"):
        KeyRing(keys={1: K1}, active_version=2)


def test_keyring_validates_key_size() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        KeyRing(keys={1: b"short"}, active_version=1)


def test_keyring_from_config_with_old_keys() -> None:
    ring = keyring_from_config(
        active_key=base64.urlsafe_b64encode(K2).decode(),
        active_version=2,
        old_keys=f"1:{base64.urlsafe_b64encode(K1).decode()}",
    )
    blob_old = encrypt(b"old", KeyRing(keys={1: K1}, active_version=1))
    assert decrypt(blob_old, ring) == b"old"
    blob_new = encrypt(b"new", ring)
    assert decrypt(blob_new, ring) == b"new"


def test_keyring_from_config_rejects_duplicates() -> None:
    key = base64.urlsafe_b64encode(K1).decode()
    with pytest.raises(ValueError, match="duplicate"):
        keyring_from_config(active_key=key, active_version=1, old_keys=f"1:{key}")


def test_keyring_from_config_rejects_empty_active() -> None:
    with pytest.raises(ValueError, match="not configured"):
        keyring_from_config(active_key="", active_version=1)
