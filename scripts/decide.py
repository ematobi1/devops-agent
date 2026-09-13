"""Records a human decision on a pending ApprovalRequest. Pure local state -
no network call, no credentials needed: approving something here does not
execute it. Applying an approved request is a separate, later step -
scripts/apply.py.

Usage:
    .venv/Scripts/python scripts/decide.py <approvals.jsonl> <approval-id> approve|reject <decided-by>
"""

import sys
from datetime import datetime, timezone

from approval import ApprovalStore


def main() -> None:
    if len(sys.argv) != 5 or sys.argv[3] not in ("approve", "reject"):
        print(f"usage: {sys.argv[0]} <approvals.jsonl> <approval-id> approve|reject <decided-by>", file=sys.stderr)
        raise SystemExit(1)
    approvals_path, approval_id, verb, decided_by = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    status = "approved" if verb == "approve" else "rejected"

    store = ApprovalStore(approvals_path)
    updated = store.decide(
        approval_id,
        status=status,
        decided_by=decided_by,
        decided_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    print(f"approval {approval_id!r} -> {updated['status']} (by {decided_by})")


if __name__ == "__main__":
    main()
