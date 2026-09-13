# AI DevOps Engineer Agent

[![CI](https://github.com/ematobi1/devops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ematobi1/devops-agent/actions/workflows/ci.yml)

Design: `docs/ARCHITECTURE.md`. Read that first — it explains why this isn't
built the way the original 8-phase plan described (no OpenAI/Langchain; local
Claude Code is the agent runtime; GitHub/AWS integrations are official MCP
servers, not custom adapters), and has the full validation history and every
real bug/finding hit along the way.

## Status (2026-09-13)

- **Diagnostic loop:** working end to end against real GitHub and real AWS
  CloudWatch. `orchestrator.run_task()` takes a `TaskRequest`, runs local
  Claude Code headlessly and read-only against the registered MCP servers,
  and produces a pending `ApprovalRequest` in `services/approval`.
- **Apply (v1, GitHub only):** working end to end. `scripts/apply.py`
  executes an already-approved request via a write-scoped Claude Code run
  (`orchestrator.apply.apply_approved()`). Approving a request and applying
  it are two separate, human-triggered actions — nothing auto-applies.
- **AWS apply, `services/verifier`, `services/codegen`:** not built — no
  concrete use case to build them against yet. See `docs/ARCHITECTURE.md`.

## Setup

```
scripts/setup.sh          # or scripts/setup.ps1 on Windows
```

Then, before running anything against real GitHub/AWS data — see
`services/mcp-servers/github/README.md` and
`services/mcp-servers/cloud/README.md` for exact scoping (fine-grained PAT
permissions, IAM policy) and prerequisites (Docker for GitHub; `uv`/`uvx`
for AWS):

```
export GITHUB_PERSONAL_ACCESS_TOKEN=...
export GITHUB_READ_ONLY_FLAG=--read-only=true   # or =false for write/apply
export AWS_PROFILE=...
export AWS_REGION=...
```

## Tests

```
.venv/Scripts/python -m pytest
```

68 gate tests, all deterministic, no network, runs in well under a second.
`scripts/setup.sh` wires these into a pre-commit hook
(`git config core.hooksPath scripts/hooks`).

## Manual scripts (cost real money, need secrets configured)

```
.venv/Scripts/python scripts/smoke_test.py read|write "<task>"    # GitHub
.venv/Scripts/python scripts/smoke_test_aws.py "<task>"           # AWS CloudWatch, read-only
.venv/Scripts/python scripts/apply.py <approvals.jsonl> <approval-id> <changes.jsonl>
```

## Layout

```
contracts/            shared JSON Schemas + validate() - both sides import, never reach into internals
services/
  orchestrator/        trigger -> Claude Code run -> ApprovalRequest; apply_approved() executes an approved one
  approval/             append-only approval + change audit log
  mcp-servers/
    github/README.md    registers github/github-mcp-server (official) - validated read + write + PR
    cloud/README.md      registers awslabs.cloudwatch-mcp-server (official) - validated read-only
docs/ARCHITECTURE.md
```
