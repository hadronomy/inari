---
status: accepted
---

# Pair each POS browser with a client key

Each POS browser creates a non-exportable Ed25519 key. Device Center displays
the exact Odoo origin, database, POS configuration, Devices, and requested
scopes before a Device Manager approves the Client Pairing.

The Agent issues 15-minute access tokens and renews them through signed
challenges. A change to the Device Binding or scope requires new approval.

The Agent binds each access token to the paired key through RFC 9449 DPoP. It
enforces a nonce, a two-minute clock window, and a `jti` replay cache.

Every Pairing Request includes a signed Pairing Assertion from Odoo. The
assertion binds the browser JWK thumbprint, request, Agent, audience, actor,
database, company, Organization, Site, POS configuration, session, role,
scopes, issue time, expiry, and one-time `jti`.

The Client Grant contains the same actor and business scope. The access token
contains `cnf.jkt`. The Agent checks `htm`, `htu`, `iat`, `ath`, nonce, and
`jti` on every DPoP proof.

The Agent issues a nonce through the RFC 9449 resource-server challenge. A
proof with an unknown, expired, or consumed nonce receives `401`,
`WWW-Authenticate: DPoP error="use_dpop_nonce"`, and one `DPoP-Nonce` header.
The Agent validates the token, key, method, URI, token hash, and signature
before it issues this challenge. A repeated `jti` fails without a new nonce.
The browser retries the same idempotent request once with a new proof and the
challenge nonce. It does not share the single-use nonce between POS tabs.

During a Controller outage, a Policy Snapshot can authorize new pairing for 24
hours while Odoo remains reachable. An Odoo outage blocks new pairing.
Existing Client Grants continue within their offline limit.

The Agent keeps the replay cache through the token lifetime and clock window.
An Agent restart cannot make a used proof valid again.

The Agent accepts the exact paired browser origin and rejects wildcard,
missing, and `null` origins. The Local Agent Interface uses no cookies. Device
Center uses its separate application identity.

After a clock-related rejection, the browser derives an offset from the Agent
`Date` value and retries once. A second rejection requires workstation clock
correction.

The POS status control provides the full Client Pairing flow. A Pairing Request
expires after ten minutes, and denial or expiry requires a new request.

The POS opens Device Center through `inari://` with no secret in the URI. Both
surfaces display one three-word phrase. A Device Manager approves the exact
origin, database, POS configuration, and scopes through operating-system
authentication.

The production Odoo origin uses a strict content security policy and Trusted
Types. It loads no third-party script into the paired origin.
