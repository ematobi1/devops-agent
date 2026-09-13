"""Runs a diagnostic Claude Code pass and persists the resulting
ApprovalRequest to an approval store, where a human reviews it, decides on
it via scripts/decide.py, and - only if approved - executes it via
scripts/apply.py. This is the entry point that turns a task description into
something in the approval pipeline; run_task() alone (as in
scripts/smoke_test.py) never persists anything.

v1 scope: GitHub only, read-only tools (GITHUB_READ_TOOLS) - matches the
validated Stage 1 diagnostic path exactly.

Before persisting, runs the proposed_actions through
verifier.check_proposed_actions() - a deterministic, no-LLM pattern check
for bracketed placeholders, TODO markers, and question-phrased actions (see
services/verifier). If it finds anything, the ApprovalRequest is NOT
persisted - fail closed, so a known-bad proposal never reaches a human
reviewer or scripts/apply.py. This mirrors orchestrator, approval, and
apply.py never importing each other: verifier is wired in here, at the glue
layer, not inside orchestrator itself.

Required env: GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_READ_ONLY_FLAG=--read-only=true

Usage:
    .venv/Scripts/python scripts/diagnose.py <approvals.jsonl> "<task description>"
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from approval import ApprovalStore
from orchestrator import GITHUB_READ_TOOLS, ClaudeRunConfig, EnvSecretsProvider, SubprocessClaudeRunner, render_mcp_config, run_task
from orchestrator.orchestrate import SYSTEM_PROMPT
from verifier import check_proposed_actions

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <approvals.jsonl> '<task description>'", file=sys.stderr)
        raise SystemExit(1)
    approvals_path, description = Path(sys.argv[1]), sys.argv[2]

    mcp_config_path = render_mcp_config(REPO_ROOT / ".mcp.github-only.json.template", EnvSecretsProvider())
    try:
        runner = SubprocessClaudeRunner(
            ClaudeRunConfig(
                mcp_config_path=mcp_config_path,
                system_prompt=SYSTEM_PROMPT,
                max_budget_usd=1.0,
                allowed_tools=GITHUB_READ_TOOLS,
            )
        )
        outcome = run_task(
            {
                "id": str(uuid.uuid4()),
                "description": description,
                "requester": "diagnose-script",
                "priority": "normal",
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            runner,
        )
    finally:
        mcp_config_path.unlink(missing_ok=True)

    print(f"cost: ${outcome.run_result.total_cost_usd:.4f}")
    print(f"summary: {outcome.approval_request['summary']}")
    print("proposed actions:")
    for action in outcome.approval_request["proposed_actions"]:
        print(f"  - {action}")

    findings = check_proposed_actions(outcome.approval_request["proposed_actions"])
    if findings:
        print()
        print("REFUSING to persist this proposal - verifier found problems:")
        for finding in findings:
            print(f"  - [{finding.reason}] {finding.action}")
        print()
        print("Fix the underlying diagnostic prompt/task and re-run, rather than editing the proposal by hand.")
        raise SystemExit(1)

    ApprovalStore(approvals_path).create(outcome.approval_request)
    print()
    print(f"approval id: {outcome.approval_request['id']}")
    print(f"To decide: scripts/decide.py {approvals_path} {outcome.approval_request['id']} approve|reject <your-name>")


if __name__ == "__main__":
    main()
