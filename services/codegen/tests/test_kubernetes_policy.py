import pytest

from codegen.kubernetes_policy import (
    K8S_POLICIES,
    KubernetesManifestPolicy,
    UnknownManifestTypeError,
    check_manifest_policy,
    resolve_manifest_policy,
)

POLICY = KubernetesManifestPolicy(
    allowed_kinds=frozenset({"Deployment", "Service"}),
    api_versions={"Deployment": "apps/v1", "Service": "v1"},
    namespace="team-a",
    required_labels={"app.kubernetes.io/managed-by": "devops-agent"},
)

GOOD_DEPLOYMENT = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: team-a
  labels:
    app.kubernetes.io/managed-by: devops-agent
spec:
  template:
    spec:
      containers:
        - name: web
          image: example/web:1.2.3
          imagePullPolicy: IfNotPresent
          resources:
            limits:
              cpu: "500m"
              memory: "256Mi"
"""


def test_k8s_policies_registry_is_deliberately_empty():
    """Scaffolding, not a shipped feature - see kubernetes_policy.py's
    module docstring. This test is a tripwire: if an entry gets added, the
    docstring's "deliberately empty" claim needs updating alongside it,
    and the ARCHITECTURE.md note about it should be revisited too."""
    assert K8S_POLICIES == {}


def test_resolve_unknown_manifest_type_raises():
    with pytest.raises(UnknownManifestTypeError, match="no configured policy"):
        resolve_manifest_policy("anything")


def test_policy_rejects_allowed_kind_missing_api_version_entry():
    with pytest.raises(ValueError, match="have no configured api_versions entry"):
        KubernetesManifestPolicy(allowed_kinds=frozenset({"Deployment"}), api_versions={}, namespace="x")


def test_clean_manifest_has_no_findings():
    assert check_manifest_policy(GOOD_DEPLOYMENT, policy=POLICY) == []


def test_disallowed_kind_is_flagged():
    content = GOOD_DEPLOYMENT.replace("kind: Deployment", "kind: DaemonSet")
    findings = check_manifest_policy(content, policy=POLICY)
    assert any("not in the configured allowlist" in f.reason for f in findings)


def test_wrong_api_version_is_flagged():
    content = GOOD_DEPLOYMENT.replace("apiVersion: apps/v1", "apiVersion: apps/v1beta1")
    findings = check_manifest_policy(content, policy=POLICY)
    assert any("apiVersion" in f.reason for f in findings)


def test_wrong_namespace_is_flagged():
    content = GOOD_DEPLOYMENT.replace("namespace: team-a", "namespace: default")
    findings = check_manifest_policy(content, policy=POLICY)
    assert any("namespace" in f.reason for f in findings)


def test_missing_required_label_is_flagged():
    content = GOOD_DEPLOYMENT.replace("    app.kubernetes.io/managed-by: devops-agent\n", "")
    findings = check_manifest_policy(content, policy=POLICY)
    assert any("missing required label" in f.reason for f in findings)


def test_missing_resource_limits_is_flagged():
    content = "\n".join(
        line for line in GOOD_DEPLOYMENT.splitlines() if "resources" not in line and "limits" not in line and "cpu" not in line and "memory" not in line
    )
    findings = check_manifest_policy(content, policy=POLICY)
    assert any("has no resources.limits" in f.reason for f in findings)


def test_resource_limits_not_required_when_policy_says_so():
    lenient = KubernetesManifestPolicy(
        allowed_kinds=frozenset({"Deployment"}), api_versions={"Deployment": "apps/v1"}, namespace="team-a", require_resource_limits=False
    )
    content = "\n".join(line for line in GOOD_DEPLOYMENT.splitlines() if "resources" not in line and "limits" not in line and "cpu" not in line and "memory" not in line)
    assert check_manifest_policy(content, policy=lenient) == []


def test_wrong_image_pull_policy_is_flagged():
    content = GOOD_DEPLOYMENT.replace("imagePullPolicy: IfNotPresent", "imagePullPolicy: Always")
    findings = check_manifest_policy(content, policy=POLICY)
    assert any("imagePullPolicy" in f.reason for f in findings)


def test_multi_document_manifest_checks_each_document():
    content = GOOD_DEPLOYMENT + "---\napiVersion: v1\nkind: Service\nmetadata:\n  name: web\n  namespace: wrong-ns\n"
    findings = check_manifest_policy(content, policy=POLICY)
    assert any("Service" in f.reason and "namespace" in f.reason for f in findings)


def test_invalid_yaml_is_reported_not_raised():
    findings = check_manifest_policy("kind: [this is not: valid yaml", policy=POLICY)
    assert len(findings) == 1
    assert "not valid YAML" in findings[0].reason
