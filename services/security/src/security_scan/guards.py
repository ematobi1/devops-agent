"""Runtime guards: every input that enters a pipeline run and every output a
run produces is scanned before it is stored, shown, or acted on. Deterministic
- same text, same findings. Fail closed: `blocked` is True on any high or
critical finding."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .decision_audit import DANGEROUS_OPERATIONS
from .findings import Finding
from .secrets_scan import scan_text

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "instruction-override": re.compile(r"(?i)\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|earlier|system)\b.{0,20}\b(instructions?|prompts?|rules?)\b"),
    "role-reassignment": re.compile(r"(?i)\byou are now\b|\bnew system prompt\b|\bact as (an? )?(unrestricted|jailbroken)"),
    "exfiltration-request": re.compile(r"(?i)\b(send|post|upload|email|exfiltrat\w*)\b.{0,60}\b(token|secret|credential|api[_ -]?key|password)s?\b"),
    "scope-expansion": re.compile(r"(?i)\b(merge|approve)\b.{0,20}\b(the )?(pr|pull request)\b|\bforce[- ]push\b"),
}
HIDDEN_CHARACTERS = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
URL = re.compile(r"https?://([^\s/)\"'>\]]+)[^\s)\"'>\]]*")
ENCODED_BLOB = re.compile(r"[A-Za-z0-9+/]{240,}={0,2}")

ALLOWED_URL_HOSTS = frozenset({"github.com", "www.github.com", "api.github.com", "docs.github.com", "raw.githubusercontent.com", "docs.docker.com", "docs.aws.amazon.com"})

SENSITIVE_OPERATION_SEVERITY = {"pipe-to-shell": "high", "hook-bypass": "high", "permission-change": "medium"}


@dataclass(frozen=True)
class GuardResult:
    findings: list[Finding]

    @property
    def blocked(self) -> bool:
        return any(f.rank >= 3 for f in self.findings)


def _common_findings(text: str, source: str) -> list[Finding]:
    findings = scan_text(text, source)
    if HIDDEN_CHARACTERS.search(text):
        findings.append(Finding("GUARD-hidden-characters", "high", source, "zero-width or bidi-control characters can hide instructions from a human reviewer"))
    for name, pattern in INJECTION_PATTERNS.items():
        if pattern.search(text):
            findings.append(Finding(f"GUARD-injection-{name}", "high", source, f"text matches the {name} prompt-injection shape"))
    return findings


def guard_input(text: str, source: str) -> GuardResult:
    """For text a person supplies (task description, repo target) before it
    reaches the model, a log, or a subprocess argv."""
    findings = _common_findings(text, source)
    if ENCODED_BLOB.search(text):
        findings.append(Finding("GUARD-encoded-blob", "medium", source, "long base64-like blob in input; instructions can be smuggled this way"))
    return GuardResult(findings)


def guard_output(text: str, source: str) -> GuardResult:
    """For anything the model produced (summary, actions, payloads, apply
    result) before it is persisted or shown for approval."""
    findings = _common_findings(text, source)
    for operation, pattern in DANGEROUS_OPERATIONS.items():
        if operation in SENSITIVE_OPERATION_SEVERITY and pattern.search(text):
            findings.append(Finding(f"GUARD-operation-{operation}", SENSITIVE_OPERATION_SEVERITY[operation], source, f"output contains a {operation} construct"))
    for match in URL.finditer(text):
        host = (urlparse(match.group(0)).hostname or "").lower()
        if host not in ALLOWED_URL_HOSTS:
            findings.append(Finding("GUARD-untrusted-url", "medium", source, f"output links to a host outside the allowlist: {host}"))
    if ENCODED_BLOB.search(text):
        findings.append(Finding("GUARD-encoded-blob", "medium", source, "long base64-like blob in output"))
    return GuardResult(findings)


def guard_proposal(proposal: dict, source: str) -> tuple[list[Finding], list[dict[str, str]]]:
    """Scans a proposal's summary and every action instruction and payload.
    Returns (critical findings that must stop it being persisted, warnings to
    attach as verifier_warnings so the approver sees them at decision time)."""
    findings = guard_output(str(proposal.get("summary", "")), f"{source}:summary").findings
    for index, action in enumerate(proposal.get("proposed_actions", []), start=1):
        findings += guard_output(str(action.get("instruction", "")), f"{source}:action{index}:instruction").findings
        findings += guard_output(str(action.get("payload", "")), f"{source}:action{index}:payload").findings
    critical = [f for f in findings if f.rank >= 4]
    warnings = [{"action": f.location, "reason": f"[security:{f.rule}] {f.message}"} for f in findings if 2 <= f.rank < 4]
    return critical, warnings


READ_ONLY_FLAG = re.compile(r"^--read-only=(true|false)$")


def preflight(repo_root: Path, env: Mapping[str, str] | None = None, *, stage: str) -> list[Finding]:
    """Checks that must hold before a run starts. `stage` is "read"
    (diagnose/generate) or "write" (apply)."""
    from .auth_audit import audit_mcp_templates
    from .secrets_scan import scan_tracked_files

    environment = os.environ if env is None else env
    findings: list[Finding] = []
    flag = environment.get("GITHUB_READ_ONLY_FLAG")
    if flag is not None and not READ_ONLY_FLAG.match(flag):
        findings.append(Finding("PRE-malformed-read-only-flag", "critical", "env:GITHUB_READ_ONLY_FLAG", "value is spliced into the MCP server argv and must be exactly --read-only=true or --read-only=false"))
    if stage == "read" and flag == "--read-only=false":
        findings.append(Finding("PRE-write-scope-in-read-stage", "critical", "env:GITHUB_READ_ONLY_FLAG", "a read-only stage is configured with a write-capable GitHub server"))
    if stage == "write" and flag == "--read-only=true":
        findings.append(Finding("PRE-read-only-in-write-stage", "medium", "env:GITHUB_READ_ONLY_FLAG", "apply would run read-only and cannot write"))
    token = environment.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not token:
        findings.append(Finding("PRE-missing-github-token", "high", "env:GITHUB_PERSONAL_ACCESS_TOKEN", "no GitHub credential is configured"))
    elif token.startswith("ghp_"):
        findings.append(Finding("PRE-classic-github-pat", "high", "env:GITHUB_PERSONAL_ACCESS_TOKEN", "classic PATs cannot be scoped to one repository; use a fine-grained PAT"))
    if re.match(r"^(AKIA)", environment.get("AWS_ACCESS_KEY_ID", "")):
        findings.append(Finding("PRE-long-lived-aws-key", "medium", "env:AWS_ACCESS_KEY_ID", "static IAM user key in the environment; prefer AWS_PROFILE with short-lived credentials"))
    if environment.get("ANTHROPIC_API_KEY"):
        findings.append(Finding("PRE-unexpected-api-key", "medium", "env:ANTHROPIC_API_KEY", "this project authenticates through the local Claude Code session; an API key changes billing and auth path"))
    findings.extend(audit_mcp_templates(repo_root))
    findings.extend(scan_tracked_files(repo_root))
    return findings
