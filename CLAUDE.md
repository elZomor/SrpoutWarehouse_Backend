# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

Past the initial scaffold: `rest_framework` and `inventory` are registered in `INSTALLED_APPS`, session-based auth (`login`/`logout`/`me`) is live, and the `inventory` app has two real resources — `Category` and `ProductType` — each with a model, serializer, list+create-only `ModelViewSet`, admin registration, migrations, and tests. Treat this as the pattern to replicate for new domain resources, not an empty scaffold.

## Environment

- Python 3.14, virtualenv at `.venv`; dependencies pinned in `requirements.txt` / `requirements-dev.txt`.
- Django 6.0.6, djangorestframework 3.17.1, gunicorn, psycopg2-binary, python-dotenv.
- `django-cors-headers`, `django-filter`, `django-extensions`, `factory_boy` (test fixtures), `qrcode`, `weasyprint`, `Pillow` — all already installed and wired where noted below.
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

`docker-compose.override.yml` is auto-merged on top of `docker-compose.yml` by plain `docker compose up` (no flags needed) and bind-mounts the repo into `web`, so code/`.env` edits are picked up live by `runserver`'s autoreloader — no `--build` needed after the first build. Local-dev-only; nothing else in the repo (CI included) references it.

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

# Run a single test module / case / method (tests live in the inventory/tests/ package)
python manage.py test inventory.tests.test_categories
python manage.py test inventory.tests.test_categories.<TestClassName>
python manage.py test inventory.tests.test_categories.<TestClassName>.<test_method_name>

# Check for model changes missing a migration (what CI runs)
python manage.py makemigrations --check --dry-run

# Coverage (what CI runs; fail_under = 50 in pyproject.toml)
coverage run manage.py test
coverage report

# Django shell
python manage.py shell

# Create a Django admin superuser for /admin/
python manage.py createsuperuser
```

## CI (`.github/workflows/ci.yml`)

Three jobs run on every PR — match these locally before considering a change done:
- **Lint**: `black --check --diff .` and `flake8 .`.
- **Review Bot**: `bandit -c pyproject.toml -r .` (excludes `.venv`, `migrations`, `tests` per `pyproject.toml`).
- **Unit tests**: `makemigrations --check --dry-run` (fails if a model change is missing its migration), then `coverage run manage.py test` + `coverage report` against sqlite (`USE_SQLITE=True`) rather than Postgres.

## Architecture

Standard single-project Django layout:

- `sprout_warehouse/` — project package: `settings.py` (config, reads from `.env`), `urls.py` (root URLconf: `admin/` + `api/` → `include("inventory.urls")`), `wsgi.py`/`asgi.py` (deployment entry points, `wsgi.py` is what gunicorn serves).
- `inventory/` — the single domain app so far, holding both auth views and warehouse resources:
  - `models.py` — see the file for current fields; both resources define a `SEARCH_FIELDS` tuple consumed by their viewset's `SearchFilter`.
  - `serializers.py` — one `ModelSerializer` per model, plus `UserSerializer` (read-only, used for auth responses) and `LoginSerializer` (plain `Serializer` for the login payload).
  - `views.py` — `LoginView`/`LogoutView`/`MeView` (plain `APIView`s, session-based); `ProductTypeViewSet` (`ListModelMixin` + `CreateModelMixin` only — no retrieve/update/destroy per its PRD story yet); `CategoryViewSet` (adds `DestroyModelMixin` plus a custom `archive` action — delete is blocked while Product Types are assigned, archive is the alternative). Per-resource mixin scope tracks each PRD story; don't treat a missing route as a bug unless the ticket says otherwise — check the viewset's actual mixins/actions rather than assuming.
  - `urls.py` — `auth/login/`, `auth/logout/`, `auth/me/` as explicit paths, plus a `DefaultRouter` registering `product-types` and `category` viewsets; all included under `/api/`.
  - `admin.py` — one `ModelAdmin` per model (registration API is the standard `admin.site.register`/`@admin.register`).
  - `tests/` — package (not a single `tests.py`) with one module per resource (`test_auth.py`, `test_categories.py`, `test_product_types.py`) plus `factories.py` (`factory_boy` `DjangoModelFactory` per model).
- `db/init/` — shell scripts mounted into the Postgres container's `/docker-entrypoint-initdb.d/`, run once on first DB initialization.
- `Dockerfile` / `docker-compose.yml` — containerize `web` (Django + gunicorn) and `db` (Postgres); see the Docker section below.

Auth: `SessionAuthentication` + `IsAuthenticated` are the DRF defaults (`REST_FRAMEWORK` in `settings.py`); `LoginView` explicitly disables authentication classes since there's no session/CSRF cookie yet on first contact (see the comment in `views.py`), and issues the CSRF cookie via `get_token(request)` right after `login()` so the SPA can send `X-CSRFToken` on subsequent requests. Session cookie is `HttpOnly`, backed by the DB session engine, and expires after 1 day (`SESSION_COOKIE_AGE`).

When adding a new domain resource, follow the `Category`/`ProductType` pattern above rather than introducing a new one (see the `django-conventions` skill for the exact per-feature checklist).

## Caveman mode (always-on, ultra)

Respond terse, caveman-style, **ultra** intensity, by default in this repo. All technical substance stay; only fluff die.

- Drop: articles (a/an/the), filler (just/really/basically/actually), pleasantries, hedging.
- Fragments OK. Short synonyms. Technical terms, code, commands, error strings exact/unchanged.
- Pattern: `[thing] [action] [reason]. [next step].`
- Boundaries: code/commits/PRs written normal — this rule governs prose responses only.
- Auto-clarity override: drop caveman for security warnings, irreversible-action confirmations, or when compression itself risks misread. Resume after.
- Switch level: `/caveman lite|full|ultra|wenyan`. Stop: "stop caveman" or "normal mode".
