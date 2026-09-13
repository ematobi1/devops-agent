import json

import pytest

from orchestrator.mcp_config import render_mcp_config
from orchestrator.secrets import SecretNotFoundError

TEMPLATE = """{
  "mcpServers": {
    "github": {
      "command": "docker",
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}" }
    }
  }
}"""


class FakeSecrets:
    def __init__(self, values):
        self._values = values

    def get(self, name):
        if name not in self._values:
            raise SecretNotFoundError(name)
        return self._values[name]


def test_render_substitutes_placeholder(tmp_path):
    template_path = tmp_path / ".mcp.json.template"
    template_path.write_text(TEMPLATE)

    out_path = render_mcp_config(template_path, FakeSecrets({"GITHUB_PERSONAL_ACCESS_TOKEN": "tok_123"}), out_dir=tmp_path)

    rendered = json.loads(out_path.read_text())
    assert rendered["mcpServers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "tok_123"


def test_render_raises_on_missing_secret(tmp_path):
    template_path = tmp_path / ".mcp.json.template"
    template_path.write_text(TEMPLATE)

    with pytest.raises(SecretNotFoundError):
        render_mcp_config(template_path, FakeSecrets({}), out_dir=tmp_path)


def test_render_escapes_secrets_containing_json_special_characters(tmp_path):
    template_path = tmp_path / ".mcp.json.template"
    template_path.write_text(TEMPLATE)

    out_path = render_mcp_config(
        template_path, FakeSecrets({"GITHUB_PERSONAL_ACCESS_TOKEN": 'weird"quote\\and\nnewline'}), out_dir=tmp_path
    )
    rendered = json.loads(out_path.read_text())
    assert rendered["mcpServers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == 'weird"quote\\and\nnewline'


def test_render_leaves_non_placeholder_strings_untouched(tmp_path):
    template_path = tmp_path / ".mcp.json.template"
    template_path.write_text(TEMPLATE)

    out_path = render_mcp_config(template_path, FakeSecrets({"GITHUB_PERSONAL_ACCESS_TOKEN": "tok"}), out_dir=tmp_path)
    rendered = json.loads(out_path.read_text())
    assert rendered["mcpServers"]["github"]["command"] == "docker"


ARGS_TEMPLATE = """{
  "mcpServers": {
    "github": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",
        "ghcr.io/github/github-mcp-server",
        "stdio",
        "${GITHUB_READ_ONLY_FLAG}",
        "--toolsets=repos,pull_requests"
      ],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}" }
    }
  }
}"""


def test_render_substitutes_placeholder_inside_args_list(tmp_path):
    """Mirrors the shape of .mcp.github-only.json.template - a whole-value
    placeholder sitting among literal strings inside an args array."""
    template_path = tmp_path / ".mcp.github-only.json.template"
    template_path.write_text(ARGS_TEMPLATE)

    out_path = render_mcp_config(
        template_path,
        FakeSecrets({"GITHUB_PERSONAL_ACCESS_TOKEN": "tok", "GITHUB_READ_ONLY_FLAG": "--read-only=true"}),
        out_dir=tmp_path,
    )
    rendered = json.loads(out_path.read_text())
    args = rendered["mcpServers"]["github"]["args"]
    assert args == [
        "run", "-i", "--rm",
        "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",
        "ghcr.io/github/github-mcp-server",
        "stdio",
        "--read-only=true",
        "--toolsets=repos,pull_requests",
    ]
