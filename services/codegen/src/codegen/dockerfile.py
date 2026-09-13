"""Dockerfile generation: a Claude Code prompt tailored to producing a
complete Dockerfile, extraction of that content from the run's reply, a
deterministic lint check, and deterministic policy checks - before anything
is proposed for approval.

Generation itself is genuinely a latent-space task (writing a correct
Dockerfile for an arbitrary repo requires understanding its language,
dependencies, and entry point) - it goes through the local Claude Code
runner like every other LLM call in this project, never a direct API call.
Linting and policy-checking the result are deterministic I/O/pattern
matching: the same Dockerfile content always produces the same findings,
so neither needs an LLM call.

Base image selection is NOT part of what the model decides (see
base_images.py) - a sample of 6 independent generations against the same
repo showed the model re-deriving the base image and privilege-drop
strategy differently each run, including one architecturally unsafe choice
that still passed lint. build_dockerfile_system_prompt() takes the
operator-configured base image as a hard constraint instead.
"""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


def build_dockerfile_system_prompt(base_image: str) -> str:
    return (
        "You are a DevOps agent generating a Dockerfile for a repository. "
        "Investigate the repository's dependencies and entry point using "
        "only the read-only MCP tools available to you. Do not attempt to "
        "modify any external system - you have no write access.\n\n"
        f"You MUST use exactly this base image, already selected by "
        f"operator policy: {base_image}. Do not choose, suggest, or "
        f"substitute a different base image or tag under any "
        f"circumstances, even if you believe another choice would be "
        f"better - base image selection is a security decision made once "
        f"by the operator, not something to re-derive per run.\n\n"
        "Follow Docker best practices for everything else: minimize the "
        "number of layers, keep the final image small, and use a "
        "multi-stage build only if it meaningfully helps without changing "
        "the base image already specified above.\n\n"
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
    required by build_dockerfile_system_prompt(). Returns None if no such
    block is present - deliberately does not fall back to a generic,
    unlabeled fenced block, since that risks grabbing an unrelated code
    snippet the model included while investigating (e.g. a command it
    ran)."""
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
    warning, so parsed separately).

    This validates syntax and BuildKit's own best-practice rules, not
    architectural or security decisions - found live: all 6 sampled runs
    passed this check, including one that used a silently-no-op `sed`
    against /etc/nginx/nginx.conf that left the container running as root.
    Don't treat a clean lint result as a safety guarantee; see
    check_dockerfile_policy() for the checks that actually caught that."""
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


# --- Deterministic policy checks -------------------------------------------
#
# Distinct from lint_dockerfile(): these check architectural/security
# invariants docker build --check has no opinion on, derived directly from
# a real sample of 6 generations (see base_images.py docstring). All
# annotate rather than block, same as verifier.check_proposed_actions() -
# a false positive here should cost a glance, not a discarded run.

_FROM_LINE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+[Aa][Ss]\s+(\S+))?\s*$", re.MULTILINE)
_FULLY_PINNED_TAG = re.compile(r":(\d+\.\d+\.\d+)(-\S+)?$")
_CMD_LINE = re.compile(r"^\s*CMD\s", re.MULTILINE | re.IGNORECASE)
_HEALTHCHECK_BLOCK = re.compile(r"^[ \t]*HEALTHCHECK\b(?:.*\\\r?\n)*.*$", re.MULTILINE | re.IGNORECASE)
_SED_ETC = re.compile(r"\bsed\b[^\n]*-i[^\n]*/etc/", re.IGNORECASE)


def _referenced_base_images(content: str) -> list[str]:
    """Every FROM target that isn't itself a reference to an earlier build
    stage (multi-stage Dockerfiles legitimately do `FROM builder AS
    runtime` where "builder" names a prior stage, not an external image)."""
    stage_names: set[str] = set()
    images = []
    for image_ref, stage_name in _FROM_LINE.findall(content):
        if image_ref.lower() not in {s.lower() for s in stage_names}:
            images.append(image_ref)
        if stage_name:
            stage_names.add(stage_name)
    return images


def check_dockerfile_policy(content: str, *, allowed_base_images: frozenset[str]) -> list[Finding]:
    findings = []

    for image_ref in _referenced_base_images(content):
        if image_ref not in allowed_base_images:
            findings.append(Finding(reason=f"base image {image_ref!r} is not in the configured allowlist"))
        if not _FULLY_PINNED_TAG.search(image_ref):
            findings.append(
                Finding(reason=f"base image tag in {image_ref!r} is not pinned to MAJOR.MINOR.PATCH (e.g. run 3's 1.27)")
            )

    healthcheck_match = _HEALTHCHECK_BLOCK.search(content)

    # HEALTHCHECK's own syntax is "HEALTHCHECK [OPTIONS] CMD <command>" - its
    # embedded "CMD" is not a standalone CMD instruction. Strip the matched
    # HEALTHCHECK block out before checking for a real one, or a Dockerfile
    # with a healthcheck but no actual CMD would wrongly pass.
    content_for_cmd_check = content
    if healthcheck_match is not None:
        content_for_cmd_check = content[: healthcheck_match.start()] + content[healthcheck_match.end() :]

    if not _CMD_LINE.search(content_for_cmd_check):
        findings.append(Finding(reason="no explicit CMD instruction present"))

    if healthcheck_match is None:
        findings.append(Finding(reason="no HEALTHCHECK instruction present"))
    else:
        block = healthcheck_match.group(0)
        if "--retries" not in block:
            findings.append(Finding(reason="HEALTHCHECK is missing --retries"))
        if "--start-period" not in block:
            findings.append(Finding(reason="HEALTHCHECK is missing --start-period"))

    if _SED_ETC.search(content):
        findings.append(
            Finding(
                reason="uses sed -i against a path under /etc/ - silently no-ops if the pattern doesn't "
                "match (found live: a rootless conversion that never took effect, with no build or lint "
                "failure) - verify it actually took effect"
            )
        )

    return findings
