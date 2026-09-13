# codegen

Generates IaC/config artifacts via Claude Code, lints them deterministically
before anything is proposed for approval. v1 scope: Dockerfiles only,
chosen by the operator when asked what the concrete need was (see
`docs/ARCHITECTURE.md` "services/codegen — scope").

## Modules

- `dockerfile.py`:
  - `DOCKERFILE_SYSTEM_PROMPT` — instructs the model to investigate a repo
    via read-only tools, follow Docker best practices (pinned base image,
    non-root user, multi-stage where it helps), and end with the complete
    Dockerfile in a `\`\`\`dockerfile` fenced block, nothing invented.
  - `extract_dockerfile(result_text)` — pulls that labeled block out.
    Returns `None` if absent (deliberately doesn't fall back to any
    unlabeled fenced block, to avoid grabbing an unrelated snippet).
  - `check_dockerfile_lint_output(stdout, returncode)` — pure parser for
    `docker build --check` output. Tested against three real captured
    cases: clean, lint warnings, and a genuine parse error (a different
    output shape than a warning).
  - `lint_dockerfile(content)` — the actual I/O: writes content to a temp
    Dockerfile and runs `docker build --check` against it. Requires Docker
    Desktop running (same prerequisite already established for
    `github-mcp-server`).

## Why `docker build --check`, not `hadolint`

Docker was already installed and validated on this project's dev machine
for `github-mcp-server`. `--check` (BuildKit) gives real syntax and
best-practice validation without adding a new external dependency —
"search before building" pointed at the tool already in place, not the
more commonly-cited one.

## Wiring

`scripts/generate_dockerfile.py` does **not** use `orchestrator.run_task()`
— that function's action-extraction is built for numbered diagnostic lists
("1. ... 2. ..."), not a single multi-line file's content. It calls the
Claude Code runner directly (same pattern as `smoke_test.py`'s write mode)
and builds the `ApprovalRequest` itself, then runs it through
`verifier.check_proposed_actions()` before persisting — same fail-closed
pattern as `diagnose.py`. From there it's the same `decide.py`/`apply.py`
pipeline as any other proposal; `apply_approved()` needed no changes.

## Validated

First live run (scratch repo, no app code) found no bugs: the model
correctly refused to fabricate a Dockerfile rather than inventing specifics,
and the script correctly treated the missing fenced block as a controlled
failure. The happy path (real app repo, all the way to an applied PR) is
not yet validated — needs a real target with an actual entry point.
