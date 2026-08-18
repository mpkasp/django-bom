# Package mirrors (apt, PyPI, and Docker)

If you cannot reach public package repositories or container registries while building Docker images, point the build at a local or regional mirror.

Set env-file mirrors in `.env.prod` (the file used with `docker compose --env-file .env.prod`), or in a project-root `.env` if you are not passing `--env-file`. Commented examples are in `.env.example`. A value in the shell overrides the same key in the env file.

## PyPI

PyPI is not configured from an env file. If you have a problem accessing PyPI, add a mirror link to the Dockerfile before installing pip requirements:

```
# Set the environment variable to use the mirror PyPI URL
ENV PIP_INDEX_URL=https://mirrors.sustech.edu.cn/pypi/web/simple
```

## APT (Debian)

`docker-compose.yml` passes `APT_MIRROR` into the image build. Uncomment and set it in `.env.prod`:

```
APT_MIRROR=https://mirror.example.com/debian
```

Then build as usual:

```
docker compose --env-file .env.prod up --build -d
```

You can still export the variable in the shell if you prefer not to store it in a file:

```
export APT_MIRROR=https://mirror.example.com/debian
docker compose --env-file .env.prod up --build -d
```

Note: SSL certificate verification is automatically disabled for the APT mirror to support self-signed certificates.

## Docker registry

Docker does not read a daemon `registry-mirrors` setting from this project's env files. Compose can still pull Hub images through a prefix you set in the env file.

Uncomment and set `DOCKER_REGISTRY_MIRROR` in `.env.prod`. Include a trailing slash. It is prepended to Docker Hub images (`postgres`, `caddy`, and the `debian` base image):

```
DOCKER_REGISTRY_MIRROR=mirror.example.com/
```

That pulls `mirror.example.com/postgres:17-alpine` instead of `postgres:17-alpine`. Some mirrors require a `library/` path, for example `mirror.example.com/library/`.

The uv builder image comes from GHCR (`ghcr.io/astral-sh/uv`), not Docker Hub, so the Hub prefix does not apply. If you cannot pull GHCR, set the full image name (for example a GHCR pull-through cache):

```
UV_IMAGE=ghcr.io/astral-sh/uv:bookworm-slim
```

The CSS build uses Node (`node:22-bookworm-slim`). If you cannot pull that image, set the full name:

```
NODE_IMAGE=node:22-bookworm-slim
```
