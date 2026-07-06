# Ticket Implement — Plan → Django Code

Precondition: this command assumes `/ticket-plan <JIRA-KEY>` has already run for this ticket and the user has approved the resulting plan in chat. If no approved plan exists in this conversation, stop and ask the user to run `/ticket-plan` first (or paste the plan) before continuing.

Takes a Jira issue key as its argument (e.g. `SW-123`).

---

## Step 1 — Branch

Create and check out a feature branch named `feature/<JIRA-KEY>-<short-slug>`, where `<short-slug>` is a few kebab-case words from the ticket summary. If a branch matching this ticket already exists, check it out instead of creating a new one — don't ask, just check `git branch` and act accordingly, but tell the user which you did.

---

## Step 2 — Load repo conventions

Load the `django-conventions` skill (this is a fresh command invocation, so load it again even if a prior command in this session already did).

---

## Step 3 — Write the code

Following the approved plan exactly:
- Model(s)/fields in `inventory/models.py`.
- Serializer(s) in `inventory/serializers.py`.
- ViewSet(s) in `inventory/views.py`, registered on the router in `inventory/urls.py`.
- Admin registration(s) in `inventory/admin.py`.
- Any one-time wiring the plan called out (`INSTALLED_APPS`, url includes).
- Tests per the plan's test list.

Don't add anything the plan didn't call for.

---

## Step 4 — Migrations and style

1. Run `python manage.py makemigrations`.
2. Run `black .`, then `flake8 .`. Fix anything either one flags. Repeat until both are clean.

---

## Step 5 — Local tests

Run `python manage.py test` with the required env vars (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `USE_SQLITE=True` — see `django-conventions`). Fix any failures before moving on.

---

## Step 6 — Report

Summarize the diff (files touched, what each does) in chat. Do **not** commit, push, or open a PR — that's `/ticket-ship`. Suggest the user run the `/verify` skill next to exercise the real endpoint before shipping.
