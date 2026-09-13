from orchestrator.aws_tools import CLOUDWATCH_READ_TOOLS

WRITE_SHAPED_VERBS = ("create_", "update_", "delete_", "put_", "set_", "modify_", "terminate_", "stop_", "start_")


def test_all_tools_carry_mcp_prefix():
    for tool in CLOUDWATCH_READ_TOOLS:
        assert tool.startswith("mcp__aws-cloudwatch__")


def test_no_tool_name_looks_write_shaped():
    for tool in CLOUDWATCH_READ_TOOLS:
        bare_name = tool.removeprefix("mcp__aws-cloudwatch__")
        assert not bare_name.startswith(WRITE_SHAPED_VERBS), f"{tool} looks write-shaped"


def test_expected_tool_count():
    # Enumerated live from a real tools/list call - see aws_tools.py docstring.
    # A count drift means the server version changed; re-verify before bumping.
    assert len(CLOUDWATCH_READ_TOOLS) == 19


def test_no_duplicate_tools():
    assert len(CLOUDWATCH_READ_TOOLS) == len(set(CLOUDWATCH_READ_TOOLS))
