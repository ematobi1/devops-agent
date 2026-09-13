# approval

The only service allowed to hold apply-time credentials and trigger a real
change. Everything upstream (`orchestrator`) only ever produces a *proposal*.

## Modules

- `store.py` — `ApprovalStore`: append-only JSONL audit log of approval
  requests. `create()` requires `status: "pending"`; `decide()` enforces
  single-decision (can't approve an already-decided request) and validates
  every write against `contracts/approval_request.schema.json`.
- `change_log.py` — `ChangeLog`: append-only record of what actually
  happened after a decision (`applied` / `rejected` / `failed_verification`),
  validated against `contracts/change_record.schema.json`. This is the audit
  trail for "what did the agent actually do."

## What's not built yet

The actual *apply* step (calling the GitHub/AWS MCP servers — or their write
APIs directly — with elevated credentials once an approval is `approved`)
is intentionally not implemented in this Phase 1 MVP. Building it before a
real first use case (see `docs/ARCHITECTURE.md` open question #2) would be
speculative: the shape of "apply" differs a lot between "revert a GitHub
commit" and "resize an RDS instance," and guessing now means throwing it away
later. Wire it up against your actual first tool integration, not both at
once.

## Tests

`pytest services/approval/tests` — deterministic, file-backed with `tmp_path`
fixtures, no network.
