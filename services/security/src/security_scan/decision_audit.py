from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from devops_agent_contracts import validate

from .findings import Finding
from .secrets_scan import scan_text

UNATTRIBUTED_IDENTITIES = frozenset({"web-ui", "unknown", "anonymous", "user", "admin", "system", "agent", "claude", "root"})

DANGEROUS_OPERATIONS: dict[str, re.Pattern[str]] = {
    "deletion": re.compile(r"(?i)\b(delete|remove|drop|truncate|destroy)\b"),
    "force-push": re.compile(r"(?i)force[- ]push|--force\b|push\s+-f\b"),
    "merge": re.compile(r"(?i)\bmerge\b"),
    "ci-workflow-edit": re.compile(r"\.github/workflows/"),
    "secret-handling": re.compile(r"(?i)\b(secrets?|credentials?|api[_ -]?key|token|password)\b"),
    "pipe-to-shell": re.compile(r"(?i)(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b"),
    "permission-change": re.compile(r"(?i)chmod\s+[0-7]*7[0-7]*|\bsudo\b"),
    "hook-bypass": re.compile(r"--no-verify|--no-gpg-sign"),
}


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[Finding]]:
    records: list[dict[str, Any]] = []
    findings: list[Finding] = []
    if not path.exists():
        return records, findings
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            findings.append(Finding("DEC-unparseable-record", "critical", f"{path.name}:{line_number}", "line is not valid JSON"))
    return records, findings


def _content_fingerprint(record: dict[str, Any]) -> str:
    return json.dumps({"summary": record.get("summary"), "proposed_actions": record.get("proposed_actions")}, sort_keys=True)


def action_text(record: dict[str, Any]) -> str:
    parts = []
    for action in record.get("proposed_actions", []):
        if isinstance(action, dict):
            parts.extend([action.get("instruction", ""), action.get("payload", "")])
        else:
            parts.append(str(action))
    return "\n".join(parts)


HIGH_RISK_OPERATIONS = frozenset({"force-push", "ci-workflow-edit", "pipe-to-shell", "permission-change", "hook-bypass"})


def sensitive_operations(text: str) -> list[str]:
    return sorted(op for op, pattern in DANGEROUS_OPERATIONS.items() if pattern.search(text))


def _schema_valid(schema: str, record: dict[str, Any]) -> bool:
    try:
        validate(schema, record)
    except Exception:  # noqa: BLE001 - any contract failure means "not valid"
        return False
    return True


def _audit_approvals(records: list[dict[str, Any]], name: str) -> tuple[list[Finding], dict[str, dict[str, Any]]]:
    findings: list[Finding] = []
    history: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        history.setdefault(str(record.get("id")), []).append(record)

    latest: dict[str, dict[str, Any]] = {}
    for approval_id, versions in history.items():
        where = f"{name}#{approval_id[:8]}"
        first, last = versions[0], versions[-1]
        latest[approval_id] = last

        if not all(_schema_valid("approval_request", v) for v in versions):
            findings.append(Finding("DEC-schema-invalid", "medium", where, "record fails the current approval_request schema (pre-migration record or tampering)"))
        if first.get("status") != "pending":
            findings.append(Finding("DEC-created-not-pending", "critical", where, f"first record has status {first.get('status')!r}, expected 'pending': a decision with no reviewable origin"))
        decided = [v for v in versions if v.get("status") in ("approved", "rejected")]
        if len(decided) > 1:
            findings.append(Finding("DEC-multiple-decisions", "critical", where, "more than one decision recorded for one approval: append-only violation"))
        if any(_content_fingerprint(v) != _content_fingerprint(first) for v in versions[1:]):
            findings.append(Finding("DEC-content-changed-after-review", "critical", where, "summary or proposed_actions differ between the pending and decided record"))

        for version in decided:
            if not version.get("decided_by") or not version.get("decided_at"):
                findings.append(Finding("DEC-missing-attribution", "high", where, "decision has no decided_by/decided_at"))
            elif str(version["decided_by"]).strip().lower() in UNATTRIBUTED_IDENTITIES:
                findings.append(Finding("DEC-unattributed-approver", "medium", where, f"decided_by={version['decided_by']!r} is not an identifiable person"))
            if version.get("decided_at") and version.get("created_at") and version["decided_at"] < version["created_at"]:
                findings.append(Finding("DEC-decided-before-created", "high", where, "decided_at precedes created_at"))

        text = action_text(last)
        findings.extend(Finding("DEC-secret-in-record", "critical", where, f.message) for f in scan_text(text + "\n" + str(last.get("summary", "")), where))
        flagged = sensitive_operations(text)
        if flagged:
            status = last.get("status")
            risky = bool(HIGH_RISK_OPERATIONS & set(flagged))
            severity = ("high" if risky else "medium") if status == "approved" else "low"
            findings.append(Finding("DEC-sensitive-operation", severity, where, f"{status} proposal touches: {', '.join(flagged)}"))
        if last.get("status") == "approved" and last.get("verifier_warnings"):
            findings.append(Finding("DEC-approved-with-warnings", "low", where, f"approved with {len(last['verifier_warnings'])} unresolved verifier warning(s)"))
    return findings, latest


def _audit_changes(records: list[dict[str, Any]], approvals: dict[str, dict[str, Any]], name: str) -> list[Finding]:
    findings: list[Finding] = []
    applied_counts: dict[str, int] = {}
    for index, record in enumerate(records, start=1):
        where = f"{name}:{index}"
        if not _schema_valid("change_record", record):
            findings.append(Finding("DEC-schema-invalid", "medium", where, "record fails the current change_record schema"))
        if record.get("outcome") != "applied":
            continue
        approval_id = str(record.get("approval_id"))
        applied_counts[approval_id] = applied_counts.get(approval_id, 0) + 1
        approval = approvals.get(approval_id)
        if approval is None:
            findings.append(Finding("DEC-applied-without-approval", "critical", where, "applied change references an approval that does not exist"))
        elif approval.get("status") != "approved":
            findings.append(Finding("DEC-applied-without-approval", "critical", where, f"applied change references an approval with status {approval.get('status')!r}"))
        elif record.get("recorded_at", "") < approval.get("decided_at", ""):
            findings.append(Finding("DEC-applied-before-approval", "high", where, "change recorded before its approval was decided"))
        if not record.get("applied_by"):
            findings.append(Finding("DEC-unattributed-apply", "medium", where, "applied change has no applied_by: nobody is accountable for the execution"))
        findings.extend(Finding("DEC-secret-in-record", "critical", where, f.message) for f in scan_text(record.get("detail", ""), where))
    findings.extend(
        Finding("DEC-replayed-apply", "high", f"{name}#{approval_id[:8]}", f"approval applied {count} times: one approval must authorize one execution")
        for approval_id, count in applied_counts.items()
        if count > 1
    )
    return findings


def audit_decisions(approvals_path: Path, changes_path: Path) -> list[Finding]:
    approvals, parse_findings = _read_jsonl(approvals_path)
    changes, change_parse_findings = _read_jsonl(changes_path)
    approval_findings, latest = _audit_approvals(approvals, approvals_path.name)
    return parse_findings + change_parse_findings + approval_findings + _audit_changes(changes, latest, changes_path.name)
