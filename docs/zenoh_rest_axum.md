---
title: Axum-Native Zenoh REST Design
summary: Concrete design for exposing the documented Zenoh REST surface from crates/inari-server without embedding zenoh-plugin-rest.
status: implemented
---

# Axum-Native Zenoh REST Design

This document defines a concrete design for exposing the documented Zenoh REST API from `crates/inari-server` using Axum and the public `zenoh` crate.

It intentionally does not embed `zenoh-plugin-rest`:

- the plugin crate is explicitly internal-only and unstable
- it starts its own Tide listener instead of exposing a Tower service
- it lives on `http-types` and Tide, while our server is already standardized on Axum, Tower, Tokio, and `http` 1.x

The goal is to provide the same useful HTTP surface inside the existing server, with clean ownership boundaries and a small, durable implementation.

## 1. Goals

The module should:

- mount naturally inside the existing versioned Axum API tree
- use the existing `ZenohSupervisor` lifecycle instead of creating a second Zenoh runtime
- expose the documented Zenoh REST operations:
  - query via HTTP `GET` and `POST`
  - long-lived subscription via `GET` + `Accept: text/event-stream`
  - `PUT` and `PATCH`
  - `DELETE`
  - `_raw` query responses
  - HTML query responses via `Accept: text/html`
- preserve the existing uniform error envelope from [`crates/inari-server/src/error.rs`](../crates/inari-server/src/error.rs)
- remain disabled by default until an explicit operator decision enables it

The module should not:

- depend on `zenoh-plugin-rest`
- introduce Tide or `http-types` into `inari-server`
- silently expose Zenoh admin space without an explicit configuration opt-in
- attempt transparent subscription migration across Zenoh reconnects in the first iteration

## 2. Public HTTP Contract

The Axum-native surface should live under the existing versioned API namespace:

- `GET /api/v1/zenoh`
- `GET /api/v1/zenoh/*selector`
- `POST /api/v1/zenoh/*selector`
- `PUT /api/v1/zenoh/*keyexpr`
- `PATCH /api/v1/zenoh/*keyexpr`
- `DELETE /api/v1/zenoh/*keyexpr`

This keeps the current API composition clean and avoids conflicting with the root application router.

The mounted surface intentionally keeps one app-specific addition beyond upstream parity:

- `GET /api/v1/zenoh` returns module metadata for the host server

All selector-bearing routes beneath `/api/v1/zenoh/*selector` now aim to match the upstream REST plugin behavior.

### 2.1 Root Endpoint

`GET /api/v1/zenoh`

Returns module metadata and live connection state, for example:

```json
{
  "service": "zenoh_rest",
  "enabled": true,
  "admin_space": {
    "route_enabled": true,
    "router_enabled": true,
    "read": true,
    "write": false
  },
  "state": {
    "state": "connected",
    "attempt": 1,
    "message": "Zenoh session established.",
    "observed_at": "2026-04-23T16:00:00Z"
  }
}
```

This is intentionally separate from `GET /api/v1`, which should stay small and high-level.

### 2.2 Query Endpoint

`GET /api/v1/zenoh/*selector`

This maps to `session.get(...)` over Zenoh and returns:

```json
[
  {
    "key": "demo/example/test",
    "value": "Hello World!",
    "encoding": "text/plain",
    "timestamp": "2026-04-23T16:00:00Z/ABC..."
  }
]
```

Response shape:

- `key`: resolved Zenoh key
- `value`: JSON value, string, or base64 string depending on encoding
- `encoding`: Zenoh encoding string
- `timestamp`: optional timestamp string

The current implementation now covers the upstream plugin surface for mounted selectors:

- `GET` query
- `POST` query with optional request payload
- SSE `GET`
- `PUT`
- `PATCH`
- `DELETE`
- HTML negotiation
- `_raw` first-reply mode

### 2.2.1 Additive Liveliness Extension

The Axum-native surface now also exposes Zenoh liveliness as an explicit extension without changing the default meaning of existing routes.

Reserved selector parameters:

- `_liveliness`: switch the request from normal query/subscription semantics into Zenoh liveliness semantics
- `_history`: only meaningful with `_liveliness` + SSE; requests the currently live tokens when the stream is opened
- `_raw`: preserved for parity with the normal query path and returns the first liveliness reply as a raw HTTP response

Examples:

- `GET /api/v1/zenoh/iot/v1/agents/**/presence?_liveliness`
- `GET /api/v1/zenoh/iot/v1/agents/**/presence?_liveliness&_history` with `Accept: text/event-stream`

This is intentionally additive:

- requests without `_liveliness` keep the same behavior as the native Zenoh REST plugin
- normal SSE remains a plain Zenoh subscriber and does not become a presence stream implicitly
- liveliness token declaration remains a native Zenoh client responsibility rather than an HTTP write operation

### 2.3 SSE Endpoint

`GET /api/v1/zenoh/*selector` with `Accept: text/event-stream`

This declares a Zenoh subscriber and streams events with Axum SSE:

- SSE event name: Zenoh sample kind, usually `put` or `delete`
- SSE data: the same JSON sample envelope used by the query response

