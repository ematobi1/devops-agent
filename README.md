# AI DevOps Engineer Agent

Design: `docs/ARCHITECTURE.md`. Read that first — it explains why this isn't
built the way the original 8-phase plan described (no OpenAI/Langchain; local
Claude Code is the agent runtime; GitHub/AWS integrations are official MCP
servers, not custom adapters).

## Status: Phase 1 MVP — diagnostic loop only, no apply

What works end to end right now: `orchestrator.run_task()` takes a
`TaskRequest`, runs local Claude Code headlessly and read-only against the
registered MCP servers, and produces a pending `ApprovalRequest` in
`services/approval`. Nothing gets applied to GitHub or AWS yet — that's the
next real increment, scoped to whichever tool integration you pick first
(see the open questions in the architecture doc).

## Setup

```
scripts/setup.sh          # or scripts/setup.ps1 on Windows
```

Then, before running anything against real GitHub/AWS data:

```
export GITHUB_PERSONAL_ACCESS_TOKEN=...
export AWS_PROFILE=...
export AWS_REGION=...
```

See `services/mcp-servers/github/README.md` and
`services/mcp-servers/cloud/README.md` for what each token/profile needs to
be scoped to, and prerequisites (Docker for GitHub, `uv`/`uvx` for AWS — the
AWS one isn't installed on this machine yet).

## Tests

```
.venv/Scripts/python -m pytest
```

48 gate tests, all deterministic, no network, runs in well under a second.
`scripts/setup.sh` wires these into a pre-commit hook
(`git config core.hooksPath scripts/hooks`).

## Manual smoke test (costs real money, needs secrets configured)

```
.venv/Scripts/python scripts/smoke_test.py "list open PRs on <owner>/<repo>"
```

## Layout

```
contracts/            shared JSON Schemas + validate() - both sides import, never reach into internals
services/
  orchestrator/        trigger -> Claude Code run -> ApprovalRequest
  approval/             append-only approval + change audit log; only service that will hold apply credentials
  mcp-servers/
    github/README.md    registers github/github-mcp-server (official) - no custom code
    cloud/README.md      registers awslabs/mcp servers (official) - no custom code
docs/ARCHITECTURE.md
```
