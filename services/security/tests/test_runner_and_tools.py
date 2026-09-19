import json
import subprocess
from pathlib import Path

import pytest

from security_scan import Finding, Waiver, WaiverError, apply_waivers, load_waivers
from security_scan import external
from security_scan.runner import ScanReport, run_scan

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_finding_rejects_an_unknown_severity():
    with pytest.raises(ValueError):
        Finding("X", "catastrophic", "loc", "msg")


def test_waiver_requires_a_reason(tmp_path):
    path = tmp_path / "w.json"
    path.write_text(json.dumps([{"rule": "AUTH-x", "reason": "  "}]))
    with pytest.raises(WaiverError):
        load_waivers(path)
    path.write_text(json.dumps([{"reason": "no rule"}]))
    with pytest.raises(WaiverError):
        load_waivers(path)


def test_missing_waiver_file_means_no_waivers(tmp_path):
    assert load_waivers(tmp_path / "absent.json") == []


def test_waiver_matches_rule_and_optional_location():
    findings = [Finding("A", "high", "x/one", "m"), Finding("A", "high", "y/two", "m"), Finding("B", "high", "x/one", "m")]
    active, waived = apply_waivers(findings, [Waiver("A", "because", "x/")])
    assert [f.location for f in active] == ["y/two", "x/one"]
    assert [(f.rule, w.reason) for f, w in waived] == [("A", "because")]


def test_report_blocks_on_high_and_critical_only():
    assert ScanReport([Finding("A", "medium", "l", "m"), Finding("B", "low", "l", "m")], []).passed
    assert not ScanReport([Finding("A", "high", "l", "m")], []).passed
    assert not ScanReport([Finding("A", "critical", "l", "m")], []).passed


def test_report_renders_findings_worst_first_and_shows_waivers():
    text = ScanReport([Finding("LOW1", "low", "a", "m"), Finding("CRIT", "critical", "b", "m")], [(Finding("W", "high", "c", "m"), Waiver("W", "documented"))]).render()
    assert text.index("CRIT") < text.index("LOW1")
    assert "WAIVED" in text and "documented" in text and text.rstrip().endswith("1 waived")


BANDIT_JSON = json.dumps({"results": [{"test_id": "B602", "issue_severity": "HIGH", "issue_text": "shell=True", "filename": "a\\b.py", "line_number": 7}]})
PIP_AUDIT_JSON = json.dumps({"dependencies": [{"name": "requests", "version": "2.0.0", "vulns": [{"id": "PYSEC-1", "fix_versions": ["2.1.0"]}]}, {"name": "ok", "version": "1", "vulns": []}]})


def test_parse_bandit_maps_severity_and_normalizes_paths():
    assert external.parse_bandit(BANDIT_JSON) == [Finding("SAST-B602", "high", "a/b.py:7", "shell=True")]


def test_parse_pip_audit_reports_only_vulnerable_dependencies():
    findings = external.parse_pip_audit(PIP_AUDIT_JSON)
    assert [(f.rule, f.location) for f in findings] == [("DEP-PYSEC-1", "requests==2.0.0")]
    assert "2.1.0" in findings[0].message


@pytest.mark.parametrize("runner_name, rule", [("run_bandit", "SCAN-bandit-failed"), ("run_pip_audit", "SCAN-pip-audit-failed")])
@pytest.mark.parametrize("failure", [OSError("missing"), subprocess.TimeoutExpired("x", 1), ValueError("bad json")])
def test_a_tool_that_cannot_run_is_a_blocking_finding(monkeypatch, tmp_path, runner_name, rule, failure):
    def boom(*args, **kwargs):
        raise failure

    monkeypatch.setattr(external, "_run", boom)
    findings = getattr(external, runner_name)(tmp_path)
    assert [(f.rule, f.severity) for f in findings] == [(rule, "high")]


def test_this_repository_passes_its_own_gate(tmp_path):
    """Regression gate: any new unwaived high/critical finding in tracked
    files, auth config, or history fails the commit. External tools are
    skipped here (network); CI runs them."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from ui.pipeline import redact_secrets

    report = run_scan(
        REPO_ROOT,
        approvals_path=tmp_path / "approvals.jsonl",
        changes_path=tmp_path / "changes.jsonl",
        waivers=load_waivers(REPO_ROOT / "security-waivers.json"),
        redactor=redact_secrets,
        include_external=False,
    )
    assert report.passed, report.render()
