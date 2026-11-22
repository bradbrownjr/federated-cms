"""Small immutable-ish domain records; not a federation wire format."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RequestReceipt:
    request_id: str
    status: str
    duplicate: bool


@dataclass(frozen=True)
class RequestView:
    request_id: str
    status: str
    service: str
    created_at: str
    expires_at: str
    result: dict[str, Any] | None
