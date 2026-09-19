"""Thin subprocess wrappers around the existing pipeline scripts
(scripts/generate_dockerfile.py, scripts/diagnose.py, scripts/decide.py,
scripts/apply.py).

No pipeline logic lives here - every function's job is to build the exact
argv the corresponding script already documents in its own module
docstring, run it, and parse its already-deterministic stdout into a
structured result. Nothing here recomputes a lint/policy decision the
scripts themselves already made.

Two independent ways to produce the ApprovalRequest that approve/apply
then act on - both real, both already-shipped CLI scripts, neither new:

- generate_dockerfile.py: narrow, Dockerfile-specific. Performs THREE
  things - generate, lint (`docker build --check`), and policy
  verification - in one Claude Code run and one process, because
  lint/policy findings depend on the just-generated content and there's
  no reason to pay for a second run to re-derive them. run_generate()
  makes one subprocess call and splits its stdout into three UI-facing
  stage results by parsing the script's own printed section markers
  (`--- assembled Dockerfile ---`, `--- linting via docker build
  --check ---`, `verifier warnings (persisted, review before
  deciding):`) - not by re-running or re-judging anything.
- diagnose.py: general - an arbitrary natural-language DevOps request
  against GitHub, read-only, producing a summary and a numbered list of
  proposed actions plus the same verifier check. run_diagnose() splits
  its stdout into two UI-facing stage results (diagnose/verify) the same
  way, by parsing its own printed markers (`summary:`, `proposed
  actions:`, the same verifier-warnings marker).

scripts/decide.py and scripts/apply.py are shared by both paths
unchanged - apply.py in particular has no idea which script produced the
ApprovalRequest it's executing, by design (see docs/ARCHITECTURE.md
"Apply step — scope").
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable  # the interpreter running this server - must be REPO_ROOT/.venv's, see ui/README.md

DEFAULT_TIMEOUT_SECONDS = 300

_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{12,}"),
    re.compile(r"aws_?secret\S*", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r'(?i)"(?:access|refresh)_?token"\s*:\s*"[^"]{20,}"'),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
]


def redact_secrets(text: str | None) -> str:
    """Replaces anything that looks like a GitHub or AWS credential with a
    fixed placeholder. Applied to every piece of subprocess output before
    it's stored in a run record or sent to the client - never log or
    return a real token, even redacted-except-last-4; the whole match is
    replaced."""
    if not text:
        return text or ""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


@dataclass
class StageResult:
    stage: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    artifact: str | None = None

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "status": "ok" if self.ok else "failed",
            "stdout": redact_secrets(self.stdout),
            "stderr": redact_secrets(self.stderr),
            "exit_code": self.exit_code,
            "artifact": self.artifact,
        }


class RepoTargetError(ValueError):
    """Raised before any subprocess is spawned - an obviously-malformed
    target, not something worth a 300s subprocess round trip to discover."""


_OWNER_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def validate_target_repo(target_repo: str) -> None:
    """generate_dockerfile.py's second argument is a GitHub `owner/repo`
    identifier used for a remote GitHub API investigation - there is no
    local filesystem path or local git repo involved anywhere in this
    pipeline (see ui/README.md "Deviations from the spec"). This is the
    fail-fast shape check; an owner/repo that looks well-formed but
    doesn't exist or isn't accessible surfaces naturally from the real
    GitHub API call inside the generate stage, with GitHub's own message -
    that's not something to re-implement here."""
    if not target_repo or not _OWNER_REPO.match(target_repo):
        raise RepoTargetError(f"{target_repo!r} doesn't look like a GitHub owner/repo (expected e.g. 'owner/repo')")


def _run(argv: list[str], *, timeout: int, input_text: str | None = None, env: dict | None = None) -> tuple[int, str, str]:
    """Single subprocess execution point. Returns (exit_code, stdout,
    stderr); a timeout is reported as exit_code=124 with a fixed stderr
    message, the same shape as a real failure, so callers don't need a
    separate code path for it."""
    try:
        proc = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            input=input_text,
            env=env,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, stdout, f"timeout after {timeout}s"


