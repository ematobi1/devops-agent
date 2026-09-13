"""Curated allowlists for the github MCP server (toolsets=repos,pull_requests).

The full tool set below was enumerated by hand-driving a real MCP
initialize -> tools/list JSON-RPC exchange against
ghcr.io/github/github-mcp-server:v1.12.1 with --toolsets=repos,pull_requests
- not guessed from documentation, which was stale for this image version
(see services/mcp-servers/github/README.md).

Two independent layers enforce read-only-ness for GITHUB_READ_TOOLS:
1. Claude Code's --allowedTools only lets these specific tools be invoked.
2. The github-mcp-server process itself is started with --read-only=true in
   read mode, so even a tool name mistakenly added here would be rejected by
   the server. Defense in depth, not either/or.
"""

_PREFIX = "mcp__github__"

_ALL_TOOLS = frozenset({
    "add_comment_to_pending_review",
    "add_reply_to_pull_request_comment",
    "create_branch",
    "create_or_update_file",
    "create_pull_request",
    "create_repository",
    "delete_file",
    "fork_repository",
    "get_commit",
    "get_file_contents",
    "get_latest_release",
    "get_release_by_tag",
    "get_tag",
    "list_branches",
    "list_commits",
    "list_pull_requests",
    "list_releases",
    "list_repository_collaborators",
    "list_tags",
    "merge_pull_request",
    "pull_request_read",
    "pull_request_review_write",
    "push_files",
    "search_code",
    "search_commits",
    "search_pull_requests",
    "search_repositories",
    "update_pull_request",
    "update_pull_request_branch",
})

_READ_TOOL_NAMES = (
    "get_file_contents",
    "search_code",
    "list_branches",
    "list_commits",
    "get_commit",
    "search_repositories",
    "list_pull_requests",
    "search_pull_requests",
    "pull_request_read",
)

# Adds exactly what the reversible read -> edit -> open-PR smoke test needs.
# Deliberately excludes merge_pull_request, delete_file, fork_repository,
# create_repository, pull_request_review_write, and the *_pull_request_comment
# tools - a bad run should produce one open PR, never a merge or a deletion.
_WRITE_TOOL_NAMES = _READ_TOOL_NAMES + (
    "create_branch",
    "create_or_update_file",
    "create_pull_request",
)

assert _ALL_TOOLS.issuperset(_READ_TOOL_NAMES)
assert _ALL_TOOLS.issuperset(_WRITE_TOOL_NAMES)

GITHUB_READ_TOOLS: tuple[str, ...] = tuple(_PREFIX + name for name in _READ_TOOL_NAMES)
GITHUB_WRITE_TOOLS: tuple[str, ...] = tuple(_PREFIX + name for name in _WRITE_TOOL_NAMES)
