from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from devops_agent_contracts import validate

from .claude_runner import ClaudeRunner, ClaudeRunResult

APPLY_SYSTEM_PROMPT = (
    "You are executing a previously human-approved DevOps change. Execute "
    "EXACTLY the approved plan below, nothing more. Do not expand scope "
    "beyond what is listed, do not merge or close any pull request, and do "
    "not delete anything. If the approved plan cannot be completed exactly "
    "as written, stop and report why rather than improvising an "
    "alternative."
)


class ApplyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApplyOutcome:
    approval_id: str
    run_result: ClaudeRunResult
    change_record: dict


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_action(action: dict) -> str:
    payload = action.get("payload")
    if payload:
        return f"- {action['instruction']}\n\n{payload}"
    return f"- {action['instruction']}"


def apply_approved(approval: dict, runner: ClaudeRunner) -> ApplyOutcome:
    """Executes an already-approved ApprovalRequest via `runner` and returns
    a ChangeRecord describing what happened. This is deliberately NOT called
    automatically by approval.ApprovalStore.decide() - approving a request
    and applying it are two separate, human-triggered actions (see
    docs/ARCHITECTURE.md "Apply step"). Callers are responsible for
    persisting the returned change_record to services/approval.ChangeLog -
    this module does not import the approval package, to keep the two
    services decoupled per the contracts boundary.

    Integration-agnostic by design: which MCP servers/credentials/tool
    allowlist `runner` was built with determines what this can actually do.
    v1 scope is GitHub only - see scripts/apply.py, which is where that
    choice is made, not here.
    """
    validate("approval_request", approval)
    if approval["status"] != "approved":
        raise ApplyError(
            f"approval {approval['id']!r} is not approved (status={approval['status']!r}) - "
            "apply_approved() only executes requests a human has explicitly approved"
        )

    prompt = "Approved summary:\n{}\n\nApproved actions:\n{}".format(
        approval["summary"],
        "\n\n".join(_render_action(action) for action in approval["proposed_actions"]),
    )
    run_result = runner.run(prompt)

    # The change_record schema has no distinct "apply errored" state yet -
    # services/verifier doesn't exist to make that distinction meaningful.
    # A Claude run error (budget exhausted, tool denied, etc.) is recorded
    # as failed_verification for now; revisit if/when a real verify step
    # exists and the two failure modes need to be told apart.
    outcome = "applied" if not run_result.is_error else "failed_verification"
    change_record = {
        "task_id": approval["task_id"],
        "approval_id": approval["id"],
        "outcome": outcome,
        "detail": run_result.result_text,
        "recorded_at": _now_iso(),
    }
    validate("change_record", change_record)
    return ApplyOutcome(approval_id=approval["id"], run_result=run_result, change_record=change_record)
