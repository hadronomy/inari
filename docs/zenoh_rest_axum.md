# Zenoh HTTP compatibility

The controller can expose its active Zenoh session through an Axum-owned HTTP
surface. This is useful for diagnostics and integrations that understand Zenoh
selectors but cannot open a native Zenoh session.

The route is a compatibility boundary, not another resource API. Everything
after `/api/zenoh/v1/` is the Zenoh selector or key expression itself:

```text
GET    /api/zenoh/v1/{selector}
POST   /api/zenoh/v1/{selector}
PUT    /api/zenoh/v1/{key-expression}
PATCH  /api/zenoh/v1/{key-expression}
DELETE /api/zenoh/v1/{key-expression}
```

For example:

```http
GET /api/zenoh/v1/iot/v1/agents/agt_123/status/latest
```

queries `iot/v1/agents/agt_123/status/latest`. Inari does not add another
keyspace prefix or translate the reply into an agent REST resource.

## Ownership and security

Axum mounts this router before the Leptos fallback, so Zenoh requests can never
return an application page. It shares the server’s request IDs, tracing,
limits, timeouts, panic handling, and RFC 9457 errors.

The surface is disabled by default. Enable it only behind the controller’s
authentication and authorization policy:

```toml
[http.zenoh_rest]
enabled = true
allow_admin_space = false
query_timeout = "15s"
sse_keep_alive = "15s"
sse_buffer = 64

[zenoh.admin_space]
enabled = false
read = true
write = false
```

`http.zenoh_rest.allow_admin_space` permits `@/...` routes at the HTTP
boundary. `zenoh.admin_space.enabled` controls whether the router serves that
space. Both must allow an operation before it can succeed. Keep write and
administration scopes distinct from read access.

The managed gateway continues to use native Zenoh for steady-state agent
traffic. Do not route agent commands through this HTTP bridge.

## Queries

A normal `GET` runs a Zenoh query and returns all replies as a JSON array:

```json
[
  {
    "key": "iot/v1/agents/agt_123/status/latest",
    "value": { "state": "online" },
    "encoding": "application/json",
    "timestamp": "2026-07-15T10:00:00Z/..."
  }
]
```

JSON encodings are decoded as JSON, text encodings as UTF-8, and other bytes as
base64. Invalid JSON or UTF-8 falls back to base64 rather than corrupting the
payload.

`POST` performs the same query with a request body. `Content-Type` maps to the
Zenoh encoding. The `_raw` selector parameter returns the first reply directly,
which preserves its status, content type, and body for clients that do not want
the JSON envelope.

The `@/local` alias resolves to the connected session’s Zenoh ID. Admin-space
configuration still applies after resolution.

## Subscriptions

Send `Accept: text/event-stream` with `GET` to declare a Zenoh subscriber:

```sh
curl -N \
  -H 'accept: text/event-stream' \
  http://127.0.0.1:8080/api/zenoh/v1/iot/v1/agents/agt_123/events/**
```

Each SSE event uses the Zenoh sample kind (`put` or `delete`) and the same
sample object as a query reply. Streams use a bounded buffer. A slow consumer
is disconnected rather than being allowed to grow server memory without bound.

The stream closes when its HTTP request ends, the server shuts down, or the
underlying Zenoh generation is lost. Clients reconnect explicitly; the server
does not pretend that a reconnect had no delivery gap.

## Liveliness

`_liveliness` changes a query or subscription from ordinary samples to Zenoh
liveliness tokens. `_history` asks a liveliness SSE subscription to emit the
tokens already present when it opens.

```sh
curl -N -g \
  -H 'accept: text/event-stream' \
  'http://127.0.0.1:8080/api/zenoh/v1/iot/v1/agents/**/presence/agent?_liveliness&_history'
```

HTTP clients may observe liveliness, but they cannot declare or drop tokens on
behalf of an agent. Token ownership stays with the native Zenoh client.

## Writes

`PUT` and `PATCH` publish the raw request body at the selected key expression.
An absent `Content-Type` becomes `application/octet-stream`. `DELETE` issues a
Zenoh delete operation.

Write routes should normally be disabled for human read-only roles. Enabling
the bridge does not grant an HTTP caller authority outside the controller’s
typed scopes or the router ACL.

## Failure behavior

| Condition | HTTP response |
| --- | --- |
| Zenoh is unavailable | `503 Service Unavailable` |
| Selector or key expression is invalid | `400 Bad Request` |
| Admin space is blocked | `403 Forbidden` |
| Query exceeds its deadline | `408 Request Timeout` |
| Request body is invalid for its encoding | `400 Bad Request` |

Unknown paths beneath `/api` use the same `application/problem+json` format.
Leptos never handles them.

## Implementation map

- `crates/inari-server/src/http/routes/api/zenoh.rs` adapts HTTP requests;
- `crates/inari-server/src/zenoh/rest` implements selector, encoding, query,
  write, subscription, and liveliness behavior;
- `ZenohSupervisor` owns connection, reconnect, cancellation, and shutdown;
- request handlers borrow the current session generation rather than creating a
  second Zenoh runtime.

The `fake_device` example provides a local smoke test:

```sh
cargo run -p inari-server --example fake_device -- \
  --namespace iot/v1/agents/agt_123
```

Run Rust validation with Clippy and the workspace tests; do not replace the
native Zenoh tests with mocked HTTP-only behavior.
