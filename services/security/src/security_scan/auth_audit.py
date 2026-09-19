"""Static audit of every authentication and credential-handling path in the
repo: MCP server credentials, the web UI's request authentication, container
credential exposure, CI token scope, and the secret redactor."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from pathlib import Path

from .findings import Finding
from .secrets_scan import SECRET_PATTERNS, synthetic_credentials

CREDENTIAL_KEY = re.compile(r"(?i)(token|secret|password|api[_-]?key|credential)")
WHOLE_VAR = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
DOCKER_VALUE_FLAGS = frozenset({"-e", "--env", "-v", "--volume", "-p", "--publish", "--name", "--network", "-w", "-u", "--user", "--platform"})
STATE_CHANGING_METHODS = frozenset({"post", "put", "patch", "delete"})
SENSITIVE_MOUNT = re.compile(r"-v\s+\S*(docker\.sock|\.claude(?:\.json)?|\.aws|\.ssh|\.kube)\S*")
UNBOUND_PUBLISH = re.compile(r"-p\s+(?!(?:127\.0\.0\.1|localhost|\[::1\]):)(\d+):\d+")


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _docker_image(args: list[str]) -> str | None:
    if "run" not in args:
        return None
    skip_next = False
    for arg in args[args.index("run") + 1:]:
        if skip_next:
            skip_next = False
        elif arg in DOCKER_VALUE_FLAGS:
            skip_next = True
        elif not arg.startswith("-"):
            return arg
    return None


def _image_is_pinned(image: str) -> bool:
    if "@sha256:" in image:
        return True
    tag = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else ""
    return bool(tag) and tag != "latest"


def audit_mcp_templates(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for template in sorted(repo_root.glob(".mcp*.json.template")):
        where = template.name
        servers = json.loads(template.read_text(encoding="utf-8")).get("mcpServers", {})
        for name, server in servers.items():
            args: list[str] = [str(a) for a in server.get("args", [])]
            command = server.get("command", "")
            location = f"{where}:{name}"

            for key, value in server.get("env", {}).items():
                if CREDENTIAL_KEY.search(key) and not WHOLE_VAR.match(str(value)):
                    findings.append(Finding("AUTH-literal-credential-in-template", "critical", location, f"{key} holds a literal value, not a ${{VAR}} placeholder"))

            if command == "docker":
                image = _docker_image(args)
                if image and not _image_is_pinned(image):
                    findings.append(Finding("AUTH-unpinned-mcp-image", "high", location, f"{image} has no version tag or digest; the process that receives the credential can change under you"))
                if image and "github-mcp-server" in image and not any("--read-only" in a or "READ_ONLY_FLAG" in a for a in args):
                    findings.append(Finding("AUTH-read-only-not-enforced", "high", location, "GitHub MCP server has no --read-only flag; the GITHUB_READ_ONLY env var is ignored by v1.12.1 (docs/ARCHITECTURE.md), so this server is write-capable whenever the token is"))
            elif command in ("uvx", "npx"):
                package = next((a for a in args if not a.startswith("-")), "")
                if "@latest" in package or not re.search(r"(@|==)\d", package):
                    findings.append(Finding("AUTH-unpinned-mcp-package", "high", location, f"{package} is not pinned to an exact version; a compromised release runs with your credentials"))
    return findings


def _route_functions(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    routes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr in STATE_CHANGING_METHODS | {"get"}:
                    routes.append((node, decorator.func.attr))
    return routes


def _has_auth_dependency(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) in ("Depends", "Security") for n in ast.walk(node))


def audit_web_ui(repo_root: Path) -> list[Finding]:
    source = _read(repo_root / "ui" / "app.py")
    if source is None:
        return []
    tree = ast.parse(source)
    findings: list[Finding] = []

    unauthenticated = [n.name for n, method in _route_functions(tree) if method in STATE_CHANGING_METHODS and not _has_auth_dependency(n)]  # type: ignore[attr-defined]
    if unauthenticated:
        findings.append(Finding("AUTH-ui-no-authentication", "high", "ui/app.py", f"state-changing routes with no auth dependency: {', '.join(unauthenticated)}; anyone who can reach the port can start runs and trigger a write-scoped apply"))

    if not re.search(r"TrustedHostMiddleware|allowed_hosts", source):
        findings.append(Finding("AUTH-ui-no-host-validation", "medium", "ui/app.py", "no Host header validation; a DNS-rebinding page can drive the localhost API"))
    if not re.search(r"(?i)\borigin\b", source):
        findings.append(Finding("AUTH-ui-no-origin-check", "medium", "ui/app.py", "no Origin check on state-changing requests (CSRF from any page open in the same browser)"))

    handlers = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for handler in handlers:
        names = {n.attr for n in ast.walk(handler) if isinstance(n, ast.Attribute)}
        if {"run_approve", "run_apply"} <= names:
            findings.append(Finding("AUTH-ui-approve-and-apply-collapsed", "medium", f"ui/app.py:{handler.name}", "one click approves and applies; docs/ARCHITECTURE.md decided approve and apply are two separate human actions"))

    if re.search(r"decided_by\s*:\s*str", source):
        findings.append(Finding("AUTH-ui-client-supplied-identity", "medium", "ui/app.py", "decided_by comes from the request body; the audit log records whatever the caller claims"))
    return findings


def audit_containers(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    dockerfiles = [p for p in repo_root.rglob("Dockerfile") if ".venv" not in p.parts and ".git" not in p.parts]
    dockerignore = _read(repo_root / ".dockerignore") or ""
    for dockerfile in dockerfiles:
        text = dockerfile.read_text(encoding="utf-8")
        where = dockerfile.relative_to(repo_root).as_posix()
        stages = re.findall(r"(?im)^FROM\s+(\S+)", text)
        for image in stages:
            if image.endswith(":latest") or (":" not in image and "@" not in image and image != "scratch" and not image.startswith("--")):
                findings.append(Finding("AUTH-container-unpinned-base", "high", where, f"base image {image} is not pinned to a version"))
        final_stage = text[text.upper().rfind("\nFROM "):] if "\nFROM " in text.upper() else text
        if not re.search(r"(?im)^USER\s+(?!root\b)\S+", final_stage):
            findings.append(Finding("AUTH-container-runs-as-root", "medium", where, "final stage has no non-root USER"))
        for install in re.findall(r"npm install -g (\S+)", text):
            if not re.search(r"@\d", install):
                findings.append(Finding("AUTH-container-unpinned-install", "medium", where, f"npm install -g {install} is not pinned to a version"))
        if re.search(r"--host[\"',\s]+0\.0\.0\.0", text):
            findings.append(Finding("AUTH-container-binds-all-interfaces", "medium", where, "server binds 0.0.0.0 inside the container; safe only if the published port is bound to 127.0.0.1"))
        if re.search(r"(?m)^COPY\s+\.\s", text):
            for needed in ("*.jsonl", "mcp-config-*.json", ".env"):
                if needed not in dockerignore:
                    findings.append(Finding("AUTH-dockerignore-gap", "medium", ".dockerignore", f"COPY . in {where} but .dockerignore does not exclude {needed}"))
        for mount in SENSITIVE_MOUNT.findall(text):
            findings.append(Finding("AUTH-sensitive-mount-documented", "high", where, f"documents mounting {mount} into the container"))
        if UNBOUND_PUBLISH.search(text):
            findings.append(Finding("AUTH-published-on-all-interfaces", "high", where, "documented docker run publishes the port on every host interface (use -p 127.0.0.1:PORT:PORT)"))
    for readme in repo_root.rglob("README.md"):
        if ".venv" in readme.parts or ".git" in readme.parts:
            continue
        text = readme.read_text(encoding="utf-8")
        where = readme.relative_to(repo_root).as_posix()
        for mount in SENSITIVE_MOUNT.findall(text):
            findings.append(Finding("AUTH-sensitive-mount-documented", "high", where, f"documents mounting {mount} into the container"))
        if UNBOUND_PUBLISH.search(text):
            findings.append(Finding("AUTH-published-on-all-interfaces", "high", where, "documented docker run publishes the port on every host interface"))
    return findings


def audit_ci(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for workflow in sorted((repo_root / ".github" / "workflows").glob("*.y*ml")):
        text = workflow.read_text(encoding="utf-8")
        where = f".github/workflows/{workflow.name}"
        if not re.search(r"(?m)^permissions:", text):
            findings.append(Finding("AUTH-ci-default-token-permissions", "medium", where, "no top-level permissions block; the job token gets the repository default scope"))
        if "pull_request_target" in text:
            findings.append(Finding("AUTH-ci-pull-request-target", "high", where, "pull_request_target runs untrusted PR code with repository secrets"))
        for action in re.findall(r"uses:\s*(\S+)", text):
            if not re.search(r"@[0-9a-f]{40}$", action):
                findings.append(Finding("AUTH-ci-action-not-sha-pinned", "low", where, f"{action} is pinned to a movable tag"))
    return findings


def audit_approval_flow(repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    apply_source = _read(repo_root / "scripts" / "apply.py") or ""
    if apply_source and "applied_by" not in apply_source:
        findings.append(Finding("AUTH-apply-not-attributed", "medium", "scripts/apply.py", "change records are written without applied_by"))
    decide_source = _read(repo_root / "scripts" / "decide.py") or ""
    if decide_source and "proposed_actions" not in decide_source:
        findings.append(Finding("AUTH-decide-shows-no-proposal", "medium", "scripts/decide.py", "the approver is shown verifier warnings but not the actions or payloads they are approving"))
    return findings


def audit_redactor(redact: Callable[[str], str]) -> list[Finding]:
    """Feeds one synthetic credential per detection rule through `redact` and
    reports every format it lets through. Samples are assembled at runtime so
    this file never contains a scannable secret."""
    samples = synthetic_credentials()
    findings = []
    for rule, sample in samples.items():
        assert SECRET_PATTERNS[rule].search(sample), f"sample for {rule} does not match its own detector"
        if sample in redact(f"prefix {sample} suffix"):
            findings.append(Finding("AUTH-redactor-gap", "medium", "ui/pipeline.py:redact_secrets", f"{rule} shaped values pass through the redactor unmasked"))
    return findings


def audit_auth(repo_root: Path, redactor: Callable[[str], str] | None = None) -> list[Finding]:
    findings = audit_mcp_templates(repo_root) + audit_web_ui(repo_root) + audit_containers(repo_root) + audit_ci(repo_root) + audit_approval_flow(repo_root)
    if redactor is not None:
        findings += audit_redactor(redactor)
    return findings
