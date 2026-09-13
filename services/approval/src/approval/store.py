from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from devops_agent_contracts import validate


class ApprovalNotFoundError(KeyError):
    pass


class InvalidTransitionError(ValueError):
    pass


class ApprovalStore:
    """File-backed audit log of approval requests and their decisions.

    One JSON object per line (append-only), keyed by id; the current state of
    a request is the most recent line with that id. This is the append-only
    audit trail change_record entries are derived from - never edit past
    lines, only append.
    """

    def __init__(self, path: str | os.PathLike):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("")

    def _append(self, record: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def _read_all(self) -> list[dict[str, Any]]:
        text = self._path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def create(self, approval_request: dict[str, Any]) -> dict[str, Any]:
        validate("approval_request", approval_request)
        if approval_request["status"] != "pending":
            raise InvalidTransitionError("a new approval request must start as 'pending'")
        self._append(approval_request)
        return approval_request

    def get(self, approval_id: str) -> dict[str, Any]:
        matches = [r for r in self._read_all() if r["id"] == approval_id]
        if not matches:
            raise ApprovalNotFoundError(approval_id)
        return matches[-1]

    def list_pending(self) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for record in self._read_all():
            latest[record["id"]] = record
        return [r for r in latest.values() if r["status"] == "pending"]

    def decide(self, approval_id: str, *, status: str, decided_by: str, decided_at: str) -> dict[str, Any]:
        if status not in ("approved", "rejected"):
            raise InvalidTransitionError(f"decide() status must be 'approved' or 'rejected', got {status!r}")
        current = self.get(approval_id)
        if current["status"] != "pending":
            raise InvalidTransitionError(
                f"approval {approval_id} is already {current['status']!r}, cannot decide again"
            )
        updated = dict(current, status=status, decided_by=decided_by, decided_at=decided_at)
        validate("approval_request", updated)
        self._append(updated)
        return updated
