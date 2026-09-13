"""Manual, one-off smoke test against real Claude Code + real MCP servers.

Not a gate test - costs real money (bounded by --max-budget-usd) and requires
GITHUB_PERSONAL_ACCESS_TOKEN / AWS_PROFILE / AWS_REGION to be set. Run by hand
once secrets are configured:

    .venv/Scripts/python scripts/smoke_test.py "list open PRs on <owner>/<repo>"
"""

import sys
from pathlib import Path

from orchestrator import ClaudeRunConfig, EnvSecretsProvider, SubprocessClaudeRunner, render_mcp_config, run_task
from orchestrator.orchestrate import SYSTEM_PROMPT

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} '<task description>'", file=sys.stderr)
        raise SystemExit(1)

    mcp_config_path = render_mcp_config(REPO_ROOT / ".mcp.json.template", EnvSecretsProvider())
    try:
        runner = SubprocessClaudeRunner(
            ClaudeRunConfig(mcp_config_path=mcp_config_path, system_prompt=SYSTEM_PROMPT, max_budget_usd=1.0)
        )
        outcome = run_task(
            {
                "id": "smoke-test",
                "description": sys.argv[1],
                "requester": "smoke-test-script",
                "priority": "low",
                "created_at": "2026-09-13T00:00:00Z",
            },
            runner,
        )
    finally:
        mcp_config_path.unlink(missing_ok=True)

    print(f"cost: ${outcome.run_result.total_cost_usd:.4f}")
    print(f"tool calls: {[(c.server, c.tool) for c in outcome.run_result.tool_calls]}")
    print("---")
    print(outcome.approval_request["summary"])
    print("proposed actions:", outcome.approval_request["proposed_actions"])


if __name__ == "__main__":
    main()
