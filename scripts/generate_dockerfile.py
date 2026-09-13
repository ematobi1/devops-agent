"""Generates a Dockerfile for a GitHub repo via a read-only Claude Code run,
lints it deterministically via `docker build --check` (no LLM call - the
same Dockerfile content and base-image registry state always produce the
same warnings), and persists a well-formed ApprovalRequest - or refuses to,
if generation didn't follow instructions or linting found problems.

Does NOT use orchestrator.run_task() - that function's proposed_actions
extraction is built for numbered diagnostic action lists ("1. ... 2. ..."),
not a single multi-line file's content. Calls the runner directly (same
pattern as scripts/smoke_test.py's write mode) and builds the
ApprovalRequest here using codegen's own extraction logic. The resulting
ApprovalRequest still goes through the same verifier check, and the same
decide.py / apply.py pipeline as any other proposal - apply_approved()
needed no changes to handle this, since the tightened diagnostic
SYSTEM_PROMPT already required "write content out in full" for any action
that needs it.

Required env: GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_READ_ONLY_FLAG=--read-only=true
Also requires Docker Desktop running (for the lint check, in addition to
whatever github-mcp-server already needs it for).

Usage:
    .venv/Scripts/python scripts/generate_dockerfile.py <approvals.jsonl> <owner/repo>
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from approval import ApprovalStore
from codegen import DOCKERFILE_SYSTEM_PROMPT, extract_dockerfile, lint_dockerfile
from orchestrator import GITHUB_READ_TOOLS, ClaudeRunConfig, EnvSecretsProvider, SubprocessClaudeRunner, render_mcp_config
from verifier import check_proposed_actions

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <approvals.jsonl> <owner/repo>", file=sys.stderr)
        raise SystemExit(1)
    approvals_path, target_repo = Path(sys.argv[1]), sys.argv[2]

    mcp_config_path = render_mcp_config(REPO_ROOT / ".mcp.github-only.json.template", EnvSecretsProvider())
    try:
        runner = SubprocessClaudeRunner(
            ClaudeRunConfig(
                mcp_config_path=mcp_config_path,
                system_prompt=DOCKERFILE_SYSTEM_PROMPT,
                max_budget_usd=1.0,
                allowed_tools=GITHUB_READ_TOOLS,
            )
        )
        run_result = runner.run(f"Generate a Dockerfile for the repository {target_repo}")
    finally:
        mcp_config_path.unlink(missing_ok=True)

    if run_result.is_error:
        print(f"generation run failed: terminal_reason={run_result.terminal_reason}", file=sys.stderr)
        raise SystemExit(1)

    dockerfile_content = extract_dockerfile(run_result.result_text)
    if dockerfile_content is None:
        print("generation did not produce a properly labeled ```dockerfile block - refusing to propose anything", file=sys.stderr)
        print("--- full response ---", file=sys.stderr)
        print(run_result.result_text, file=sys.stderr)
        raise SystemExit(1)

    print("--- generated Dockerfile ---")
    print(dockerfile_content)

    print("--- linting via docker build --check ---")
    lint_findings = lint_dockerfile(dockerfile_content)
    if lint_findings:
        print("REFUSING to propose this Dockerfile - lint findings:")
        for finding in lint_findings:
            print(f"  - {finding.reason}")
        raise SystemExit(1)
    print("lint clean.")

    approval_request = {
        "id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "summary": f"Generated Dockerfile for {target_repo}, linted clean via `docker build --check`.",
        "proposed_actions": [
            {
                "instruction": f"Create a Dockerfile in {target_repo} with exactly the following content, via a PR.",
                "payload": dockerfile_content,
            }
        ],
        "status": "pending",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Only the instruction is prose-scanned - never payload. Findings are
    # attached and still persisted (annotate, not block): a false positive
    # here should cost a glance at decision time, not a whole re-run. See
    # scripts/diagnose.py docstring for why this changed from blocking.
    verifier_findings = check_proposed_actions(approval_request["proposed_actions"])
    if verifier_findings:
        print("verifier warnings (persisted, review before deciding):")
        for finding in verifier_findings:
            print(f"  - [{finding.reason}] {finding.action[:200]}")
        approval_request["verifier_warnings"] = [{"action": f.action, "reason": f.reason} for f in verifier_findings]

    ApprovalStore(approvals_path).create(approval_request)
    print(f"cost: ${run_result.total_cost_usd:.4f}")
    print(f"approval id: {approval_request['id']}")
    print(f"To decide: scripts/decide.py {approvals_path} {approval_request['id']} approve|reject <your-name>")


if __name__ == "__main__":
    main()
