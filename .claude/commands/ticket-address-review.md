# Ticket Address Review — Feedback → Fixes → Re-ship

Precondition: a PR is already open for this ticket (via `/ticket-ship`) and has received feedback — either from `/code-review`/`/code-review ultra` or from a human reviewer on GitHub. Takes a Jira issue key as its argument (e.g. `SW-123`).

Every step that touches shared state (commit, push, resolving/replying to comments) requires **explicit confirmation from the user in chat** before running.

---

## Step 1 — Gather feedback

Collect all outstanding feedback for this PR:
- If `/code-review` was run in this conversation, use those findings directly.
- Otherwise, find the PR for this branch (`gh pr view --json number,url`) and pull review comments (`gh api repos/{owner}/{repo}/pulls/{number}/comments` and `gh pr view --json reviews`).

List each piece of feedback with enough context (file, line, comment) to act on it. If there's nothing outstanding, say so and stop — don't invent work.

---

## Step 2 — Load repo conventions

Load the `django-conventions` skill before making changes, same as `/ticket-implement`.

---

## Step 3 — Triage

For each piece of feedback, decide: fix it, or it needs a reply instead of a code change (e.g. reviewer asked a question, or the suggestion conflicts with something else in the ticket). Show this triage to the user before acting on anything you're not fixing outright — don't silently dismiss feedback.

---

## Step 4 — Fix

Make the agreed-upon code changes. Re-run the affected parts of the CI gauntlet (`black .`, `flake8 .`, `python manage.py test`) locally — don't re-run the full `/ticket-ship` Step 1/2 gauntlet from scratch unless the changes are broad enough to warrant it.

---

## Step 5 — Commit and push

Draft a commit message describing what feedback was addressed (e.g. `SW-123: address review feedback — <short summary>`). **Ask for explicit confirmation** before `git commit`, then **ask for explicit confirmation** before `git push`.

---

## Step 6 — Reply

For any feedback that was fixed, note that in chat so the user can reply on GitHub (or, if the user confirms, reply directly via `gh api` / `gh pr comment`). For feedback that wasn't a code change, surface your Step 3 reasoning so the user can respond to the reviewer themselves.

---

## Step 7 — Report

Summarize what changed and confirm the PR is updated. Remind the user this may need another `/code-review` pass if the changes were substantial.
