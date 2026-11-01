---
name: Replit config updates
description: The required platform workflow for changing the workspace `.replit` file.
---

Do not patch `.replit` directly. Write the complete intended TOML to a temporary file inside the workspace, then call `verifyAndReplaceDotReplit` with its absolute path so the platform validates and replaces the file. Remove the temporary file afterward.

**Why:** Direct edits to `.replit` are rejected by the environment; the validation helper is the supported path.

**How to apply:** Use this whenever a project change needs to modify `.replit`; preserve the full existing file content in the temporary replacement.
