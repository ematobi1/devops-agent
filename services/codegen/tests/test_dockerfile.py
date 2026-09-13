from codegen.base_images import AppImagePolicy
from codegen.dockerfile import check_dockerfile_lint_output, extract_copy_instructions, render_dockerfile

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


POLICY = AppImagePolicy(
    image="nginxinc/nginx-unprivileged:1.27.4-alpine",
    cmd='["nginx", "-g", "daemon off;"]',
    healthcheck="--interval=30s --timeout=3s --start-period=5s --retries=3 \\\n    CMD wget -qO- http://127.0.0.1:8080/ || exit 1",
    port=8080,
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
