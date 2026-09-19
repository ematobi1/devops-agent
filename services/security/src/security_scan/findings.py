from __future__ import annotations

from dataclasses import dataclass

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    location: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_RANK:
            raise ValueError(f"unknown severity {self.severity!r}")

    @property
    def rank(self) -> int:
        return SEVERITY_RANK[self.severity]
