from dataclasses import dataclass, field

import jsonschema
import pytest

from orchestrator.apply import ApplyError, apply_approved
from orchestrator.claude_runner import ClaudeRunResult

APPROVED = {
    "id": "appr-1",
    "task_id": "task-1",
    "summary": "roll back checkout deploy to previous version",
    "proposed_actions": [{"instruction": "revert commit abc123 on main"}],
    "status": "approved",
    "decided_by": "emma",
    "decided_at": "2026-09-13T01:00:00Z",
    "created_at": "2026-09-13T00:00:00Z",
}


@dataclass
class FakeRunner:
    result_text: str
    is_error: bool = False
    seen_prompts: list = field(default_factory=list)

    def run(self, prompt, *, json_schema=None):
        self.seen_prompts.append(prompt)
        return ClaudeRunResult(
            result_text=self.result_text,
            is_error=self.is_error,
            session_id="s1",
            total_cost_usd=0.2,
            num_turns=1,
            terminal_reason="completed" if not self.is_error else "error",
            tool_calls=[],
            raw_events=[],
        )


def test_apply_approved_rejects_pending_request():
    pending = {k: v for k, v in APPROVED.items() if k not in ("decided_by", "decided_at")}
    pending["status"] = "pending"
    with pytest.raises(ApplyError):
        apply_approved(pending, FakeRunner(result_text=""))


def test_apply_approved_rejects_rejected_request():
    rejected = dict(APPROVED, status="rejected")
    with pytest.raises(ApplyError):
        apply_approved(rejected, FakeRunner(result_text=""))


def test_apply_approved_runs_successfully_records_applied():
    runner = FakeRunner(result_text="Opened PR #4 reverting commit abc123")
    outcome = apply_approved(APPROVED, runner)

    assert outcome.approval_id == "appr-1"
    assert outcome.change_record["task_id"] == "task-1"
    assert outcome.change_record["approval_id"] == "appr-1"
    assert outcome.change_record["outcome"] == "applied"
    assert outcome.change_record["detail"] == "Opened PR #4 reverting commit abc123"


def test_apply_approved_records_failed_verification_on_run_error():
    runner = FakeRunner(result_text="budget exhausted before completing", is_error=True)
    outcome = apply_approved(APPROVED, runner)
    assert outcome.change_record["outcome"] == "failed_verification"


def test_apply_approved_builds_prompt_from_summary_and_actions():
    runner = FakeRunner(result_text="done")
    apply_approved(APPROVED, runner)
    prompt = runner.seen_prompts[0]
    assert APPROVED["summary"] in prompt
    assert "revert commit abc123 on main" in prompt


def test_apply_approved_rejects_malformed_approval():
    malformed = dict(APPROVED)
    del malformed["proposed_actions"]
    with pytest.raises(jsonschema.ValidationError):
        apply_approved(malformed, FakeRunner(result_text=""))


def test_apply_approved_includes_payload_in_prompt():
    """Actions built by scripts/generate_dockerfile.py carry generated file
    content in `payload`, separate from `instruction` - apply must still
    surface that content to the write-scoped run."""
    with_payload = dict(
        APPROVED,
        proposed_actions=[
            {"instruction": "Create a Dockerfile with exactly this content, via a PR.", "payload": "FROM python:3.12-slim"}
        ],
    )
    runner = FakeRunner(result_text="done")
    apply_approved(with_payload, runner)
    prompt = runner.seen_prompts[0]
    assert "Create a Dockerfile with exactly this content, via a PR." in prompt
    assert "FROM python:3.12-slim" in prompt


def test_apply_approved_omits_payload_section_when_absent():
    runner = FakeRunner(result_text="done")
    apply_approved(APPROVED, runner)
    prompt = runner.seen_prompts[0]
    assert prompt.endswith("- revert commit abc123 on main")
