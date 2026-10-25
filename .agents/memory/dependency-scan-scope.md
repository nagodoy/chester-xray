---
name: Dependency scan scope
description: Distinguishing reproducible application dependencies from stale installed packages during vulnerability remediation.
---

Check both the declared dependency graph and installed extraneous packages before treating a dependency finding as resolved.

**Why:** The dependency scanner can report a clean manifest graph while vulnerable packages remain in an old, undeclared root installation. An npm prune also left those packages in place when the root manifest declared no dependencies.

**How to apply:** Audit the lockfile used by the actual build, inspect installed extraneous packages separately, and confirm any legacy tooling is optional before clearing its generated installation. Do not add unused packages to the application just to upgrade them.
