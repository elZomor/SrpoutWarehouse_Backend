---
name: verify
description: How to drive a running instance of this Django API for runtime verification (not just tests). Load before verifying any backend change.
---

# verify (SrpoutWarehouse_Backend)

## Get a handle

A real dev stack is usually already running (check first):

```bash
docker compose ps
```

If `db` and `web` are both `Up`, use them directly — `docker-compose.override.yml`
bind-mounts the repo into `web`, so code edits (and new migrations, once applied)
are live immediately, no rebuild needed.

If nothing is running: `docker compose up -d --build` (see root CLAUDE.md / repo
CLAUDE.md Docker section).

New migration in your diff? Apply it to the running container before testing:

```bash
docker compose exec web python manage.py migrate
```

## Get a session

Auth is a server-side session cookie (not a token) — see Technical Notes v1.0.
Curl against the real HTTP surface (`http://localhost:8000`) using a cookie jar,
the same way the real SPA would:

```bash
JAR=/tmp/verify_cookies.txt
# Make a throwaway user once per session (or reuse if it already exists):
docker compose exec web python manage.py shell -c "
from django.contrib.auth.models import User
u, _ = User.objects.get_or_create(username='verifyuser', defaults={'email': 'verify@example.com'})
u.set_password('verify-pass-123'); u.save()
"

curl -s -c $JAR -H "Content-Type: application/json" \
  -d '{"email":"verify@example.com","password":"verify-pass-123"}' \
  http://localhost:8000/api/auth/login/ -w "\nHTTP_STATUS:%{http_code}\n"
```

CSRF is enforced on unsafe methods — pull the token out of the cookie jar and
send it back as `X-CSRFToken` on every POST/PUT/PATCH/DELETE:

```bash
CSRF=$(grep csrftoken $JAR | awk '{print $7}')
curl -s -b $JAR -c $JAR -H "Content-Type: application/json" -H "X-CSRFToken: $CSRF" \
  -d '{...}' http://localhost:8000/api/<endpoint>/ -w "\nHTTP_STATUS:%{http_code}\n"
```

Worth probing explicitly, since both are enforced app-wide and easy to silently
break: a POST **without** `X-CSRFToken` (expect 403) and a request with no
session cookie at all (expect 403, `IsAuthenticated` is the default).

## Cleanup

Dev DB is shared/persistent across sessions (real Postgres via the `db`
container) — delete whatever you created when done:

```bash
docker compose exec web python manage.py shell -c "
from django.contrib.auth.models import User
User.objects.filter(username='verifyuser').delete()
# plus any model rows you created for the test
"
```

## Gotchas

- `docker compose` commands must run from this repo's root (where
  `docker-compose.yml` lives) — cwd resets between tool calls in this
  environment, so `cd` back explicitly each time rather than assuming it stuck.
- Endpoints that only mix in `ListModelMixin`/`CreateModelMixin` (check
  `inventory/views.py`) have no detail route — `GET/PUT/PATCH/DELETE
  .../<id>/` correctly 404s. Don't mistake that for a bug if the ticket
  scoped out retrieve/update/destroy.
