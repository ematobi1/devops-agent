import json

from security_scan.auth_audit import audit_approval_flow, audit_ci, audit_containers, audit_mcp_templates, audit_redactor, audit_web_ui

PINNED_IMAGE = "ghcr.io/github/github-mcp-server:v1.12.1@sha256:" + "a" * 64


def rules(findings):
    return {f.rule for f in findings}


def write_template(root, servers, name=".mcp.test.json.template"):
    (root / name).write_text(json.dumps({"mcpServers": servers}))


def github_server(image=PINNED_IMAGE, read_only_arg="--read-only=true", env=None):
    return {
        "command": "docker",
        "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", image, "stdio", read_only_arg],
        "env": env or {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"},
    }


def test_hardened_github_template_is_clean(tmp_path):
    write_template(tmp_path, {"github": github_server()})
    assert audit_mcp_templates(tmp_path) == []


def test_placeholder_read_only_flag_counts_as_enforced(tmp_path):
    write_template(tmp_path, {"github": github_server(read_only_arg="${GITHUB_READ_ONLY_FLAG}")})
    assert audit_mcp_templates(tmp_path) == []


def test_env_var_read_only_is_not_enforcement(tmp_path):
    server = github_server()
    server["args"] = ["run", "-i", "--rm", "-e", "GITHUB_READ_ONLY=1", PINNED_IMAGE]
    write_template(tmp_path, {"github": server})
    assert "AUTH-read-only-not-enforced" in rules(audit_mcp_templates(tmp_path))


def test_unpinned_image_forms(tmp_path):
    for image in ("ghcr.io/github/github-mcp-server", "ghcr.io/github/github-mcp-server:latest"):
        write_template(tmp_path, {"github": github_server(image=image)})
        assert "AUTH-unpinned-mcp-image" in rules(audit_mcp_templates(tmp_path)), image


def test_image_after_value_flags_is_found_not_the_flag_value(tmp_path):
    write_template(tmp_path, {"github": github_server(image="ghcr.io/x/y")})
    findings = [f for f in audit_mcp_templates(tmp_path) if f.rule == "AUTH-unpinned-mcp-image"]
    assert "ghcr.io/x/y" in findings[0].message


def test_unpinned_uvx_and_npx_packages(tmp_path):
    write_template(tmp_path, {"a": {"command": "uvx", "args": ["pkg@latest"]}, "b": {"command": "npx", "args": ["-y", "pkg"]}, "c": {"command": "uvx", "args": ["pkg@1.2.3"]}})
    locations = {f.location for f in audit_mcp_templates(tmp_path) if f.rule == "AUTH-unpinned-mcp-package"}
    assert locations == {".mcp.test.json.template:a", ".mcp.test.json.template:b"}


def test_literal_credential_in_template_is_critical(tmp_path):
    write_template(tmp_path, {"github": github_server(env={"GITHUB_PERSONAL_ACCESS_TOKEN": "not-a-placeholder"})})
    finding = next(f for f in audit_mcp_templates(tmp_path) if f.rule == "AUTH-literal-credential-in-template")
    assert finding.severity == "critical" and "not-a-placeholder" not in finding.message


SECURE_UI = '''
from fastapi import Depends, FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware
app = FastAPI()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost"])
def check_origin(): "origin"
def auth(): ...
@app.post("/api/run")
async def start(dep=Depends(auth)): ...
@app.get("/api/health")
def health(): ...
'''


def write_ui(root, source):
    (root / "ui").mkdir(exist_ok=True)
    (root / "ui" / "app.py").write_text(source)


def test_authenticated_guarded_ui_is_clean(tmp_path):
    write_ui(tmp_path, SECURE_UI)
    assert audit_web_ui(tmp_path) == []


def test_only_state_changing_routes_need_auth(tmp_path):
    write_ui(tmp_path, SECURE_UI.replace("dep=Depends(auth)", "").replace('@app.get("/api/health")', '@app.post("/api/x")'))
    finding = next(f for f in audit_web_ui(tmp_path) if f.rule == "AUTH-ui-no-authentication")
    assert "start" in finding.message and "health" in finding.message


