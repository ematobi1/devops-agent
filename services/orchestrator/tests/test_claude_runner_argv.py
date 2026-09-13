from pathlib import Path

from orchestrator.claude_runner import ClaudeRunConfig, SubprocessClaudeRunner


def _runner(**overrides):
    config = ClaudeRunConfig(
        mcp_config_path=Path("/tmp/.mcp.json"),
        system_prompt="be a devops agent",
        **overrides,
    )
    return SubprocessClaudeRunner(config)


def test_argv_isolates_from_personal_global_config():
    argv = _runner()._argv("do the task", None)
    assert "--setting-sources" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project,local"
    assert "--strict-mcp-config" in argv


def test_argv_never_prompts_and_never_persists_session():
    argv = _runner()._argv("do the task", None)
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--no-session-persistence" in argv


def test_argv_enforces_budget_cap():
    argv = _runner(max_budget_usd=2.5)._argv("do the task", None)
    assert argv[argv.index("--max-budget-usd") + 1] == "2.5"


def test_argv_restricted_by_default_blocks_shell_tools():
    argv = _runner()._argv("do the task", None)
    assert "--restricted" in argv


def test_argv_unrestricted_when_explicitly_disabled():
    argv = _runner(restricted=False)._argv("do the task", None)
    assert "--restricted" not in argv


def test_argv_omits_model_flag_by_default_no_silent_downgrade():
    argv = _runner()._argv("do the task", None)
    assert "--model" not in argv


def test_argv_passes_model_when_explicitly_set():
    argv = _runner(model="opus")._argv("do the task", None)
    assert argv[argv.index("--model") + 1] == "opus"


def test_argv_includes_json_schema_when_requested():
    schema = {"type": "object"}
    argv = _runner()._argv("do the task", schema)
    assert "--json-schema" in argv


def test_prompt_is_last_argument():
    argv = _runner()._argv("do the task", None)
    assert argv[-1] == "do the task"


def test_argv_omits_allowed_tools_when_empty():
    argv = _runner()._argv("do the task", None)
    assert "--allowedTools" not in argv
    assert "--" not in argv


def test_argv_passes_allowed_tools_as_single_space_joined_arg():
    argv = _runner(allowed_tools=("mcp__github__get_file_contents", "mcp__github__search_code"))._argv(
        "do the task", None
    )
    assert argv[argv.index("--allowedTools") + 1] == "mcp__github__get_file_contents mcp__github__search_code"


def test_argv_terminates_allowed_tools_before_prompt():
    """Regression test: --allowedTools is a variadic flag that greedily
    consumes every following bare argv token - including the prompt itself -
    unless a "--" terminator marks where its value list ends. Reproduced live
    against the real claude binary: omitting "--" fails with 'Input must be
    provided either through stdin or as a prompt argument when using --print'."""
    argv = _runner(allowed_tools=("mcp__github__get_file_contents",))._argv("do the task", None)
    tools_value_index = argv.index("--allowedTools") + 1
    assert argv[tools_value_index + 1] == "--"
    assert argv[-1] == "do the task"


def test_argv_allowed_tools_is_the_last_flag_before_terminator():
    """Any flag placed after --allowedTools's value (e.g. --model) would also
    get swallowed by the variadic parse without hitting "--" first - so
    --allowedTools must always be added last, right before the terminator."""
    argv = _runner(model="opus", allowed_tools=("mcp__github__get_file_contents",))._argv("do the task", None)
    model_index = argv.index("--model")
    allowed_tools_index = argv.index("--allowedTools")
    assert model_index < allowed_tools_index
