---
status: accepted
---

# Sign compliance exports with a database identity

Odoo signs each Compliance Export through OpenBao Transit with one
database-scoped Ed25519 Compliance Key.

The Controller publishes an attested public key with its database and
Organization scope. Public-key history has no time limit, and private key
material remains inside OpenBao.
