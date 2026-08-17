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
1.1. create .env.prod and .env.db files or use example files (just rename them).

1.2. If you cannot reach PyPI, Debian, or Docker Hub, see [Package mirrors](docs/mirrors.md).
1.3. Go to project dir
```
cd project_dir
```
2. Build and run the containers
```
docker compose --env-file .env.prod up --build -d
```
The web container runs `collectstatic` and migrations automatically on startup.
3. Restore database from dump (optional)
```
gunzip < dump_file.sql.gz | docker compose exec -T db psql -U bom_user -d bom_db
```

## Production operations

Production services use `restart: unless-stopped`, so containers recover from crashes and reboots but stay down after an intentional `docker stop`.

Postgres exposes a healthcheck; `web` and `backup` wait for a healthy database before starting, which reduces crash-loops during startup.

Container logs are rotated automatically (`10m` max size, `3` files per service). The optional `monitor` service watches `web`, `caddy`, `db`, and `backup` for restart loops and appends alerts to `monitoring/restart-loops.log` when a container's restart count crosses `RESTART_ALERT_THRESHOLD` (default `5`). Tune via `.env.prod`:

```
RESTART_ALERT_THRESHOLD=5
CHECK_INTERVAL_SECONDS=60
```

View alerts:

```
tail -f monitoring/restart-loops.log
```

## Backup and restore database (If using docker-compose and postgres)
Backup:
```
docker compose exec -T db pg_dump -c -U bom_user bom_db | gzip > ./dump_bom_db_$(date +"%Y-%m-%d_%H_%M_%S").sql.gz
```
Restore:
```
gunzip < dump_file.sql.gz | docker compose exec -T db psql -U bom_user -d bom_db
```
Restore (Unzipped dump on Windows):
```
cat dump_file.sql | docker compose exec -T db psql -U bom_user -d bom_db
```

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
    'materializecssform',
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
The base template can be customized to your pleasing. Just add the following configuration to your settings.py:

```
BOM_CONFIG = {
    'base_template': 'base.html',
}
```

where `base.html` is your base template.

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
