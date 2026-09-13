from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from devops_agent_contracts import validate

from .claude_runner import ClaudeRunner, ClaudeRunResult

SYSTEM_PROMPT = (
    "You are a DevOps diagnostic agent. Investigate the task using only the "
    "read-only MCP tools available to you. Do not attempt to modify any "
    "external system - you have no write access. End your reply with a "
    "concise summary of findings, then a numbered list of proposed actions. "
    "Every item in that numbered list must be complete and directly "
    "executable verbatim by a second agent with no further judgment calls: "
    "no bracketed placeholders, no TODOs, no unresolved options. If a "
    "genuinely useful action would require content you were not asked to "
    "produce (e.g. a full section of prose), write that content out in full "
    "in the action itself rather than describing it. Anything that is "
    "actually a question for the human operator, or a decision only they "
    "can make, belongs in the summary prose - never phrase a question as a "
    "numbered action, and omit the numbered list entirely if there is "
    "nothing yet that could be executed as-is."
)


class OrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrchestrationOutcome:
    task_id: str
    run_result: ClaudeRunResult
    approval_request: dict


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_task(task_request: dict, runner: ClaudeRunner) -> OrchestrationOutcome:
    """Runs a diagnostic Claude Code pass for task_request and produces a
    pending ApprovalRequest. This function never applies anything - apply
    only happens in services/approval after a human decides, per
    docs/ARCHITECTURE.md. Raises OrchestrationError if the run itself failed
    (the human never even gets an approval prompt for a failed run)."""
    validate("task_request", task_request)

    run_result = runner.run(task_request["description"])
    if run_result.is_error:
        raise OrchestrationError(
            f"Claude Code run failed for task {task_request['id']!r}: "
            f"terminal_reason={run_result.terminal_reason}"
        )

    proposed_actions = _extract_proposed_actions(run_result.result_text)
    approval_request = {
        "id": str(uuid.uuid4()),
        "task_id": task_request["id"],
        "summary": run_result.result_text,
        "proposed_actions": proposed_actions or ["(agent proposed no concrete actions - review summary)"],
        "status": "pending",
        "created_at": _now_iso(),
    }
    validate("approval_request", approval_request)
    return OrchestrationOutcome(task_id=task_request["id"], run_result=run_result, approval_request=approval_request)


def _extract_proposed_actions(result_text: str) -> list[str]:
    """Pulls numbered list items ("1. ...", "2. ...") out of the agent's
    reply. Deterministic parsing of a deterministic format request made in
    SYSTEM_PROMPT - not an LLM call."""
    actions = []
    for line in result_text.splitlines():
        stripped = line.strip()
        if len(stripped) > 2 and stripped[0].isdigit():
            rest = stripped.lstrip("0123456789")
            if rest.startswith((".", ")")):
                actions.append(rest[1:].strip())
    return actions