def test_get_route_without_auth_is_not_flagged(tmp_path):
    write_ui(tmp_path, SECURE_UI)
    assert "AUTH-ui-no-authentication" not in rules(audit_web_ui(tmp_path))


def test_missing_host_and_origin_validation(tmp_path):
    write_ui(tmp_path, 'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/")\ndef i(): ...\n')
    assert {"AUTH-ui-no-host-validation", "AUTH-ui-no-origin-check"} <= rules(audit_web_ui(tmp_path))


def test_approve_and_apply_in_one_handler_and_client_identity(tmp_path):
    source = SECURE_UI + '''
async def both(decided_by: str):
    await run(pipeline.run_approve)
    await run(pipeline.run_apply)
'''
    write_ui(tmp_path, source)
    assert {"AUTH-ui-approve-and-apply-collapsed", "AUTH-ui-client-supplied-identity"} <= rules(audit_web_ui(tmp_path))


def write_dockerfile(root, text, ignore=None, name="Dockerfile"):
    (root / name).write_text(text)
    if ignore is not None:
        (root / ".dockerignore").write_text(ignore)


GOOD_DOCKERFILE = "FROM python:3.13.1-slim\nRUN useradd app\nUSER app\nCMD [\"python\"]\n"


def test_good_dockerfile_is_clean(tmp_path):
    write_dockerfile(tmp_path, GOOD_DOCKERFILE)
    assert audit_containers(tmp_path) == []


def test_container_rules(tmp_path):
    write_dockerfile(
        tmp_path,
        "FROM node:latest AS build\nRUN npm install -g some-cli\nFROM python\nCOPY . /app\n"
        "# docker run -p 8000:8000 -v /var/run/docker.sock:/var/run/docker.sock x\n"
        'CMD ["uvicorn", "--host", "0.0.0.0"]\n',
        ignore=".venv/\n",
    )
    found = rules(audit_containers(tmp_path))
    assert {
        "AUTH-container-unpinned-base",
        "AUTH-container-runs-as-root",
        "AUTH-container-unpinned-install",
        "AUTH-container-binds-all-interfaces",
        "AUTH-dockerignore-gap",
        "AUTH-sensitive-mount-documented",
        "AUTH-published-on-all-interfaces",
    } <= found


def test_loopback_bound_publish_is_fine(tmp_path):
    write_dockerfile(tmp_path, GOOD_DOCKERFILE + "# docker run -p 127.0.0.1:8000:8000 img\n")
    assert "AUTH-published-on-all-interfaces" not in rules(audit_containers(tmp_path))


def test_readme_mount_documentation_is_flagged(tmp_path):
    (tmp_path / "README.md").write_text('docker run -v "C:/Users/x/.claude.json:/root/.claude.json" img\n')
    assert "AUTH-sensitive-mount-documented" in rules(audit_containers(tmp_path))


def write_workflow(root, text):
    directory = root / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "ci.yml").write_text(text)


def test_workflow_hardening_rules(tmp_path):
    write_workflow(tmp_path, "on: pull_request_target\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n")
    assert {"AUTH-ci-default-token-permissions", "AUTH-ci-pull-request-target", "AUTH-ci-action-not-sha-pinned"} <= rules(audit_ci(tmp_path))


def test_hardened_workflow_is_clean(tmp_path):
    write_workflow(tmp_path, "permissions:\n  contents: read\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@" + "a" * 40 + "\n")
    assert audit_ci(tmp_path) == []


def test_approval_flow_attribution_and_visibility(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "apply.py").write_text("record(change_record)\n")
    (tmp_path / "scripts" / "decide.py").write_text("print(warnings)\n")
    assert {"AUTH-apply-not-attributed", "AUTH-decide-shows-no-proposal"} <= rules(audit_approval_flow(tmp_path))
    (tmp_path / "scripts" / "apply.py").write_text("dict(x, applied_by=user)\n")
    (tmp_path / "scripts" / "decide.py").write_text("print(pending['proposed_actions'])\n")
    assert audit_approval_flow(tmp_path) == []


def test_redactor_that_masks_nothing_is_reported_for_every_format(tmp_path):
    assert len(audit_redactor(lambda text: text)) == 8


def test_redactor_that_masks_everything_is_clean():
    assert audit_redactor(lambda text: "[REDACTED]") == []
