from codegen.base_images import AppImagePolicy
from codegen.dockerfile import (
    check_dockerfile_lint_output,
    extract_copy_instructions,
    extract_install_instructions,
    render_dockerfile,
)

# Captured verbatim from real `docker build --check` runs during development
# (Docker 28.4.0) - see docs/ARCHITECTURE.md "services/codegen" for context.

REAL_CLEAN_OUTPUT = """#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile.good
#1 transferring dockerfile:
#1 transferring dockerfile: 198B 0.6s done
#1 DONE 2.6s

#2 [internal] load metadata for docker.io/library/python:3.12-slim
#2 DONE 8.0s
Check complete, no warnings found.
"""

REAL_WARNING_OUTPUT = """#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile.bad
#1 DONE 0.7s
Check complete, 1 warning has been found!

WARNING: ConsistentInstructionCasing - https://docs.docker.com/go/dockerfile/rule/consistent-instruction-casing/
Command 'run' should match the case of the command majority (uppercase)
C:/scratch/Dockerfile.bad:2
--------------------
   1 |     FROM python:latest
   2 | >>> run pip install flask
   3 |     ADD . /app
   4 |
--------------------
"""

REAL_PARSE_ERROR_OUTPUT = """#0 building with "desktop-linux" instance using docker driver

#1 [internal] load build definition from Dockerfile.broken
#1 DONE 0.1s
ERROR: dockerfile parse error on line 2: unknown instruction: THIS_IS_NOT_A_REAL_INSTRUCTION
C:/scratch/Dockerfile.broken:2
--------------------
   1 |     FROM python:3.12-slim
   2 | >>> THIS_IS_NOT_A_REAL_INSTRUCTION something
   3 |
--------------------
"""


def test_clean_output_produces_no_findings():
    assert check_dockerfile_lint_output(REAL_CLEAN_OUTPUT, returncode=0) == []


def test_warning_output_is_parsed_correctly():
    findings = check_dockerfile_lint_output(REAL_WARNING_OUTPUT, returncode=1)
    assert len(findings) == 1
    assert findings[0].reason == "ConsistentInstructionCasing: Command 'run' should match the case of the command majority (uppercase)"


def test_parse_error_output_is_parsed_correctly():
    findings = check_dockerfile_lint_output(REAL_PARSE_ERROR_OUTPUT, returncode=1)
    assert len(findings) == 1
    assert "unknown instruction: THIS_IS_NOT_A_REAL_INSTRUCTION" in findings[0].reason


def test_multiple_warnings_all_extracted():
    two_warnings = REAL_WARNING_OUTPUT + (
        "\nWARNING: JSONArgsRecommended - https://docs.docker.com/go/dockerfile/rule/json-args-recommended/\n"
        "JSON arguments recommended for CMD to prevent unintended behavior\n"
        "C:/scratch/Dockerfile.bad:5\n"
        "--------------------\n"
    )
    findings = check_dockerfile_lint_output(two_warnings, returncode=1)
    assert len(findings) == 2


def test_unrecognized_nonzero_exit_is_not_silently_swallowed():
    findings = check_dockerfile_lint_output("some totally different error text", returncode=125)
    assert len(findings) == 1
    assert "125" in findings[0].reason


def test_extract_copy_instructions_pulls_labeled_fenced_block():
    text = "Here are the COPY instructions:\n\n```dockerfile-copy\nCOPY --chown=nginx:nginx . /usr/share/nginx/html\n```\n"
    content = extract_copy_instructions(text)
    assert content == "COPY --chown=nginx:nginx . /usr/share/nginx/html"


def test_extract_copy_instructions_returns_none_when_no_labeled_block():
    assert extract_copy_instructions("Sorry, I could not determine the app's language.") is None


def test_extract_copy_instructions_ignores_unlabeled_fenced_blocks():
    """Regression against grabbing the wrong code block: an unlabeled fence
    (e.g. a shell command the model quoted while investigating) must not be
    mistaken for the COPY instructions."""
    text = "I ran ```pip freeze``` to check dependencies.\n\nNo instructions produced yet."
    assert extract_copy_instructions(text) is None


def test_extract_copy_instructions_takes_first_labeled_block_when_multiple_present():
    text = "```dockerfile-copy\nCOPY a /a\n```\nsome text\n```dockerfile-copy\nCOPY b /b\n```"
    assert extract_copy_instructions(text) == "COPY a /a"


def test_extract_copy_instructions_rejects_block_with_no_copy_line():
    """If the model followed the fence label but didn't actually produce a
    COPY instruction, that's still a failure to follow instructions - don't
    silently accept whatever text is there."""
    text = "```dockerfile-copy\n# nothing to copy\n```"
    assert extract_copy_instructions(text) is None


