"""Shared application API used by terminal adapters and a future B2F adapter."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .model import RequestReceipt, RequestView
from .store import SQLiteStore

GRID = re.compile(r"^[A-R]{2}[0-9]{2}(?:[A-X]{2})?$", re.I)


def utc_now() -> datetime:
    return datetime.now(UTC)


def wire_time(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CMSCore:
    """Application-facing local CMS service; intentionally transport agnostic."""
    def __init__(self, store: SQLiteStore, node_id: str = "local-node"):
        self.store, self.node_id = store, node_id
        self.store.initialize()

    def submit_weather(self, grid: str, owner_scope: str, *, idempotency_key: str | None = None,
                       ttl: timedelta = timedelta(hours=24)) -> RequestReceipt:
        grid = grid.upper()
        if not GRID.fullmatch(grid):
            raise ValueError("grid must be 4 or 6 Maidenhead characters")
        now = utc_now()
        payload = {"operation": "forecast", "parameters": {"grid": grid}, "max_response_bytes": 1024,
                   "cache_policy": "allow_stale", "fcm_version": "0.1"}
        # Callers control dedup only when they explicitly supply an idempotency key.
        key = idempotency_key or str(uuid.uuid4())
        digest = hashlib.sha256((owner_scope + "\0weather.forecast\0" + key + "\0" + json.dumps(payload, sort_keys=True)).encode()).hexdigest()
        receipt = self.store.enqueue_request(request_id=str(uuid.uuid4()), dedup_key=digest, owner_scope=owner_scope,
                                             service="weather.forecast", payload=payload, created_at=wire_time(now),
                                             expires_at=wire_time(now + ttl), public=True)
        return receipt

    def process_pending(self) -> int:
        """Run local deterministic handlers. Future peer workers use this same API."""
        done = 0
        while row := self.store.claim_next(wire_time(utc_now()), wire_time(utc_now() + timedelta(seconds=30))):
            payload = json.loads(row["payload_json"])
            try:
                if row["service"] != "weather.forecast":
                    raise ValueError("service unavailable")
                grid = payload["parameters"]["grid"]
                result: dict[str, Any] = {
                    "status": "ok", "media_type": "text/plain; charset=utf-8",
                    "content": f"TEST DATA: Weather for {grid}: clear, 20 C, light wind.",
                    "truncated": False,
                    "provenance": {"source": "Federated CMS deterministic test weather", "fetched_at": wire_time(utc_now()), "stale": False},
                }
                self.store.finish(row["id"], row["processing_lease_token"], "completed", result, wire_time(utc_now()))
            except Exception as exc:
                self.store.finish(row["id"], row["processing_lease_token"], "failed", {"status": "error", "error_code": "internal_error", "error_detail": str(exc)[:160]}, wire_time(utc_now()))
            done += 1
        return done

    def store_local_message(self, mailbox: str, sender: str, subject: str, body: str) -> str:
        """Internal mailbox API for future authorized B2F/mail adapters."""
        if not all((mailbox, sender, subject)) or len(body.encode("utf-8")) > 65536:
            raise ValueError("invalid local message")
        message_id = str(uuid.uuid4())
        self.store.store_message(message_id, mailbox, sender, subject, body, wire_time(utc_now()))
        return message_id

    def local_mailbox(self, mailbox: str) -> list[dict[str, str]]:
        return self.store.mailbox_messages(mailbox)

    def request(self, request_id: str, owner_scope: str) -> RequestView | None:
        return self.store.get_request(request_id, owner_scope)

    def public_request(self, request_id: str) -> RequestView | None:
        return self.store.get_public_request(request_id)

    def jobs(self, owner_scope: str) -> list[RequestView]:
        return self.store.list_requests(owner_scope)

    def status(self) -> dict[str, int]:
        return self.store.status_counts()
