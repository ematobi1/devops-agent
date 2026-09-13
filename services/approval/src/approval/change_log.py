from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devops_agent_contracts import validate


class ChangeLog:
    """Append-only audit trail of what happened after an approval decision."""

    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("")

    def record(self, change_record: dict[str, Any]) -> dict[str, Any]:
        validate("change_record", change_record)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(change_record) + "\n")
        return change_record

    def all(self) -> list[dict[str, Any]]:
        text = self._path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]
