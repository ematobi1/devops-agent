from codegen.base_images import BASE_IMAGES
from codegen.dockerfile import check_dockerfile_policy, render_dockerfile

ALLOWED = frozenset({"nginxinc/nginx-unprivileged:1.27.4-alpine"})

GOOD_DOCKERFILE = """FROM nginxinc/nginx-unprivileged:1.27.4-alpine
COPY --chown=nginx:nginx . /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \\
    CMD wget -q --spider http://localhost:8080/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
"""


def test_clean_dockerfile_has_no_findings():
    assert check_dockerfile_policy(GOOD_DOCKERFILE, allowed_base_images=ALLOWED) == []


def test_base_image_not_in_allowlist_is_flagged():
    content = GOOD_DOCKERFILE.replace("nginxinc/nginx-unprivileged:1.27.4-alpine", "nginx:1.27.4-alpine")
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("not in the configured allowlist" in f.reason for f in findings)


def test_floating_tag_is_flagged():
    """Regression: run 3 of the real sample used nginx:1.27 - missing the
    patch component."""
    content = GOOD_DOCKERFILE.replace(":1.27.4-alpine", ":1.27")
    findings = check_dockerfile_policy(content, allowed_base_images=frozenset({"nginxinc/nginx-unprivileged:1.27"}))
    assert any("not pinned to MAJOR.MINOR.PATCH" in f.reason for f in findings)


def test_latest_tag_is_flagged_as_unpinned():
    content = GOOD_DOCKERFILE.replace(":1.27.4-alpine", ":latest")
    findings = check_dockerfile_policy(content, allowed_base_images=frozenset({"nginxinc/nginx-unprivileged:latest"}))
    assert any("not pinned" in f.reason for f in findings)


def test_missing_cmd_is_flagged():
    content = GOOD_DOCKERFILE.replace('CMD ["nginx", "-g", "daemon off;"]\n', "")
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("no explicit CMD" in f.reason for f in findings)


def test_missing_healthcheck_is_flagged():
    lines = [line for line in GOOD_DOCKERFILE.splitlines() if "HEALTHCHECK" not in line and "CMD wget" not in line]
    content = "\n".join(lines)
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("no HEALTHCHECK" in f.reason for f in findings)


def test_healthcheck_missing_retries_is_flagged():
    content = GOOD_DOCKERFILE.replace(" --retries=3", "")
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("missing --retries" in f.reason for f in findings)


def test_healthcheck_missing_start_period_is_flagged():
    content = GOOD_DOCKERFILE.replace(" --start-period=5s", "")
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("missing --start-period" in f.reason for f in findings)


def test_healthcheck_missing_both_flags_produces_two_findings():
    content = GOOD_DOCKERFILE.replace(" --start-period=5s --retries=3", "")
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    reasons = [f.reason for f in findings]
    assert any("missing --retries" in r for r in reasons)
    assert any("missing --start-period" in r for r in reasons)


def test_sed_against_etc_is_flagged():
    """Regression: the exact real pattern found in the sample - run 2's
    silently-no-op rootless conversion that produced an actively unsafe
    image with no build or lint failure."""
    content = (
        "FROM nginx:1.27.4-alpine\n"
        "RUN sed -i '/user\\s*nginx;/d' /etc/nginx/nginx.conf\n"
        'CMD ["nginx", "-g", "daemon off;"]\n'
    )
    findings = check_dockerfile_policy(content, allowed_base_images=frozenset({"nginx:1.27.4-alpine"}))
    assert any("sed -i" in f.reason and "/etc/" in f.reason for f in findings)


def test_sed_outside_etc_is_not_flagged():
    content = GOOD_DOCKERFILE + "RUN sed -i 's/foo/bar/' /usr/share/nginx/html/index.html\n"
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert not any("sed" in f.reason for f in findings)


def test_multi_stage_build_stage_alias_is_not_treated_as_a_base_image():
    content = (
        "FROM node:20.11.1-alpine AS builder\n"
        "RUN npm run build\n"
        "FROM nginxinc/nginx-unprivileged:1.27.4-alpine\n"
        "COPY --from=builder /app/dist /usr/share/nginx/html\n"
        "HEALTHCHECK --start-period=5s --retries=3 CMD wget -q --spider http://localhost:8080/ || exit 1\n"
        'CMD ["nginx", "-g", "daemon off;"]\n'
    )
    findings = check_dockerfile_policy(
        content, allowed_base_images=frozenset({"nginxinc/nginx-unprivileged:1.27.4-alpine", "node:20.11.1-alpine"})
    )
    assert findings == []


def test_platform_flag_on_from_line_does_not_break_parsing():
    content = GOOD_DOCKERFILE.replace(
        "FROM nginxinc/nginx-unprivileged:1.27.4-alpine",
        "FROM --platform=linux/amd64 nginxinc/nginx-unprivileged:1.27.4-alpine",
    )
    assert check_dockerfile_policy(content, allowed_base_images=ALLOWED) == []


