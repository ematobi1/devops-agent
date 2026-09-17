"""Per-app-type image AND runtime policy. The operator sets this ONCE; the
model does not choose or vary it between runs.

First fix (base image as config, 2026-09-13) removed base-image/privilege-
model variance. A follow-up 4-run sample showed the *next* layer of the
same problem: CMD and HEALTHCHECK still regressed even with the base image
fixed - 1/4 runs had a correct CMD, 1/4 had a correct HEALTHCHECK, no run
had both, though the verifier correctly caught and annotated every case.

CMD and HEALTHCHECK are properties of the base image and how it's meant to
run, not something that varies per repo - same reasoning as the base image
fix, one level further: template them deterministically instead of asking
the model to remember them. The model's job narrows to what actually
varies per repo: which files to COPY, where, and the --chown form.

`healthcheck`/`port` are optional (2026-09-17): both assume a long-running
network service with an HTTP endpoint to poll, which is true for
static-site but not for a CLI-shaped app type with no listening port -
forcing them there would template a meaningless HTTP HEALTHCHECK against a
process that never opens a socket. `None` means "not applicable to this
app type", not "the operator forgot" - render_dockerfile() omits the
corresponding line entirely rather than emitting a broken one.

`needs_install_step` (2026-09-17): static-site has zero build/install step
- nginx serves the copied files as-is. A CLI-shaped app type installs from
its own package manifests, which - like COPY - is genuine per-repo
variance (which manifests exist, in what order), not policy. When set,
the model is also asked to determine the RUN install instruction(s); see
dockerfile.py's build_generation_prompt().

`workdir` (2026-09-17): required whenever needs_install_step is True (see
__post_init__). The model is told to COPY the repo to this exact path and
write install paths relative to it; render_dockerfile() then emits a
matching `WORKDIR` instruction so RUN executes with that as its cwd -
without it, a model-written relative install path (e.g. `./contracts`)
would resolve against Docker's default `/` cwd, not where COPY actually
put the files, and silently fail at real `docker build` time (a
`docker build --check` lint pass would NOT catch this - it validates
syntax, not that referenced paths exist). Caught by manually rendering a
realistic devops-agent-cli Dockerfile before the first live run, not by
any automated check - see codegen/tests for the regression this guards.

`user` (2026-09-17): static-site's base image (nginxinc/nginx-unprivileged)
already ships and runs as a non-root user - that's WHY it was chosen (see
fix 1 above). A generic base image like python:slim has no such user, so
without this field the container runs as root with no warning - found
live via an actual `docker build` + `docker run id` (not just
`--check`, which has no opinion on this): a plausible-looking
`COPY --chown=app:app` silently no-op'd (`app` doesn't exist in the base
image) and the process ran as uid 0. When set, render_dockerfile()
templates user creation and a `USER` switch AFTER the install step (which
needs root to write into the image's system Python) and BEFORE CMD -
COPY itself stays unprivileged (root-owned, world-readable source needs
no chown to be read by another user) so the model is never asked to name
a --chown target that may not exist yet.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppImagePolicy:
    image: str
    cmd: str  # exec-form JSON array, as literal Dockerfile text, e.g. '["nginx", "-g", "daemon off;"]'
    healthcheck: str | None = None  # everything after "HEALTHCHECK " - may include a `\` line continuation; None omits the instruction
    port: int | None = None  # None omits the EXPOSE instruction
    needs_install_step: bool = False  # True asks the model for a RUN install instruction alongside COPY
    workdir: str | None = None  # None omits WORKDIR; required (see __post_init__) when needs_install_step is True
    user: str | None = None  # None omits user creation/USER (base image already runs non-root); otherwise templated, never model-chosen

    def __post_init__(self) -> None:
        if self.needs_install_step and self.workdir is None:
            raise ValueError(
                "needs_install_step=True requires workdir to be set - otherwise a model-written relative "
                "RUN install path resolves against Docker's default `/` cwd, not the COPY destination, and "
                "silently fails at real `docker build` time (docker build --check won't catch it)"
            )


BASE_IMAGES: dict[str, AppImagePolicy] = {
    "static-site": AppImagePolicy(
        image="nginxinc/nginx-unprivileged:1.27.4-alpine",
        cmd='["nginx", "-g", "daemon off;"]',
        healthcheck="--interval=30s --timeout=3s --start-period=5s --retries=3 \\\n    CMD wget -qO- http://127.0.0.1:8080/ || exit 1",
        port=8080,
    ),
    # Scoped to this repo's own CLI shape, not a generic "any Python CLI
    # app" claim - unlike static-site's CMD (the base image's own runtime,
    # true for any nginx-served site), a CLI app's entrypoint script is
    # inherently repo-specific. Reusing this entry for a different
    # CLI-shaped repo would silently run the wrong script.
    "devops-agent-cli": AppImagePolicy(
        image="python:3.13.15-slim",
        cmd='["python", "scripts/diagnose.py"]',
        healthcheck=None,
        port=None,
        needs_install_step=True,
        workdir="/app",
        user="app",
    ),
}


class UnknownAppTypeError(KeyError):
    pass


def resolve_app_image_policy(app_type: str) -> AppImagePolicy:
    try:
        return BASE_IMAGES[app_type]
    except KeyError:
        raise UnknownAppTypeError(
            f"no configured policy for app_type={app_type!r} - "
            f"add one to BASE_IMAGES rather than letting the model choose. "
            f"Configured types: {sorted(BASE_IMAGES)}"
        ) from None
