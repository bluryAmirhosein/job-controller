import base64
import uuid
from datetime import datetime, timezone

import pytest

from app.core.pagination import decode_cursor, encode_cursor


class TestEncodeDecodeCursor:
    def test_round_trips_created_at_and_id(self, make_job):
        job = make_job(
            created_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        )

        cursor = encode_cursor(job)
        decoded_created_at, decoded_id = decode_cursor(cursor)

        assert decoded_created_at == job.created_at
        assert decoded_id == job.id

    def test_cursor_is_url_safe_base64(self, make_job):
        job = make_job()

        cursor = encode_cursor(job)

        # Must not raise, and must only contain URL-safe base64 characters.
        base64.urlsafe_b64decode(cursor.encode("ascii"))
        assert "+" not in cursor
        assert "/" not in cursor


class TestDecodeCursorErrors:
    def test_raises_value_error_for_non_base64_garbage(self):
        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor("not-valid-base64-!!!")

    def test_raises_value_error_for_valid_base64_but_invalid_json(self):
        garbage = base64.urlsafe_b64encode(b"not json at all").decode("ascii")

        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor(garbage)

    def test_raises_value_error_when_required_keys_are_missing(self):
        payload = base64.urlsafe_b64encode(b'{"created_at": "2024-01-01T00:00:00"}')

        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor(payload.decode("ascii"))

    def test_raises_value_error_for_a_malformed_uuid(self):
        import json

        raw = json.dumps(
            {"created_at": "2024-01-01T00:00:00+00:00", "id": "not-a-uuid"}
        ).encode("utf-8")
        cursor = base64.urlsafe_b64encode(raw).decode("ascii")

        with pytest.raises(ValueError, match="Invalid cursor"):
            decode_cursor(cursor)