---
name: Workflow listener cleanup
description: Operational guidance for stale application processes after workflow restarts.
---

When a workflow restart fails because port 5000 is already in use, verify which process owns the listener before retrying; a previous application process can remain alive after the workflow transition.

**Why:** Re-running the workflow without clearing the stale listener produces another bind failure even though the application itself is healthy.

**How to apply:** Check the listener and process tree, terminate only the stale instance belonging to this project, then restart the configured workflow once and confirm the health endpoint.