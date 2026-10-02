---
packages:
  "group:edge": patch
---

### Pair each POS browser securely

The POS now guides an operator through Agent approval when a receipt printer
needs a Client Grant. It keeps the browser key non-exportable, resumes the same
ticket after approval, and renews short-lived access without exposing security
credentials to the operator.

Odoo now validates and signs each approved Pairing Request through an Ed25519
OpenBao Transit key. The private signing key stays outside Odoo.