_ASSEMBLED_RE = re.compile(r"--- assembled Dockerfile ---\n(.*?)\n--- linting via docker build --check ---", re.DOTALL)
_LINT_SECTION_RE = re.compile(
    r"--- linting via docker build --check ---\n(.*?)(?=\napproval id:|\ncost: \$|\Z)", re.DOTALL
)
_VERIFY_SECTION_RE = re.compile(
    r"verifier warnings \(persisted, review before deciding\):\n(.*?)(?=\ncost: \$|\Z)", re.DOTALL
)
_APPROVAL_ID_RE = re.compile(r"approval id: (\S+)")
_COST_RE = re.compile(r"cost: \$(\d+\.\d+)")
_PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")


@dataclass
class GenerateOutcome:
    generate: StageResult
    lint: StageResult
    verify: StageResult
    approval_id: str | None = None
    cost_usd: float | None = None


def run_generate(*, approvals_path: Path, target_repo: str, app_type: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> GenerateOutcome:
    """Runs scripts/generate_dockerfile.py once and splits its output into
    the three UI-facing pseudo-stages (generate/lint/verify) the real
    script performs in a single Claude Code run. Forces
    GITHUB_READ_ONLY_FLAG=--read-only=true regardless of what's set in the
    server process's own environment - generation must never run
    write-scoped, no matter what apply's stage later needs."""
    validate_target_repo(target_repo)
    env = dict(os.environ, GITHUB_READ_ONLY_FLAG="--read-only=true")
    argv = [PYTHON, str(REPO_ROOT / "scripts" / "generate_dockerfile.py"), str(approvals_path), target_repo, app_type]
    exit_code, stdout, stderr = _run(argv, timeout=timeout, env=env)

    assembled = _ASSEMBLED_RE.search(stdout)
    dockerfile = assembled.group(1).strip("\n") if assembled else None
    generate_ok = dockerfile is not None
    generate_result = StageResult(
        stage="generate", ok=generate_ok, stdout=stdout, stderr=stderr, exit_code=exit_code, artifact=dockerfile
    )
    if not generate_ok:
        skipped = StageResult(stage="lint", ok=False, stdout="", stderr="skipped - generate did not produce a Dockerfile", exit_code=exit_code)
        skipped_verify = StageResult(stage="verify", ok=False, stdout="", stderr="skipped - generate did not produce a Dockerfile", exit_code=exit_code)
        return GenerateOutcome(generate=generate_result, lint=skipped, verify=skipped_verify)

    lint_match = _LINT_SECTION_RE.search(stdout)
    lint_text = lint_match.group(1).strip() if lint_match else ""
    lint_ok = "lint clean" in lint_text
    lint_result = StageResult(stage="lint", ok=lint_ok, stdout=lint_text, stderr="" if lint_ok else "", exit_code=exit_code)
    if not lint_ok:
        skipped_verify = StageResult(stage="verify", ok=False, stdout="", stderr="skipped - lint failed", exit_code=exit_code)
        return GenerateOutcome(generate=generate_result, lint=lint_result, verify=skipped_verify)

    verify_match = _VERIFY_SECTION_RE.search(stdout)
    verify_text = verify_match.group(1).strip() if verify_match else "no warnings."
    # verifier findings annotate, never block (see codegen/README.md) - this stage is "ok" whenever
    # generate+lint succeeded, warnings or not; the warnings text itself is what's worth seeing.
    verify_result = StageResult(stage="verify", ok=True, stdout=verify_text, exit_code=exit_code)

    approval_id_match = _APPROVAL_ID_RE.search(stdout)
    cost_match = _COST_RE.search(stdout)
    return GenerateOutcome(
        generate=generate_result,
        lint=lint_result,
        verify=verify_result,
        approval_id=approval_id_match.group(1) if approval_id_match else None,
        cost_usd=float(cost_match.group(1)) if cost_match else None,
    )


_DIAGNOSE_SUMMARY_RE = re.compile(r"^summary: (.*?)\nproposed actions:\n", re.DOTALL | re.MULTILINE)
_DIAGNOSE_ACTIONS_RE = re.compile(
    r"\nproposed actions:\n(.*?)(?=\n\nverifier warnings \(persisted|\n\napproval id:|\Z)", re.DOTALL
)
_DIAGNOSE_VERIFY_RE = re.compile(
    r"verifier warnings \(persisted, review before deciding\):\n(.*?)(?=\n\napproval id:|\Z)", re.DOTALL
)


@dataclass
class DiagnoseOutcome:
    diagnose: StageResult
    verify: StageResult
    approval_id: str | None = None
    cost_usd: float | None = None


def run_diagnose(*, approvals_path: Path, task_description: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> DiagnoseOutcome:
    """Runs scripts/diagnose.py once - the general, natural-language path
    (as opposed to run_generate()'s narrow Dockerfile-specific one).
    Splits its stdout into two UI-facing pseudo-stages (diagnose/verify)
    by parsing the script's own printed markers. Forces
    GITHUB_READ_ONLY_FLAG=--read-only=true - diagnose is always read-only,
    same as generate; only apply ever runs write-scoped."""
    env = dict(os.environ, GITHUB_READ_ONLY_FLAG="--read-only=true")
    argv = [PYTHON, str(REPO_ROOT / "scripts" / "diagnose.py"), str(approvals_path), task_description]
    exit_code, stdout, stderr = _run(argv, timeout=timeout, env=env)

    summary_match = _DIAGNOSE_SUMMARY_RE.search(stdout)
    actions_match = _DIAGNOSE_ACTIONS_RE.search(stdout)
    approval_id_match = _APPROVAL_ID_RE.search(stdout)
    cost_match = _COST_RE.search(stdout)

    artifact_parts = []
    if summary_match:
        artifact_parts.append("Summary:\n" + summary_match.group(1).strip())
    if actions_match:
        artifact_parts.append("Proposed actions:\n" + actions_match.group(1).strip())
    artifact = "\n\n".join(artifact_parts) if artifact_parts else None

    diagnose_ok = approval_id_match is not None
    diagnose_result = StageResult(
        stage="diagnose", ok=diagnose_ok, stdout=stdout, stderr=stderr, exit_code=exit_code, artifact=artifact
    )
    if not diagnose_ok:
        skipped_verify = StageResult(stage="verify", ok=False, stdout="", stderr="skipped - diagnose did not produce an approval", exit_code=exit_code)
        return DiagnoseOutcome(diagnose=diagnose_result, verify=skipped_verify)

    verify_match = _DIAGNOSE_VERIFY_RE.search(stdout)
    verify_text = verify_match.group(1).strip() if verify_match else "no warnings."
    # Same "annotate, never block" contract as run_generate()'s verify pseudo-stage.
    verify_result = StageResult(stage="verify", ok=True, stdout=verify_text, exit_code=exit_code)

    return DiagnoseOutcome(
        diagnose=diagnose_result,
        verify=verify_result,
        approval_id=approval_id_match.group(1) if approval_id_match else None,
        cost_usd=float(cost_match.group(1)) if cost_match else None,
    )


def run_approve(*, approvals_path: Path, approval_id: str, decided_by: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> StageResult:
    argv = [PYTHON, str(REPO_ROOT / "scripts" / "decide.py"), str(approvals_path), approval_id, "approve", decided_by]
    exit_code, stdout, stderr = _run(argv, timeout=timeout)
    return StageResult(stage="approve", ok=exit_code == 0, stdout=stdout, stderr=stderr, exit_code=exit_code)


@dataclass
class ApplyOutcome:
    apply: StageResult
    pr_url: str | None = None


def run_apply(*, approvals_path: Path, approval_id: str, changes_path: Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> ApplyOutcome:
    """apply.py itself prompts interactively ("Type 'apply' to proceed") -
    that is a second, script-level confirmation on top of the UI's own
    Approve button, not a replacement for it. The UI's Approve click is
    the real human gate (nothing reaches this function until the operator
    has clicked it); feeding "apply\\n" on stdin here only satisfies
    apply.py's own prompt so it can run non-interactively, the same way a
    human operator would type it at a terminal after already having
    decided to proceed. Forces GITHUB_READ_ONLY_FLAG=--read-only=false -
    apply is the one stage that must run write-scoped."""
    env = dict(os.environ, GITHUB_READ_ONLY_FLAG="--read-only=false")
    argv = [PYTHON, str(REPO_ROOT / "scripts" / "apply.py"), str(approvals_path), approval_id, str(changes_path)]
    exit_code, stdout, stderr = _run(argv, timeout=timeout, input_text="apply\n", env=env)
    pr_match = _PR_URL_RE.search(stdout)
    ok = exit_code == 0 and pr_match is not None
    return ApplyOutcome(
        apply=StageResult(stage="apply", ok=ok, stdout=stdout, stderr=stderr, exit_code=exit_code, artifact=pr_match.group(0) if pr_match else None),
        pr_url=pr_match.group(0) if pr_match else None,
    )
