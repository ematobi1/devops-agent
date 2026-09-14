"""One-off diagnostic: proves whether a read-scoped GitHub token can
actually read a specific file from a specific repo, by printing the FULL
raw tool call (server, tool, input, output, is_error) - not a model summary.

Exists because generate_dockerfile.py and diagnose.py don't persist
run_result.tool_calls anywhere (ClaudeRunConfig always sets
--no-session-persistence), so a past run's actual tool-call trace can't be
retroactively inspected. Use this to check *current* access; it says
nothing about what a *past* run's token could or couldn't do.

Required env: GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_READ_ONLY_FLAG=--read-only=true

Usage:
    .venv/Scripts/python scripts/verify_read_access.py <owner/repo> <file_path>
"""

import sys
from pathlib import Path

from orchestrator import GITHUB_READ_TOOLS, ClaudeRunConfig, EnvSecretsProvider, SubprocessClaudeRunner, render_mcp_config

REPO_ROOT = Path(__file__).resolve().parent.parent

SYSTEM_PROMPT = (
    "Call the get_file_contents tool exactly once for the exact repo and "
    "path given in the task. Do not call any other tool. Report only "
    "whether the call succeeded or failed."
)


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <owner/repo> <file_path>", file=sys.stderr)
        raise SystemExit(1)
    target_repo, file_path = sys.argv[1], sys.argv[2]

    mcp_config_path = render_mcp_config(REPO_ROOT / ".mcp.github-only.json.template", EnvSecretsProvider())
    try:
        runner = SubprocessClaudeRunner(
            ClaudeRunConfig(
                mcp_config_path=mcp_config_path,
                system_prompt=SYSTEM_PROMPT,
                max_budget_usd=0.5,
                allowed_tools=GITHUB_READ_TOOLS,
            )
        )
        run_result = runner.run(f"Get the contents of {file_path} in {target_repo}")
    finally:
        mcp_config_path.unlink(missing_ok=True)

    print(f"is_error (overall run): {run_result.is_error}")
    print(f"terminal_reason: {run_result.terminal_reason}")
    print(f"number of tool calls: {len(run_result.tool_calls)}")
    print()
    for i, call in enumerate(run_result.tool_calls):
        print(f"--- tool call {i} ---")
        print(f"server: {call.server}")
        print(f"tool: {call.tool}")
        print(f"input: {call.input}")
        print(f"is_error: {call.is_error}")
        print(f"output: {call.output}")
        print()


if __name__ == "__main__":
    main()
