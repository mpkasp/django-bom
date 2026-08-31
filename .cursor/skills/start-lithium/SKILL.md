---
name: start-lithium
description: >-
  Start the django-bom Docker stack locally. Use when the user asks to start
  the stack, bring up services, run docker compose, or start the app locally.
---

# Start Lithium

Start the full production-like Docker stack for this project.

## Command

From the repository root:

```bash
docker compose --env-file .env.prod up --build -d
```

Run with full permissions (`all`) so Docker can access the daemon.

## After start

Verify containers are up:

```bash
docker compose --env-file .env.prod ps
```

If `web` is restarting, check logs:

```bash
docker compose --env-file .env.prod logs web
```

## Prerequisites

- Docker running locally
- `.env.prod` present in the repo root (not committed; copy from `.env.example` if missing)

## Notes

- Rebuilds the `web` image on each start (`--build`), so branch changes are picked up.
- `web` runs `migrate` and `collectstatic` on startup via `entrypoint.sh`.
- Default HTTP port comes from `NGINX_PORT` in `.env.prod` (Caddy reverse proxy).
