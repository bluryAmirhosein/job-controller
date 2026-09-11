from datetime import timedelta

import pytest
from jose import jwt

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

settings = get_settings()


class TestPasswordHashing:
    def test_hash_password_returns_different_value_than_plain(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"

    def test_verify_password_succeeds_with_correct_password(self):
        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed) is True

    def test_verify_password_fails_with_wrong_password(self):
        hashed = hash_password("secret123")
        assert verify_password("wrong-password", hashed) is False

    def test_hash_password_is_salted_and_not_deterministic(self):
        # bcrypt generates a new salt each time, so two hashes of the same password should not be equal.
        assert hash_password("secret123") != hash_password("secret123")


class TestAccessToken:
    def test_create_access_token_contains_subject_role_and_expiry(self):
        token = create_access_token(subject="user-id-123", role="user")

        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )

        assert payload["sub"] == "user-id-123"
        assert payload["role"] == "user"
        assert "exp" in payload

    def test_decode_access_token_returns_original_claims(self):
        token = create_access_token(subject="user-id-123", role="admin")

        decoded = decode_access_token(token)

        assert decoded["sub"] == "user-id-123"
        assert decoded["role"] == "admin"

    def test_decode_access_token_raises_value_error_for_malformed_token(self):
        with pytest.raises(ValueError):
            decode_access_token("this-is-not-a-valid-jwt")

    def test_decode_access_token_raises_value_error_for_expired_token(self):
        token = create_access_token(
            subject="user-id-123", role="user", expires_delta=timedelta(seconds=-1)
        )

        with pytest.raises(ValueError):
            decode_access_token(token)