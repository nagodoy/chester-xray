---
name: Worker separation rollout
description: Deployment sequencing when separating queue processing from the API.
---

Before publishing an API-only startup, verify that a separately deployed worker actually claims jobs from the same production queue. A development Worker workflow and an external scaler implementation are not evidence of a deployed consumer.

**Why:** An API-only publication accepted STOW uploads normally while every analysis stayed queued with zero attempts; the prior revision had logged worker activity.

**How to apply:** Treat a successful production job claim by the external consumer as a rollout prerequisite. Do not infer readiness from HTTP health checks or similarly named external environments.