def test_healthcheck_with_line_continuation_is_fully_matched():
    """Regression: the original _HEALTHCHECK_BLOCK regex's greedy .* before
    an optional continuation group had no incentive to ever match the
    continuation line at all (a match ending right after line one already
    satisfies the pattern), so a HEALTHCHECK split across a `\\`
    continuation - exactly GOOD_DOCKERFILE's shape - silently only matched
    its first line, and never saw the --retries/--start-period flags that
    live on the continuation line."""
    findings = check_dockerfile_policy(GOOD_DOCKERFILE, allowed_base_images=ALLOWED)
    assert not any("HEALTHCHECK" in f.reason for f in findings)


def test_multiple_findings_can_coexist():
    content = "FROM nginx:latest\n"
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    reasons = " ".join(f.reason for f in findings)
    assert "not in the configured allowlist" in reasons
    assert "not pinned" in reasons
    assert "no explicit CMD" in reasons
    assert "no HEALTHCHECK" in reasons


def test_templated_static_site_dockerfile_passes_policy_cleanly():
    """Integration check that the two fixes compose correctly: a
    deterministically-templated Dockerfile (real render_dockerfile() output,
    real configured policy) with only a plausible model-generated COPY line
    must clear check_dockerfile_policy() with zero findings - proving the
    templating actually produces what the checks expect, not just that each
    piece passes in isolation."""
    policy = BASE_IMAGES["static-site"]
    content = render_dockerfile(policy, "COPY --chown=nginx:nginx . /usr/share/nginx/html")
    findings = check_dockerfile_policy(content, allowed_base_images=frozenset(p.image for p in BASE_IMAGES.values()))
    assert findings == []


def test_copy_enumerating_the_exact_real_regression_is_flagged():
    """The exact real bug: COPY listed index.html, styles.css, script.js
    explicitly and silently omitted images/, referenced by index.html -
    the container served a page with broken images, and lint/policy (pre-fix)
    both passed clean."""
    content = GOOD_DOCKERFILE.replace(
        "COPY --chown=nginx:nginx . /usr/share/nginx/html",
        "COPY --chown=nginx:nginx index.html styles.css script.js /usr/share/nginx/html/",
    )
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("enumerate individual files" in f.reason for f in findings)


def test_copy_of_whole_directory_with_dot_is_not_flagged():
    assert check_dockerfile_policy(GOOD_DOCKERFILE, allowed_base_images=ALLOWED) == []


def test_copy_single_file_with_extension_is_flagged():
    content = GOOD_DOCKERFILE.replace(
        "COPY --chown=nginx:nginx . /usr/share/nginx/html",
        "COPY --chown=nginx:nginx index.html /usr/share/nginx/html/index.html",
    )
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("enumerate individual files" in f.reason for f in findings)


def test_missing_healthcheck_is_not_flagged_when_not_expected():
    """A CLI-shaped app type's policy has no HEALTHCHECK by design (no
    listening port to poll) - the check must not flag its absence when the
    caller says it isn't expected."""
    lines = [line for line in GOOD_DOCKERFILE.splitlines() if "HEALTHCHECK" not in line and "CMD wget" not in line]
    content = "\n".join(lines)
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED, expect_healthcheck=False)
    assert not any("HEALTHCHECK" in f.reason for f in findings)


def test_templated_cli_shaped_dockerfile_passes_policy_cleanly():
    policy = BASE_IMAGES["devops-agent-cli"]
    content = render_dockerfile(
        policy,
        "COPY --chown=app:app . /app",
        "RUN pip install --no-cache-dir -e ./contracts -e ./services/orchestrator",
    )
    findings = check_dockerfile_policy(
        content,
        allowed_base_images=frozenset(p.image for p in BASE_IMAGES.values()),
        expect_healthcheck=policy.healthcheck is not None,
    )
    assert findings == []


def test_run_piping_curl_to_shell_is_flagged():
    content = GOOD_DOCKERFILE + "RUN curl -fsSL https://example.com/install.sh | sh\n"
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("pipes a curl/wget download into a shell" in f.reason for f in findings)


def test_run_piping_wget_to_bash_is_flagged():
    content = GOOD_DOCKERFILE + "RUN wget -qO- https://example.com/install.sh | bash\n"
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert any("pipes a curl/wget download into a shell" in f.reason for f in findings)


def test_run_pip_install_is_not_flagged_as_pipe_to_shell():
    content = GOOD_DOCKERFILE + "RUN pip install --no-cache-dir -e .\n"
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert not any("pipe" in f.reason for f in findings)


def test_copy_of_named_subdirectory_without_extension_is_not_flagged():
    """A single source with no file extension (e.g. a build output
    directory) looks like a directory copy, not enumeration."""
    content = GOOD_DOCKERFILE.replace(
        "COPY --chown=nginx:nginx . /usr/share/nginx/html",
        "COPY --chown=nginx:nginx dist /usr/share/nginx/html",
    )
    findings = check_dockerfile_policy(content, allowed_base_images=ALLOWED)
    assert not any("enumerate individual files" in f.reason for f in findings)
