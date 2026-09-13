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
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppImagePolicy:
    image: str
    cmd: str  # exec-form JSON array, as literal Dockerfile text, e.g. '["nginx", "-g", "daemon off;"]'
    healthcheck: str  # everything after "HEALTHCHECK " - may include a `\` line continuation
    port: int


BASE_IMAGES: dict[str, AppImagePolicy] = {
    "static-site": AppImagePolicy(
        image="nginxinc/nginx-unprivileged:1.27.4-alpine",
        cmd='["nginx", "-g", "daemon off;"]',
        healthcheck="--interval=30s --timeout=3s --start-period=5s --retries=3 \\\n    CMD wget -qO- http://127.0.0.1:8080/ || exit 1",
        port=8080,
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
