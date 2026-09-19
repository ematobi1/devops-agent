"""Executes a previously approved DevOps change.

NOT auto-triggered by approval. A human must (1) approve a request via
services/approval.ApprovalStore.decide(status="approved") and (2) separately
run this script - two deliberate actions, not one, per docs/ARCHITECTURE.md
"Apply step" decision (2026-09-13). There is no code path anywhere in this
repo that goes from "approved" straight to "applied" without a human
explicitly invoking this script in between.

Scope (v1): GitHub only. AWS has no validated write path - the CloudWatch
MCP server exposes no write-capable tools at all (see
services/mcp-servers/cloud/README.md). Uses GITHUB_WRITE_TOOLS, the exact
allowlist proven live in scripts/smoke_test.py's write mode: read tools plus
create_branch, create_or_update_file, create_pull_request. Never
merge_pull_request or delete_file - not in this allowlist, so Claude Code
cannot call them regardless of what the approved plan says.

Required env: GITHUB_PERSONAL_ACCESS_TOKEN (write-scoped, per
services/mcp-servers/github/README.md Stage 2), GITHUB_READ_ONLY_FLAG=--read-only=false

Real bug found live (2026-09-14): apply_approved() classifies outcome using
only the overall Claude Code run's is_error flag. A model that hits a 403
on create_branch and gracefully explains "I can't do this, here's why"
before stopping produces is_error=False at the run level - identical to
genuine success - so the ChangeRecord said "applied" when nothing was ever
written. Fixed here, not in apply_approved() (which must stay
integration-agnostic and can't know what "a successful write" means for an
arbitrary integration): after a GitHub apply, if the outcome claims
"applied" but no PR URL appears anywhere in the result text, that's a
contradiction - downgrade to failed_verification and say why, using
verifier.find_pr_reference() (already built for scripts/verify_apply.py).

Usage:
    .venv/Scripts/python scripts/apply.py <approvals.jsonl> <approval-id> <changes.jsonl>
"""

import getpass
import sys
from pathlib import Path

from approval import ApprovalStore, ChangeLog
from orchestrator import (
    GITHUB_WRITE_TOOLS,
    ClaudeRunConfig,
    EnvSecretsProvider,
    SubprocessClaudeRunner,
    apply_approved,
    render_mcp_config,
)
from orchestrator.apply import APPLY_SYSTEM_PROMPT
from security_scan import require_preflight, scan_text, screen_proposal
from verifier import find_pr_reference

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    # Real bug found live (2026-09-17, via the ui/ web frontend piping this
    # script's stdout - a redirected-stdout execution mode the interactive
    # CLI never exercised): printing Claude-generated text containing a
    # character outside cp1252 (Windows' default console codepage, used
    # even when stdout isn't a real console) crashes with
    # UnicodeEncodeError, right after a real apply already ran - the same
    # class of bug already fixed once for the *decoding* side of a
    # subprocess boundary (claude_runner.py, see docs/ARCHITECTURE.md
    # "Apply step end-to-end validation"), never fixed for this script's
    # own *encoding* side until now.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} <approvals.jsonl> <approval-id> <changes.jsonl>", file=sys.stderr)
        raise SystemExit(1)
    approvals_path, approval_id, changes_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])

    approval = ApprovalStore(approvals_path).get(approval_id)
    require_preflight(REPO_ROOT, stage="write")
    screen_proposal(approval, "approved content")

    print(f"About to APPLY approval {approval_id!r} (task {approval['task_id']!r}):")
    print(f"  summary: {approval['summary']}")
    print("  actions:")
    for action in approval["proposed_actions"]:
        print(f"    - {action['instruction']}")
        if action.get("payload"):
            print("      payload:")
            for payload_line in action["payload"].splitlines():
                print(f"        | {payload_line}")
    if input("Type 'apply' to proceed, anything else to abort: ").strip() != "apply":
        print("Aborted - nothing was executed.")
        raise SystemExit(1)

    mcp_config_path = render_mcp_config(REPO_ROOT / ".mcp.github-only.json.template", EnvSecretsProvider())
    try:
        runner = SubprocessClaudeRunner(
            ClaudeRunConfig(
                mcp_config_path=mcp_config_path,
                system_prompt=APPLY_SYSTEM_PROMPT,
                max_budget_usd=1.0,
                allowed_tools=GITHUB_WRITE_TOOLS,
            )
        )
        outcome = apply_approved(approval, runner)
    finally:
        mcp_config_path.unlink(missing_ok=True)

    change_record = dict(outcome.change_record, applied_by=getpass.getuser())
    if scan_text(change_record.get("detail", ""), "apply result"):
        print("WARNING: the apply result text contains a secret-shaped value; it is withheld from the audit log.")
        change_record["detail"] = "[withheld: secret-shaped value in apply result]"
    if change_record["outcome"] == "applied" and find_pr_reference(outcome.run_result.result_text) is None:
        print(
            "WARNING: apply_approved() reported outcome=applied, but no PR URL appears anywhere in the "
            "result text - this contradicts a real GitHub apply, which always ends in a PR. Downgrading "
            "to failed_verification rather than persisting a false success."
        )
        change_record = dict(change_record, outcome="failed_verification")

    ChangeLog(changes_path).record(change_record)

    print(f"cost: ${outcome.run_result.total_cost_usd:.4f}")
    print(f"outcome: {change_record['outcome']}")
    print("---")
    print(outcome.run_result.result_text)


if __name__ == "__main__":
    main()
