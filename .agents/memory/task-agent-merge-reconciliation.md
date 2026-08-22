---
name: Task-agent merge reconciliation
description: Preserve recent user-facing customizations when isolated task work is merged.
---

After any isolated task-agent merge that overlaps user-facing work, compare the
merged result with the latest user instructions before considering the merge
finished.

**Why:** An isolated feature merge reintroduced an acceptance gate and legacy
informational content that had already been removed in the main workspace.

**How to apply:** Recheck the exact customized flow and search for content the
user explicitly removed. Reconcile only the stale overlap while preserving the
new feature delivered by the task agent.