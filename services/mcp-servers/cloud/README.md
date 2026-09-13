# cloud (AWS) MCP servers

Not custom code — registers two of AWS's officially maintained servers from
[`awslabs/mcp`](https://github.com/awslabs/mcp) in `.mcp.json.template`:

- `awslabs.cloudwatch-mcp-server` — metrics, alarms, logs (visibility, read-only)
- `awslabs.aws-iac-mcp-server` — CloudFormation/CDK docs, construct examples,
  security validation (used by `services/codegen`, not by diagnostic runs)

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) installed (`uvx` ships with it) — **not
  yet installed on this machine**, run `pip install uv` or the platform
  installer before first use.
- An AWS credentials profile with read-only access (`ReadOnlyAccess` managed
  policy is a reasonable starting point — tighten to exactly what's needed
  once real usage tells you what that is).

## Configuration

```
AWS_PROFILE=<your read-only profile>
AWS_REGION=<default region>
```

## Why not the Cost Explorer server from the original plan

`awslabs/mcp` doesn't ship a dedicated Cost Explorer server; the closest
matches are `awslabs.aws-pricing-mcp-server` (pricing lookups, not actual
spend) and `awslabs.billing-cost-management-mcp-server` (real billing data).
Add whichever one actually answers your cost questions once you have a
concrete use case — don't wire in both speculatively.

## Verifying it's reachable

```
uvx awslabs.cloudwatch-mcp-server@latest --help
```
