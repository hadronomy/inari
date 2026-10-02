---
status: accepted
---

# Separate signing identities by trust boundary

The system uses separate Ed25519 signing identities for Controller dispatch,
Controller webhooks, each Agent's State Envelopes, each company's Pairing
Assertions, each database's Compliance Exports, and each Controller
environment's Recovery Points.

Dispatch and Agent State Envelopes use compact JWS over RFC 8785 canonical
JSON. Webhooks use RFC 9421 HTTP Message Signatures with a content digest.

Each webhook retry keeps its delivery identity and uses a fresh timestamp and
signature.

Each key record has one immutable purpose. The Controller exposes separate JWK
sets at purpose-specific Interface endpoints. A public-key fingerprint cannot
appear in more than one purpose set. Each verifier rejects a key from another
set or purpose before it checks the signature.

No signing identity crosses these trust boundaries. This rule prevents a key
that one verifier trusts from authorizing a different protocol or authority.
