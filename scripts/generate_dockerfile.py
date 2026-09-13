"""Generates a Dockerfile for a GitHub repo via a read-only Claude Code run,
lints it deterministically via `docker build --check`, checks it against
deterministic architectural/security policy invariants, and persists an
ApprovalRequest - refusing only on generation failure or a real lint error;
policy and prose findings are attached and persisted, not blocking.

Base image is a CONFIG INPUT (codegen.resolve_base_image), not something the
model decides - see codegen/base_images.py for why: a sample of 6
independent generations against the same repo showed the model re-deriving
the base image and privilege-drop strategy differently each run, including
one architecturally unsafe choice (a silently-no-op `sed` rootless
conversion) that still passed `docker build --check` clean. Don't treat
that lint check as a safety oracle - it validates syntax, not decisions;
codegen.check_dockerfile_policy() is what actually catches this class of
problem, and it's a separate, deterministic check from the lint.

Does NOT use orchestrator.run_task() - that function's proposed_actions
extraction is built for numbered diagnostic action lists ("1. ... 2. ..."),
not a single multi-line file's content. Calls the runner directly (same
pattern as scripts/smoke_test.py's write mode) and builds the
ApprovalRequest here using codegen's own extraction logic.

Required env: GITHUB_PERSONAL_ACCESS_TOKEN, GITHUB_READ_ONLY_FLAG=--read-only=true
Also requires Docker Desktop running (for the lint check, in addition to
whatever github-mcp-server already needs it for).

Usage:
    .venv/Scripts/python scripts/generate_dockerfile.py <approvals.jsonl> <owner/repo> <app-type>

<app-type> must be a key in codegen.BASE_IMAGES (currently: static-site).
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from approval import ApprovalStore
from codegen import (
    BASE_IMAGES,
    build_dockerfile_system_prompt,
    check_dockerfile_policy,
    extract_dockerfile,
    lint_dockerfile,
    resolve_base_image,
)
from orchestrator import GITHUB_READ_TOOLS, ClaudeRunConfig, EnvSecretsProvider, SubprocessClaudeRunner, render_mcp_config
from verifier import check_proposed_actions

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} <approvals.jsonl> <owner/repo> <app-type>", file=sys.stderr)
        print(f"configured app types: {sorted(BASE_IMAGES)}", file=sys.stderr)
        raise SystemExit(1)
    approvals_path, target_repo, app_type = Path(sys.argv[1]), sys.argv[2], sys.argv[3]

    base_image = resolve_base_image(app_type)
    print(f"base image (operator policy, app_type={app_type!r}): {base_image}")

    mcp_config_path = render_mcp_config(REPO_ROOT / ".mcp.github-only.json.template", EnvSecretsProvider())
    try:
        runner = SubprocessClaudeRunner(
            ClaudeRunConfig(
                mcp_config_path=mcp_config_path,
                system_prompt=build_dockerfile_system_prompt(base_image),
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
    print("lint clean (syntax/best-practice only - not a security review).")

    approval_request = {
        "id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "summary": f"Generated Dockerfile for {target_repo} (app_type={app_type}), linted clean via `docker build --check`.",
        "proposed_actions": [
            {
                "instruction": f"Create a Dockerfile in {target_repo} with exactly the following content, via a PR.",
                "payload": dockerfile_content,
            }
        ],
        "status": "pending",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Prose check (instruction only, never payload) and Dockerfile policy
    # check (content itself) are both deterministic, and both annotate
    # rather than block - a false positive should cost a glance at decision
    # time, not a discarded run. See scripts/diagnose.py and
    # codegen/base_images.py docstrings for why each was built this way.
    all_findings = [
        {"action": f.action, "reason": f.reason} for f in check_proposed_actions(approval_request["proposed_actions"])
    ] + [
        {"action": "generated Dockerfile", "reason": f.reason}
        for f in check_dockerfile_policy(dockerfile_content, allowed_base_images=frozenset(BASE_IMAGES.values()))
    ]
    if all_findings:
        print("verifier warnings (persisted, review before deciding):")
        for finding in all_findings:
            print(f"  - [{finding['reason']}] {finding['action'][:200]}")
        approval_request["verifier_warnings"] = all_findings

    ApprovalStore(approvals_path).create(approval_request)
    print(f"cost: ${run_result.total_cost_usd:.4f}")
    print(f"approval id: {approval_request['id']}")
    print(f"To decide: scripts/decide.py {approvals_path} {approval_request['id']} approve|reject <your-name>")


if __name__ == "__main__":
    main()
