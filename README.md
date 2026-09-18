# AI DevOps Engineer Agent

[![CI](https://github.com/ematobi1/devops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ematobi1/devops-agent/actions/workflows/ci.yml)

Design: `docs/ARCHITECTURE.md`. Read that first — it explains why this isn't
built the way the original 8-phase plan described (no OpenAI/Langchain; local
Claude Code is the agent runtime; GitHub/AWS integrations are official MCP
servers, not custom adapters), and has the full validation history and every
real bug/finding hit along the way.

## Status (2026-09-17)

- **Diagnostic loop:** working end to end against real GitHub and real AWS
  CloudWatch. `scripts/diagnose.py` runs `orchestrator.run_task()`, verifies
  the result with `services/verifier`, and persists a pending
  `ApprovalRequest` in `services/approval` — or refuses to, if verification
  finds a problem.
- **Approve/apply (v1, GitHub only):** working end to end. `scripts/decide.py`
  records an approve/reject decision (pure local state, no credentials);
  `scripts/apply.py` executes an already-approved request via a write-scoped
  Claude Code run. Two separate, human-triggered actions — nothing
  auto-applies.
- **CI:** gate tests run on every push/PR via GitHub Actions.
- **Post-apply diff verification:** `scripts/verify_apply.py` fetches the
  real PR diff for an applied change and checks it's non-empty and deletes
  nothing — structural checks only, doesn't judge intent.
- **Codegen (v1, Dockerfiles):** `scripts/generate_dockerfile.py` generates
  a Dockerfile via Claude Code, lints it with `docker build --check`, then
  feeds the same approve/apply pipeline as any other proposal. Now
  supports a CLI-shaped app type (`devops-agent-cli`: no listening port, a
  `RUN`-install step, a non-root user templated after install) alongside
  the original static-site type. **Full happy path validated live
  (2026-09-17)** end to end against `ematobi1/devops-agent` itself:
  generate → lint clean → zero warnings → approve → apply → real PR
  ([#1](https://github.com/ematobi1/devops-agent/pull/1)) →
  post-apply diff verification, plus an independent real `docker build` +
  `docker run` of the model's own generated Dockerfile (non-root, all
  packages import, correct `CMD` behavior). See
  `services/codegen/README.md` "Validated" for two real bugs a manual
  `docker build`/`docker run` sanity check (lint/policy checks alone don't
  execute anything) caught and fixed before that live run: a relative
  install path resolving against the wrong `WORKDIR`, and a `--chown`
  silently no-op'ing against a user that didn't exist yet.
- **AWS apply:** not built — no concrete use case to build it against yet.
  See `docs/ARCHITECTURE.md`.

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

145 gate tests, all deterministic, no network, runs in well under a second.
`scripts/setup.sh` wires these into a pre-commit hook
(`git config core.hooksPath scripts/hooks`); CI runs them on every push/PR.

## Running the pipeline

```
.venv/Scripts/python scripts/diagnose.py <approvals.jsonl> "<task description>"
.venv/Scripts/python scripts/decide.py <approvals.jsonl> <approval-id> approve|reject <your-name>
.venv/Scripts/python scripts/apply.py <approvals.jsonl> <approval-id> <changes.jsonl>
.venv/Scripts/python scripts/verify_apply.py <changes.jsonl> <approval-id>
```

## Web UI (optional)

`ui/` is a thin local FastAPI/vanilla-JS front end over the same pipeline
scripts below, in two modes sharing one approve/apply flow: **Dockerfile**
(generate/lint/verify, narrow) and **Diagnose** (a general natural-language
DevOps request against GitHub, read-only - added 2026-09-18 to expose a
capability this project already had since Phase 1 but the UI never
surfaced). Explicit browser Approve step either way, apply live via SSE.
It invokes the real scripts as subprocesses; it does not reimplement any
pipeline logic. See `ui/README.md`, including its "Deviations from the
spec" section on where the original build prompt's assumptions didn't
match this pipeline's actual shape.

```
.venv/Scripts/python -m pip install -r ui/requirements.txt   # once
export GITHUB_PERSONAL_ACCESS_TOKEN=<your fine-grained PAT>
.venv/Scripts/python -m uvicorn ui.app:app --host 127.0.0.1 --port 8000
```

Or in a container - see `ui/README.md` "Run it in a container" for why
the Docker socket mount isn't optional, and "Claude Code auth in the
container" for why the session mount is too (real generate/apply calls
need Claude Code authenticated, and this project deliberately uses your
subscription session, not an API key):

```
docker build -f ui/Dockerfile -t devops-agent-ui .
docker run -d --name devops-agent-ui \
  -p 127.0.0.1:8000:8000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "<path-to-your-home>/.claude.json:/root/.claude.json" \
  -v "<path-to-your-home>/.claude:/root/.claude" \
  -e GITHUB_PERSONAL_ACCESS_TOKEN=<your PAT> \
  devops-agent-ui
```

## Manual scripts (cost real money, need secrets configured)

```
.venv/Scripts/python scripts/smoke_test.py read|write "<task>"    # GitHub
.venv/Scripts/python scripts/smoke_test_aws.py "<task>"           # AWS CloudWatch, read-only
.venv/Scripts/python scripts/verify_read_access.py <owner/repo> <file_path>  # raw tool-call proof of read access
.venv/Scripts/python scripts/generate_dockerfile.py <approvals.jsonl> <owner/repo> <app-type>
```

## Layout

```
contracts/            shared JSON Schemas + validate() - both sides import, never reach into internals
services/
  orchestrator/        trigger -> Claude Code run -> ApprovalRequest; apply_approved() executes an approved one
  approval/             append-only approval + change audit log
  verifier/              deterministic (no LLM) checks - see services/verifier/README.md
  codegen/                generates + lints Dockerfiles - see services/codegen/README.md
  mcp-servers/
    github/README.md    registers github/github-mcp-server (official) - validated read + write + PR
    cloud/README.md      registers awslabs.cloudwatch-mcp-server (official) - validated read-only
docs/ARCHITECTURE.md
```
