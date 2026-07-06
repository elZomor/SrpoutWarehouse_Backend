# Ticket Auto-Ship — Fully Autonomous, Ticket → PR

Runs a Jira ticket all the way to an open PR **with no interactive approvals** — don't ask the user "should I proceed?" at any point in this command. Takes a Jira issue key as its argument (e.g. `SW-123`). If none was given, ask for one (that's the one thing you can't proceed without).

This command **stops once the PR is open**. It never merges. For the fully-autonomous version that also merges and closes out, use `/ticket-auto-merge` instead.

The only reasons to stop before the end are genuine failures you can't safely resolve yourself (see each step) — not requests for permission.

---

## Step 1 — Plan

Fetch the issue via Rovo MCP `getJiraIssue`. Load the `django-conventions` skill. Read current `inventory/models.py`, `sprout_warehouse/settings.py`, `sprout_warehouse/urls.py` for present state. Produce a concrete plan (models, serializers, viewset, routes, admin, migration, tests) mapped to the ticket's AC. Do not present it for approval — proceed straight to Step 2, but keep the plan to include in your final report.

---

## Step 2 — Branch and implement

Create/check out `feature/<JIRA-KEY>-<slug>`. Write the code per the plan. Run `python manage.py makemigrations`. Run `black .` / `flake8 .` and self-fix in a loop until clean. Run `python manage.py test`; if anything fails, fix and re-run (a few iterations is fine). If tests are still failing after reasonable attempts, stop and report — don't push broken code.

---

## Step 3 — Verify

Exercise the real endpoint(s) yourself (dev server + curl, or the Django test client) to confirm behavior matches the AC — don't just trust the unit tests. Do this automatically, don't ask first.

---

## Step 4 — Plan-compliance check

Diff the branch against `main`. If you find scope the plan didn't call for, or planned scope that's missing, this is a genuine judgment call you can't safely resolve alone — stop and report it instead of guessing. Otherwise, continue silently.

---

## Step 5 — CI gauntlet

Run, exactly as CI does:
```
black --check --diff .
flake8 .
bandit -c pyproject.toml -r .
python manage.py makemigrations --check --dry-run
coverage run manage.py test
coverage report
```
(env: `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS=localhost,127.0.0.1`, `USE_SQLITE=True`)

If something fails, attempt a fix and re-run (bounded — a couple of iterations). If it's still failing after that, stop and report rather than pushing red code.

---

## Step 6 — Commit, push, open PR, transition Jira

No prompts for any of these — just do them:
1. `git commit` with a message referencing the Jira key.
2. `git push -u origin <branch>`.
3. `gh pr create` with a description (summary, test plan, Jira link).
4. Transition the Jira issue to "In Review" via `transitionJiraIssue`, and comment the PR link via `addCommentToJiraIssue`.

---

## Step 7 — Report and stop

Report: the plan you used, branch name, PR URL, Jira status. Note explicitly that this command stops here — mention `/code-review` as the recommended next manual step, and `/ticket-auto-merge` or `/ticket-close` for continuing further.
