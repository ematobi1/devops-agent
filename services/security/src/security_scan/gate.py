"""Glue-facing enforcement: each function prints what it found and raises
SystemExit(1) on anything that must stop the run, so a script needs one line
per guarded boundary."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from .findings import Finding
from .guards import guard_input, guard_proposal, preflight
from .waivers import apply_waivers, load_waivers

WAIVERS_FILE = "security-waivers.json"


def _print_blocking(header: str, findings: list[Finding]) -> None:
    print(header, file=sys.stderr)
    for finding in findings:
        print(f"  - [{finding.severity}] {finding.rule} {finding.location}: {finding.message}", file=sys.stderr)


def require_preflight(repo_root: Path, *, stage: str, env: Mapping[str, str] | None = None) -> None:
    active, _ = apply_waivers(preflight(repo_root, env, stage=stage), load_waivers(repo_root / WAIVERS_FILE))
    blocking = [f for f in active if f.rank >= 3]
    if blocking:
        _print_blocking(f"SECURITY PREFLIGHT FAILED ({stage} stage) - nothing was started:", blocking)
        raise SystemExit(1)


def require_clean_input(text: str, source: str) -> None:
    result = guard_input(text, source)
    if result.blocked:
        _print_blocking(f"SECURITY INPUT CHECK FAILED for {source} - nothing was started:", [f for f in result.findings if f.rank >= 3])
        raise SystemExit(1)


def screen_proposal(proposal: dict, source: str) -> list[dict[str, str]]:
    """Refuses a proposal containing a secret-shaped value; returns the
    remaining findings as warnings for the caller to attach."""
    critical, warnings = guard_proposal(proposal, source)
    if critical:
        _print_blocking(f"SECURITY OUTPUT CHECK FAILED for {source} - refusing to persist:", critical)
        raise SystemExit(1)
    return warnings
