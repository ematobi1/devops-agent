# github MCP server

Not custom code — this registers GitHub's own officially maintained
[`github/github-mcp-server`](https://github.com/github/github-mcp-server) in
`.mcp.json.template` at the repo root. Per the "search before building" rule,
writing a bespoke GitHub adapter would just be worse, unmaintained duplicate
of what GitHub already ships.

## Prerequisites

- Docker (already installed on this machine — `docker --version` to confirm)
- A GitHub Personal Access Token with read-only scopes (`repo:status`,
  `public_repo` or `repo` read access, `read:org` if you need org-level
  visibility). Fine-grained PATs are preferred — scope to the specific repos
  the agent needs.

## Configuration

Set before running the orchestrator:

```
GITHUB_PERSONAL_ACCESS_TOKEN=<your token>
```

The template pins the server to read-only mode (`GITHUB_READ_ONLY=1`) and a
restricted toolset (`repos,issues,pull_requests,actions`) — this MCP server
never gets write access. Any change that requires writing to GitHub (merging
a PR, pushing a revert) goes through `services/approval`, using separate,
explicitly-elevated credentials — never this server's token.

## Verifying it's reachable

```
docker pull ghcr.io/github/github-mcp-server
docker run --rm -e GITHUB_PERSONAL_ACCESS_TOKEN -e GITHUB_READ_ONLY=1 ghcr.io/github/github-mcp-server --help
```
