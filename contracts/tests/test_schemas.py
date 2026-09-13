import datetime

import jsonschema
import pytest

from devops_agent_contracts import validate

VALID_TASK_REQUEST = {
    "id": "task-1",
    "description": "diagnose 500s on checkout service",
    "requester": "emma",
    "priority": "high",
    "created_at": "2026-09-13T00:00:00Z",
}

VALID_TOOL_RESULT = {
    "server": "github",
    "tool": "list_pull_requests",
    "input": {"owner": "acme", "repo": "checkout"},
    "is_error": False,
    "timestamp": "2026-09-13T00:00:00Z",
}

VALID_APPROVAL_REQUEST = {
    "id": "appr-1",
    "task_id": "task-1",
    "summary": "roll back checkout deploy to previous version",
    "proposed_actions": ["revert commit abc123 on main"],
    "status": "pending",
    "created_at": "2026-09-13T00:00:00Z",
}

VALID_CHANGE_RECORD = {
    "task_id": "task-1",
    "approval_id": "appr-1",
    "outcome": "applied",
    "recorded_at": "2026-09-13T00:00:00Z",
}


@pytest.mark.parametrize(
    "name,instance",
    [
        ("task_request", VALID_TASK_REQUEST),
        ("tool_result", VALID_TOOL_RESULT),
        ("approval_request", VALID_APPROVAL_REQUEST),
        ("change_record", VALID_CHANGE_RECORD),
    ],
)
def test_valid_instances_pass(name, instance):
    validate(name, instance)


@pytest.mark.parametrize(
    "name,instance,broken_field",
    [
        ("task_request", VALID_TASK_REQUEST, "priority"),
        ("tool_result", VALID_TOOL_RESULT, "server"),
        ("approval_request", VALID_APPROVAL_REQUEST, "status"),
        ("change_record", VALID_CHANGE_RECORD, "outcome"),
    ],
)
def test_missing_required_field_fails(name, instance, broken_field):
    broken = dict(instance)
    del broken[broken_field]
    with pytest.raises(jsonschema.ValidationError):
        validate(name, broken)


def test_task_request_rejects_unknown_priority():
    broken = dict(VALID_TASK_REQUEST, priority="whenever")
    with pytest.raises(jsonschema.ValidationError):
        validate("task_request", broken)


def test_approval_request_rejects_unknown_status():
    broken = dict(VALID_APPROVAL_REQUEST, status="maybe")
    with pytest.raises(jsonschema.ValidationError):
        validate("approval_request", broken)


def test_unknown_schema_name_raises_value_error():
    with pytest.raises(ValueError):
        validate("not_a_schema", {})
