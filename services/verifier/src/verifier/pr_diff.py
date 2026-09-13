"""Post-apply diff verification: given a ChangeRecord produced by an apply
run, fetch the actual resulting PR diff and confirm basic structural
invariants hold. Formalizes the manual `gh pr diff`/`gh pr view` checks done
by hand throughout this project's own validation into a repeatable step.

Fetching a diff for a known PR number is deterministic I/O (a lookup, not a
judgment call), so this stays true to services/verifier's "no LLM call"
charter - same reasoning as orchestrator shelling out to `claude` for LLM
work and to `docker`/`uvx` for MCP servers: an external CLI dependency
(`gh`), not a Python package one, and not a reasoning task.

Deliberately does NOT judge whether the diff's *content* matches the
*intent* of the approved plan - that requires understanding meaning, the
same latent-space judgment call check_proposed_actions() already declined
to attempt for "is this actually a question." If that judgment is ever
needed, it belongs in a periodic, LLM-judged eval with a pass threshold -
a different test lane than this gate check, per the project's own
tests-and-evals split (see CLAUDE.md).
"""

import re
import subprocess
from dataclasses import dataclass

_PR_URL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int


def find_pr_reference(text: str) -> PullRequestRef | None:
    """Extracts the first GitHub PR URL found in `text` (typically a
    ChangeRecord's `detail` field, i.e. the apply run's own result text).
    Returns None if no PR URL is present - not every applied change opens
    one. If multiple PR URLs appear, only the first is used."""
    match = _PR_URL.search(text)
    if match is None:
        return None
    return PullRequestRef(owner=match.group(1), repo=match.group(2), number=int(match.group(3)))


@dataclass(frozen=True)
class Finding:
    reason: str


def check_diff(diff_text: str) -> list[Finding]:
    """Structural, deterministic checks on an already-fetched diff. Does not
    judge whether the diff's content matches the approved plan's intent -
    see module docstring.

    Deliberately does NOT check PR state (open/merged/closed). An earlier
    version flagged any non-"OPEN" state as a finding, reasoning that
    apply's tool allowlist (no merge_pull_request) should make it
    impossible - but tested live against a real applied PR that a human had
    since legitimately merged after review, which is the normal, expected
    lifecycle, not a violation of anything. A PR's terminal state is decided
    by subsequent human action, not by apply - there's no way to
    distinguish "apply somehow merged its own PR" (impossible given the
    tool allowlist, and not what happened here) from "a human merged it
    later" just by looking at current state, so the check was removed
    rather than kept producing false positives."""
    findings = []
    if not diff_text.strip():
        findings.append(Finding(reason="diff is empty - apply claimed success but nothing was actually changed"))
    if "deleted file mode" in diff_text:
        findings.append(Finding(reason="diff deletes a file - apply's tool allowlist should make this impossible"))
    return findings


class FetchError(RuntimeError):
    pass


def fetch_pr_diff_and_state(ref: PullRequestRef, *, timeout_seconds: int = 30) -> tuple[str, str]:
    """Shells out to the gh CLI - a known-PR lookup by number is
    deterministic I/O, not a task needing an LLM. Requires `gh`
    authenticated with read access to ref.owner/ref.repo."""
    repo_flag = f"{ref.owner}/{ref.repo}"

    diff_proc = subprocess.run(
        ["gh", "pr", "diff", str(ref.number), "--repo", repo_flag],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    if diff_proc.returncode != 0:
        raise FetchError(f"gh pr diff failed (exit {diff_proc.returncode}): {(diff_proc.stderr or '').strip()}")

    state_proc = subprocess.run(
        ["gh", "pr", "view", str(ref.number), "--repo", repo_flag, "--json", "state", "--jq", ".state"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    if state_proc.returncode != 0:
        raise FetchError(f"gh pr view failed (exit {state_proc.returncode}): {(state_proc.stderr or '').strip()}")

    return diff_proc.stdout or "", (state_proc.stdout or "").strip()
