---
name: OsiriX STOW interoperability
description: Compatibility constraints for OsiriX sending DICOM instances to the worklist.
---

Keep `/dicomweb/studies` as the canonical STOW-RS endpoint, but retain the
WADO-style compatibility aliases and HTTPS Basic authentication for OsiriX.

**Why:** OsiriX configurations may append `studies` to an already suffixed WADO
base path, causing a POST to a duplicate path that otherwise falls through to
the SPA and returns HTTP 405. Its connection UI also supplies HTTP Basic
credentials rather than custom ingestion headers.

**How to apply:** Route the compatibility paths through the same authenticated
STOW handler, accept the service token as the Basic-auth password over HTTPS,
and keep POST at `/` unsupported. Test that the aliases reach authentication
(401 without a credential) rather than the frontend's 405 response.

When anonymous WADO is explicitly enabled, keep an authorized owner configured
server-side; the client must not send an owner header.

**Why:** The worklist still needs a trusted recipient for each received study.
Without a server-side owner, an unauthenticated WADO request correctly reaches
STOW but is rejected before ingestion.

**How to apply:** Enable the anonymous WADO setting only for the compatibility
paths, retain protection for DICOMweb and the external gateway, and configure
the owner to an authorized worklist email in the environment.