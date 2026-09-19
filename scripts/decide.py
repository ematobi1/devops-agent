"""Records a human decision on a pending ApprovalRequest. Pure local state -
no network call, no credentials needed: approving something here does not
execute it. Applying an approved request is a separate, later step -
scripts/apply.py.

Prints any verifier_warnings stored on the request before recording the
decision - services/verifier annotates rather than blocks (see
scripts/diagnose.py), so this is the point where a human actually needs to
see them, not just when the proposal was first generated (which could have
happened in a different session, hours or days earlier).

Usage:
    .venv/Scripts/python scripts/decide.py <approvals.jsonl> <approval-id> approve|reject <decided-by>
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from approval import ApprovalStore


def main() -> None:
    # See scripts/apply.py's identical fix (2026-09-17): the
    # verifier_warnings printed below are derived from Claude-generated
    # text and can crash with UnicodeEncodeError under cp1252, Windows'
    # console codepage default even for redirected stdout.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) != 5 or sys.argv[3] not in ("approve", "reject"):
        print(f"usage: {sys.argv[0]} <approvals.jsonl> <approval-id> approve|reject <decided-by>", file=sys.stderr)
        raise SystemExit(1)
    approvals_path, approval_id, verb, decided_by = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    status = "approved" if verb == "approve" else "rejected"

    store = ApprovalStore(approvals_path)
    pending = store.get(approval_id)

    print(f"summary: {pending['summary']}")
    print("proposed actions:")
    for action in pending["proposed_actions"]:
        print(f"  - {action['instruction']}")
        for payload_line in (action.get("payload") or "").splitlines():
            print(f"    | {payload_line}")
    print()

    warnings = pending.get("verifier_warnings") or []
    if warnings:
        print("verifier warnings on this proposal:")
        for warning in warnings:
            print(f"  - [{warning['reason']}] {warning['action']}")
        print()

    updated = store.decide(
        approval_id,
        status=status,
        decided_by=decided_by,
        decided_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    print(f"approval {approval_id!r} -> {updated['status']} (by {decided_by})")


if __name__ == "__main__":
    main()
