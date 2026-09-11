# app/core/pagination.py
import base64
import binascii
import json
import uuid
from datetime import datetime

from app.models.job import Job


def encode_cursor(job: Job) -> str:
    """Builds an opaque, URL-safe cursor from a job's (created_at, id).
    This is the last row of the current page; the next page starts strictly
    after this position."""
    payload = {"created_at": job.created_at.isoformat(), "id": str(job.id)}
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw)
        created_at = datetime.fromisoformat(payload["created_at"])
        job_id = uuid.UUID(payload["id"])
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc
    return created_at, job_id