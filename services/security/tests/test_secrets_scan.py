import subprocess
from pathlib import Path

import pytest

from security_scan.secrets_scan import SECRET_PATTERNS, scan_history, scan_text, scan_tracked_files, synthetic_credentials


@pytest.mark.parametrize("rule", sorted(SECRET_PATTERNS))
def test_every_detector_matches_its_synthetic_credential(rule):
    findings = scan_text(f"value = {synthetic_credentials()[rule]}", "f.py")
    assert any(f.rule == f"SEC-{rule}" for f in findings)


def test_finding_never_contains_the_secret_value():
    secret = synthetic_credentials()["github-token"]
    findings = scan_text(secret, "f.py")
    assert findings and all(secret not in f.message and secret not in f.location for f in findings)


def test_reports_line_number():
    findings = scan_text("clean\nclean\n" + synthetic_credentials()["aws-access-key-id"], "a.txt")
    assert findings[0].location == "a.txt:3"


def test_clean_text_has_no_findings():
    assert scan_text("token = os.environ['GITHUB_PERSONAL_ACCESS_TOKEN']\nAKIA is a prefix\n", "a.py") == []


def _git_repo(path: Path) -> Path:
    def git(*args):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    return path


def _commit(path: Path, message: str):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True, capture_output=True)


def test_scan_tracked_files_finds_secret_and_forbidden_files(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "config.py").write_text(f"KEY = '{synthetic_credentials()['anthropic-api-key']}'\n")
    (repo / ".env").write_text("X=1\n")
    (repo / "approvals.jsonl").write_text("{}\n")
    _commit(repo, "add")
    rules = {f.rule for f in scan_tracked_files(repo)}
    assert {"SEC-anthropic-api-key", "SEC-forbidden-tracked-file"} <= rules
    assert len([f for f in scan_tracked_files(repo) if f.rule == "SEC-forbidden-tracked-file"]) == 2


def test_scan_tracked_files_skips_binary_and_untracked(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"\0" + synthetic_credentials()["github-token"].encode())
    (repo / "ok.txt").write_text("fine")
    _commit(repo, "add")
    (repo / "untracked.txt").write_text(synthetic_credentials()["github-token"])
    assert scan_tracked_files(repo) == []


def test_history_finds_a_secret_that_was_later_removed(tmp_path):
    repo = _git_repo(tmp_path)
    secret_file = repo / "settings.py"
    secret_file.write_text(f"T = '{synthetic_credentials()['github-token']}'\n")
    _commit(repo, "oops")
    secret_file.write_text("T = None\n")
    _commit(repo, "remove")
    assert scan_tracked_files(repo) == []
    findings = scan_history(repo)
    assert [f.rule for f in findings] == ["SEC-history-github-token"]
    assert findings[0].location.startswith("settings.py@")
