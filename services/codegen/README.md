# codegen

Generates IaC/config artifacts via Claude Code, lints and policy-checks them
deterministically before anything is proposed for approval. v1 scope:
Dockerfiles only, chosen by the operator when asked what the concrete need
was (see `docs/ARCHITECTURE.md` "services/codegen — scope").

## Modules

- `base_images.py`:
  - `AppImagePolicy` — `image`, `cmd`, `healthcheck`, `port` as one
    operator-configured unit. Started as just the base image; a follow-up
    sample showed `CMD`/`HEALTHCHECK` regressing too (1/4 correct `CMD`,
    1/4 correct `HEALTHCHECK`, no run with both) even with the base image
    fixed — they're properties of the base image and how it's meant to
    run, not of the repo, so they moved into policy alongside it. See the
    module docstring for the full history (two samples, two fixes).
  - `BASE_IMAGES` — operator-set, per-`app_type` policy table.
    `resolve_app_image_policy(app_type)` / `UnknownAppTypeError`.

- `dockerfile.py`:
  - `build_copy_instructions_prompt(policy)` — a narrow prompt: the model's
    only job is determining which files to `COPY`, where, and the
    `--chown` form. Explicitly forbids producing `FROM`/`CMD`/`HEALTHCHECK`/
    `EXPOSE` — those are templated, not generated.
  - `extract_copy_instructions(result_text)` — pulls the labeled
    `\`\`\`dockerfile-copy` block out. Returns `None` if absent *or* if
    present but contains no line that looks like `COPY` — following the
    fence label without producing anything usable is still a failure to
    follow instructions.
  - `render_dockerfile(policy, copy_instructions)` — deterministic
    templating. Same policy and copy instructions always produce the same
    Dockerfile text; no LLM call.
  - `check_dockerfile_lint_output(stdout, returncode)` / `lint_dockerfile(content)`
    — `docker build --check` parsing and I/O. Validates syntax and
    BuildKit's own best-practice rules — **not** architectural or security
    decisions. Passed all 6 sampled runs from the first investigation,
    including the unsafe one; don't treat a clean lint result as a safety
    guarantee.
  - `check_dockerfile_policy(content, *, allowed_base_images)` — deterministic
    checks the lint has no opinion on: base image in the configured
    allowlist, tag fully pinned to `MAJOR.MINOR.PATCH`, explicit `CMD`
    present, `HEALTHCHECK` present with `--retries` and `--start-period`,
    `sed -i` targeting `/etc/` flagged, and `COPY` enumerating individual
    files rather than a whole directory flagged (found live: a Dockerfile
    copying `index.html`/`styles.css`/`script.js` explicitly silently
    omitted `images/`, referenced by `index.html` — lint and every other
    policy invariant passed clean; a heuristic backstop, not proof of
    completeness — see `docs/ARCHITECTURE.md`'s note on why real
    completeness verification is a fundamentally different, unbuilt class
    of check). Runs against the *final assembled* Dockerfile even now that
    `FROM`/`CMD`/`HEALTHCHECK` are templated — it now asserts the
    templating worked, not that the model remembered. All annotate rather
    than block.

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
for the `COPY`-instructions-only prompt, assembles the full Dockerfile via
`render_dockerfile()`, then builds the `ApprovalRequest` as
`{instruction, payload}` (payload carries the raw generated file content
and is never prose-scanned — see `docs/ARCHITECTURE.md`'s verifier
false-positive writeup), runs both `verifier.check_proposed_actions()` and
`check_dockerfile_policy()`, and attaches any findings as
`verifier_warnings` rather than blocking. From there it's the same
`decide.py`/`apply.py` pipeline as any other proposal; `apply_approved()`
needed no changes.

## Validated

First live run (scratch repo, no app code): no bugs. First happy-path
attempt (real static-site repo): a verifier false positive (fixed), then a
6-run sample surfaced an architectural base-image/privilege-model
inconsistency including one unsafe run that passed lint clean (fixed via
base-image policy). A follow-up 4-run sample — base image now fixed —
showed `CMD`/`HEALTHCHECK` regressing anyway, fixed by moving them into
policy too and templating the whole non-`COPY` portion of the Dockerfile
deterministically. Not yet validated: a fresh sample against the
fully-templated pipeline (expectation is zero warnings), and the full path
through to an applied PR.
