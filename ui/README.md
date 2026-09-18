# Dockerfile codegen web UI

A thin local web UI over the existing pipeline
(`scripts/generate_dockerfile.py` &rarr; `scripts/decide.py` &rarr;
`scripts/apply.py`). It invokes those scripts exactly as documented in
their own module docstrings and parses their already-deterministic stdout
into stage results - it does not reimplement any generation, lint, or
policy logic. See `pipeline.py`'s module docstring for exactly how.

## Run it

```
.venv/Scripts/python -m pip install -r ui/requirements.txt   # once
export GITHUB_PERSONAL_ACCESS_TOKEN=<your fine-grained PAT>
.venv/Scripts/python -m uvicorn ui.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Docker Desktop must be running (the generate
stage lints via `docker build --check`).

You only need to export `GITHUB_PERSONAL_ACCESS_TOKEN` once, before
starting the server - the server process inherits it, and every
subprocess call forces the correct `GITHUB_READ_ONLY_FLAG` itself
(`--read-only=true` for generate, `--read-only=false` for apply), so you
never set that flag yourself and it's never exposed through the browser.
The token needs `Contents: Read-only` at minimum to generate; add
`Contents: Read and write` + `Pull requests: Read and write` before
clicking Approve on a run that includes `apply` (same two-token discipline
`services/mcp-servers/github/README.md` documents for the CLI pipeline -
one token with both scopes works fine, as validated live in this repo's
own PR #1).

## Run it in a container

`ui/Dockerfile` builds a real, correctly-shaped image for this app - a
Python/FastAPI backend serving both `/static` and `/api`, not a
static-file-only image (nginx alone has no backend behind it and every
`/api/*` call would 404 - see the git history around 2026-09-17 if a
Dockerfile proposing exactly that shows up again). Build context is the
**repo root**, not `ui/` - the app needs the whole monorepo:

```
docker build -f ui/Dockerfile -t devops-agent-ui .
docker run --rm -p 8000:8000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e GITHUB_PERSONAL_ACCESS_TOKEN=<your PAT> \
  devops-agent-ui
```

The Docker socket mount is required, not optional: the generate stage
shells out to `docker build --check` (see `services/codegen/dockerfile.py`),
and that needs a real daemon. This container carries only the `docker`
CLI and the `buildx` plugin (no daemon of its own - Docker-outside-of-
Docker, talking to the *host's* daemon, not Docker-in-Docker) - **both
are required**: the bare CLI alone exits 125 with no usable output on
`--check`, since that flag is implemented by `buildx`, not the classic
builder the CLI falls back to without it. Found live building this image
the same way every other real bug in this project was found - by
actually running it, not just `docker build --check`ing the Dockerfile
itself.

Runs as root inside the container, deliberately - see the Dockerfile's
own comment for why a non-root `USER` here would add real,
host-GID-dependent brittleness for no actual security benefit once the
host's Docker socket is already mounted.

If `-v /var/run/docker.sock:...` gets silently rewritten to a Windows
path and the container fails to start, that's Git Bash's MSYS path
conversion, not Docker - prefix the command with `MSYS_NO_PATHCONV=1` (or
run from PowerShell/cmd instead, which doesn't do this).

### Claude Code auth in the container: bind-mount the host session

`claude` (the actual agent runtime every stage depends on) is installed
in the image - a real bug, found live the same way as the buildx one:
the first real pipeline run through the container failed with a plain
`FileNotFoundError: 'claude'`, because nothing had ever put the binary
there. Fixed by installing `@anthropic-ai/claude-code` via npm in a
throwaway Node build stage and copying out just the resulting binary
(verified via `ldd` to be self-contained - no Node.js needed at runtime,
only at install time) - see the Dockerfile's own comment.

The binary alone has no credentials. `docs/ARCHITECTURE.md` documents a
deliberate project-wide choice to use the operator's logged-in
*subscription session*, not `ANTHROPIC_API_KEY` (`--bare` mode, which
would force API-key auth, was explicitly rejected as "the wrong
tradeoff"). **Decision (2026-09-18): bind-mount the operator's own
session into the container**, rather than override that choice for the
container specifically:

```
docker run -d --name devops-agent-ui \
  -p 127.0.0.1:8000:8000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "<path-to-your-home>/.claude.json:/root/.claude.json" \
  -v "<path-to-your-home>/.claude:/root/.claude" \
  -e GITHUB_PERSONAL_ACCESS_TOKEN=<your PAT> \
  devops-agent-ui
```

(On Windows via Git Bash, prefix with `MSYS_NO_PATHCONV=1` for the socket
mount as above, but give the `.claude.json`/`.claude` sources as native
Windows-style paths with forward slashes, e.g.
`"C:/Users/<you>/.claude.json:/root/.claude.json"` - MSYS's path
mangling is what you *want* to avoid for the socket, but these two really
are real host filesystem paths and should resolve as such.)

**Why this over the alternative:** consistent with the Docker-socket
mount's already-established threat model (local, trusted,
single-operator - see "Security" above) and avoids adding a second,
divergent auth path this project didn't already have. **The real cost:**
`~/.claude.json` is a personal account credential, not a project secret -
mounting it gives the container everything your own `claude` CLI can do,
under your account, for as long as the mount exists. That's a materially
different thing to hand a container than a Docker socket (host-root vs.
account access), even though both fit the same "local and trusted"
framing - worth being deliberate about, not just consistent for
consistency's sake.

**Validated live end-to-end** (2026-09-18) with the session mounted:
`claude auth status` inside the container correctly showed `loggedIn:
true` under the real account; a full run through the container's own
HTTP API (generate → lint → verify → approve → apply against
`ematobi1/devops-agent`) completed with a real Claude Code call at each
stage - `apply` correctly declined to open a no-op PR (the file already
exists there identically from earlier in this project), the same correct
refusal already documented elsewhere in `docs/ARCHITECTURE.md`, not a new
bug. Two of the three `generate` attempts during this test produced an
empty `RUN` block and were correctly refused rather than proposed - known
LLM sampling variance (this project's history has recorded similar rates
elsewhere), not a container-specific defect; confirmed by manually
spawning the GitHub MCP server's sibling container from inside `ui`'s
container and watching it start and connect cleanly over stdio.

**Not pursued now, tracked for later:** using `ANTHROPIC_API_KEY` instead,
for deployment shapes where mounting a personal session doesn't fit (a
shared/remote deployment, CI, multiple operators) - filed as
[issue #2](https://github.com/ematobi1/devops-agent/issues/2) rather than
decided speculatively now.

## Deviations from the spec (read before assuming a UI field means what it looks like)

The build prompt for this UI assumed a shape for the underlying pipeline
that doesn't quite match what's actually in this repo. Per the prompt's
own instruction ("stop and print the actual invocation rather than
guessing"), here's every place this adapted to reality instead of
fabricating something that would look right and silently do the wrong
thing:

1. **"Repo path" is a GitHub `owner/repo` identifier, not a local
   filesystem path.** `generate_dockerfile.py`'s target argument drives a
   *remote* investigation over the GitHub MCP server - the pipeline never
   reads a local git checkout at all. The UI labels the field accordingly
   ("Target GitHub repo (owner/repo)"); the "fail fast if it isn't a git
   repo" requirement is implemented as an `owner/repo` shape check
   (`pipeline.validate_target_repo`) rather than a local-path/git check,
   since no such local check would ever be meaningful here. A
   well-formed-but-nonexistent or inaccessible repo still fails fast, just
   from a real GitHub API 404 surfaced verbatim in the generate stage's
   output (seen live: `"There is no repository named ... under the
   ematobi1 GitHub account"`), not a synthetic pre-check.
2. **`lint` and `verify` aren't separate scripts - they're two sections of
   `generate_dockerfile.py`'s own single Claude Code run and single
   process.** There is no `<linter command>` or `<verify command>` to
   invoke; docker-lint and policy-check both happen inside that one
   script, informed by the content it just generated. `pipeline.run_generate()`
   makes one subprocess call and splits its stdout into three UI-facing
   stage results by parsing the script's own printed section markers -
   it does not re-run or re-judge anything. The UI reflects this: the
   `lint`/`verify` checkboxes are locked on whenever `generate` is checked,
   since the real script has no way to produce one without the other two.
3. **`apply` is `scripts/apply.py`, not a root-level `apply.py`**, and
   its exact signature is positional (`<approvals.jsonl> <approval-id>
   <changes.jsonl>`), not `--repo`/`--app-type` flags - those flags don't
   exist on `generate_dockerfile.py` either (its signature is also
   positional: `<approvals.jsonl> <owner/repo> <app-type>`). No flags were
   invented; the real positional CLI is used throughout `pipeline.py`.
4. **The approve step is its own explicit action, not a checkbox that
   fires automatically.** Clicking "Run" only ever executes
   generate/lint/verify. If `approve` and/or `apply` were requested, the
   run stops at `awaiting_approval` and does *nothing* further until the
   operator clicks **Approve & continue** in the browser - only that
   click triggers `scripts/decide.py approve`, immediately followed by
   `scripts/apply.py` if `apply` was also requested. `apply.py` itself
   still prompts interactively (`Type 'apply' to proceed`); the backend
   feeds it `"apply\n"` on stdin only because the browser click already
   *is* the real human confirmation gate the project's own
   `docs/ARCHITECTURE.md` "Apply step" design calls for - it isn't a way
   to skip that gate, it's what lets the same script run non-interactively
   once the gate has already been passed.
5. **App type list doesn't include a `python-service` entry** - the
   actual CLI-shaped policy added to this repo (see `services/codegen/base_images.py`)
   is named `devops-agent-cli`, deliberately scoped to this repo rather
   than a generic "any Python service" claim (its `CMD` runs
   `scripts/diagnose.py`, which only exists here - see that file's own
   comment for why reusing it elsewhere would silently run the wrong
   script). `GET /api/app-types` reads `codegen.BASE_IMAGES` live, so
   whatever is actually configured shows up correctly regardless of name.
6. **Two endpoints beyond the four originally listed were necessary** and
   were added rather than shoehorned into the existing four, since none of
   them fit: `POST /api/runs/{run_id}/approve` (the explicit human gate
   from point 4 - there is no way to satisfy "must not auto-apply" without
   a distinct endpoint for it) and `POST /api/runs/{run_id}/retry` (the
   spec's own acceptance criteria require a working "Retry from failed
   stage" button, which needs somewhere to POST to).

## Repo layout

```
ui/
  app.py            FastAPI app: routes, in-memory run state, SSE
  pipeline.py        subprocess wrappers + stdout parsing - no pipeline logic
  static/
    index.html
    app.js
    style.css
  requirements.txt
```

Per-run scratch files (`approvals.jsonl`/`changes.jsonl`, one pair per
`run_id` so concurrent runs never collide) live under `ui/.runs/<run_id>/`,
git-ignored (the repo's existing `*.jsonl` ignore rule already covers
them).

## API

- `GET /` - the UI
- `GET /api/health` - `{"status": "ok"}`
- `GET /api/app-types` - live from `codegen.BASE_IMAGES`
- `POST /api/run` - `{repo_path, app_type, stages}` &rarr; `{run_id}`; starts generate/lint/verify in a background task
- `GET /api/stream/{run_id}` - SSE, one JSON event per stage transition, replays full history to a new/reconnecting subscriber, ends with `{"stage": "done", "status": "ok"|"failed", "pr_url": ...}`
- `GET /api/runs/{run_id}` - full run record (for the SSE-drop polling fallback, or just inspecting a past run)
- `POST /api/runs/{run_id}/approve` - `{decided_by}`; only valid while `status == "awaiting_approval"`; runs approve, then apply if requested
- `POST /api/runs/{run_id}/retry` - only valid while `status == "failed"`; re-runs from whichever stage actually failed

## Security

Binds to `127.0.0.1` only. No auth (local tool). Every piece of subprocess
stdout/stderr is passed through `pipeline.redact_secrets()` - which
matches `ghp_...`, `github_pat_...`, `AKIA...`, and `aws_secret...` shaped
strings and replaces them with `[REDACTED]` - before it's stored in a run
record or sent to the browser in any form (SSE event or `/api/runs/{id}`).
Tokens themselves are never accepted through the API or UI at all; the
server process only ever reads `GITHUB_PERSONAL_ACCESS_TOKEN` from its own
environment (see "Run it" above), so a token never crosses the network to
the browser in the first place.

## Known limits (by design, not oversights)

- **In-memory only.** `RUNS` is a plain dict in `app.py`; a server restart
  loses every run's history. Fine for a local tool with no database, per
  spec - just don't expect a run to survive `Ctrl+C`.
- **One `uvicorn` process, no auth.** Anyone who can reach `127.0.0.1:8000`
  on this machine can drive the real pipeline, including `apply` (a real
  PR) once a run reaches `awaiting_approval`. Never bind this to anything
  but `127.0.0.1`.
- **300s per-stage timeout**, matching `ClaudeRunConfig.timeout_seconds`'s
  existing default elsewhere in this project - not independently chosen.
