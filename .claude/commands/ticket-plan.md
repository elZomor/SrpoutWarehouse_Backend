# Ticket Plan — Jira Ticket → Implementation Plan

You are turning an existing Jira ticket into a concrete Django implementation plan for the `sprout_warehouse_BE` repo. Take a Jira issue key as your argument (e.g. `SW-123`). If none was given, ask for one before proceeding.

Do **not** write or edit any application code during this command. This command only produces a plan for the user to approve.

---

## Step 1 — Fetch the ticket

Use the Atlassian Rovo MCP `getJiraIssue` tool to fetch the issue by key. Pull out:
- Summary / title
- Description (user story)
- Acceptance Criteria (Gherkin, if present — these are usually already written by the `po-agent` command)
- Test cases table, if present
- Story points / priority (for context, not action)

If the ticket has no AC or test cases, note that as a gap in your plan output rather than inventing scope.

---

## Step 2 — Load repo conventions

Load the `django-conventions` skill before designing anything. It documents this repo's current state, per-feature file layout, and CI-matching style rules.

---

## Step 3 — Read current app state

Read `inventory/models.py`, `sprout_warehouse/settings.py`, and `sprout_warehouse/urls.py` (and `inventory/urls.py`/`inventory/serializers.py` if they exist) to see what's already there. Don't assume the one-time wiring gaps noted in `django-conventions` are still open — check.

---

## Step 4 — Produce the plan

Write a concrete plan, mapped 1:1 to the ticket's Acceptance Criteria, covering:
- Any one-time wiring still needed (`INSTALLED_APPS`, `inventory/urls.py`, router include).
- Exact model(s)/fields to add or change.
- Serializer(s).
- ViewSet(s) and the actions/routes they expose.
- Admin registration(s).
- Migration (name it descriptively; don't hand-write the file, just note it'll be generated).
- Test list — one test per AC/test case, named descriptively.

Flag anything ambiguous in the ticket rather than guessing.

---

## Step 5 — Present and stop

Show the plan in chat. Do not proceed to implementation. Tell the user to run `/ticket-implement <JIRA-KEY>` once they've reviewed and approved it.
