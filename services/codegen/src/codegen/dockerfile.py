"""Dockerfile generation: a Claude Code prompt tailored to producing a
complete Dockerfile, extraction of that content from the run's reply, and a
deterministic lint check before anything is proposed for approval.

Generation itself is genuinely a latent-space task (writing a correct
Dockerfile for an arbitrary repo requires understanding its language,
dependencies, and entry point) - it goes through the local Claude Code
runner like every other LLM call in this project, never a direct API call.
Linting the result is deterministic I/O: the same Dockerfile content and
base-image registry state always produce the same `docker build --check`
output, so that part needs no LLM call at all.
"""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DOCKERFILE_SYSTEM_PROMPT = (
    "You are a DevOps agent generating a Dockerfile for a repository. "
    "Investigate the repository's language, dependencies, and entry point "
    "using only the read-only MCP tools available to you. Do not attempt "
    "to modify any external system - you have no write access.\n\n"
    "Follow Docker best practices: pin the base image to a specific "
    "version tag (never :latest), run the process as a non-root user, "
    "minimize the number of layers and final image size, and use a "
    "multi-stage build if it meaningfully reduces the final image size.\n\n"
    "End your reply with the complete Dockerfile content in a single "
    "fenced code block labeled dockerfile, and nothing else after it:\n"
    "```dockerfile\n"
    "...\n"
    "```\n"
    "Every line must be complete and correct as written - no placeholder "
    "comments, no TODOs, no options left for someone else to fill in."
)

_FENCED_DOCKERFILE_BLOCK = re.compile(r"```dockerfile\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_dockerfile(result_text: str) -> str | None:
    """Pulls the Dockerfile content out of a ```dockerfile fenced block, as
    required by DOCKERFILE_SYSTEM_PROMPT. Returns None if no such block is
    present - deliberately does not fall back to a generic, unlabeled
    fenced block, since that risks grabbing an unrelated code snippet the
    model included while investigating (e.g. a command it ran)."""
    match = _FENCED_DOCKERFILE_BLOCK.search(result_text)
    if match is None:
        return None
    return match.group(1).strip("\n")


@dataclass(frozen=True)
class Finding:
    reason: str


_WARNING_BLOCK = re.compile(r"^WARNING: (\S+) - \S+\n(.+)$", re.MULTILINE)
_PARSE_ERROR = re.compile(r"^ERROR: dockerfile parse error.*$", re.MULTILINE)


def check_dockerfile_lint_output(stdout: str, returncode: int) -> list[Finding]:
    """Pure parser for `docker build --check` output - verified live
    against three real cases (see services/codegen/tests/test_dockerfile.py
    fixtures, captured from actual runs): clean (exit 0, 'no warnings
    found'), lint warnings (exit 1, one or more 'WARNING: <Rule> - <url>'
    blocks), and a genuine parse error (exit 1, 'ERROR: dockerfile parse
    error...', no 'Check complete' line at all - a different shape than a
    warning, so parsed separately)."""
    if returncode == 0:
        return []

    parse_error = _PARSE_ERROR.search(stdout)
    if parse_error:
        return [Finding(reason=parse_error.group(0).strip())]

    findings = [
        Finding(reason=f"{match.group(1)}: {match.group(2).strip()}") for match in _WARNING_BLOCK.finditer(stdout)
    ]
    if not findings:
        # Non-zero exit but neither known shape matched - don't silently
        # swallow an unrecognized failure mode.
        findings.append(Finding(reason=f"docker build --check exited {returncode} with unrecognized output: {stdout[:500]}"))
    return findings


class DockerCheckError(RuntimeError):
    pass


def lint_dockerfile(content: str, *, timeout_seconds: int = 60) -> list[Finding]:
    """Writes `content` to a temp Dockerfile in an empty build context and
    runs `docker build --check` against it. Requires Docker Desktop running
    (same prerequisite already established for github-mcp-server - see
    services/mcp-servers/github/README.md)."""
    with tempfile.TemporaryDirectory() as tmp:
        dockerfile_path = Path(tmp) / "Dockerfile"
        dockerfile_path.write_text(content, encoding="utf-8")
        try:
            proc = subprocess.run(
                ["docker", "build", "--check", "-f", str(dockerfile_path), tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerCheckError(f"docker build --check exceeded {timeout_seconds}s timeout") from exc

    return check_dockerfile_lint_output(proc.stdout, proc.returncode)
