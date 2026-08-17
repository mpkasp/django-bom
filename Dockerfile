# An example of using standalone Python builds with multistage images.

# First, build the application in the `/app` directory
# FROM substitutions only see ARGs declared before the first FROM.
ARG UV_IMAGE=ghcr.io/astral-sh/uv:bookworm-slim
ARG DEBIAN_IMAGE=debian:bookworm-slim
FROM ${UV_IMAGE} AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Configure the Python directory so it is consistent
ENV UV_PYTHON_INSTALL_DIR=/python

# Only use the managed Python version
ENV UV_PYTHON_PREFERENCE=only-managed
ENV UV_PYTHON=3.14

# Install Python before the project for caching
RUN uv python install 3.14

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Then, use a final image without uv
FROM ${DEBIAN_IMAGE}

ARG APT_MIRROR

# Required for "entrypoint.sh, makemessages"
RUN if [ -n "$APT_MIRROR" ]; then \
      find /etc/apt -type f \( -name 'sources.list' -o -name '*.sources' \) -exec sed -i \
        -e "s|http://deb.debian.org/debian|$APT_MIRROR|g" \
        -e "s|http://security.debian.org/debian-security|$APT_MIRROR-security|g" \
        {} +; \
      printf 'Acquire::https::Verify-Peer "false";\nAcquire::https::Verify-Host "false";\nAPT::Get::AllowUnauthenticated "true";\n' \
        > /etc/apt/apt.conf.d/99insecure-mirror; \
    fi && \
    apt-get -o Acquire::https::Verify-Peer=false -o Acquire::https::Verify-Host=false update && \
    apt-get -o Acquire::https::Verify-Peer=false -o Acquire::https::Verify-Host=false \
            install -y --allow-unauthenticated netcat-openbsd gettext \
    && rm -rf /var/lib/apt/lists/*
    
# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

# Copy the Python version
COPY --from=builder --chown=python:python /python /python

# Copy the application from the builder
COPY --from=builder --chown=nonroot:nonroot /app /app

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Use the non-root user to run our application
USER nonroot

# Use `/app` as the working directory
WORKDIR /app

RUN mkdir /app/staticfiles
RUN mkdir /app/log

# Compile translation messages
RUN python manage.py compilemessages -l fa_IR

