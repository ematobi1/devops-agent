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
    for app_type, policy in BASE_IMAGES.items():
        assert "--retries" in policy.healthcheck, f"{app_type} healthcheck missing --retries"
        assert "--start-period" in policy.healthcheck, f"{app_type} healthcheck missing --start-period"


def test_configured_cmd_is_exec_form_json_array():
    import json

    for app_type, policy in BASE_IMAGES.items():
        parsed = json.loads(policy.cmd)
        assert isinstance(parsed, list), f"{app_type} cmd is not a JSON array: {policy.cmd!r}"
