"""Unit tests for ui/pipeline.py's output parsers. These test the
deterministic parsing logic against realistic, hand-built stdout samples
matching scripts/diagnose.py's and scripts/generate_dockerfile.py's own
print statements exactly - no subprocess, no Docker, no network, no
Claude Code call. Run with: .venv/Scripts/python -m pytest ui/test_pipeline.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import (  # noqa: E402
    _DIAGNOSE_ACTIONS_RE,
    _DIAGNOSE_SUMMARY_RE,
    _DIAGNOSE_VERIFY_RE,
    redact_secrets,
    validate_target_repo,
    RepoTargetError,
)

DIAGNOSE_WITH_WARNINGS = """cost: $0.0400
summary: The checkout service is throwing 500s due to a missing env var.
I checked the deployment config and found DATABASE_URL is unset in the
staging environment.
proposed actions:
  - Set DATABASE_URL in the staging deployment config to the correct connection string
  - Restart the checkout-service deployment

verifier warnings (persisted, review before deciding):
  - [contains a bracketed placeholder] Set DATABASE_URL in the staging deployment config to the correct connection string

approval id: abc-123
To decide: scripts/decide.py approvals.jsonl abc-123 approve|reject <your-name>
"""

DIAGNOSE_NO_WARNINGS = """cost: $0.0250
summary: Multi-line summary
with a second line here.
proposed actions:
  - Do the one obvious thing

approval id: xyz-789
To decide: scripts/decide.py approvals.jsonl xyz-789 approve|reject <your-name>
"""


def test_diagnose_summary_captures_multiline_text():
    match = _DIAGNOSE_SUMMARY_RE.search(DIAGNOSE_WITH_WARNINGS)
    assert match.group(1) == (
        "The checkout service is throwing 500s due to a missing env var.\n"
        "I checked the deployment config and found DATABASE_URL is unset in the\n"
        "staging environment."
    )


def test_diagnose_summary_stops_before_proposed_actions_marker():
    match = _DIAGNOSE_SUMMARY_RE.search(DIAGNOSE_NO_WARNINGS)
    assert match.group(1) == "Multi-line summary\nwith a second line here."
    assert "proposed actions" not in match.group(1)


def test_diagnose_actions_captures_all_numbered_items():
    match = _DIAGNOSE_ACTIONS_RE.search(DIAGNOSE_WITH_WARNINGS)
    assert "Set DATABASE_URL" in match.group(1)
    assert "Restart the checkout-service deployment" in match.group(1)


def test_diagnose_actions_stops_before_verify_warnings_when_present():
    match = _DIAGNOSE_ACTIONS_RE.search(DIAGNOSE_WITH_WARNINGS)
    assert "verifier warnings" not in match.group(1)


def test_diagnose_actions_stops_before_approval_id_when_no_warnings():
    match = _DIAGNOSE_ACTIONS_RE.search(DIAGNOSE_NO_WARNINGS)
    assert match.group(1) == "  - Do the one obvious thing"
    assert "approval id" not in match.group(1)


def test_diagnose_verify_extracts_warnings_when_present():
    match = _DIAGNOSE_VERIFY_RE.search(DIAGNOSE_WITH_WARNINGS)
    assert "bracketed placeholder" in match.group(1)


def test_diagnose_verify_absent_when_no_warnings_section():
    assert _DIAGNOSE_VERIFY_RE.search(DIAGNOSE_NO_WARNINGS) is None


def test_redact_secrets_matches_github_and_aws_patterns():
    text = "token=ghp_abcdefghijklmnopqrstuvwxyz123456 and github_pat_11ABCDEFabcdef01234567890abcdefghijklmnop and AKIAABCDEFGHIJKLMNOP and aws_secret_access_key=xyz"
    redacted = redact_secrets(text)
    assert "ghp_" not in redacted
    assert "github_pat_" not in redacted
    assert "AKIA" not in redacted
    assert "aws_secret" not in redacted.lower() or "[REDACTED]" in redacted


def test_redact_secrets_handles_none_and_empty():
    assert redact_secrets(None) == ""
    assert redact_secrets("") == ""


def test_validate_target_repo_accepts_owner_slash_repo():
    validate_target_repo("ematobi1/devops-agent")  # must not raise


def test_validate_target_repo_rejects_malformed_input():
    for bad in ("not-a-repo-shape", "", "/leadingslash", "trailing/"):
        try:
            validate_target_repo(bad)
            raised = False
        except RepoTargetError:
            raised = True
        assert raised, f"expected RepoTargetError for {bad!r}"
