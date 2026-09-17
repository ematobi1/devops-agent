import re

import pytest

from codegen import BASE_IMAGES, UnknownAppTypeError, resolve_app_image_policy


def test_resolve_known_app_type():
    assert resolve_app_image_policy("static-site") == BASE_IMAGES["static-site"]


def test_resolve_unknown_app_type_raises_with_configured_types_listed():
    with pytest.raises(UnknownAppTypeError, match="static-site"):
        resolve_app_image_policy("rust-api")


def test_configured_images_are_fully_pinned():
    """The policy table itself must not regress into the same
    unpinned-tag problem the checks exist to catch."""
    for app_type, policy in BASE_IMAGES.items():
        assert re.search(r":\d+\.\d+\.\d+", policy.image), f"{app_type}={policy.image!r} is not pinned to MAJOR.MINOR.PATCH"


def test_configured_healthcheck_has_retries_and_start_period():
    """A policy that omits HEALTHCHECK entirely (None - e.g. a CLI-shaped
    app type with no listening port) is not a regression to check; only
    policies that configure one must configure it correctly."""
    for app_type, policy in BASE_IMAGES.items():
        if policy.healthcheck is None:
            continue
        assert "--retries" in policy.healthcheck, f"{app_type} healthcheck missing --retries"
        assert "--start-period" in policy.healthcheck, f"{app_type} healthcheck missing --start-period"


def test_configured_cmd_is_exec_form_json_array():
    import json

    for app_type, policy in BASE_IMAGES.items():
        parsed = json.loads(policy.cmd)
        assert isinstance(parsed, list), f"{app_type} cmd is not a JSON array: {policy.cmd!r}"


def test_cli_shaped_app_type_omits_port_and_healthcheck():
    policy = BASE_IMAGES["devops-agent-cli"]
    assert policy.port is None
    assert policy.healthcheck is None
    assert policy.needs_install_step is True


def test_static_site_app_type_is_service_shaped_not_cli():
    policy = BASE_IMAGES["static-site"]
    assert policy.port is not None
    assert policy.healthcheck is not None
    assert policy.needs_install_step is False


def test_needs_install_step_without_workdir_is_rejected():
    """Regression: without a fixed WORKDIR, a model-written relative RUN
    install path would resolve against Docker's default `/` cwd, not
    wherever COPY actually put the repository - a silent failure at real
    `docker build` time that `docker build --check` (syntax only) would
    never catch. Caught by manual sanity-check before the first live run,
    not by any prior automated check - see AppImagePolicy.__post_init__."""
    from codegen import AppImagePolicy

    with pytest.raises(ValueError, match="workdir"):
        AppImagePolicy(image="python:3.13.15-slim", cmd='["python", "x.py"]', needs_install_step=True)
