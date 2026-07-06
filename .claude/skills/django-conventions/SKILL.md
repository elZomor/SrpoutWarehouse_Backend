---
name: django-conventions
description: Repo-specific Django/DRF conventions for sprout_warehouse_BE — models, serializers, viewsets, urls, admin, tests, migrations, and the exact style rules CI enforces. Load before writing or planning any Django code in this repo.
---

# django-conventions

## One-time wiring gaps (check every time — may already be closed)

As of the last check, none of this exists yet. Before adding a feature that depends on it, verify and close any gap still open:

- `rest_framework` and `inventory` are not registered in `INSTALLED_APPS` (`sprout_warehouse/settings.py`).
- `inventory/urls.py` does not exist.
- `sprout_warehouse/urls.py` only mounts `/admin/` — no app include, no DRF router.

To close: add `"rest_framework"` and `"inventory"` to `INSTALLED_APPS`; create `inventory/urls.py` with a `rest_framework.routers.DefaultRouter`; `include()` it from `sprout_warehouse/urls.py` under an `api/` prefix.

## Per-feature file layout

For each domain concept, touch all of:

- **Model** — `inventory/models.py`.
- **Serializer** — one `ModelSerializer` per model, in `inventory/serializers.py` (create this file the first time it's needed).
- **ViewSet** — a DRF `ModelViewSet` (not a raw `APIView`) in `inventory/views.py`, registered on the `DefaultRouter` in `inventory/urls.py`. Prefer viewsets + router over hand-wired paths so CRUD stays consistent across the app.
- **Admin** — register every new model in `inventory/admin.py` (project uses `django-unfold` as the admin theme — registrations still use the standard `admin.site.register` / `ModelAdmin` pattern, unfold reskins it automatically).
- **Migration** — generate with `python manage.py makemigrations`, never hand-write.
- **Tests** — `inventory/tests.py` (split into an `inventory/tests/` package once it outgrows one file). Use `factory_boy` for model fixtures — it's already a dev dependency.

## Style constraints (match CI exactly, don't rely on autofix)

- **black**: line-length 88 (`pyproject.toml`).
- **flake8**: max-line-length 88, `extend-ignore = E203,W503` (`.flake8`). The `F401` (unused import) ignore is only per-file-listed for the four pre-existing boilerplate files (`inventory/admin.py`, `models.py`, `tests.py`, `views.py`) — any new file you create must not have unused imports.
- **bandit**: excludes `.venv`, `migrations`, `tests` dirs — don't worry about triggering it from migrations or test files, but application code in `models.py`/`views.py`/`serializers.py` is scanned.
- Run `black .` and `flake8 .` after writing code and fix everything before considering a change done.

## Local env vars (needed to run manage.py commands outside Docker)

`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` are required; set `USE_SQLITE=True` to run against in-memory sqlite instead of needing a live Postgres (this is what CI does for the test job).

## Coverage bar

`pyproject.toml` sets `fail_under = 50` for `coverage report`. Write real tests for new behavior, but don't chase 100% — matching CI's bar is enough.
