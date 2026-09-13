import pytest

from codegen import BASE_IMAGES, UnknownAppTypeError, resolve_base_image


def test_resolve_known_app_type():
    assert resolve_base_image("static-site") == BASE_IMAGES["static-site"]


def test_resolve_unknown_app_type_raises_with_configured_types_listed():
    with pytest.raises(UnknownAppTypeError, match="static-site"):
        resolve_base_image("rust-api")


def test_configured_base_images_are_fully_pinned():
    """The policy table itself must not regress into the same
    unpinned-tag problem the checks exist to catch."""
    import re

    for app_type, image in BASE_IMAGES.items():
        assert re.search(r":\d+\.\d+\.\d+", image), f"{app_type}={image!r} is not pinned to MAJOR.MINOR.PATCH"
