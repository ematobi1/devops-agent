# Build context is the REPO ROOT, not ui/ - the app needs the whole
# monorepo (contracts/ + all of services/), not just this directory. Build
# from the repo root:
#   docker build -f ui/Dockerfile -t devops-agent-ui .
#
# Run it with the host's Docker socket mounted - the generate stage shells
# out to `docker build --check` (see services/codegen/dockerfile.py), and
# that has to reach a real Docker daemon. This container has no daemon of
# its own (Docker-in-Docker is unnecessary complexity for a local dev tool);
# it talks to the HOST's daemon instead (Docker-outside-of-Docker):
#   docker run --rm -p 127.0.0.1:8000:8000 \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -e GITHUB_PERSONAL_ACCESS_TOKEN=<your PAT> \
#     devops-agent-ui
#
# Runs as root, deliberately, not templated with a non-root user the way
# services/codegen/base_images.py's policies are for other targets: mounting
# the host's Docker socket already grants whoever holds it root-equivalent
# control of the host machine, so an in-container non-root USER would add
# real, host-GID-dependent brittleness (the container's user needs a GID
# matching the socket's group ownership, which varies by host and doesn't
# hold across Docker Desktop's Windows/Mac proxy vs. native Linux) for no
# actual security benefit - see ui/README.md "Security" for the existing
# no-auth, localhost-only threat model this fits into.
#
# NOT SOLVED YET: Claude Code auth. The `claude` binary below runs, but a
# real generate/apply call will still fail - this project deliberately
# uses the operator's logged-in subscription session, not ANTHROPIC_API_KEY
# (see docs/ARCHITECTURE.md's `--setting-sources`/`--bare` note: forcing
# API-key auth was already considered and rejected as "the wrong tradeoff"
# at the orchestrator level, for reasons unrelated to containers). Getting
# a real session into this container means bind-mounting the operator's
# own ~/.claude.json/~/.claude/ (a real personal-account-credential
# exposure decision, not a Dockerfile detail) or deliberately overriding
# that decision for the container specifically - both left to the operator
# to decide, not decided here. See docs/ARCHITECTURE.md's note on this
# image for the two options and why neither was picked silently.

FROM docker:29.8.1-cli AS docker-cli
FROM docker/buildx-bin:0.37.1 AS buildx-bin
FROM node:22.23.2-slim AS claude-cli
RUN npm install -g @anthropic-ai/claude-code@2.1.278

FROM python:3.13.15-slim
WORKDIR /app

# The actual Claude Code agent runtime this entire pipeline depends on -
# missing from every earlier build of this image (found live: a real
# pipeline run through the container failed with a plain
# FileNotFoundError: 'claude', since claude_runner.py shells out to a bare
# `claude` on PATH and nothing had ever installed it here). The published
# binary (verified: a self-contained native executable with only glibc/
# librt/libpthread dependencies, no Node.js needed at runtime - checked
# via `ldd` before trusting this) is distributed through npm's global
# install as a convenient channel, not because it needs Node.js to run;
# only the install stage above needs Node, and only that one file is
# copied into the final image.
COPY --from=claude-cli /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude
RUN chmod +x /usr/local/bin/claude

# Just the `docker` CLI binary, not the daemon - copied from the official
# CLI-only image rather than `apt-get install docker.io`, which pulls in
# the full engine this container never runs. `docker build --check` (what
# services/codegen/dockerfile.py's lint_dockerfile() actually runs) needs
# the buildx plugin too - found live: the bare CLI alone exits 125 with no
# usable output on `--check`, since BuildKit's --check flag is implemented
# by buildx, not the plain classic builder the bare CLI falls back to
# without it.
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=buildx-bin /buildx /usr/local/lib/docker/cli-plugins/docker-buildx

COPY . /app

RUN pip install --no-cache-dir \
    -e ./contracts \
    -e ./services/approval \
    -e ./services/verifier \
    -e ./services/codegen \
    -e ./services/orchestrator \
    -e ./services/security \
    -r ./ui/requirements.txt

EXPOSE 8000
# python:slim has no wget/curl by default (unlike the alpine-based
# static-site image) - urllib is always available, no extra package needed.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["python", "-m", "uvicorn", "ui.app:app", "--host", "0.0.0.0", "--port", "8000"]
