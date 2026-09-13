from unittest.mock import patch

import pytest

from verifier.pr_diff import (
    FetchError,
    PullRequestRef,
    check_diff,
    fetch_pr_diff_and_state,
    find_pr_reference,
)

REAL_DETAIL_TEXT = (
    "Done. Executed exactly the approved plan:\n\n"
    "4. Applied the edit via a PR, not a direct push or merge: "
    "[PR #2](https://github.com/ematobi1/AI-AGENT-DEVOPS-ENGINEER-MCP-AGENT/pull/2), left open for normal review."
)

CLEAN_DIFF = (
    "diff --git a/README.md b/README.md\n"
    "index 04f97bc..0732b00 100644\n"
    "--- a/README.md\n"
    "+++ b/README.md\n"
    "@@ -1,4 +1,12 @@\n"
    " # title\n"
    "+new line\n"
)

DELETION_DIFF = (
    "diff --git a/old.txt b/old.txt\n"
    "deleted file mode 100644\n"
    "index abc1234..0000000\n"
    "--- a/old.txt\n"
    "+++ /dev/null\n"
)


def test_find_pr_reference_extracts_owner_repo_number():
    ref = find_pr_reference(REAL_DETAIL_TEXT)
    assert ref == PullRequestRef(owner="ematobi1", repo="AI-AGENT-DEVOPS-ENGINEER-MCP-AGENT", number=2)


def test_find_pr_reference_returns_none_when_absent():
    assert find_pr_reference("Investigated the issue, no PR was needed.") is None


def test_find_pr_reference_takes_the_first_of_multiple_urls():
    text = (
        "See https://github.com/acme/repo/pull/1 for context, "
        "opened https://github.com/acme/repo/pull/2 for the fix."
    )
    assert find_pr_reference(text).number == 1


def test_check_diff_clean_diff_has_no_findings():
    assert check_diff(CLEAN_DIFF) == []


def test_check_diff_flags_empty_diff():
    findings = check_diff("")
    assert len(findings) == 1
    assert "empty" in findings[0].reason


def test_check_diff_flags_whitespace_only_diff_as_empty():
    findings = check_diff("   \n  \n")
    assert len(findings) == 1
    assert "empty" in findings[0].reason


def test_check_diff_flags_file_deletion():
    findings = check_diff(DELETION_DIFF)
    assert len(findings) == 1
    assert "deletes a file" in findings[0].reason


def test_check_diff_does_not_flag_pr_state_at_all():
    """Regression test: an earlier version flagged any non-OPEN PR state,
    but a human merging an applied PR after review is normal and expected,
    not a violation - found via a real run against an actually-merged PR.
    check_diff() takes no pr_state argument at all now."""
    import inspect

    assert "pr_state" not in inspect.signature(check_diff).parameters


def test_check_diff_can_return_multiple_findings():
    empty_deletion_diff = "diff --git a/old.txt b/old.txt\ndeleted file mode 100644\n"
    findings = check_diff(empty_deletion_diff)
    assert len(findings) == 1  # non-empty (has the deletion marker text), so only the deletion finding
    assert "deletes a file" in findings[0].reason


class FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_fetch_pr_diff_and_state_returns_diff_and_state():
    ref = PullRequestRef(owner="acme", repo="repo", number=5)
    with patch("verifier.pr_diff.subprocess.run") as mock_run:
        mock_run.side_effect = [
            FakeCompletedProcess(stdout=CLEAN_DIFF),
            FakeCompletedProcess(stdout="OPEN\n"),
        ]
        diff_text, state = fetch_pr_diff_and_state(ref)
    assert diff_text == CLEAN_DIFF
    assert state == "OPEN"


def test_fetch_pr_diff_and_state_raises_on_diff_command_failure():
    ref = PullRequestRef(owner="acme", repo="repo", number=5)
    with patch("verifier.pr_diff.subprocess.run") as mock_run:
        mock_run.return_value = FakeCompletedProcess(stderr="not found", returncode=1)
        with pytest.raises(FetchError, match="gh pr diff failed"):
            fetch_pr_diff_and_state(ref)


def test_fetch_pr_diff_and_state_raises_on_view_command_failure():
    ref = PullRequestRef(owner="acme", repo="repo", number=5)
    with patch("verifier.pr_diff.subprocess.run") as mock_run:
        mock_run.side_effect = [
            FakeCompletedProcess(stdout=CLEAN_DIFF),
            FakeCompletedProcess(stderr="auth error", returncode=1),
        ]
        with pytest.raises(FetchError, match="gh pr view failed"):
            fetch_pr_diff_and_state(ref)
