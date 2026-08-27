# BOM

BoM is a Django app to manage a bill of materials. It supports multiple part numbering schemes, tracking component sourcing information and estimates costs. BoM is written in Python 3.14 and Django 6.

BoM can be added to an existing (or new) Django project, or stand alone on its own, which can be more convenient if you're interested in tweaking the tool. 

If you already have a django project, you can skip to [Add Django Bom To Your App](#add-django-bom-to-your-app), otherwise [Start From Scratch: Add to new Django project](#start-from-scratch-add-to-a-new-django-project) to add it to a new django project, or [Start From Scratch: Use as standalone Django project](#start-from-scratch-use-as-a-standalone-django-project).

## Table of contents
   * [Add Django Bom To Your App](#add-django-bom-to-your-app)
   * [Start From Scratch: Use as standalone Django project](#start-from-scratch-use-as-a-standalone-django-project)
   * [Start from docker](#start-from-docker-recommended)
   * [Production operations](#production-operations)
   * [Backup and restore database](#backup-and-restore-database-if-using-docker-compose-and-postgres)
   * [Uninstall](#uninstall)
   * [Run the tests](#run-the-tests)
   * [Customize Base Template](#customize-base-template)
   * [Add To Your App](#add-to-your-app)
   * [Integrations](#integrations)
   * [Contributing](#contributing)
   * [Installation pitfalls](#installation-pitfalls)

## Start from docker (Recommended)
1. Create `.env.prod` and `.env.db` files or use example files (just rename them).

2. If you cannot reach PyPI, Debian, or Docker Hub, see [Package mirrors](docs/mirrors.md).

3. Go to project dir
```
cd project_dir
```

4. If you have a database dump, restore it **before** starting `web`. Start only Postgres, restore, then continue with step 5. See [Backup and restore database](#backup-and-restore-database-if-using-docker-compose-and-postgres) for the full procedure.

```
docker compose --env-file .env.prod up -d db
gunzip < dump_file.sql.gz | docker compose --env-file .env.prod exec -T db psql -U bom_user -d bom_db
```

Do not start the full stack first. The `web` container runs `python manage.py migrate` on startup; migrating an empty database and then restoring fails with "relation already exists" errors and a partial restore. If this volume already has tables, use [Workflow A](#workflow-a--fresh-install-with-a-dump-recommended) or [Workflow B](#workflow-b--re-restore-on-a-running-stack) instead of restoring on top. Before step 5, you can [inspect pending migrations and take a pre-migrate dump](#after-restore-inspect-migrations).

5. Build and run the containers
```
docker compose --env-file .env.prod up --build -d
```
The web container runs `collectstatic` and `python manage.py migrate` automatically on startup. After a restore, that migrate applies only migrations that are in the image but not in the dump (for example new models). That is the correct order.

## Production operations

Production services use `restart: unless-stopped`, so containers recover from crashes and reboots but stay down after an intentional `docker stop`.

Postgres exposes a healthcheck; `web` and `backup` wait for a healthy database before starting, which reduces crash-loops during startup.

Container logs are rotated automatically (`10m` max size, `3` files per service). Application log lines include a date/time header, for example:

```
2026-08-17 22:24:01 INFO bom.forms Upload completed
```

Gunicorn access logs use Apache-style timestamps (`%(t)s`) on each request line. Docker also stores per-line timestamps in log metadata; pass `-t` to `docker compose logs` to show them:

```
docker compose logs -t web
```

The optional `monitor` service watches `web`, `caddy`, `db`, and `backup` for restart loops and appends timestamped alerts to `monitoring/restart-loops.log` when a container's restart count crosses `RESTART_ALERT_THRESHOLD` (default `5`). Tune via `.env.prod`:

```
RESTART_ALERT_THRESHOLD=5
CHECK_INTERVAL_SECONDS=60
```

View alerts:

```
tail -f monitoring/restart-loops.log
```

## Backup and restore database (If using docker-compose and postgres)

Always pass `--env-file .env.prod` on every `docker compose` command (including `exec`).

Backup:
```
docker compose --env-file .env.prod exec -T db pg_dump -c -U bom_user bom_db | gzip > ./dump_bom_db_$(date +"%Y-%m-%d_%H_%M_%S").sql.gz
```

The `backup` service also writes a nightly dump to `postgres_backup/` and keeps the newest seven files.

### Restore

**Important:** Restore first, then migrate. Do not restore on top of a database that already has tables from `migrate`. The web container runs migrations on startup, so if you run `docker compose up -d` before restoring, the dump will fail partially (materials/part data will be missing while some other tables may load).

#### Workflow A — Fresh install with a dump (recommended)

Use this when setting up from scratch or after `down --volumes`:

```
docker compose --env-file .env.prod down --volumes --rmi local
docker compose --env-file .env.prod up -d db
gunzip < dump_file.sql.gz | docker compose --env-file .env.prod exec -T db psql -U bom_user -d bom_db 2>&1 | tee restore.log
grep -iE 'error|fatal|violates' restore.log
```

Start only `db` first so migrations do not create tables before the restore. Then continue with [After restore: inspect migrations](#after-restore-inspect-migrations).

#### Workflow B — Re-restore on a running stack

Use this to fix a failed restore without wiping the Docker volume:

```
docker compose --env-file .env.prod stop web backup caddy
docker compose --env-file .env.prod exec -T db psql -U bom_user -d bom_db -c \
  "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO bom_user; GRANT ALL ON SCHEMA public TO public;"
gunzip < dump_file.sql.gz | docker compose --env-file .env.prod exec -T db psql -U bom_user -d bom_db 2>&1 | tee restore.log
grep -iE 'error|fatal|violates' restore.log
```

Do not start `web` yet. Continue with [After restore: inspect migrations](#after-restore-inspect-migrations).

#### After restore: inspect migrations

The dump includes the `django_migrations` table, so Django knows which migrations the restored schema already reflects. Starting `web` then applies only newer migrations (new models, new columns, and so on). That is the correct order when the image is ahead of the dump.

Before starting `web`, list applied vs pending migrations. This command builds the image if needed but replaces the entrypoint, so it does **not** run `migrate`:

```
docker compose --env-file .env.prod run --rm --build --no-deps --entrypoint python web manage.py showmigrations
```

Unapplied migrations are marked `[ ]`.

If any migrations are unapplied, take a dump of the restored database first so a failed migration does not cost you the restore:

```
docker compose --env-file .env.prod exec -T db pg_dump -c -U bom_user bom_db | gzip > ./dump_bom_db_pre_migrate_$(date +"%Y-%m-%d_%H_%M_%S").sql.gz
```

Then start the remaining services. This is when `migrate` actually runs:

```
docker compose --env-file .env.prod up --build -d
```

On a re-restore (Workflow B) you can omit `--build` if the image is already current:

```
docker compose --env-file .env.prod up -d
```

If `web` restart-loops, check `docker compose logs web` for a migration error.

#### Restore (unzipped dump on Windows)

```
cat dump_file.sql | docker compose --env-file .env.prod exec -T db psql -U bom_user -d bom_db
```

#### Verify restore succeeded

After restore, confirm parts and revisions loaded (counts should be greater than zero):

```
docker compose --env-file .env.prod exec -T db psql -U bom_user -d bom_db -c "
SELECT 'part' t, COUNT(*) FROM bom_part
UNION ALL SELECT 'partrevision', COUNT(*) FROM bom_partrevision
UNION ALL SELECT 'seller', COUNT(*) FROM bom_seller;"
```

A clean `restore.log` should have no `already exists` or foreign-key violation errors on `bom_*` tables.

## Uninstall
To take the server down and remove images and volumes (including database volume):
```
docker compose --env-file .env.prod down --volumes --rmi local
```

## Run the tests
```
docker compose --env-file .env.test -f docker-compose.test.yml up --abort-on-container-exit --remove-orphans
```
Cleanup after running the tests:
```
docker compose -f docker-compose.test.yml down -v --rmi local
```

## Add To Your App
django-bom is a [reusable django application](https://docs.djangoproject.com/en/1.11/intro/reusable-apps/). If you don't already have a django project, you can follow some quick steps below to get up and running, or read about creating your first django app [here](https://docs.djangoproject.com/en/1.11/intro/tutorial01/).

```
pip install django-bom
```

1. Add "bom" to your INSTALLED_APPS setting like this::

```
INSTALLED_APPS = [
    ...
    'bom',
    'djmoney', # for currency
    'djmoney.contrib.exchange', # for currency
]
```

2. Update your URLconf in your project urls.py like this::

```
path('bom/', include('bom.urls')),
```

And don't forget to import include:

```
from django.urls import include
```

3. Update your settings.py to add the bom context processor `'bom.context_processors.bom_config',` to your TEMPLATES variable, and create a new empty dictionary BOM_CONFIG.

```
TEMPLATES = [
    {
        ...
        'OPTIONS': {
            'context_processors': [
                ...
                'bom.context_processors.bom_config',
            ],
        },
    },
]
```

and

```
BOM_CONFIG = {}
```

4. Run `python manage.py migrate` to create the bom models.

5. Start the development server `python manage.py runserver` and visit http://127.0.0.1:8000/admin/
   to manage the bom (you'll need the Admin app enabled).

6. Visit http://127.0.0.1:8000/bom/ to begin.

## Customize Base Template
The base template can be customized. Add the following configuration to your settings.py:

```
BOM_CONFIG = {
    'base_template': 'base.html',
    'page_loading_delay_ms': 300,  # ms before overlay; 0 = show immediately
}
```

where `base.html` is your base template. `page_loading_delay_ms` controls how long a navigation must take before the Lithium loading overlay appears (default `300`). Set it to `0` to show immediately, or higher to avoid flashes on fast pages.

**CSS contract:** Lithium BoM pages load Tailwind CSS from `{% static 'bom/css/app.css' %}`. An overriding base template must:

- Include `app.css` (do not load Materialize CSS or JS on the same page)
- Keep `dir="rtl"` (or inherit it) for the Persian UI
- Provide `{% block content %}`, `{% block head %}`, `{% block script %}`, `{% block menu %}`, and `{% block menu-mobile %}`
- Include `{% include 'bom/ui/page-loading.html' %}` and load `bom-ui.js` after jQuery so the page-navigation progress bar still appears on slow loads

## Integrations

### FIXER
Fixer.io is used to handle exchange rate calculations. This is helpful if you may be purchasing parts from another currency (especially via Mouser) and you still need to estimate your part costs.

To set this up you just need to add your API key to local_settings.py as shown in the example.

To update rates, migrate and run `python manage.py update_rates`. Some day we will need to add a (celerybeat?) task to update rates on a schedule. Explained more [here](https://github.com/django-money/django-money#working-with-exchange-rates).

## Installation Pitfalls

### Windows
#### Sqlite
You may get an error during your `pip install -r requirements.txt` related to sqlite. This may be fixed by installing Visual C++ for python...

#### Cryptography
Sometimes you'll have issues installing cryptography, if this is the case you may just need to set up some environment variables. This [stackoverflow](https://stackoverflow.com/questions/46288737/error-while-installing-sqlite-using-pip-on-python-2-7-13) may help.
