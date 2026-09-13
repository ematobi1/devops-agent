from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from .secrets import SecretsProvider

_WHOLE_VALUE_VAR_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _substitute(node: Any, secrets: SecretsProvider) -> Any:
    if isinstance(node, dict):
        return {k: _substitute(v, secrets) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, secrets) for v in node]
    if isinstance(node, str):
        match = _WHOLE_VALUE_VAR_PATTERN.match(node)
        if match:
            return secrets.get(match.group(1))
    return node


def render_mcp_config(template_path: Path, secrets: SecretsProvider, *, out_dir: Path | None = None) -> Path:
    """Substitutes ${VAR}-only string values in an .mcp.json template with
    values from `secrets` and writes the result to a fresh file. Substitution
    happens on the parsed JSON structure (never raw text splicing), so a
    secret value containing quotes or other JSON-special characters can never
    corrupt the output or inject extra keys. The template (checked into git)
    never contains a real credential; the rendered file does and must never
    be committed or logged."""
    template = json.loads(template_path.read_text(encoding="utf-8"))
    rendered = _substitute(template, secrets)

    fd, path_str = tempfile.mkstemp(prefix="mcp-config-", suffix=".json", dir=out_dir)
    out_path = Path(path_str)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(rendered, f)
    return out_path
