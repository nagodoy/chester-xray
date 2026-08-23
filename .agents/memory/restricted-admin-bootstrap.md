---
name: Restricted admin bootstrap
description: The custom OTP authentication boundary and configuration-owned administrator lifecycle.
---

The app intentionally keeps public registration hidden. Email codes may be requested only after the backend resolves access. Environment-configured administrators take precedence over individual email rules and domain rules, and the environment configuration is the source of truth for those managed administrator accounts.

**Why:** Access needs to remain explicit while allowing the initial administrator to sign in without a manually pre-created account. Treating an inactive direct email as a domain fallback, or retaining an admin removed from environment configuration, can silently preserve access.

**How to apply:** Preserve backend authorization as the authority: resolve environment admin, then direct email, then domain; an inactive direct email is a terminal deny. Reconcile removed environment-managed admins at startup by deactivating and unmarking them, with an audit event. Keep public sign-up disabled and require verified OTP before creating a session.