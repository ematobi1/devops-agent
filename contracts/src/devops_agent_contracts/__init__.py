import json
from importlib import resources
from typing import Any

import jsonschema

_SCHEMA_NAMES = (
    "task_request",
    "tool_result",
    "approval_request",
    "change_record",
)


def _load_schema(name: str) -> dict[str, Any]:
    if name not in _SCHEMA_NAMES:
        raise ValueError(f"unknown schema: {name}")
    text = resources.files("devops_agent_contracts.schemas").joinpath(f"{name}.schema.json").read_text()
    return json.loads(text)


def validate(name: str, instance: dict[str, Any]) -> None:
    """Raise jsonschema.ValidationError if instance does not match the named contract."""
    jsonschema.validate(instance=instance, schema=_load_schema(name))
