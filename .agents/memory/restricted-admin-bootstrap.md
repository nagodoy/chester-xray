---
name: Restricted admin bootstrap
description: The authentication boundary for the first authorized Chester user.
---

The app intentionally keeps public registration hidden. If the sole allowlisted administrator is missing from a Clerk environment, the login screen may bootstrap only that exact allowlisted email through Clerk's email verification flow; all other emails remain blocked before authentication.

**Why:** Development and Production Clerk user stores are separate, so a user present in preview is not automatically available in the published app.

**How to apply:** Preserve the backend allowlist as the authority. Any future onboarding change must keep email verification and must not expose a general public sign-up path.