# codegen

Generates IaC/config artifacts via Claude Code, lints and policy-checks them
deterministically before anything is proposed for approval. v1 scope:
Dockerfiles only, chosen by the operator when asked what the concrete need
was (see `docs/ARCHITECTURE.md` "services/codegen — scope").

## Modules

- `base_images.py`:
  - `BASE_IMAGES` — operator-set, per-`app_type` policy table mapping to one
    specific, fully-pinned base image. The model does not choose or vary
    the base image between runs — see the module docstring for why: a
    sample of 6 independent generations against the same repo showed the
    model re-deriving the base image and privilege-drop strategy
    differently each time, including one architecturally unsafe choice
    that still passed lint clean.
  - `resolve_base_image(app_type)` / `UnknownAppTypeError`.

- `dockerfile.py`:
  - `build_dockerfile_system_prompt(base_image)` — instructs the model to
    investigate a repo via read-only tools, use *exactly* the given base
    image (non-negotiable), and end with the complete Dockerfile in a
    `\`\`\`dockerfile` fenced block, nothing invented.
  - `extract_dockerfile(result_text)` — pulls that labeled block out.
    Returns `None` if absent (deliberately doesn't fall back to any
    unlabeled fenced block, to avoid grabbing an unrelated snippet).
  - `check_dockerfile_lint_output(stdout, returncode)` / `lint_dockerfile(content)`
    — `docker build --check` parsing and I/O. Validates syntax and
    BuildKit's own best-practice rules — **not** architectural or security
    decisions. Passed all 6 sampled runs, including the unsafe one; don't
    treat a clean lint result as a safety guarantee.
  - `check_dockerfile_policy(content, *, allowed_base_images)` — deterministic
    checks the lint has no opinion on, derived directly from the sample:
    base image in the configured allowlist, tag fully pinned to
    `MAJOR.MINOR.PATCH`, explicit `CMD` present, `HEALTHCHECK` present with
    `--retries` and `--start-period`, and `sed -i` targeting `/etc/`
    flagged (catches the exact silently-no-op rootless-conversion pattern
    found in the sample). All annotate rather than block.

## Why `docker build --check`, not `hadolint`

Docker was already installed and validated on this project's dev machine
for `github-mcp-server`. `--check` (BuildKit) gives real syntax and
best-practice validation without adding a new external dependency —
"search before building" pointed at the tool already in place, not the
more commonly-cited one.

## Why the policy checks live here, not in `services/verifier`

A judgment call, flagged explicitly in case a literal `services/verifier`
placement was actually wanted: these checks need Dockerfile-domain
semantics (parsing `FROM`/`HEALTHCHECK`/`CMD` structure), which doesn't fit
a package scoped to generic `ApprovalRequest` proposal prose. The
architecture already draws this line — `verifier` checks the shape of a
proposal, `codegen` checks the artifact it generated, the same way
`lint_dockerfile()` already lived here rather than in `verifier`.

## Wiring

`scripts/generate_dockerfile.py` does **not** use `orchestrator.run_task()`
— that function's action-extraction is built for numbered diagnostic lists
("1. ... 2. ..."), not a single multi-line file's content. It calls the
Claude Code runner directly (same pattern as `smoke_test.py`'s write mode)
and builds the `ApprovalRequest` itself as `{instruction, payload}` (payload
carries the raw generated file content and is never prose-scanned — see
`docs/ARCHITECTURE.md`'s verifier false-positive writeup), runs both
`verifier.check_proposed_actions()` and `check_dockerfile_policy()`, and
attaches any findings as `verifier_warnings` rather than blocking. From
there it's the same `decide.py`/`apply.py` pipeline as any other proposal;
`apply_approved()` needed no changes.

## Validated

First live run (scratch repo, no app code) found no bugs: the model
correctly refused to fabricate a Dockerfile rather than inventing
specifics. First happy-path attempt (a real static-site repo) found a
verifier false positive (fixed) and, on a 6-run sample, an architectural
base-image/privilege-model inconsistency including one unsafe run that
passed lint clean (fixed via base-image policy + new checks above). Not
yet validated: a fresh sample against the fixed pipeline, and the full
path through to an applied PR.
