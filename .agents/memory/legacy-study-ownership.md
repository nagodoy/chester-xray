---
name: Legacy study ownership
description: Safe cutover rules for studies created under Clerk subject identifiers.
---

Do not infer a legacy study owner from a domain rule, display name, or configured administrator. A legacy Clerk subject must be explicitly associated with an already-authorized email by an administrator, then its studies are reassigned and the alias retained for DICOM ingress compatibility.

**Why:** A subject identifier and an email are different identity namespaces. Guessing or allowing migration of current email owners would break study isolation and can transfer another clinician's data.

**How to apply:** Accept only Clerk-shaped `user_…` source identifiers in the migration path; reject email owners and unassigned legacy records. Keep the alias, study reassignment, and audit event in one transaction. Unmapped ingress owners must remain unchanged rather than being guessed.