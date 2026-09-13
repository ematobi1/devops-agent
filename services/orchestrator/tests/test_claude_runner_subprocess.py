import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.claude_runner import ClaudeRunConfig, ClaudeRunError, SubprocessClaudeRunner


def _runner(**overrides):
    config = ClaudeRunConfig(
        mcp_config_path=Path("/tmp/.mcp.json"),
        system_prompt="be a devops agent",
        **overrides,
    )
    return SubprocessClaudeRunner(config)


class FakeCompletedProcess:
    def __init__(self, stdout, stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_run_passes_explicit_utf8_encoding_with_replace_errors():
    """Regression test: Windows' default locale encoding (cp1252) can't
    decode Claude Code's UTF-8 output and raises UnicodeDecodeError mid-read
    unless encoding/errors are set explicitly. Reproduced live."""
    with patch("orchestrator.claude_runner.subprocess.run") as mock_run:
        mock_run.return_value = FakeCompletedProcess(stdout='{"type":"result","result":"ok"}')
        _runner().run("do the task")

    _, kwargs = mock_run.call_args
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"


def test_run_raises_clear_error_when_stdout_is_none():
    """Regression test: if the stdout reader thread dies (e.g. a decode
    error elsewhere), proc.stdout can be None. Must raise ClaudeRunError,
    not an unguarded AttributeError from stdout.splitlines()."""
    with patch("orchestrator.claude_runner.subprocess.run") as mock_run:
        mock_run.return_value = FakeCompletedProcess(stdout=None, stderr="reader thread failed", returncode=1)
        with pytest.raises(ClaudeRunError, match="no readable stdout"):
            _runner().run("do the task")


def test_run_raises_clear_error_when_stderr_is_also_none():
    """Same guard must not itself crash if stderr is None too."""
    with patch("orchestrator.claude_runner.subprocess.run") as mock_run:
        mock_run.return_value = FakeCompletedProcess(stdout=None, stderr=None, returncode=1)
        with pytest.raises(ClaudeRunError, match="no readable stdout"):
            _runner().run("do the task")


def test_run_still_raises_on_timeout():
    with patch("orchestrator.claude_runner.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=300)
        with pytest.raises(ClaudeRunError, match="300s timeout"):
            _runner().run("do the task")
