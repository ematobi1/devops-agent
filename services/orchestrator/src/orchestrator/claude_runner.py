from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ClaudeRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaudeRunConfig:
    mcp_config_path: Path
    system_prompt: str
    permission_mode: str = "dontAsk"
    max_budget_usd: float = 1.0
    timeout_seconds: int = 300
    restricted: bool = True
    # None lets Claude Code use the account's configured default model.
    # Never hardcode a cheaper model here - see CLAUDE.md "no silent downgrades".
    model: str | None = None
    executable: str = "claude"


@dataclass(frozen=True)
class ToolCall:
    server: str
    tool: str
    input: dict[str, Any]
    output: Any = None
    is_error: bool = False


@dataclass(frozen=True)
class ClaudeRunResult:
    result_text: str
    is_error: bool
    session_id: str
    total_cost_usd: float
    num_turns: int
    terminal_reason: str
    tool_calls: list[ToolCall]
    raw_events: list[dict[str, Any]]


class ClaudeRunner(Protocol):
    def run(self, prompt: str, *, json_schema: dict[str, Any] | None = None) -> ClaudeRunResult: ...


def _split_mcp_tool_name(name: str) -> tuple[str, str]:
    """MCP tools surface as mcp__<server>__<tool>; built-ins (Read, Bash, ...) have no server."""
    parts = name.split("__", 2)
    if len(parts) == 3 and parts[0] == "mcp":
        return parts[1], parts[2]
    return "claude-code", name


def parse_stream_json(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def build_result(events: list[dict[str, Any]]) -> ClaudeRunResult:
    result_event = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result_event is None:
        raise ClaudeRunError("stream contained no terminal 'result' event")

    tool_uses: dict[str, ToolCall] = {}
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                server, tool = _split_mcp_tool_name(block["name"])
                tool_uses[block["id"]] = ToolCall(server=server, tool=tool, input=block.get("input", {}))

    for event in events:
        if event.get("type") != "user":
            continue
        for block in event.get("message", {}).get("content", []):
            if block.get("type") != "tool_result":
                continue
            call = tool_uses.get(block.get("tool_use_id"))
            if call is None:
                continue
            tool_uses[block["tool_use_id"]] = ToolCall(
                server=call.server,
                tool=call.tool,
                input=call.input,
                output=block.get("content"),
                is_error=bool(block.get("is_error", False)),
            )

    return ClaudeRunResult(
        result_text=result_event.get("result", ""),
        is_error=bool(result_event.get("is_error", False)),
        session_id=result_event.get("session_id", ""),
        total_cost_usd=float(result_event.get("total_cost_usd", 0.0)),
        num_turns=int(result_event.get("num_turns", 0)),
        terminal_reason=result_event.get("terminal_reason", "unknown"),
        tool_calls=list(tool_uses.values()),
        raw_events=events,
    )


class SubprocessClaudeRunner:
    """Drives local Claude Code headlessly. This is the only place in the
    codebase allowed to invoke an LLM, and it does so via the local `claude`
    binary - never a hosted API. See docs/ARCHITECTURE.md "LLM access"."""

    def __init__(self, config: ClaudeRunConfig):
        self._config = config

    def _argv(self, prompt: str, json_schema: dict[str, Any] | None) -> list[str]:
        argv = [
            self._config.executable,
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            # Exclude the operator's personal ~/.claude/CLAUDE.md and global rules -
            # irrelevant context that costs cache-creation tokens on every run.
            "--setting-sources", "project,local",
            "--mcp-config", str(self._config.mcp_config_path),
            "--strict-mcp-config",
            "--permission-mode", self._config.permission_mode,
            "--permission-prompts", "none",
            "--no-session-persistence",
            "--max-budget-usd", str(self._config.max_budget_usd),
            "--system-prompt", self._config.system_prompt,
        ]
        if self._config.restricted:
            argv.append("--restricted")
        if self._config.model:
            argv.extend(["--model", self._config.model])
        if json_schema is not None:
            argv.extend(["--json-schema", json.dumps(json_schema)])
        argv.append(prompt)
        return argv

    def run(self, prompt: str, *, json_schema: dict[str, Any] | None = None) -> ClaudeRunResult:
        argv = self._argv(prompt, json_schema)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeRunError(f"claude run exceeded {self._config.timeout_seconds}s timeout") from exc

        events = parse_stream_json(proc.stdout)
        if not events:
            raise ClaudeRunError(f"no output from claude (exit {proc.returncode}): {proc.stderr.strip()}")
        return build_result(events)
