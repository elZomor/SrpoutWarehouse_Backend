# Ticket Pipeline — Full Orchestration

Runs a Jira ticket through the full pipeline to a merged, closed-out ticket: plan → implement → verify → ship → review (with a feedback loop) → merge reminder → close-out. Takes a Jira issue key as its argument (e.g. `SW-123`). If none was given, ask for one.

This command is a sequencer — it doesn't duplicate the logic of the stages, it runs them and stops at each human checkpoint.

---

## Stage 1 — Plan

Run the `/ticket-plan <JIRA-KEY>` command.

**Checkpoint**: present the plan and stop. Do not continue to Stage 2 until the user explicitly approves the plan in chat.

---

## Stage 2 — Implement

Run the `/ticket-implement <JIRA-KEY>` command.

---

## Stage 3 — Verify

Run the `/verify` skill against the change to exercise the real endpoint(s), not just the unit tests written in Stage 2.

**Checkpoint**: report the implementation + verification results and stop. Do not continue to Stage 4 until the user explicitly says to proceed to shipping.

---

## Stage 4 — Ship

Run the `/ticket-ship <JIRA-KEY>` command. Note that this command has its own internal confirmation gates (commit, push, PR, Jira transition) — let those run as designed, don't skip them.

**Checkpoint**: once the PR is open, stop.

---

## Stage 5 — Review, with a feedback loop

Tell the user to run `/code-review` (or `/code-review ultra` for higher-stakes tickets) against the PR before merging. Do not run it automatically — the user may want to iterate on the diff first.

If the review (or a human reviewer on GitHub) comes back with change requests, run `/ticket-address-review <JIRA-KEY>` to fix them and re-push. This can loop as many times as needed — stay on Stage 5 until the PR is genuinely clean.

**Checkpoint**: once review is clean, stop.

---

## Stage 6 — Merge reminder

Remind the user that merging (`gh pr merge`) is always a manual, user-run action. This pipeline never merges on its own, regardless of how clean CI or the review comes back.

---

## Stage 7 — Close-out

Once the user confirms the PR has been merged, run `/ticket-close <JIRA-KEY>` to sync `main`, delete the feature branch, and transition the Jira ticket to Done. This is the final stage — the ticket's lifecycle in this pipeline ends here.
