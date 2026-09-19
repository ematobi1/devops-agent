from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .findings import Finding

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "github-fine-grained-pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    "aws-access-key-id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "aws-secret-access-key": re.compile(r"(?i)aws_?secret_?access_?key\W{0,4}[A-Za-z0-9/+=]{40}\b"),
    "private-key-block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"),
    "anthropic-api-key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "oauth-token-field": re.compile(r'(?i)"(?:access|refresh)_?token"\s*:\s*"[^"]{20,}"'),
}

def synthetic_credentials() -> dict[str, str]:
    """One fake credential per SECRET_PATTERNS rule, assembled at runtime so
    no source file contains a scannable literal. Shared by the redactor audit,
    the tests and the evals."""
    return {
        "github-token": "gh" + "o_" + "A1" * 20,
        "github-fine-grained-pat": "github_" + "pat_" + "A1b2C3d4E5f6G7h8I9j0K1",
        "aws-access-key-id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "aws-secret-access-key": "aws_secret_access_key=" + "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "private-key-block": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "anthropic-api-key": "sk-" + "ant-" + "api03-" + "A1" * 15,
        "slack-token": "xox" + "b-" + "1234567890-abcdefghij",
        "oauth-token-field": '"access' + 'Token": "' + "a1" * 15 + '"',
    }


FORBIDDEN_TRACKED_NAMES = (
    re.compile(r"(^|/)\.env(\..+)?$"),
    re.compile(r"\.jsonl$"),
    re.compile(r"(^|/)mcp-config-[^/]*\.json$"),
    re.compile(r"(^|/)\.claude\.json$"),
    re.compile(r"\.(pem|p12|pfx)$"),
    re.compile(r"(^|/)id_(rsa|ed25519|ecdsa)$"),
)

MAX_FILE_BYTES = 1_000_000


def scan_text(text: str, location: str) -> list[Finding]:
    """Never puts the matched value in the finding - only the rule name and line."""
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(line):
                findings.append(
                    Finding(f"SEC-{name}", "critical", f"{location}:{line_number}", f"{name} shaped value present")
                )
    return findings


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, check=True)


def scan_tracked_files(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    listing = _git(repo_root, "ls-files", "-z").stdout.decode("utf-8", "replace")
    for relative in filter(None, listing.split("\0")):
        for forbidden in FORBIDDEN_TRACKED_NAMES:
            if forbidden.search(relative):
                findings.append(Finding("SEC-forbidden-tracked-file", "high", relative, "file type must never be committed"))
        path = repo_root / relative
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        findings.extend(scan_text(raw.decode("utf-8", "replace"), relative))
    return findings


def scan_history(repo_root: Path) -> list[Finding]:
    """Scans every added line in every commit on every ref. A secret removed
    in a later commit is still leaked; only a rotation fixes it."""
    process = subprocess.Popen(
        ["git", "log", "--all", "-p", "-U0", "--no-color", "--format=commit:%H"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    findings: list[Finding] = []
    commit, current_file = "", ""
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.decode("utf-8", "replace").rstrip("\n")
        if line.startswith("commit:"):
            commit = line[len("commit:"):][:12]
        elif line.startswith("+++ b/"):
            current_file = line[len("+++ b/"):]
        elif line.startswith("+") and not line.startswith("+++"):
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append(
                        Finding(f"SEC-history-{name}", "critical", f"{current_file}@{commit}", f"{name} shaped value in commit history")
                    )
    process.wait()
    return findings
