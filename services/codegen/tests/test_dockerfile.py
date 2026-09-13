from codegen.dockerfile import check_dockerfile_lint_output, extract_dockerfile

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


def test_extract_dockerfile_pulls_labeled_fenced_block():
    text = "Here is the Dockerfile:\n\n```dockerfile\nFROM python:3.12-slim\nCMD [\"python\", \"app.py\"]\n```\n"
    content = extract_dockerfile(text)
    assert content == 'FROM python:3.12-slim\nCMD ["python", "app.py"]'


def test_extract_dockerfile_returns_none_when_no_labeled_block():
    assert extract_dockerfile("Sorry, I could not determine the app's language.") is None


def test_extract_dockerfile_ignores_unlabeled_fenced_blocks():
    """Regression against grabbing the wrong code block: an unlabeled fence
    (e.g. a shell command the model quoted while investigating) must not be
    mistaken for the Dockerfile."""
    text = "I ran ```pip freeze``` to check dependencies.\n\nNo Dockerfile produced yet."
    assert extract_dockerfile(text) is None


def test_extract_dockerfile_takes_first_labeled_block_when_multiple_present():
    text = "```dockerfile\nFROM a\n```\nsome text\n```dockerfile\nFROM b\n```"
    assert extract_dockerfile(text) == "FROM a"
