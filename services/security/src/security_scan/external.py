"""Generic layers delegated to established tools rather than reimplemented:
bandit (Python SAST) and pip-audit (known-vulnerable dependencies). A tool
that cannot run is itself a high finding - a scan that silently skipped is
worse than a scan that failed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .findings import Finding

BANDIT_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
BANDIT_TARGETS = ("services", "scripts", "ui", "contracts")
BANDIT_SKIPPED_TESTS = ("B101",)


def _run(argv: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def parse_bandit(output: str) -> list[Finding]:
    return [
        Finding(
            f"SAST-{issue['test_id']}",
            BANDIT_SEVERITY.get(issue["issue_severity"], "low"),
            f"{issue['filename'].replace(chr(92), '/')}:{issue['line_number']}",
            issue["issue_text"],
        )
        for issue in json.loads(output).get("results", [])
    ]


def parse_pip_audit(output: str) -> list[Finding]:
    findings = []
    for dependency in json.loads(output).get("dependencies", []):
        for vuln in dependency.get("vulns", []):
            fixes = ", ".join(vuln.get("fix_versions", [])) or "no fix released"
            findings.append(Finding(f"DEP-{vuln['id']}", "high", f"{dependency['name']}=={dependency['version']}", f"known vulnerability; fixed in: {fixes}"))
    return findings


def run_bandit(repo_root: Path, timeout: int = 120) -> list[Finding]:
    targets = [t for t in BANDIT_TARGETS if (repo_root / t).exists()]
    argv = [sys.executable, "-m", "bandit", "-r", *targets, "-f", "json", "-q", "-ll", "--exclude", "**/tests/**,**/evals/**,**/test_*.py,**/.venv/**", "--skip", ",".join(BANDIT_SKIPPED_TESTS)]
    try:
        result = _run(argv, repo_root, timeout)
        return parse_bandit(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        return [Finding("SCAN-bandit-failed", "high", "bandit", f"SAST did not run: {type(exc).__name__}")]


def run_pip_audit(repo_root: Path, timeout: int = 180) -> list[Finding]:
    argv = [sys.executable, "-m", "pip_audit", "-f", "json", "--skip-editable", "--progress-spinner", "off"]
    try:
        result = _run(argv, repo_root, timeout)
        return parse_pip_audit(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        return [Finding("SCAN-pip-audit-failed", "high", "pip-audit", f"dependency audit did not run (offline?): {type(exc).__name__}")]
