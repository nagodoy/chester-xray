---
name: Publish snapshots and rebases
description: Prevent stale production builds when Version Control is resolving or rebasing changes during Publish.
---

Do not start Publish while the workspace is in a merge or rebase, even if the visible files appear corrected. First confirm the branch is attached, no Git operation is active, and the final publishing configuration is present.

**Why:** A Publish started during an interactive rebase captured an older production command and crash-looped, although the workspace later showed the corrected command.

**How to apply:** Before requesting another Publish after conflict resolution, verify Git reports the normal branch with no unmerged paths, then test the exact production run command and its HTTP health response locally.