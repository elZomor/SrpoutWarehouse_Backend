# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

This is a brand-new Django project scaffold (generated via `django-admin startproject sprout_warehouse` + `startapp inventory`). No models, views, URLs, serializers, or tests have been implemented yet — the app files (`inventory/models.py`, `inventory/views.py`, `inventory/admin.py`, `inventory/tests.py`) only contain the default Django boilerplate comments.

Notable gaps to be aware of:
- `inventory` is not yet registered in `INSTALLED_APPS` (`sprout_warehouse/settings.py`).
- `djangorestframework` is installed (`requirements.txt`) but not added to `INSTALLED_APPS`, and no DRF routers/serializers exist yet.
- No `.git` repository has been initialized yet.

## Environment

- Python 3.14, virtualenv at `.venv`; dependencies pinned in `requirements.txt`.
- Django 6.0.6, djangorestframework 3.17.1, gunicorn, psycopg2-binary, python-dotenv.
- Activate with `source .venv/bin/activate` before running any `manage.py` commands, or invoke via `.venv/bin/python manage.py ...`.
- Database is PostgreSQL (no more sqlite). All secrets/config (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `POSTGRES_*`, `DB_SUPERUSER_*`) live in `.env`, loaded via `python-dotenv` in `sprout_warehouse/settings.py`. `.env` is gitignored — copy `.env.example` to `.env` and fill in real values for any new environment. Running `manage.py` locally (outside Docker) still requires a reachable Postgres instance (e.g. `POSTGRES_HOST=localhost` if you run `docker compose up db` alone).

## Docker

`docker-compose.yml` defines two services:
- `db` — `postgres:17`, bootstrapped from `.env` (`POSTGRES_DB/USER/PASSWORD`), with a named volume `pgdata` and `db/init/` mounted to `/docker-entrypoint-initdb.d/`.
- `web` — built from `Dockerfile` (Python 3.14 slim + gunicorn), runs `manage.py migrate` then serves on `:8000`.

```bash
# Build and start everything (first run also initializes the DB via db/init/*.sh)
docker compose up -d --build

# Tail logs
docker compose logs -f web
docker compose logs -f db

# Run manage.py commands inside the running web container
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py test

# Stop (add -v to also drop the pgdata volume and force re-init on next `up`)
docker compose down
```

`db/init/01-init-superuser.sh` only runs the first time the `pgdata` volume is initialized (empty data dir) — it creates an *additional* PostgreSQL superuser role (`DB_SUPERUSER_NAME`/`DB_SUPERUSER_PASSWORD` from `.env`), separate from the app's own `POSTGRES_USER` role. To re-run it, drop the volume (`docker compose down -v`) and bring the stack back up.

## Common commands

These assume a local virtualenv with a reachable Postgres (see Environment above); inside Docker use `docker compose exec web ...` instead.

```bash
# Run the dev server
python manage.py runserver

# Make/apply migrations after changing models
python manage.py makemigrations
python manage.py migrate

# Run tests (whole project)
python manage.py test

# Run tests for a single app
python manage.py test inventory

# Run a single test case / method
python manage.py test inventory.tests.<TestClassName>
python manage.py test inventory.tests.<TestClassName>.<test_method_name>

# Django shell
python manage.py shell

# Create a Django admin superuser for /admin/
python manage.py createsuperuser
```

## Architecture

Standard single-project Django layout:

- `sprout_warehouse/` — project package: `settings.py` (config, reads from `.env`), `urls.py` (root URLconf, currently only mounts `/admin/`), `wsgi.py`/`asgi.py` (deployment entry points, `wsgi.py` is what gunicorn serves).
- `inventory/` — the first (currently empty) Django app, intended to hold the domain models/views for warehouse inventory. Follow standard Django app conventions here: models in `models.py`, request handling in `views.py`, admin registration in `admin.py`, migrations auto-generated into `inventory/migrations/`.
- `db/init/` — shell scripts mounted into the Postgres container's `/docker-entrypoint-initdb.d/`, run once on first DB initialization.
- `Dockerfile` / `docker-compose.yml` — containerize `web` (Django + gunicorn) and `db` (Postgres); see the Docker section below.

When adding a new app, remember to register it in `INSTALLED_APPS` in `sprout_warehouse/settings.py` and wire its URLs into `sprout_warehouse/urls.py` (e.g. via `include()`).

Since REST framework is already installed but unconfigured, if/when API endpoints are added: add `'rest_framework'` to `INSTALLED_APPS`, build serializers/viewsets in the relevant app, and register routes via a DRF router included from `sprout_warehouse/urls.py`.
