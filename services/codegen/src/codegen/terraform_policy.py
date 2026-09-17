"""Terraform config generation policy.

SCAFFOLDING - see kubernetes_policy.py's module docstring for the full
reasoning (same status: not wired into any script or prompt, never run
against a real Terraform-managed target).

Validation here is text-pattern based (regex), not a full HCL parser -
deliberately mirrors codegen.dockerfile's own approach to Dockerfiles
(regex-based structural checks, not a full Dockerfile AST) rather than
adding a new dependency on an HCL parser (python-hcl2 exists but has far
less real-world mileage than PyYAML, and there is no real .tf file yet to
prove it's even the right tool - "search before building" cuts against
adding it speculatively). Revisit if a real target's actual .tf files
turn out to need something a regex can't reliably see.
"""

import re
from dataclasses import dataclass, field

from .dockerfile import Finding


@dataclass(frozen=True)
class TerraformConfigPolicy:
    allowed_resource_types: frozenset[str]  # e.g. {"aws_instance", "aws_s3_bucket"}
    required_provider: str  # e.g. "aws" - the one provider policy allows
    required_tags: dict[str, str] = field(default_factory=dict)
    disallow_hardcoded_secrets: bool = True


TERRAFORM_POLICIES: dict[str, TerraformConfigPolicy] = {
    # Deliberately empty - see module docstring.
}


class UnknownTerraformTypeError(KeyError):
    pass


def resolve_terraform_policy(config_type: str) -> TerraformConfigPolicy:
    try:
        return TERRAFORM_POLICIES[config_type]
    except KeyError:
        raise UnknownTerraformTypeError(
            f"no configured policy for config_type={config_type!r} - add one to TERRAFORM_POLICIES against a "
            f"real target, not speculatively. Configured types: {sorted(TERRAFORM_POLICIES)}"
        ) from None


_RESOURCE_BLOCK = re.compile(r'resource\s+"([\w]+)"\s+"([\w-]+)"\s*\{')
_PROVIDER_BLOCK = re.compile(r'provider\s+"([\w]+)"\s*\{')
_POSSIBLE_SECRET = re.compile(r'(?:password|secret|token|api_key|access_key)\s*=\s*"(?!\$\{)[^"]{4,}"', re.IGNORECASE)


def check_terraform_policy(config_text: str, *, policy: TerraformConfigPolicy) -> list[Finding]:
    """Deterministic regex-based checks against `policy`. Untested against
    any real generated Terraform config - see module docstring."""
    findings: list[Finding] = []

    for resource_type, resource_name in _RESOURCE_BLOCK.findall(config_text):
        if resource_type not in policy.allowed_resource_types:
            findings.append(Finding(reason=f"resource type {resource_type!r} ({resource_name!r}) is not in the configured allowlist"))

    providers_found = _PROVIDER_BLOCK.findall(config_text)
    if providers_found and policy.required_provider not in providers_found:
        findings.append(Finding(reason=f"no provider {policy.required_provider!r} block found (found: {providers_found})"))

    if policy.disallow_hardcoded_secrets:
        for match in _POSSIBLE_SECRET.finditer(config_text):
            findings.append(Finding(reason=f"possible hardcoded secret: {match.group(0)[:60]!r}"))

    for tag_key in policy.required_tags:
        # HCL map keys are written bare (ManagedBy = "x"), not quoted like
        # a resource type/name - matching a literal quoted string here
        # would never find a real tag block.
        if not re.search(rf'\b{re.escape(tag_key)}\s*=', config_text):
            findings.append(Finding(reason=f"required tag key {tag_key!r} not found anywhere in config"))

    return findings
