"""Per-app-type base image policy. The operator sets this ONCE; the model
does not choose or vary the base image between runs.

Found live: sampled 6 independent Dockerfile generations against the same
static-site repo. 3 chose `nginxinc/nginx-unprivileged` (runs as non-root
out of the box); 3 chose plain `nginx` and hand-rolled a rootless
conversion via `sed` against the image's own /etc/nginx/nginx.conf. One of
those three (run 2) used `sed -i '/user\\s*nginx;/d' /etc/nginx/nginx.conf`
- a command that silently no-ops if the pattern doesn't match, leaving the
container running as root with no error, no failed build, and no lint
warning (`docker build --check` passed all 6 runs, including that one - it
validates syntax, not architectural or security decisions; don't treat it
as a safety oracle). The variance was architectural, not stylistic, and
letting the model re-derive the base image and privilege-drop strategy
every run is what produced it. Base image selection is a security decision
made once by the operator, not something to re-decide per generation.
"""

BASE_IMAGES: dict[str, str] = {
    "static-site": "nginxinc/nginx-unprivileged:1.27.4-alpine",
}


class UnknownAppTypeError(KeyError):
    pass


def resolve_base_image(app_type: str) -> str:
    try:
        return BASE_IMAGES[app_type]
    except KeyError:
        raise UnknownAppTypeError(
            f"no configured base image for app_type={app_type!r} - "
            f"add one to BASE_IMAGES rather than letting the model choose. "
            f"Configured types: {sorted(BASE_IMAGES)}"
        ) from None
