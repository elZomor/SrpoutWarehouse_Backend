# Ticket Auto-Merge — Fully Autonomous, Ticket → Merged & Closed

Runs a Jira ticket all the way to a merged PR and closed-out ticket **with no interactive approvals**. Takes a Jira issue key as its argument (e.g. `SW-123`).

The only safety gate in this command is Step 2, the automated code review — it's fail-closed (blocks merge on findings) but it is **not** an interactive approval, it's a correctness check. Nothing else in this flow asks the user anything.

Steps 2 and 3 run in **fresh, isolated subagents** (via the `Agent` tool, not a fork) rather than inline in this conversation. This keeps their context clean — no risk of stale or auto-compacted plan/implementation detail leaking into the review or the merge/close-out steps. They are handed only the ticket cache file and PR/Jira identifiers below, nothing else from this session.

---

## Step 1 — Ship

Run through `/ticket-auto-ship <JIRA-KEY>` exactly: plan → implement → verify → plan-compliance check → CI gauntlet → commit → push → open PR → transition Jira to "In Review". Same stop-on-genuine-failure conditions apply (broken tests/gauntlet you can't fix, or unexplained scope drift) — if `/ticket-auto-ship` would have stopped, stop here too.

Once `/ticket-plan`'s `getJiraIssue` fetch (inside the ship flow) has run, write the issue's summary, description, AC, and test cases to a cache file at `/tmp/ticket-auto-merge-<JIRA-KEY>.md`. Steps 2 and 3 read guardrails from this file instead of re-fetching Jira.

---

## Step 2 — Automated review gate (fail-closed)

Dispatch a fresh `general-purpose` Agent (not a fork) with only: the PR number/URL, the cache file path (`/tmp/ticket-auto-merge-<JIRA-KEY>.md`), and this instruction — read the cache file for AC/guardrails, run `gh pr diff` for the current diff (do not rely on any diff described in the prompt), load the `django-conventions` skill, then run `/code-review` against that diff and report findings only (no fixing).

This is not optional and not skippable:
- **If it returns any findings**: stop. Delete the cache file (`rm /tmp/ticket-auto-merge-<JIRA-KEY>.md`). Report the findings and the PR URL. Do not merge. The user can either fix and re-run `/ticket-address-review`, or re-invoke this command after that.
- **If it's clean**: continue silently to Step 3 — no need to report the clean result separately, just proceed.

Do not use `/code-review ultra` here — that's a billed, user-triggered cloud review and can't be launched autonomously by this command.

---

## Step 3 — Merge + close out

Dispatch a second fresh Agent (not a fork) with only: the PR number, the Jira key, and the cache file path. It should:

1. Merge the PR: `gh pr merge --squash --delete-branch`. This deletes the remote branch as part of the merge, which simplifies close-out.
2. `git checkout main && git pull`.
3. Delete the local feature branch (`git branch -d feature/<JIRA-KEY>-<slug>`) — the remote copy is already gone from step 1's `--delete-branch`.
4. Transition the Jira issue to "Done" via `transitionJiraIssue`, and comment the merge commit SHA + PR URL via `addCommentToJiraIssue` (use the cache file for any AC context needed in the comment).
5. Delete the cache file (`rm /tmp/ticket-auto-merge-<JIRA-KEY>.md`) as its final action.
6. Report back the merge commit SHA and final Jira status to this conversation.

---

## Step 4 — Report

Report: the plan used, PR URL, merge commit SHA, and final Jira status. This is the end of the ticket's lifecycle in this pipeline.
