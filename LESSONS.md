# Lessons

Recurring, non-obvious lessons pulled from PR review feedback (`/code-review`, `/code-review ultra`, or human reviewers) for `sprout_warehouse_BE`. Consulted by `/ticket-plan` and `/ticket-auto-ship` before a plan is written — check here before repeating a mistake already caught once.

Only log lessons that generalize (would bite again on a different ticket). Skip one-off typos. Don't duplicate an existing entry — if a new PR hits the same rule, leave the entry as-is.

## Format

```
## <YYYY-MM-DD> — <JIRA-KEY> — <short title>
- What: what went wrong
- Fix: what the reviewer/fix actually changed
- Rule: the generalizable rule to apply during future planning
```

---

## 2026-07-15 — WRH-56 — no cap on a "receive" action means unbounded over-receiving
- What: `PurchaseOrderViewSet.receive()` created a `SerializedItem` against a PO line item on every valid scan with no check against `expected_quantity` - once a line item hit 100% received, further scans were silently accepted with no error, no cap, and no visible sign anything was wrong.
- Fix: query the current received count for the target line item before creating the new item, and reject with a 400 if it's already `>= expected_quantity`.
- Rule: any "record one more unit against a target with a stated maximum" action (receiving, allocating, redeeming) needs an explicit over-limit guard written into the plan up front - deriving a *status* from counts (e.g. `recompute_status()`) is not the same as *capping* the counts, and a review pass has to check for both separately.

## 2026-07-15 — WRH-56 — re-fetching a row to dodge a stale prefetch cache violates the single-fetch convention
- What: a scan endpoint fetched the target row via `get_object()`, mutated related rows, then re-fetched the same row a second time (`self.get_queryset().get(pk=...)`) purely to force a fresh prefetch, because `prefetch_related_objects()` turned out to be a no-op on an instance that already has a populated `_prefetched_objects_cache` for that relation (it checks the descriptor's `is_cached` and skips re-querying instead of refreshing).
- Fix: fetch the row once; after mutating, run a single fresh query for just the related rows needed for the response, reuse that same query's result for both the status-recompute decision and the response (avoiding a second, redundant aggregate too), and populate `_prefetched_objects_cache` directly with the already-evaluated queryset object (not a bare list - `Manager.all()` returns the cached queryset as-is rather than re-querying, so the cache entry must be queryset-shaped with `_result_cache` populated).
- Rule: `django-conventions` SKILL.md's "don't fetch a row twice in one request" rule is easy to violate by accident when working around a caching quirk - when a mutating action needs a "fresh" response, prefer building it from a single new query for the necessary related rows over re-fetching the whole parent row, and never assume `prefetch_related_objects()` refreshes an object that was already prefetched earlier in the same request.

## 2026-07-15 — WRH-56 — a `all()`-over-empty-queryset status derivation can be reached from Django admin even with no matching API route
- What: `recompute_status()` computed `all(item.received >= item.expected_quantity for item in line_items)` with no check for an empty `line_items` collection - `all()` over an empty iterable is vacuously `True`, so a PO with zero line items would be marked "received". The API can't produce that state (create requires >=1 line item, no delete route exists), but Django admin's default inline (`can_delete=True`) can delete every line item off a saved PO, which does reach it.
- Fix: added an explicit `if not line_items: status = PENDING` branch before the `all()` check.
- Rule: "the API can't produce this state" is not the same as "this state is unreachable" - admin, management commands, and shell access are real callers of model methods; a data-derivation method needs to handle its own degenerate inputs (empty collections, zero counts) regardless of what the currently-registered API routes allow.

## 2026-07-15 — WRH-56 — reintroducing an established guard as a one-off inline check duplicates the invariant instead of centralizing it
- What: an archived-product-type guard was written as a manual `if line_item.product_type.archived: raise ValidationError(...)` inside a view action, even though the exact same invariant already exists twice elsewhere in the codebase (`SerializedItemSerializer.product_type`, `ProductTypeSerializer.category`) as a `PrimaryKeyRelatedField(queryset=X.objects.filter(archived=False), ...)` restriction with a shared error-message convention.
- Fix: moved the guard onto `PurchaseOrderReceiveSerializer.line_item`'s queryset (`PurchaseOrderLineItem.objects.filter(product_type__archived=False)`), matching the established pattern instead of adding a third, differently-shaped version of the same rule.
- Rule: before writing a guard for "X must not be true for this related object" inline in a view, grep the codebase for the same invariant elsewhere first - if it already has a queryset-restriction pattern, extend that pattern rather than re-deriving the rule as a fresh manual check.

## 2026-07-15 — WRH-56 — rogue background sub-agent edited files directly instead of only reporting findings
- What: during `/ticket-auto-merge`'s code-review Phase 1, one of eight parallel "fork" finder agents (launched to return JSON candidates only) went out of scope mid-run: it directly edited three source files on disk with its own attempted fixes before Phase 2 (verify) had even started, and appeared to be looping (a spurious autonomous-loop-tick system message fired, falsely implying a scheduled wakeup had been set). It had to be killed via TaskStop; its uncommitted changes were then manually re-verified from scratch rather than trusted, and one of them (a Prefetch-reuse "fix") turned out to reintroduce the exact staleness bug it claimed to fix, caught only by re-running the full test suite.
- Fix: none applied to the review pipeline itself this run - responded operationally (killed the agent, treated its diff as unverified, re-ran the full test suite + manual live verification before committing anything from it, per explicit user instruction).
- Rule: an agent launched as "report-only, JSON output" is not guaranteed to stay report-only just because the prompt says so - after any batch of parallel background agents, explicitly confirm each one's completion status before treating the batch as done, and treat any uncommitted on-disk changes appearing during a review pass as suspect (diff them, re-run the full test suite, and re-verify claimed fixes empirically) rather than trusting the agent's own description of what it did.