If the underlying Zenoh session drops, the SSE stream should close cleanly. The client is expected to reconnect. We should not hide reconnect gaps or risk duplicate replay in the first version.

### 2.4 Write Endpoints

`PUT /api/v1/zenoh/*keyexpr`

- request body: raw bytes
- `Content-Type`: mapped to Zenoh `Encoding`
- response: `200 OK`

`PATCH /api/v1/zenoh/*keyexpr`

- same behavior as `PUT`
- response: `200 OK`

`DELETE /api/v1/zenoh/*keyexpr`

- no body
- response: `200 OK`

## 3. Configuration

Add a new config section under `http`, because this is an HTTP exposure decision:

```toml
[http.zenoh_rest]
enabled = false
allow_admin_space = false
query_timeout = "15s"
sse_keep_alive = "15s"
sse_buffer = 64

[zenoh.admin_space]
enabled = false
read = true
write = false
```

Proposed shape:

```rust
pub struct ZenohRestConfig {
    pub enabled: bool,
    pub allow_admin_space: bool,
    #[serde(with = "humantime_serde")]
    pub query_timeout: Duration,
    #[serde(with = "humantime_serde")]
    pub sse_keep_alive: Duration,
    pub sse_buffer: usize,
}

pub struct ZenohAdminSpaceConfig {
    pub enabled: bool,
    pub read: bool,
    pub write: bool,
}
```

Defaults:

- `enabled = false`
- `allow_admin_space = false`
- `query_timeout = "15s"`
- `sse_keep_alive = "15s"`
- `sse_buffer = 64`

These defaults are intentionally conservative because the current scaffold does not yet have authentication or authorization.

`http.zenoh_rest.allow_admin_space` and `zenoh.admin_space.enabled` are intentionally separate:

- the HTTP flag controls whether this surface is willing to expose `@/...`
- the Zenoh flag controls whether the embedded router actually serves admin space

If the route-level flag is enabled while the router-level flag is disabled, the HTTP API now returns a clear `503` instead of a confusing empty result set.

## 4. Module Layout

The implementation should separate HTTP adaptation from Zenoh operations.

### 4.1 HTTP Layer

Add:

- [`crates/inari-server/src/http/routes/api/v1/zenoh.rs`](../crates/inari-server/src/http/routes/api/v1/)

Responsibilities:

- register routes
- parse selector tails and request headers
- map request bodies into Zenoh operation inputs
- map Zenoh replies into JSON and SSE responses
- keep error handling in terms of `AppError`

`v1/mod.rs` should then nest:

```rust
.nest("/zenoh", zenoh::router(state))
```

### 4.2 Zenoh Service Layer

Extend the existing Zenoh subsystem instead of putting session logic in handlers.

Prefer adding:

- `crates/inari-server/src/zenoh/access.rs`
- `crates/inari-server/src/zenoh/reply.rs`

Responsibilities:

- obtain an active session handle
- resolve `@/local` aliases using the active session ZID
- execute query, publish, delete, and subscribe operations
- normalize failures into `AppError`

### 4.3 Optional Small HTTP Helpers

If the route module gets crowded, add:

- `crates/inari-server/src/http/zenoh_rest/model.rs`
- `crates/inari-server/src/http/zenoh_rest/encoding.rs`

This should stay small. We do not want to build a second framework inside the crate.

## 5. Required Evolution Of The Current Zenoh Boundary

The current [`ZenohHandle`](../crates/inari-server/src/zenoh/handle.rs) is publish-oriented and command-channeled. That is not the right shape for request/response queries and per-request subscribers.

The clean design is:

### 5.1 Supervisor Still Owns Lifecycle

[`ZenohSupervisor`](../crates/inari-server/src/zenoh/supervisor.rs) remains responsible for:

- connect
- reconnect
- shutdown
- status updates
- event emission

### 5.2 Request-Path Operations Use Cloned Session Handles

Instead of routing every operation through `mpsc + oneshot`, the supervisor should publish the active session through a watch channel:

```rust
pub struct SessionLease {
    pub zid: Option<String>,
    pub session: Option<Arc<zenoh::Session>>,
    pub generation: u64,
}
```

`ZenohHandle` then becomes able to do:

- `session_snapshot() -> Option<Arc<Session>>`
- `query(...)`
- `publish_bytes(...)`
- `delete(...)`
- `declare_subscriber(...)`

This matches Zenoh’s cloneable session model and removes unnecessary actor round-trips from the hot path.

### 5.3 Keep A Small Fault Signal Path

Direct session operations still need a way to inform the supervisor when the session appears broken.

Add a small internal signal channel such as:

```rust
enum SupervisorSignal {
    SessionFault { message: String },
}
```

When a direct query, publish, delete, or subscribe setup fails against an active session, `ZenohHandle` should `try_send` a fault signal. The supervisor can then:

- move status to reconnecting/degraded
- close the current session
- resume normal retry behavior

This keeps lifecycle centralized without forcing every data-plane operation through a bespoke actor API.

## 6. Selector Resolution

