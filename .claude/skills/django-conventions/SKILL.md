---
name: django-conventions
description: Repo-specific Django/DRF conventions for sprout_warehouse_BE — models, serializers, viewsets, urls, admin, tests, migrations, and the exact style rules CI enforces. Load before writing or planning any Django code in this repo.
---

# django-conventions

## Existing resources — follow this pattern, don't reinvent it

`rest_framework` and `inventory` are registered in `INSTALLED_APPS`; `inventory/urls.py` exists with a `DefaultRouter` included from `sprout_warehouse/urls.py` under `api/`. Two resources are already implemented end-to-end and are the reference pattern for anything new: `Category` and `ProductType` (models, serializers, viewsets, admin, migrations, tests — see `inventory/models.py` / `serializers.py` / `views.py` / `admin.py` / `tests/`). Session auth (`LoginView`/`LogoutView`/`MeView`) is also live in `inventory/views.py`.

Both existing viewsets deliberately only mix in `ListModelMixin` + `CreateModelMixin` (no retrieve/update/destroy) because that's all their PRD story scoped — don't assume every new resource needs full CRUD; check the ticket's scope before adding mixins/routes beyond it.

## Per-feature file layout

For each domain concept, touch all of:

- **Model** — `inventory/models.py`. If the resource needs search, add a `SEARCH_FIELDS` tuple on the model (matches the existing `Category`/`ProductType` convention) and wire it into the viewset's `search_fields`.
- **Serializer** — one `ModelSerializer` per model, in `inventory/serializers.py`.
- **ViewSet** — a DRF viewset in `inventory/views.py` built from `GenericViewSet` + only the mixins the ticket actually scopes (`ListModelMixin`/`CreateModelMixin`/etc.), registered on the `DefaultRouter` in `inventory/urls.py`. Prefer viewsets + router over hand-wired paths so CRUD stays consistent across the app. Add `filter_backends = [SearchFilter]` + `search_fields` when the resource needs search, matching the existing pattern.
- **Admin** — register every new model in `inventory/admin.py` (project uses `django-unfold` as the admin theme — registrations still use the standard `admin.site.register` / `ModelAdmin` pattern, unfold reskins it automatically).
- **Migration** — generate with `python manage.py makemigrations`, never hand-write. CI runs `makemigrations --check --dry-run` and fails if one is missing.
- **Tests** — one module per resource under `inventory/tests/` (e.g. `test_<resource>.py`), following `test_categories.py`/`test_product_types.py`/`test_auth.py`. Add factories to `inventory/tests/factories.py` (`factory_boy` `DjangoModelFactory`, one per model) rather than building instances by hand.

## Correctness pitfalls (check before shipping, not just at review)

Recurring patterns that `/code-review` has caught after implementation on this repo — check for these while writing the code, not after:

- **Check-then-act races on DB constraints.** Don't pre-check a condition (e.g. `instance.related.count()`) and then perform a separate delete/update statement — a row can be created in between, and it duplicates a guarantee the DB already enforces. Prefer letting the DB constraint fire and catching the resulting exception (e.g. `on_delete=PROTECT` → catch `django.db.models.deletion.ProtectedError` around the delete itself). DRF's default exception handler does **not** translate `ProtectedError` to a 400 — an uncaught one 500s.
- **Don't call `get_object()` (or any single-row fetch) twice in one request.** If a custom `destroy`/`update` override fetches the instance and then calls `super().destroy(...)`/`super().update(...)`, that re-fetches internally — restructure to fetch once and call `perform_destroy`/`perform_update` directly.
- **Catch the specific exception the field/lookup can actually raise.** E.g. a lookup by a non-unique field (`User.email` has no unique constraint) can raise `MultipleObjectsReturned`, not just `DoesNotExist` — catch both wherever a "credentials/lookup didn't find exactly one row" path exists.
- **Serializer/computed fields that hit the DB per-row are an N+1 risk.** If a `SerializerMethodField` or property does a query, check whether the viewset's queryset needs `select_related`/`prefetch_related` before shipping, especially on list endpoints.
- **State that should be excluded (archived/soft-deleted rows) needs to be excluded everywhere it's reachable**, not just the default list action — e.g. filtering archived categories out of `list` but not out of a FK-selection queryset used elsewhere leaks them back in through a different endpoint.

## Style constraints (match CI exactly, don't rely on autofix)

- **black**: line-length 88 (`pyproject.toml`).
- **flake8**: max-line-length 88, `extend-ignore = E203,W503` (`.flake8`). The `F401` (unused import) ignore is only per-file-listed for the four pre-existing boilerplate files (`inventory/admin.py`, `models.py`, `tests.py`, `views.py`) — any new file you create must not have unused imports.
- **bandit**: excludes `.venv`, `migrations`, `tests` dirs — don't worry about triggering it from migrations or test files, but application code in `models.py`/`views.py`/`serializers.py` is scanned.
- Run `black .` and `flake8 .` after writing code and fix everything before considering a change done.

## Local env vars (needed to run manage.py commands outside Docker)

`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` are required; set `USE_SQLITE=True` to run against in-memory sqlite instead of needing a live Postgres (this is what CI does for the test job).

## Coverage bar

`pyproject.toml` sets `fail_under = 50` for `coverage report`. Write real tests for new behavior, but don't chase 100% — matching CI's bar is enough.
