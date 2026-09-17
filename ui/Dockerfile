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
#   docker run --rm -p 8000:8000 \
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

FROM docker:29.8.1-cli AS docker-cli
FROM docker/buildx-bin:0.37.1 AS buildx-bin

FROM python:3.13.15-slim
WORKDIR /app

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
    -r ./ui/requirements.txt

EXPOSE 8000
# python:slim has no wget/curl by default (unlike the alpine-based
# static-site image) - urllib is always available, no extra package needed.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["python", "-m", "uvicorn", "ui.app:app", "--host", "0.0.0.0", "--port", "8000"]
