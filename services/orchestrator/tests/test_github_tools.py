from orchestrator.github_tools import GITHUB_READ_TOOLS, GITHUB_WRITE_TOOLS

WRITE_TOOL_NAMES = {
    "create_branch",
    "create_or_update_file",
    "create_pull_request",
    "merge_pull_request",
    "delete_file",
    "fork_repository",
    "create_repository",
    "push_files",
    "update_pull_request",
    "update_pull_request_branch",
    "pull_request_review_write",
    "add_comment_to_pending_review",
    "add_reply_to_pull_request_comment",
}

DANGEROUS_TOOLS = {
    "mcp__github__merge_pull_request",
    "mcp__github__delete_file",
    "mcp__github__fork_repository",
    "mcp__github__create_repository",
}


def test_all_tools_carry_mcp_github_prefix():
    for tool in GITHUB_READ_TOOLS + GITHUB_WRITE_TOOLS:
        assert tool.startswith("mcp__github__")


def test_read_tools_contain_no_write_shaped_names():
    for tool in GITHUB_READ_TOOLS:
        bare_name = tool.removeprefix("mcp__github__")
        assert bare_name not in WRITE_TOOL_NAMES


def test_write_tools_never_include_merge_delete_or_fork():
    assert not DANGEROUS_TOOLS.intersection(GITHUB_WRITE_TOOLS)


def test_write_tools_are_a_superset_of_read_tools():
    assert set(GITHUB_READ_TOOLS).issubset(set(GITHUB_WRITE_TOOLS))


def test_write_tools_include_exactly_the_pr_flow_additions():
    added = set(GITHUB_WRITE_TOOLS) - set(GITHUB_READ_TOOLS)
    assert added == {
        "mcp__github__create_branch",
        "mcp__github__create_or_update_file",
        "mcp__github__create_pull_request",
    }
