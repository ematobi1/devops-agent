"""Post-apply diff verification. Given a ChangeRecord (produced by
scripts/apply.py), fetches the actual resulting PR diff and state via the
`gh` CLI and reports whether basic structural invariants hold - formalizes
the manual `gh pr diff`/`gh pr view` checks done by hand throughout this
project's own validation into a repeatable step.

Does NOT judge whether the diff's content matches the approved plan's
intent - see services/verifier/src/verifier/pr_diff.py for why. This is a
human-facing report, not a gate: nothing here blocks merging a PR
automatically. The operator still decides, and should still read the diff.

Uses your own `gh` session (read access), not the agent's scoped
GITHUB_PERSONAL_ACCESS_TOKEN - this is independent verification of what the
agent did, not another action taken by the agent, so it deliberately uses a
different credential path.

Usage:
    .venv/Scripts/python scripts/verify_apply.py <changes.jsonl> <approval-id>
"""

import sys
from pathlib import Path

from approval import ChangeLog
from verifier import check_diff, fetch_pr_diff_and_state, find_pr_reference


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <changes.jsonl> <approval-id>", file=sys.stderr)
        raise SystemExit(1)
    changes_path, approval_id = Path(sys.argv[1]), sys.argv[2]

    records = [r for r in ChangeLog(changes_path).all() if r["approval_id"] == approval_id]
    if not records:
        print(f"no change record found for approval {approval_id!r}", file=sys.stderr)
        raise SystemExit(1)
    record = records[-1]

    if record["outcome"] != "applied":
        print(f"change record outcome is {record['outcome']!r}, not 'applied' - nothing to verify")
        raise SystemExit(1)

    ref = find_pr_reference(record.get("detail", ""))
    if ref is None:
        print("no PR URL found in change record detail - nothing to verify")
        raise SystemExit(1)

    print(f"verifying {ref.owner}/{ref.repo}#{ref.number} ...")
    diff_text, pr_state = fetch_pr_diff_and_state(ref)
    findings = check_diff(diff_text)

    # PR state is informational only - a human merging/closing this PR
    # after review is expected, not a finding. See pr_diff.py docstring.
    print(f"PR state: {pr_state}")
    print(f"diff size: {len(diff_text)} chars")
    if findings:
        print("FINDINGS:")
        for finding in findings:
            print(f"  - {finding.reason}")
        raise SystemExit(1)

    print("no structural issues found. This only checks structure, not intent - read the diff yourself.")


if __name__ == "__main__":
    main()
