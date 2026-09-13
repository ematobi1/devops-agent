"""Allowlist for the aws-cloudwatch MCP server.

Enumerated by hand-driving a real MCP initialize -> tools/list JSON-RPC
exchange against a live `uvx awslabs.cloudwatch-mcp-server@latest` process
(env AWS_PROFILE=devops-agent-ro) - not guessed from documentation.

Unlike github-mcp-server, this server has no CLI flags at all (no --help
output, no --read-only, no --toolsets) and unconditionally registers every
tool across Logs, Metrics, and Alarms. There is also no mixed read/write
split to make here: every tool listed below describes, gets, lists,
analyzes, or queries - none of them create, update, or delete anything. The
read-only guarantee for this integration rests entirely on the IAM policy
attached to the AWS_PROFILE in use (CloudWatchReadOnlyAccess - verified live:
`aws logs describe-log-groups` succeeds, `aws ec2 describe-instances` is
denied under this profile) plus Claude Code's --allowedTools layer, not on
any server-side restriction flag.
"""

_PREFIX = "mcp__aws-cloudwatch__"

CLOUDWATCH_READ_TOOLS: tuple[str, ...] = tuple(
    _PREFIX + name
    for name in (
        "describe_log_groups",
        "analyze_log_group",
        "execute_log_insights_query",
        "get_logs_insight_query_results",
        "cancel_logs_insight_query",
        "execute_cwl_insights_batch",
        "recommend_indexes_loggroup",
        "recommend_indexes_account",
        "get_metric_data",
        "get_metric_metadata",
        "analyze_metric",
        "get_recommended_metric_alarms",
        "execute_promql_query",
        "execute_promql_range_query",
        "get_promql_label_values",
        "get_promql_series",
        "get_promql_labels",
        "get_active_alarms",
        "get_alarm_history",
    )
)
