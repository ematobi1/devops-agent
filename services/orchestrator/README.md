# orchestrator

Owns the policy around a Claude Code run: build the prompt, render the MCP
config with real secrets, run Claude Code headlessly and read-only, parse the
proposed actions out of its reply, and produce a pending `ApprovalRequest`.

This service **never applies anything**. It has no write credentials for
GitHub or AWS. The only thing downstream of `run_task()` is a record in
`services/approval` waiting for a human decision.

## Modules

- `claude_runner.py` — subprocess wrapper around the local `claude` binary.
  The only place in this codebase allowed to invoke an LLM (see
  `docs/ARCHITECTURE.md` § LLM access). `SubprocessClaudeRunner` does the
  real I/O; `parse_stream_json`/`build_result` are pure functions tested
  without shelling out.
- `mcp_config.py` — renders `.mcp.json.template` into a temp file with
  secrets substituted in, at the JSON-value level (never raw text splicing,
  so a secret containing a quote can't corrupt the config or inject a key).
- `secrets.py` — `SecretsProvider` protocol; `EnvSecretsProvider` for local
  dev. Swap in a Vault/AWS Secrets Manager-backed implementation for
  production behind the same interface — don't build that until you deploy
  somewhere that has one.
- `orchestrate.py` — `run_task(task_request, runner)`: validates the request
  against `contracts/task_request.schema.json`, runs Claude Code with a
  diagnostic-only system prompt, and builds the `ApprovalRequest`.
- `apply.py` — `apply_approved(approval, runner)`: executes an already
  `approved` `ApprovalRequest` and builds a `ChangeRecord`. Deliberately
  integration-agnostic (doesn't know GitHub/AWS/anything — that's entirely
  determined by `runner`'s MCP config and tool allowlist), so it classifies
  outcome using only the Claude Code run's own `is_error` flag. **Known
  limitation, found live:** a model that hits a real failure (e.g. a 403)
  and gracefully explains it before stopping produces `is_error: False` —
  identical to genuine success — so `apply_approved()` alone cannot tell
  "nothing happened, explained clearly" apart from "it worked." Callers
  that know their integration must verify further; `scripts/apply.py` does
  this for GitHub by checking `verifier.find_pr_reference()` against the
  result text before trusting an "applied" outcome.

## Running a real task (requires secrets configured — see
`services/mcp-servers/*/README.md`)

```python
from pathlib import Path
from orchestrator import (
    ClaudeRunConfig, SubprocessClaudeRunner, EnvSecretsProvider,
    render_mcp_config, run_task,
)
from orchestrator.orchestrate import SYSTEM_PROMPT

mcp_config_path = render_mcp_config(Path(".mcp.json.template"), EnvSecretsProvider())
runner = SubprocessClaudeRunner(ClaudeRunConfig(
    mcp_config_path=mcp_config_path,
    system_prompt=SYSTEM_PROMPT,
    max_budget_usd=1.0,
))
outcome = run_task(task_request, runner)
print(outcome.approval_request)
```

## Tests

`pytest services/orchestrator/tests` — all gate tests, all deterministic, no
live `claude` invocation and no network. Runs in well under a second.
