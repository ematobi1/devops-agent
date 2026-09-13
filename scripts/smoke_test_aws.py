"""Manual, one-off validation against real Claude Code + the real AWS
CloudWatch MCP server. Not a gate test - costs real money (bounded by
--max-budget-usd).

Read-only only: the aws-cloudwatch MCP server exposes no write-capable tools
at all (see orchestrator/aws_tools.py), so there's no write/read mode split
here like the GitHub script has. Read-only-ness is enforced by the IAM
policy on AWS_PROFILE (CloudWatchReadOnlyAccess), not a server flag.

Required env: AWS_PROFILE, AWS_REGION (both plain config, not secrets - the
aws-cloudwatch server runs as a local uvx process and reads credentials
straight from ~/.aws/credentials via the profile name).

Usage:
    .venv/Scripts/python scripts/smoke_test_aws.py "list CloudWatch log groups"
"""

import sys
from pathlib import Path

from orchestrator import CLOUDWATCH_READ_TOOLS, ClaudeRunConfig, EnvSecretsProvider, SubprocessClaudeRunner, render_mcp_config, run_task

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} '<task description>'", file=sys.stderr)
        raise SystemExit(1)

    mcp_config_path = render_mcp_config(REPO_ROOT / ".mcp.cloudwatch-only.json.template", EnvSecretsProvider())
    try:
        runner = SubprocessClaudeRunner(
            ClaudeRunConfig(
                mcp_config_path=mcp_config_path,
                system_prompt=_diagnostic_prompt(),
                max_budget_usd=1.0,
                allowed_tools=CLOUDWATCH_READ_TOOLS,
            )
        )
        outcome = run_task(
            {
                "id": "smoke-test-aws-read",
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


def _diagnostic_prompt() -> str:
    from orchestrator.orchestrate import SYSTEM_PROMPT

    return SYSTEM_PROMPT


if __name__ == "__main__":
    main()
