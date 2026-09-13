"""Manual, one-off validation against real Claude Code + the real GitHub MCP
server. Not a gate test - costs real money (bounded by --max-budget-usd).

Phase 1 validates GitHub only. AWS is deliberately not wired into this script
yet - see docs/ARCHITECTURE.md "Decisions" for why (uv not installed, and
adding two new servers at once makes a failure ambiguous between auth,
transport, and orchestration).

Two modes:

  read   Runs the task through orchestrator.run_task() - the real shipped
         diagnostic policy (never applies anything, only produces a pending
         ApprovalRequest). Use with a read-only-scoped PAT.
         Required env: GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_READ_ONLY_FLAG=--read-only=true

  write  Bypasses run_task() and calls the Claude Code runner directly with a
         system prompt that explicitly permits editing files and opening a
         PR. This is NOT the shipped apply path (that doesn't exist yet, see
         docs/ARCHITECTURE.md "Explicitly not built yet") - it's a deliberate,
         human-driven, one-off exercise of the write scopes to prove the
         whole loop (auth, transport, read, write, PR) works before anything
         is automated. Use with a write-scoped PAT pointed at a throwaway repo.
         Required env: GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_READ_ONLY_FLAG=--read-only=false

Usage:
    .venv/Scripts/python scripts/smoke_test.py read "list open PRs on <owner>/<repo>"
    .venv/Scripts/python scripts/smoke_test.py write "in <owner>/<repo>, read README.md, \
append a line, and open a PR with the change against main"
"""

import sys
from pathlib import Path

from orchestrator import (
    GITHUB_READ_TOOLS,
    GITHUB_WRITE_TOOLS,
    ClaudeRunConfig,
    EnvSecretsProvider,
    SubprocessClaudeRunner,
    render_mcp_config,
    run_task,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

WRITE_SYSTEM_PROMPT = (
    "You are validating GitHub MCP write access for a DevOps agent test "
    "harness, running against a throwaway scratch repository. You may read "
    "files, create a branch, edit file contents, and open a pull request "
    "using the available tools. Do not merge or close any pull request. Do "
    "not delete any branch, file, or repository. When finished, report "
    "exactly what you changed, including the PR URL."
)


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("read", "write"):
        print(f"usage: {sys.argv[0]} read|write '<task description>'", file=sys.stderr)
        raise SystemExit(1)
    mode, description = sys.argv[1], sys.argv[2]

    mcp_config_path = render_mcp_config(REPO_ROOT / ".mcp.github-only.json.template", EnvSecretsProvider())
    try:
        if mode == "read":
            runner = SubprocessClaudeRunner(
                ClaudeRunConfig(
                    mcp_config_path=mcp_config_path,
                    system_prompt=_diagnostic_prompt(),
                    max_budget_usd=1.0,
                    allowed_tools=GITHUB_READ_TOOLS,
                )
            )
            outcome = run_task(
                {
                    "id": "smoke-test-read",
                    "description": description,
                    "requester": "smoke-test-script",
                    "priority": "low",
                    "created_at": "2026-09-13T00:00:00Z",
                },
                runner,
            )
            _report_read(outcome)
        else:
            runner = SubprocessClaudeRunner(
                ClaudeRunConfig(
                    mcp_config_path=mcp_config_path,
                    system_prompt=WRITE_SYSTEM_PROMPT,
                    max_budget_usd=1.0,
                    allowed_tools=GITHUB_WRITE_TOOLS,
                )
            )
            result = runner.run(description)
            _report_write(result)
    finally:
        mcp_config_path.unlink(missing_ok=True)


def _diagnostic_prompt() -> str:
    from orchestrator.orchestrate import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _report_read(outcome) -> None:
    print(f"cost: ${outcome.run_result.total_cost_usd:.4f}")
    print(f"tool calls: {[(c.server, c.tool) for c in outcome.run_result.tool_calls]}")
    print("---")
    print(outcome.approval_request["summary"])
    print("proposed actions:", outcome.approval_request["proposed_actions"])


def _report_write(result) -> None:
    print(f"cost: ${result.total_cost_usd:.4f}")
    print(f"is_error: {result.is_error}  terminal_reason: {result.terminal_reason}")
    print(f"tool calls: {[(c.server, c.tool, c.is_error) for c in result.tool_calls]}")
    print("---")
    print(result.result_text)


if __name__ == "__main__":
    main()
