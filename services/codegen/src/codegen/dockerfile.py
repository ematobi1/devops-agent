"""Dockerfile generation: a narrow Claude Code prompt for the one part that
genuinely varies per repo, deterministic templating for everything that
doesn't, a deterministic lint check, and deterministic policy checks -
before anything is proposed for approval.

Two fixes landed here, each removing model discretion one level further
after live sampling showed it varying:

1. Base image (and privilege-drop strategy) became config, not a
   generation decision - see base_images.py. A 6-run sample had shown the
   model re-deriving the container's security architecture every run,
   including one unsafe choice that still passed lint.
2. CMD and HEALTHCHECK are ALSO properties of the base image and how it's
   meant to run, not the repo - a follow-up 4-run sample (base image now
   fixed) showed them regressing anyway: 1/4 correct CMD, 1/4 correct
   HEALTHCHECK, no run with both, though the verifier caught and annotated
   every case correctly. render_dockerfile() now templates FROM/CMD/
   HEALTHCHECK/EXPOSE deterministically from AppImagePolicy; the model's
   job narrows to what actually varies per repo - which files to COPY,
   where, and the --chown form.

Determining the COPY instructions is still genuinely latent-space (reading
an arbitrary repo's file layout to decide what belongs in the image) - it
goes through the local Claude Code runner like every other LLM call in
this project. Templating, linting, and policy-checking the result are all
deterministic: the same inputs always produce the same output/findings, so
none of them need an LLM call.
"""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .base_images import AppImagePolicy


def build_copy_instructions_prompt(policy: AppImagePolicy) -> str:
    return (
        "You are a DevOps agent determining how to get a repository's "
        "files into a Docker image. The Dockerfile's base image, CMD, "
        "HEALTHCHECK, and EXPOSE are already fixed by operator policy - "
        f"do not produce or repeat them. The image is {policy.image}.\n\n"
        "Investigate the repository using only the read-only MCP tools "
        "available to you. Determine exactly which files or directories "
        "need to be copied into the image, the correct destination path "
        "for this base image, and an appropriate --chown form so the "
        "process (running as a non-root user in this image) can read "
        "them.\n\n"
        "End your reply with only the Dockerfile COPY instruction(s) "
        "needed - one or more lines, nothing else - in a single fenced "
        "code block labeled dockerfile-copy:\n"
        "```dockerfile-copy\n"
        "...\n"
        "```\n"
        "Do not include FROM, CMD, HEALTHCHECK, EXPOSE, or any other "
        "instruction - only COPY. Every line must be complete and correct "
        "as written - no placeholders."
    )


_FENCED_COPY_BLOCK = re.compile(r"```dockerfile-copy\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_COPY_LINE = re.compile(r"^\s*COPY\s", re.MULTILINE | re.IGNORECASE)


def extract_copy_instructions(result_text: str) -> str | None:
    """Pulls the COPY instruction(s) out of a ```dockerfile-copy fenced
    block, as required by build_copy_instructions_prompt(). Returns None if
    no such block is present, or if it's present but contains no line that
    actually looks like a COPY instruction - either way the model didn't
    follow instructions and the caller should refuse rather than guess."""
    match = _FENCED_COPY_BLOCK.search(result_text)
    if match is None:
        return None
    content = match.group(1).strip("\n")
    if not _COPY_LINE.search(content):
        return None
    return content


def render_dockerfile(policy: AppImagePolicy, copy_instructions: str) -> str:
    """Deterministically assembles the final Dockerfile from the
    operator-configured policy (FROM/EXPOSE/HEALTHCHECK/CMD - fixed, never
    model-generated) and the model-generated COPY instruction(s), the only
    part that legitimately varies per repo. Templating, not generation -
    the same policy and copy_instructions always produce the same output."""
    return (
        f"FROM {policy.image}\n"
        f"{copy_instructions.strip()}\n"
        f"EXPOSE {policy.port}\n"
        f"HEALTHCHECK {policy.healthcheck}\n"
        f"CMD {policy.cmd}\n"
    )


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
# real samples (see base_images.py docstring). Still run against the final
# assembled Dockerfile even now that FROM/CMD/HEALTHCHECK are templated
# rather than model-generated - they now assert render_dockerfile() itself
# produced correct output (a templating bug is still a bug) rather than
# that the model remembered. All annotate rather than block, same as
# verifier.check_proposed_actions() - a false positive here should cost a
# glance, not a discarded run.

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
