"""Kubernetes manifest generation policy.

SCAFFOLDING - not wired into any script, prompt, or the diagnose/decide/
verify/apply flow, and never run against a real cluster or a real
generated manifest. See docs/ARCHITECTURE.md "Kubernetes/Terraform
codegen scaffolding" for why this stops at the policy+check layer: every
real fix in this codebase's Dockerfile codegen history came from testing
against a real target (a real repo, a real docker build, a real PR) -
building the generation prompt and the orchestrator wiring for Kubernetes
before a real cluster or manifest repo exists to validate against would
repeat the exact mistake this project has otherwise avoided (see the
base-image/CMD/HEALTHCHECK regressions found via live sampling, and the
WORKDIR/non-root bugs found only by an actual `docker build`+`run`).

What's real here: the policy dataclass and the deterministic structural
checker (check_manifest_policy) - both are ordinary, tested Python, ready
to use the moment a real target exists. What's not real: K8S_POLICIES has
no entries (adding one is a decision to make against an actual target,
not in advance), and nothing generates a manifest or talks to a cluster.

Same operator-policy-vs-model-discretion split as codegen.base_images:
whatever shouldn't vary per manifest (allowed resource kinds, the
apiVersion each kind must use, namespace, labels every resource must
carry, pull policy, whether resource limits are mandatory) is policy,
fixed by the operator - the same reasoning that moved FROM/CMD/HEALTHCHECK
out of the Dockerfile-generation prompt after live sampling showed the
model regressing on exactly this kind of decision. What would genuinely
vary per target (resource name, image reference, replica count, container
port, env var names) is where a generation prompt would eventually narrow
the model's job to, the same way build_generation_prompt() narrows it to
COPY (and RUN, for a CLI-shaped app) rather than the whole Dockerfile -
but no such prompt exists yet, on purpose.
"""

from dataclasses import dataclass, field

from .dockerfile import Finding


@dataclass(frozen=True)
class KubernetesManifestPolicy:
    allowed_kinds: frozenset[str]
    api_versions: dict[str, str]  # kind -> the one apiVersion policy allows for it
    namespace: str
    required_labels: dict[str, str] = field(default_factory=dict)
    image_pull_policy: str = "IfNotPresent"
    require_resource_limits: bool = True

    def __post_init__(self) -> None:
        missing = self.allowed_kinds - set(self.api_versions)
        if missing:
            raise ValueError(f"allowed_kinds {sorted(missing)} have no configured api_versions entry")


K8S_POLICIES: dict[str, KubernetesManifestPolicy] = {
    # Deliberately empty. Add an entry only once a real target cluster or
    # manifest repo exists to validate it against - see the module
    # docstring, and docs/ARCHITECTURE.md for the shape a first real entry
    # would take (modeled on a hypothetical target, explicitly not one).
}


class UnknownManifestTypeError(KeyError):
    pass


def resolve_manifest_policy(manifest_type: str) -> KubernetesManifestPolicy:
    try:
        return K8S_POLICIES[manifest_type]
    except KeyError:
        raise UnknownManifestTypeError(
            f"no configured policy for manifest_type={manifest_type!r} - add one to K8S_POLICIES against a "
            f"real target, not speculatively. Configured types: {sorted(K8S_POLICIES)}"
        ) from None


def check_manifest_policy(manifest_yaml: str, *, policy: KubernetesManifestPolicy) -> list[Finding]:
    """Deterministic structural checks against `policy` - parses real YAML
    (a Kubernetes manifest is genuinely structured data, unlike a
    Dockerfile, so this uses a real parser rather than codegen.dockerfile's
    regex-based approach). Mirrors check_dockerfile_policy()'s shape and
    "annotate, not block" philosophy. Untested against any real generated
    manifest - see module docstring."""
    import yaml

    try:
        docs = [doc for doc in yaml.safe_load_all(manifest_yaml) if doc]
    except yaml.YAMLError as exc:
        return [Finding(reason=f"not valid YAML: {exc}")]

    findings: list[Finding] = []
    for doc in docs:
        kind = doc.get("kind")
        if kind not in policy.allowed_kinds:
            findings.append(Finding(reason=f"kind {kind!r} is not in the configured allowlist"))
            continue

        api_version = doc.get("apiVersion")
        expected_api_version = policy.api_versions.get(kind)
        if expected_api_version and api_version != expected_api_version:
            findings.append(Finding(reason=f"{kind} apiVersion {api_version!r} does not match policy {expected_api_version!r}"))

        metadata = doc.get("metadata") or {}
        namespace = metadata.get("namespace")
        if namespace != policy.namespace:
            findings.append(Finding(reason=f"{kind} namespace {namespace!r} does not match policy {policy.namespace!r}"))

        labels = metadata.get("labels") or {}
        for label_key, label_value in policy.required_labels.items():
            if labels.get(label_key) != label_value:
                findings.append(Finding(reason=f"{kind} missing required label {label_key}={label_value!r}"))

        if kind in ("Deployment", "StatefulSet", "DaemonSet"):
            containers = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
            for container in containers:
                name = container.get("name", "?")
                if policy.require_resource_limits and not (container.get("resources") or {}).get("limits"):
                    findings.append(Finding(reason=f"container {name!r} in {kind} has no resources.limits"))
                pull_policy = container.get("imagePullPolicy", policy.image_pull_policy)
                if pull_policy != policy.image_pull_policy:
                    findings.append(
                        Finding(reason=f"container {name!r} in {kind} imagePullPolicy {pull_policy!r} does not match policy {policy.image_pull_policy!r}")
                    )

    return findings
