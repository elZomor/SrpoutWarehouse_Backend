# Ticket Close — Post-merge Close-out

Precondition: the PR for this ticket has already been merged (by the user, manually). Takes a Jira issue key as its argument (e.g. `SW-123`).

This command only cleans up after a merge that already happened — it never merges anything itself. If you can't confirm the PR is actually merged, stop and ask rather than assuming.

---

## Step 1 — Confirm the merge

Check the PR status: `gh pr view --json state,mergedAt,mergeCommit` for the branch tied to this ticket. If it's not merged, stop and tell the user — don't proceed with cleanup for an unmerged PR.

---

## Step 2 — Sync local main

**Ask for explicit confirmation**, then:
```
git checkout main
git pull
```

---

## Step 3 — Delete the feature branch

**Ask for explicit confirmation** before deleting the branch:
```
git branch -d feature/<JIRA-KEY>-<slug>
git push origin --delete feature/<JIRA-KEY>-<slug>   # only if the remote branch wasn't already auto-deleted on merge
```
If the branch is already gone (e.g. GitHub auto-deletes on merge), just note that and skip.

---

## Step 4 — Close out the Jira ticket

**Ask for explicit confirmation** before:
1. Transitioning the Jira issue to "Done" (or the repo's equivalent terminal status) via the Rovo MCP `transitionJiraIssue` tool.
2. Adding a comment with the merge commit SHA and PR URL via `addCommentToJiraIssue`.

If these Rovo tools aren't permitted yet, report what you would have done and let the user handle it manually.

---

## Step 5 — Report

Confirm: branch cleaned up, `main` is up to date locally, and the Jira ticket status. This is the last step of the pipeline for this ticket.
