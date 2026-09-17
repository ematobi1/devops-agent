import pytest

from codegen.terraform_policy import (
    TERRAFORM_POLICIES,
    TerraformConfigPolicy,
    UnknownTerraformTypeError,
    check_terraform_policy,
    resolve_terraform_policy,
)

POLICY = TerraformConfigPolicy(
    allowed_resource_types=frozenset({"aws_s3_bucket", "aws_instance"}),
    required_provider="aws",
    required_tags={"ManagedBy": "devops-agent"},
)

GOOD_CONFIG = """
provider "aws" {
  region = "us-east-1"
}

resource "aws_s3_bucket" "data" {
  bucket = "example-data-bucket"
  tags = {
    ManagedBy = "devops-agent"
  }
}
"""


def test_terraform_policies_registry_is_deliberately_empty():
    """Scaffolding, not a shipped feature - see terraform_policy.py's
    module docstring. Tripwire: an added entry should come with an
    ARCHITECTURE.md update too."""
    assert TERRAFORM_POLICIES == {}


def test_resolve_unknown_config_type_raises():
    with pytest.raises(UnknownTerraformTypeError, match="no configured policy"):
        resolve_terraform_policy("anything")


def test_clean_config_has_no_findings():
    assert check_terraform_policy(GOOD_CONFIG, policy=POLICY) == []


def test_disallowed_resource_type_is_flagged():
    content = GOOD_CONFIG.replace('resource "aws_s3_bucket" "data"', 'resource "aws_iam_role" "data"')
    findings = check_terraform_policy(content, policy=POLICY)
    assert any("not in the configured allowlist" in f.reason for f in findings)


def test_missing_required_provider_is_flagged():
    content = GOOD_CONFIG.replace('provider "aws" {\n  region = "us-east-1"\n}\n\n', 'provider "gcp" {\n  project = "x"\n}\n\n')
    findings = check_terraform_policy(content, policy=POLICY)
    assert any("no provider" in f.reason for f in findings)


def test_hardcoded_secret_is_flagged():
    content = GOOD_CONFIG + '\nresource "aws_instance" "x" {\n  password = "supersecretvalue123"\n}\n'
    findings = check_terraform_policy(content, policy=POLICY)
    assert any("possible hardcoded secret" in f.reason for f in findings)


def test_interpolated_secret_reference_is_not_flagged():
    """A secret pulled from a variable/data source, e.g.
    `password = var.db_password` or an interpolation, is the correct
    pattern - only a literal string value should be flagged."""
    content = GOOD_CONFIG + '\nresource "aws_instance" "x" {\n  password = "${var.db_password}"\n}\n'
    findings = check_terraform_policy(content, policy=POLICY)
    assert not any("secret" in f.reason for f in findings)


def test_missing_required_tag_is_flagged():
    content = GOOD_CONFIG.replace('    ManagedBy = "devops-agent"\n', "")
    findings = check_terraform_policy(content, policy=POLICY)
    assert any("required tag key" in f.reason for f in findings)


def test_multiple_resources_all_checked():
    content = GOOD_CONFIG + '\nresource "aws_iam_role" "bad" {\n  name = "x"\n}\n'
    findings = check_terraform_policy(content, policy=POLICY)
    assert len(findings) == 1
    assert "aws_iam_role" in findings[0].reason
