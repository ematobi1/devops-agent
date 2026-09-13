# github MCP server

Not custom code — this registers GitHub's own officially maintained
[`github/github-mcp-server`](https://github.com/github/github-mcp-server)
(verified locally: image `ghcr.io/github/github-mcp-server`, version
`v1.12.1`) in `.mcp.github-only.json.template` at the repo root. Per the
"search before building" rule, writing a bespoke GitHub adapter would just be
a worse, unmaintained duplicate of what GitHub already ships.

## Prerequisites

- **Docker Desktop must be running.** `docker info` must succeed before
  Claude Code can spawn this server — it will otherwise fail silently
  mid-run with no clear auth-vs-transport signal. Confirmed on this machine:
  Docker Desktop was not running by default and had to be started manually
  (`"/c/Program Files/Docker/Docker/Docker Desktop.exe"`), then polled with
  `docker info` until the daemon responded (~10s).
- A GitHub **fine-grained** personal access token (not classic — classic
  tokens can't be scoped to a single repo). Create one at
  github.com → Settings → Developer settings → Personal access tokens →
  Fine-grained tokens → Generate new token.

## Verified server invocation (not assumed from docs — checked against
`docker run --rm ghcr.io/github/github-mcp-server stdio --help` directly,
since the public docs for env-var configuration were stale for this image
version)

- Read-only and toolset restriction are **CLI flags on the `stdio`
  subcommand**, not environment variables: `stdio --read-only=<true|false>
  --toolsets=repos,pull_requests`. `GITHUB_READ_ONLY` / `GITHUB_TOOLSETS` env
  vars are **not** read by this version — don't rely on stale blog posts or
  older READMEs that say otherwise.
- The token **is** read from the `GITHUB_PERSONAL_ACCESS_TOKEN` environment
  variable (confirmed: no `--token`/`--pat` flag exists; `app-id`/
  `app-private-key-path` flags are a separate GitHub App auth path we don't
  use).
- `docker run <image> <flags>` (no explicit `stdio`) drops you into the root
  command's help text instead of starting the server — the image's default
  CMD is `stdio` with *no* args, and any arguments after the image name
  **replace** that default rather than extend it. You must pass `stdio`
  explicitly whenever you also pass flags.

`.mcp.github-only.json.template` bakes in `stdio --read-only=${GITHUB_READ_ONLY_FLAG} --toolsets=repos,pull_requests` accordingly. Toolset is trimmed to
`repos,pull_requests` only — the minimum needed for the Phase 1 smoke test
(no `issues`, `actions`, etc. until a real task needs them).

## Required environment variables

```
GITHUB_PERSONAL_ACCESS_TOKEN=<fine-grained token, see scopes below>
GITHUB_READ_ONLY_FLAG=--read-only=true    # stage 1
GITHUB_READ_ONLY_FLAG=--read-only=false   # stage 2, write-scoped token only
```

`GITHUB_READ_ONLY_FLAG` has no default — `EnvSecretsProvider` raises if it's
unset, by design: every run requires a conscious choice, never a silent
default to write mode.

## Stage 1 — read-only validation (do this first)

**Token scope:** fine-grained PAT, repository access limited to one
throwaway repo, permissions:
- Contents: **Read-only**
- Metadata: **Read-only** (mandatory on every fine-grained token)

No `Pull requests` permission needed yet — this stage doesn't touch PRs.

**Run:**
```
export GITHUB_PERSONAL_ACCESS_TOKEN=<token>
export GITHUB_READ_ONLY_FLAG=--read-only=true
.venv/Scripts/python scripts/smoke_test.py read "read the contents of README.md in <owner>/<scratch-repo> and summarize it"
```

This proves: token auth works, the MCP transport works (Docker spawns
correctly, stdio handshake succeeds), and `orchestrator.run_task()` produces
a sane `ApprovalRequest` from a real Claude Code run. If this fails, the
failure is isolated to exactly these things — no write scope, no AWS, no
orchestration-vs-transport ambiguity.

## Stage 2 — write + PR validation (only after stage 1 passes)

**Token scope:** same fine-grained PAT (edit its permissions, or use a second
one), same throwaway repo, permissions:
- Contents: **Read and write**
- Metadata: **Read-only**
- Pull requests: **Read and write**

**Run:**
```
export GITHUB_READ_ONLY_FLAG=--read-only=false
.venv/Scripts/python scripts/smoke_test.py write "in <owner>/<scratch-repo>, read README.md, append a line with today's date, and open a PR against main with that one-line change"
```

This goes through `WRITE_SYSTEM_PROMPT` in `scripts/smoke_test.py`, **not**
`orchestrator.run_task()` — it was a deliberate, human-run exception to prove
the write path works, run once before the real apply step existed. A bad
run here is one open PR on a throwaway repo, closeable by hand — not debris
in anything that matters.

## Apply (v1) — the shipped path, now that write access is validated

`scripts/apply.py` executes an already-approved `ApprovalRequest` (see
`docs/ARCHITECTURE.md` "Apply step — scope (v1)"). Same write-scoped token
requirements as Stage 2 above (`GITHUB_PERSONAL_ACCESS_TOKEN`,
`GITHUB_READ_ONLY_FLAG=--read-only=false`), same `GITHUB_WRITE_TOOLS`
allowlist — this is the productionized version of the Stage 2 smoke test,
not a separate mechanism. Requires a prior, separate approval decision
(`ApprovalStore.decide(status="approved")`) and its own typed confirmation
before it runs — approving something and applying it are never the same
action.

## Cleanup after validation

Close/delete the test PR and branch, and consider revoking or re-scoping the
token back to read-only once you're done validating.
