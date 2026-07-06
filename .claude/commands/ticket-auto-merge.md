# Ticket Auto-Merge — Fully Autonomous, Ticket → Merged & Closed

Runs a Jira ticket all the way to a merged PR and closed-out ticket **with no interactive approvals**. Takes a Jira issue key as its argument (e.g. `SW-123`).

The only safety gate in this command is Step 2, the automated code review — it's fail-closed (blocks merge on findings) but it is **not** an interactive approval, it's a correctness check. Nothing else in this flow asks the user anything.

---

## Step 1 — Ship

Run through `/ticket-auto-ship <JIRA-KEY>` exactly: plan → implement → verify → plan-compliance check → CI gauntlet → commit → push → open PR → transition Jira to "In Review". Same stop-on-genuine-failure conditions apply (broken tests/gauntlet you can't fix, or unexplained scope drift) — if `/ticket-auto-ship` would have stopped, stop here too.

---

## Step 2 — Automated review gate (fail-closed)

Run `/code-review` against the diff. This is not optional and not skippable:
- **If it returns any findings**: stop. Report the findings and the PR URL. Do not merge. The user can either fix and re-run `/ticket-address-review`, or re-invoke this command after that.
- **If it's clean**: continue silently to Step 3 — no need to report the clean result separately, just proceed.

Do not use `/code-review ultra` here — that's a billed, user-triggered cloud review and can't be launched autonomously by this command.

---

## Step 3 — Merge

Merge the PR: `gh pr merge --squash --delete-branch`. This deletes the remote branch as part of the merge, which simplifies close-out.

---

## Step 4 — Close out

1. `git checkout main && git pull`.
2. Delete the local feature branch (`git branch -d feature/<JIRA-KEY>-<slug>`) — the remote copy is already gone from Step 3's `--delete-branch`.
3. Transition the Jira issue to "Done" via `transitionJiraIssue`, and comment the merge commit SHA + PR URL via `addCommentToJiraIssue`.

---

## Step 5 — Report

Report: the plan used, PR URL, merge commit SHA, and final Jira status. This is the end of the ticket's lifecycle in this pipeline.
