# Ticket Ship — Code → PR

Precondition: implementation for this ticket is done and locally tested (normally via `/ticket-implement` and ideally `/verify`). Takes a Jira issue key as its argument (e.g. `SW-123`).

Every step below that touches shared state (commit, push, PR, Jira transition) requires an **explicit go-ahead from the user in chat** before running — don't rely solely on the ambient tool-permission prompt, actually ask.

---

## Step 1 — Plan-compliance check

Before running anything else, diff the branch against `main` (`git diff main...HEAD --stat` and a full `git diff main...HEAD` for substance) and compare it against the plan approved in `/ticket-plan`. Flag:
- Any file touched that the plan didn't call for.
- Any model/serializer/viewset/route/admin registration in the plan that's missing from the diff.
- Any leftover debug code, commented-out blocks, or stray `print`/`TODO` markers.

If you find unexplained scope, stop and show it to the user before continuing — don't silently ship extra changes or silently drop planned ones. If the diff matches the plan (or the user confirms the extra scope is intentional), continue.

---

## Step 2 — Run the CI gauntlet locally

Run these in order, exactly as CI does (see `.github/workflows/ci.yml`):

```
black --check --diff .
flake8 .
bandit -c pyproject.toml -r .
python manage.py makemigrations --check --dry-run
coverage run manage.py test
coverage report
```

Required env vars: `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS=localhost,127.0.0.1`, `USE_SQLITE=True`.

If anything fails, stop and report it — fix and re-run before continuing. Do not proceed to Step 3 until all of the above pass clean.

---

## Step 3 — Commit

Draft a commit message referencing the Jira key (e.g. `SW-123: <summary>`). Show it to the user and **wait for explicit confirmation** before running `git commit`.

---

## Step 4 — Push

**Ask for explicit confirmation** before running `git push -u origin <branch>`.

---

## Step 5 — Open the PR

Draft a PR title and description with:
- Summary of the change
- Test plan (what you ran in Step 2, plus any manual verification from `/verify`)
- Link to the Jira ticket

Show it to the user and **ask for explicit confirmation** before running `gh pr create`.

---

## Step 6 — Jira transition (optional)

**Ask for explicit confirmation** before transitioning the Jira issue to "In Review" via the Rovo MCP `transitionJiraIssue` tool (and optionally leaving a comment with the PR link via `addCommentToJiraIssue`). If the user declines or these tools aren't permitted yet, just report the PR URL and skip this step — it's not required for the pipeline to be useful.

---

## Step 7 — Report

Report the branch name, PR URL, and Jira ticket status. Remind the user that:
- `/code-review` (or `/code-review ultra` for higher-stakes changes) should run before merge.
- If review comes back with change requests, use `/ticket-address-review <JIRA-KEY>` rather than fixing ad hoc.
- Merging is always a manual step — this command never runs `gh pr merge`. Once merged, run `/ticket-close <JIRA-KEY>` to close out.
