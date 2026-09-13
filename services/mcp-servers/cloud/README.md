# cloud (AWS) MCP servers

Not custom code — registers officially maintained servers from
[`awslabs/mcp`](https://github.com/awslabs/mcp). Only `awslabs.cloudwatch-mcp-server`
is validated and wired in so far (`.mcp.cloudwatch-only.json.template`, per
the same "one server at a time" discipline used for GitHub).
`awslabs.aws-iac-mcp-server` is listed for later, not yet validated — hold
off on it until there's a concrete IaC-generation task to validate against.

## Prerequisites

- **`uv`/`uvx`.** Installed this session via `pip install uv` (landed at
  `%APPDATA%\Python\Python314\Scripts\uv.exe`, added to the persistent user
  `PATH`). **Caveat verified live:** a shell already open before that PATH
  update won't see it — `claude` spawning `uvx` as a bare command in such a
  shell fails with a generic MCP "Connection closed", which looks like an
  auth or transport problem but is actually just PATH staleness. Open a
  fresh shell, or explicitly prepend the Scripts directory to `PATH` for
  that one invocation, if this MCP server fails to connect right after a
  fresh `uv` install.
- An AWS credentials profile scoped to **`CloudWatchReadOnlyAccess`** only
  (the AWS-managed policy) — not the broader `ReadOnlyAccess` policy, which
  grants read access across nearly every AWS service. Verified live with a
  dedicated IAM user (`devops-agent-ro`, distinct from any
  broader-provisioning credential like a Terraform user already on the
  machine): `aws logs describe-log-groups` succeeds, `aws ec2
  describe-instances` is denied.

## Verified server behavior (checked live, not assumed from docs)

Unlike `github-mcp-server`, this server has **no CLI flags at all** —
`uvx awslabs.cloudwatch-mcp-server@latest --help` doesn't print help, it just
starts the server and unconditionally registers every tool across Logs,
Metrics, and Alarms. There's no `--read-only` or `--toolsets` equivalent.
The read-only guarantee here rests entirely on the IAM policy attached to
`AWS_PROFILE`, plus Claude Code's `--allowedTools` layer
(`orchestrator.aws_tools.CLOUDWATCH_READ_TOOLS`, enumerated via a real MCP
`tools/list` call — see that module's docstring) — not on anything the
server itself enforces.

Every one of the 19 tools this server exposes is inherently read-only by
name and behavior (describe/get/analyze/query/list/recommend — nothing
create/update/delete/put-shaped), so unlike GitHub there's no mixed
read/write tool set to carefully split.

**Known limitation, found during validation:** `describe_log_groups` did not
paginate through all results on a 92-log-group account — it reported 87.
Confirmed independently via raw `aws logs describe-log-groups` (aggregated
JSON, not the AWS CLI's `--query`-with-pagination gotcha which applies
JMESPath per-page and looks like an undercount too if you're not careful).
Don't trust an agent-reported count from this tool on an account with more
log groups than fit in one page without cross-checking — this needs either
an explicit "paginate through everything" instruction in the task prompt or
a fix upstream.

## Configuration

```
AWS_PROFILE=<your CloudWatchReadOnlyAccess-scoped profile>
AWS_REGION=<region>
```

Neither is a secret — this server runs as a plain local `uvx` process (not
containerized), so it reads credentials straight from
`~/.aws/credentials`/`~/.aws/config` via the profile name, the same way the
`aws` CLI does. `AWS_PROFILE`/`AWS_REGION` just need to be visible to the
Python process that calls `render_mcp_config()` — nothing sensitive ever
flows through the rendered `.mcp.json` for AWS the way the GitHub PAT does.

## Why not the Cost Explorer server from the original plan

`awslabs/mcp` doesn't ship a dedicated Cost Explorer server; the closest
matches are `awslabs.aws-pricing-mcp-server` (pricing lookups, not actual
spend) and `awslabs.billing-cost-management-mcp-server` (real billing data).
Add whichever one actually answers your cost questions once you have a
concrete use case — don't wire in both speculatively.

## Running the validated smoke test

```
export AWS_PROFILE=devops-agent-ro
export AWS_REGION=us-east-1
.venv/Scripts/python scripts/smoke_test_aws.py "list CloudWatch log groups and report any active alarms"
```
