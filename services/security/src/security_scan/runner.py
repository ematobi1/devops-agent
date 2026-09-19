from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .auth_audit import audit_auth
from .decision_audit import audit_decisions
from .external import run_bandit, run_pip_audit
from .findings import SEVERITY_RANK, Finding
from .secrets_scan import scan_history, scan_tracked_files
from .waivers import Waiver, apply_waivers

BLOCKING_RANK = SEVERITY_RANK["high"]


@dataclass(frozen=True)
class ScanReport:
    active: list[Finding]
    waived: list[tuple[Finding, Waiver]]

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.active if f.rank >= BLOCKING_RANK]

    @property
    def passed(self) -> bool:
        return not self.blocking

    def render(self) -> str:
        lines = []
        for finding in sorted(self.active, key=lambda f: (-f.rank, f.rule, f.location)):
            lines.append(f"[{finding.severity.upper():8}] {finding.rule}  {finding.location}\n           {finding.message}")
        for finding, waiver in self.waived:
            lines.append(f"[WAIVED  ] {finding.rule}  {finding.location}\n           reason: {waiver.reason}")
        counts = {s: sum(1 for f in self.active if f.severity == s) for s in SEVERITY_RANK}
        summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: -SEVERITY_RANK[kv[0]]) if n)
        lines.append(f"\n{'PASS' if self.passed else 'FAIL'}: {summary or 'no findings'}; {len(self.waived)} waived")
        return "\n".join(lines)


def run_scan(
    repo_root: Path,
    *,
    approvals_path: Path,
    changes_path: Path,
    waivers: list[Waiver],
    redactor: Callable[[str], str] | None = None,
    include_external: bool = True,
) -> ScanReport:
    findings = scan_tracked_files(repo_root) + scan_history(repo_root) + audit_decisions(approvals_path, changes_path) + audit_auth(repo_root, redactor)
    if include_external:
        findings += run_bandit(repo_root) + run_pip_audit(repo_root)
    active, waived = apply_waivers(list(dict.fromkeys(findings)), waivers)
    return ScanReport(active, waived)