We should explicitly define how HTTP path tails map to Zenoh selectors.

Rules:

1. The Axum route uses a wildcard tail.
2. The tail is percent-decoded once.
3. Empty tails are only valid for the mounted metadata route `GET /api/v1/zenoh`.
4. Admin-space access is rejected unless `allow_admin_space = true`.
5. `@/local` and `@/local/...` are rewritten to `@/{zid}` and `@/{zid}/...` using the connected session ZID.

The rewrite is important because the official plugin supports `@/local`, and it is genuinely useful for operator workflows.

## 7. Response Encoding Rules

The JSON payload mapping should be deterministic and documented.

### 7.1 Query And SSE Sample Envelope

Use:

```rust
pub struct ZenohRestSample {
    pub key: String,
    pub value: serde_json::Value,
    pub encoding: String,
    pub timestamp: Option<String>,
}
```

### 7.2 Value Mapping

- JSON encodings:
  - deserialize as JSON
  - if parsing fails, fall back to base64 string
- text encodings:
  - decode as UTF-8 string
  - if decoding fails, fall back to base64 string
- all other encodings:
  - return base64 string

This matches the practical behavior operators expect from the existing plugin without importing its whole implementation.

### 7.3 Write Encoding

`Content-Type` maps directly to Zenoh `Encoding`.

If `Content-Type` is absent:

- default to `application/octet-stream`

## 8. Timeouts, Backpressure, And Cancellation

### 8.1 Query Collection

Queries should be bounded by `http.zenoh_rest.query_timeout`.

Implementation shape:

- create query
- collect replies until completion or timeout
- on timeout, return `408 Request Timeout`

### 8.2 SSE Delivery

Per-client SSE should use a bounded Tokio `mpsc` channel with `sse_buffer` capacity.

Behavior:

- Zenoh subscriber task forwards samples into the channel
- Axum SSE response streams the receiver
- if the channel is full, close the SSE stream rather than growing memory unbounded

### 8.3 Shutdown

When the HTTP connection closes or the response stream is dropped:

- stop forwarding events
- undeclare the subscriber if possible

If graceful shutdown begins while SSE streams are active, they should be allowed to terminate naturally inside the normal server shutdown window. We do not need a second shutdown system just for this module.

## 9. Error Semantics

All failures stay inside the existing error envelope from [`error.rs`](../crates/inari-server/src/error.rs).

Mapping:

- Zenoh not connected: `503 service_unavailable`
- admin space blocked by config: `403` once we introduce a dedicated forbidden error, or `400` in the current scaffold
- invalid selector/key expression: `400 bad_request`
- query timeout: `408 request_timeout`
- malformed JSON payload for JSON content-type: `400 bad_request`
- internal session/runtime failure: `500 internal`

One change worth making before implementation is adding:

```rust
AppError::forbidden(...)
```

That lets `allow_admin_space = false` fail honestly.

## 10. Integration Into Current Router

The current router stack in [`crates/inari-server/src/http/mod.rs`](../crates/inari-server/src/http/mod.rs) already gives us:

- request IDs
- tracing
- timeout handling
- compression
- panic capture
- uniform error serialization

The Zenoh REST module should mount inside that stack rather than bypass it.

That means:

- no second HTTP listener
- no separate CORS logic
- no separate tracing subscriber

## 11. Testing Plan

Add tests at three layers.

### 11.1 Route Tests

In the HTTP route module:

- `GET /api/v1/zenoh` returns module metadata
- disconnected Zenoh returns `503`
- invalid selector returns `400`
- disabled admin space rejects `@/...`

### 11.2 Encoding Tests

Small unit tests for:

- JSON payload decoding
- UTF-8 text decoding
- binary payload base64 fallback
- `Content-Type` to `Encoding` mapping

### 11.3 Integration Tests

With an in-process Zenoh session:

- `PUT` then `GET` round-trip
- `DELETE` removes a value
- SSE receives a published sample
- liveliness `GET` returns currently live tokens
- liveliness SSE with `_history` emits initial `PUT` and drop `DELETE`
- reconnect closes active SSE stream cleanly

For manual smoke tests, the repository now also includes
[`crates/inari-server/examples/fake_device.rs`](../crates/inari-server/examples/fake_device.rs),
which acts as a small Zenoh client publishing JSON telemetry and status while maintaining a
liveliness token at `{namespace}/presence`.

## 12. Recommended Implementation Order

1. Add `ZenohRestConfig` and route registration with `enabled = false`.
2. Evolve `ZenohHandle` to expose direct session-backed operations plus a small fault signal path.
3. Implement `GET` query support.
4. Implement `PUT` and `DELETE`.
5. Implement SSE.
6. Add `@/local` alias support.
7. Add tests.

## 13. Remaining Non-Goals

The current implementation still intentionally does not try to solve:

- transparent SSE resubscription across reconnects
- built-in authz policy beyond `enabled`, `allow_admin_space`, and router-level admin-space permissions
- HTTP operations that declare or drop liveliness tokens on behalf of devices

Those can be added later, but they should not distort the current architecture.
