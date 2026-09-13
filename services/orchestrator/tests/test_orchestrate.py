from dataclasses import dataclass, field

import pytest

from orchestrator.claude_runner import ClaudeRunResult
from orchestrator.orchestrate import OrchestrationError, run_task

TASK = {
    "id": "task-1",
    "description": "diagnose 500s on checkout service",
    "requester": "emma",
    "priority": "high",
    "created_at": "2026-09-13T00:00:00Z",
}


@dataclass
class FakeRunner:
    result_text: str
    is_error: bool = False
    terminal_reason: str = "completed"
    seen_prompts: list = field(default_factory=list)

    def run(self, prompt, *, json_schema=None):
        self.seen_prompts.append(prompt)
        return ClaudeRunResult(
            result_text=self.result_text,
            is_error=self.is_error,
            session_id="s1",
            total_cost_usd=0.1,
            num_turns=1,
            terminal_reason=self.terminal_reason,
            tool_calls=[],
            raw_events=[],
        )


def test_run_task_produces_pending_approval_with_parsed_actions():
    runner = FakeRunner(result_text="Findings: bad deploy.\n1. revert commit abc123\n2. page on-call")
    outcome = run_task(TASK, runner)

    assert outcome.task_id == "task-1"
    assert outcome.approval_request["status"] == "pending"
    assert outcome.approval_request["task_id"] == "task-1"
    assert outcome.approval_request["proposed_actions"] == [
        {"instruction": "revert commit abc123"},
        {"instruction": "page on-call"},
    ]


def test_run_task_falls_back_when_no_numbered_actions_found():
    runner = FakeRunner(result_text="Everything looks fine, no action needed.")
    outcome = run_task(TASK, runner)
    assert len(outcome.approval_request["proposed_actions"]) == 1


def test_run_task_raises_on_failed_claude_run_without_creating_approval():
    runner = FakeRunner(result_text="", is_error=True, terminal_reason="error_max_budget_usd")
    with pytest.raises(OrchestrationError):
        run_task(TASK, runner)


def test_run_task_sends_task_description_as_prompt():
    runner = FakeRunner(result_text="1. noop")
    run_task(TASK, runner)
    assert runner.seen_prompts == [TASK["description"]]


def test_run_task_rejects_malformed_task_request():
    bad_task = dict(TASK)
    del bad_task["priority"]
    with pytest.raises(Exception):
        run_task(bad_task, FakeRunner(result_text="1. noop"))
