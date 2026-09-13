import json

import pytest

from orchestrator.claude_runner import ClaudeRunError, build_result, parse_stream_json

INIT_EVENT = {"type": "system", "subtype": "init", "session_id": "s1"}

ASSISTANT_TOOL_USE_EVENT = {
    "type": "assistant",
    "message": {
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "mcp__github__list_pull_requests", "input": {"repo": "checkout"}}
        ]
    },
}

BUILTIN_TOOL_USE_EVENT = {
    "type": "assistant",
    "message": {"content": [{"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {"file_path": "x"}}]},
}

TOOL_RESULT_EVENT = {
    "type": "user",
    "message": {
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "[]", "is_error": False}]
    },
}

RESULT_EVENT = {
    "type": "result",
    "result": "1. roll back deploy",
    "is_error": False,
    "session_id": "s1",
    "total_cost_usd": 0.05,
    "num_turns": 2,
    "terminal_reason": "completed",
}


def _stdout(*events):
    return "\n".join(json.dumps(e) for e in events)


def test_parse_stream_json_skips_blank_lines():
    events = parse_stream_json(_stdout(INIT_EVENT) + "\n\n" + _stdout(RESULT_EVENT))
    assert events == [INIT_EVENT, RESULT_EVENT]


def test_build_result_extracts_mcp_tool_calls_with_output():
    events = [INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, TOOL_RESULT_EVENT, RESULT_EVENT]
    result = build_result(events)

    assert result.result_text == "1. roll back deploy"
    assert result.is_error is False
    assert result.session_id == "s1"
    assert result.total_cost_usd == 0.05
    assert result.terminal_reason == "completed"

    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.server == "github"
    assert call.tool == "list_pull_requests"
    assert call.output == "[]"
    assert call.is_error is False


def test_build_result_labels_builtin_tools_as_claude_code():
    events = [INIT_EVENT, BUILTIN_TOOL_USE_EVENT, RESULT_EVENT]
    result = build_result(events)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].server == "claude-code"
    assert result.tool_calls[0].tool == "Read"


def test_build_result_without_terminal_event_raises():
    with pytest.raises(ClaudeRunError):
        build_result([INIT_EVENT])


def test_tool_call_without_matching_result_stays_unresolved():
    events = [INIT_EVENT, ASSISTANT_TOOL_USE_EVENT, RESULT_EVENT]
    result = build_result(events)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].output is None
    assert result.tool_calls[0].is_error is False