def test_extract_install_instructions_pulls_labeled_fenced_block():
    text = "```dockerfile-run\nRUN pip install --no-cache-dir -e ./services/orchestrator\n```\n"
    assert extract_install_instructions(text) == "RUN pip install --no-cache-dir -e ./services/orchestrator"


def test_extract_install_instructions_returns_none_when_no_labeled_block():
    assert extract_install_instructions("no fenced block here") is None


def test_extract_install_instructions_rejects_block_with_no_run_line():
    text = "```dockerfile-run\n# nothing to install\n```"
    assert extract_install_instructions(text) is None


def test_extract_install_instructions_ignores_copy_block():
    """The two-block prompt puts dockerfile-copy first, dockerfile-run
    second - extraction must not cross-match the wrong fence label."""
    text = "```dockerfile-copy\nCOPY --chown=app:app . /app\n```\n```dockerfile-run\nRUN pip install -e .\n```\n"
    assert extract_install_instructions(text) == "RUN pip install -e ."


POLICY = AppImagePolicy(
    image="nginxinc/nginx-unprivileged:1.27.4-alpine",
    cmd='["nginx", "-g", "daemon off;"]',
    healthcheck="--interval=30s --timeout=3s --start-period=5s --retries=3 \\\n    CMD wget -qO- http://127.0.0.1:8080/ || exit 1",
    port=8080,
)

CLI_POLICY = AppImagePolicy(
    image="python:3.13.15-slim",
    cmd='["python", "scripts/diagnose.py"]',
    healthcheck=None,
    port=None,
    needs_install_step=True,
    workdir="/app",
    user="app",
)


def test_render_dockerfile_assembles_all_policy_fields():
    content = render_dockerfile(POLICY, "COPY --chown=nginx:nginx . /usr/share/nginx/html")
    assert content == (
        "FROM nginxinc/nginx-unprivileged:1.27.4-alpine\n"
        "COPY --chown=nginx:nginx . /usr/share/nginx/html\n"
        "EXPOSE 8080\n"
        "HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \\\n"
        "    CMD wget -qO- http://127.0.0.1:8080/ || exit 1\n"
        'CMD ["nginx", "-g", "daemon off;"]\n'
    )


def test_render_dockerfile_is_deterministic():
    a = render_dockerfile(POLICY, "COPY . /usr/share/nginx/html")
    b = render_dockerfile(POLICY, "COPY . /usr/share/nginx/html")
    assert a == b


def test_render_dockerfile_strips_extraneous_whitespace_from_copy_instructions():
    content = render_dockerfile(POLICY, "\n  COPY . /usr/share/nginx/html  \n\n")
    assert "COPY . /usr/share/nginx/html\nEXPOSE" in content


def test_render_dockerfile_omits_expose_and_healthcheck_when_policy_has_none():
    content = render_dockerfile(CLI_POLICY, "COPY . /app", "RUN pip install -e .")
    assert content == (
        "FROM python:3.13.15-slim\n"
        "WORKDIR /app\n"
        "COPY . /app\n"
        "RUN pip install -e .\n"
        "RUN useradd --create-home app\n"
        'USER app\n'
        'CMD ["python", "scripts/diagnose.py"]\n'
    )
    assert "EXPOSE" not in content
    assert "HEALTHCHECK" not in content


def test_render_dockerfile_creates_user_after_install_and_before_cmd():
    """The install step needs root (writing into the image's system Python);
    switching to a non-root USER must happen after RUN install and before
    CMD, never before - installing as the app user would fail with a
    permission error against system site-packages."""
    content = render_dockerfile(CLI_POLICY, "COPY . /app", "RUN pip install -e .")
    assert content.index("RUN pip install") < content.index("RUN useradd")
    assert content.index("RUN useradd") < content.index("USER app")
    assert content.index("USER app") < content.index("CMD")


def test_render_dockerfile_omits_user_creation_when_policy_has_none():
    content = render_dockerfile(POLICY, "COPY . /usr/share/nginx/html")
    assert "useradd" not in content
    assert "USER" not in content


def test_render_dockerfile_omits_workdir_when_policy_has_none():
    content = render_dockerfile(POLICY, "COPY . /usr/share/nginx/html")
    assert "WORKDIR" not in content


def test_render_dockerfile_omits_install_run_line_when_not_given():
    """The RUN that always appears for CLI_POLICY is user creation
    (policy.user) - only the model-generated install RUN is conditional on
    install_instructions being passed."""
    content = render_dockerfile(CLI_POLICY, "COPY . /app")
    assert "pip install" not in content
    assert content.count("RUN ") == 1
    assert "RUN useradd" in content


def test_render_dockerfile_strips_extraneous_whitespace_from_install_instructions():
    content = render_dockerfile(CLI_POLICY, "COPY . /app", "\n  RUN pip install -e .  \n\n")
    assert "COPY . /app\nRUN pip install -e .\nRUN useradd" in content
