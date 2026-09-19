from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .findings import Finding


class WaiverError(ValueError):
    pass


@dataclass(frozen=True)
class Waiver:
    rule: str
    reason: str
    location_contains: str = ""

    def matches(self, finding: Finding) -> bool:
        return finding.rule == self.rule and self.location_contains in finding.location


def load_waivers(path: Path) -> list[Waiver]:
    """A waiver without a reason is refused: an accepted risk must say why."""
    if not path.is_file():
        return []
    waivers = []
    for entry in json.loads(path.read_text(encoding="utf-8")):
        reason = str(entry.get("reason", "")).strip()
        if not entry.get("rule") or not reason:
            raise WaiverError(f"waiver {entry!r} needs both 'rule' and a non-empty 'reason'")
        waivers.append(Waiver(entry["rule"], reason, entry.get("location_contains", "")))
    return waivers


def apply_waivers(findings: list[Finding], waivers: list[Waiver]) -> tuple[list[Finding], list[tuple[Finding, Waiver]]]:
    active, waived = [], []
    for finding in findings:
        waiver = next((w for w in waivers if w.matches(finding)), None)
        if waiver is None:
            active.append(finding)
        else:
            waived.append((finding, waiver))
    return active, waived
