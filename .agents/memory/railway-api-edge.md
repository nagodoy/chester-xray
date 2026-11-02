---
name: Railway API edge behavior
description: Distinguishing Railway edge blocks from invalid credentials in background controllers.
---

Railway's GraphQL endpoint can reject Python urllib's default user agent with HTTP 403 and edge error 1010 even for a public introspection query. Do not assume this response proves a bad token.

**Why:** The same unauthenticated query succeeded with an application-specific user agent but failed with the Python default.

**How to apply:** Use a stable application user agent for controllers, and distinguish edge blocks from GraphQL authorization errors before asking the user to replace credentials.
