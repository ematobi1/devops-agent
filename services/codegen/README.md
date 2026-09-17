# codegen

Generates IaC/config artifacts via Claude Code, lints and policy-checks them
deterministically before anything is proposed for approval. v1 scope:
Dockerfiles only, chosen by the operator when asked what the concrete need
was (see `docs/ARCHITECTURE.md` "services/codegen — scope").

## Modules

- `base_images.py`:
  - `AppImagePolicy` — `image`, `cmd`, `healthcheck`, `port`, `workdir`,
    `user`, `needs_install_step` as one operator-configured unit. Started
    as just the base image; a follow-up sample showed `CMD`/`HEALTHCHECK`
    regressing too (1/4 correct `CMD`, 1/4 correct `HEALTHCHECK`, no run
    with both) even with the base image fixed — they're properties of the
    base image and how it's meant to run, not of the repo, so they moved
    into policy alongside it. See the module docstring for the full
    history (two samples, two fixes, plus the CLI-shaped-app fixes below).
  - `healthcheck`/`port` are optional — a CLI-shaped app type has no
    listening port to poll, so forcing an HTTP `HEALTHCHECK` would template
    a meaningless instruction. `workdir`/`user` are new for the same
    reason: a generic base image (unlike `nginx-unprivileged`) has no
    non-root user and no fixed install directory built in — found live via
    an actual `docker build` + `docker run id` (not just `--check`, which
    has no opinion on either): a plausible `COPY --chown=app:app` silently
    no-op'd (no such user existed yet) and the container ran as root, and
    a model-written relative `RUN pip install -e ./contracts` would have
    resolved against Docker's default `/` cwd, not wherever `COPY` put the
    files. `AppImagePolicy.__post_init__` now requires `workdir` whenever
    `needs_install_step` is set, so this can't silently regress.
  - `needs_install_step` — static-site has zero build/install step (nginx
    serves copied files as-is); a CLI-shaped app type installs from its
    own package manifests, which — like `COPY` — is genuine per-repo
    variance (which manifests exist, in what order), not policy.
  - `BASE_IMAGES` — operator-set, per-`app_type` policy table. Currently
    `static-site` and `devops-agent-cli` (scoped to this repo's own CLI
    shape, not a generic "any Python CLI app" claim — see the table's
    comment for why reusing it elsewhere would be wrong).
    `resolve_app_image_policy(app_type)` / `UnknownAppTypeError`.

- `dockerfile.py`:
  - `build_generation_prompt(policy)` — the model's job is `COPY`
    (always) and, when `policy.needs_install_step`, also the `RUN` install
    instruction(s) (two labeled fenced blocks). Explicitly forbids
    producing `FROM`/`CMD`/`WORKDIR`/`HEALTHCHECK`/`EXPOSE` — those are
    templated, not generated. The install branch is told the working
    directory is already fixed (`policy.workdir`) and to write install
    paths relative to it, and never to fetch anything from a URL or pipe a
    remote command into a shell.
  - `extract_copy_instructions(result_text)` / `extract_install_instructions(result_text)`
    — pull the labeled `\`\`\`dockerfile-copy` / `\`\`\`dockerfile-run`
    blocks out. Return `None` if absent *or* if present but contains no
    line that looks like `COPY`/`RUN` — following the fence label without
    producing anything usable is still a failure to follow instructions.
  - `render_dockerfile(policy, copy_instructions, install_instructions=None)`
    — deterministic templating: `FROM`, `WORKDIR` (if set), `COPY`, `RUN`
    install (if given), `RUN useradd`+`EXPOSE`+`HEALTHCHECK`+`USER` (each
    only if the policy sets it — user creation/switch is templated
    *after* the install step, since installing needs root, and *before*
    `CMD`), `CMD`. Same inputs always produce the same Dockerfile text; no
    LLM call.
  - `check_dockerfile_lint_output(stdout, returncode)` / `lint_dockerfile(content)`
    — `docker build --check` parsing and I/O. Validates syntax and
    BuildKit's own best-practice rules — **not** architectural or security
    decisions, and does **not** execute anything, so it can't catch a
    silently no-op'd `--chown` or a `RUN` path that doesn't exist at build
    time — both found only by an actual `docker build` + `docker run`, not
    `--check`. Don't treat a clean lint result as a safety guarantee.
  - `check_dockerfile_policy(content, *, allowed_base_images, expect_healthcheck=True)`
    — deterministic checks the lint has no opinion on: base image in the
    configured allowlist, tag fully pinned to `MAJOR.MINOR.PATCH`, explicit
    `CMD` present, `HEALTHCHECK` present with `--retries` and
    `--start-period` (only when `expect_healthcheck` — a CLI-shaped app
    type has none by policy, so its absence isn't a finding), a `RUN`
    piping `curl`/`wget` into a shell flagged, `sed -i` targeting `/etc/`
    flagged, and `COPY` enumerating individual files rather than a whole
    directory flagged (found live: a Dockerfile copying
    `index.html`/`styles.css`/`script.js` explicitly silently omitted
    `images/`, referenced by `index.html` — lint and every other policy
    invariant passed clean; a heuristic backstop, not proof of
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
deterministically.

CLI-shaped app type (`devops-agent-cli`, targeting this repo itself — a
5-package monorepo with no root requirements file, the first target that
isn't a single-directory static site): required a new `RUN`
install-instruction generation path (real per-repo variance, same as
`COPY`) and exposed two bugs that `docker build --check`/policy can't
catch because neither executes anything — found only via a real
`docker build` + `docker run id` against a manually-assembled but
realistic Dockerfile, *before* spending a live Claude Code run on it:
(1) a relative `RUN pip install -e ./contracts` resolved against Docker's
default `/` cwd rather than the `COPY` destination (fixed: `workdir` is
now policy, templated as `WORKDIR`, required whenever
`needs_install_step` is set — see `AppImagePolicy.__post_init__`); (2) the
generic `python:slim` base has no non-root user, so a model-plausible
`COPY --chown=app:app` silently no-op'd and the container ran as root
(fixed: `user` is policy too, templated as `RUN useradd` + `USER` after
the install step (needs root) and before `CMD`; the model is no longer
asked to produce a `--chown` for this app type at all). Both real
`docker build`s (before and after the fix) are reproducible via
`codegen`'s tests plus a manual `render_dockerfile()` + `docker build`
against this repo.

Not yet validated: an actual live Claude Code generation run against a
real GitHub-hosted target (needs a configured `GITHUB_PERSONAL_ACCESS_TOKEN`
— not yet supplied in this environment), and the full path through
`decide.py`/`apply.py` to an applied PR.
