# Local IPC and RPC candidates for Inari

> Supporting research. The [connectivity reference](connectivity/README.md)
> defines the consolidated target. Follow its requirements and implementation
> stages. Candidate rankings and rollout orders here are historical evidence.

Checked 2026-10-08. Sources are project documentation, specifications, or vendor documentation.

## Scope

Device Center is a user-session GPUI application. Agent is a long-running system service. They can run under different OS users. Opening a remote Agent transport with Iroh is a proposed separate concern; this note does not treat it as a current implementation.

A local connection has four layers:

1. **Transport** carries bytes or messages between processes. Examples are Unix sockets and Windows named pipes.
2. **Codec and schema** define the data shape. Examples are Protobuf and Cap’n Proto.
3. **RPC or messaging** defines calls, replies, streams, signals, queues, and cancellation.
4. **Inari authorization** maps an accepted process to a Client Pairing and Client Grant.

An OS user, UID, SID, PID, code-signing requirement, or socket ACL is an admission signal. It is not a Client Grant. An Iroh `EndpointId` is also a separate remote identity. The Agent must make the final application authorization decision.

The selection criteria are cross-platform packaging, peer identity across a service/user boundary, bounded messages and event queues, cancellation and deadlines, reconnect after process restart, and a contract that can survive the future Rust Agent migration. Generic throughput numbers are not a decision input without an Inari benchmark.

## Candidate findings

### Tokio-native local stream plus a typed contract

A local stream is the smallest useful transport boundary. The [`interprocess` crate](https://docs.rs/interprocess/latest/interprocess/) supplies local sockets and native Windows named pipes, with Tokio support and CI for Windows, Linux, and macOS. It does not define RPC, serialization, OS authorization, or event replay. The exact peer-credential API and service ACL behavior still need a focused audit before adoption.

This shape keeps the OS-specific code in one adapter. It lets Inari choose one RPC layer without adopting a message bus or a shared-memory allocator. The adapter must expose peer identity where the platform provides it, apply a maximum frame size, and turn disconnects into a reconnectable client state. This is the baseline against which the higher-level candidates below should be measured.

### Tonic and Protobuf over the local stream

[Tonic](https://github.com/hyperium/tonic) provides gRPC over HTTP/2, Protobuf code generation through `prost`, metadata, and bidirectional streaming. Its server can accept a custom `AsyncRead + AsyncWrite` stream through [`serve_with_incoming`](https://docs.rs/tonic/latest/tonic/transport/struct.Server.html), and its client supports custom connectors through [`connect_with_connector`](https://docs.rs/tonic/latest/tonic/transport/channel/struct.Endpoint.html). This means Tonic can sit above a Unix socket or a Windows named pipe, but the named-pipe adapter is Inari work.

Tonic's built-in Unix transport is compiled only for non-Windows targets. Its current source explicitly returns an error for the Tokio UDS path on Windows: [`uds_connector.rs`](https://docs.rs/tonic/latest/src/tonic/transport/channel/uds_connector.rs.html). On Unix, [`UdsConnectInfo`](https://docs.rs/tonic/latest/src/tonic/transport/server/unix.rs.html) exposes the peer address and Unix process credentials. Tonic does not automatically authenticate a Windows named-pipe peer, and a custom `Connected` implementation must not be treated as proof of identity.

Tonic is the strongest choice when a stable `.proto` contract, future Python clients, or standard gRPC tooling has high value. It gives Device Center a natural unary and event-stream API. It also adds HTTP/2 framing, Protobuf code generation, `protoc` or a replacement, and a second contract if Inari keeps OpenAPI models as a separate authority. Request deadlines and stream cancellation exist at the gRPC layer, but application code must set deadlines, define cancellation behavior, and test reconnect and replay. See the open Tonic issues on [deadline ergonomics](https://github.com/hyperium/tonic/issues/2316) and [RST_STREAM cancellation](https://github.com/hyperium/tonic/issues/2288).

### tarpc over a local stream

[`tarpc`](https://github.com/google/tarpc) defines services in Rust code and accepts any asynchronous `Stream<Item = Request> + Sink<Response>` transport. Dropping a request sends cancellation to the server, and the framework supports deadlines and deadline propagation. The current package also ships a generic [`serde_transport`](https://github.com/google/tarpc/blob/main/tarpc/src/serde_transport.rs) over `AsyncRead + AsyncWrite`, with feature-gated TCP and Unix helpers. The Unix helper uses Tokio Unix sockets; tarpc does not ship a Windows named-pipe listener wrapper or expose OS peer credentials. Inari can provide a Windows named-pipe stream adapter and pass it to the generic transport.

The generated service API documents ordinary asynchronous methods with one response. It does not provide a transparent gRPC-style server-streaming return API. A long-lived event feed therefore needs an explicit Inari design, such as a separate notification channel or a response protocol that carries repeated events. Tarpc also does not provide reconnect or event replay. It is a small and idiomatic fit after Agent becomes Rust, but it has no schema for Python or browser clients. Use it only if an all-Rust contract is an explicit decision and Inari accepts its Rust-first schema model.

### Cap’n Proto RPC over a local stream

[`capnp-rpc`](https://github.com/capnproto/capnproto-rust/tree/master/capnp-rpc) uses generated schema code, bidirectional ordered streams, object-capability RPC, and [`RpcSystem`](https://docs.rs/capnp-rpc/latest/capnp_rpc/struct.RpcSystem.html). It accepts an ordered bidirectional byte-stream transport. The Rust RPC crate does not provide a universal local endpoint. Do not assume that reconnect or flow-limit APIs from another language's Cap’n Proto implementation exist in this Rust API.

This is a strong technical option for typed capability RPC and call pipelining. Capability references are application authority and do not replace OS peer checks or Inari Client Grants. The schema compiler and object-capability model add more build and API weight than Inari needs for a small Device Center boundary. It is mainly attractive when the team wants Cap’n Proto’s object model.

### D-Bus and zbus

[D-Bus](https://dbus.freedesktop.org/doc/dbus-specification.html) is a local message bus with method calls, replies, signals, names, service activation, and policy. Its specification says the main use cases are a system bus and per-user session buses, and also says D-Bus is not a generic IPC system. The system bus can authenticate with Unix credentials or Windows SIDs through SASL `EXTERNAL`; the standard interfaces expose connection UID, PID, and other credentials. [D-Bus API guidance](https://dbus.freedesktop.org/doc/dbus-api-design.html) requires service files and, for system services, policy files.

[`zbus`](https://github.com/z-galaxy/zbus) is a pure-Rust D-Bus API with Tokio integration, proxies, interfaces, method timeouts, and bounded `MessageStream` queues. Its repository lists Unix, Windows, and macOS targets, with Linux as the main target. The Tokio Unix-stream builder is unavailable on Windows because Tokio does not provide Unix domain sockets there; the alternative async-io path uses a Windows UDS compatibility crate. zbus still requires a D-Bus broker for the usual session/system usage.

D-Bus can reduce Linux service wiring and gives a mature policy and signal model. It also adds a broker, bus names, service activation, XML policy, and platform-specific deployment. A session bus is owned by one user and is not a privilege boundary. A system bus can bridge the different-user boundary, but Inari still needs to map the peer credentials to Client Grants. D-Bus is a good Linux integration adapter, not the portable Inari protocol.

### NNG

[NNG](https://github.com/nanomsg/nng) is a brokerless messaging library with request/reply, publish/subscribe, and other patterns. Its [`ipc`](https://nng.nanomsg.org/man/v1.10.0/nng_ipc.7.html) transport uses Unix domain sockets on POSIX and named pipes on Windows. The IPC options document POSIX listener permissions, Windows security descriptors, and peer UID, GID, and PID options where the OS supplies them: [`nng_ipc_options`](https://nng.nanomsg.org/man/v1.10.0/nng_ipc_options.5.html). NNG also provides maximum receive sizes, bounded send buffers, send timeouts, and asynchronous operation cancellation through AIO APIs ([options](https://nng.nanomsg.org/man/v1.10.0/nng_options.5.html), [AIO stop](https://nng.nanomsg.org/man/v1.10.0/nng_aio_stop.3.html)).

NNG is the most complete messaging transport in this comparison for OS permissions, bounded queues, and multi-client patterns. It is still not an IDL or RPC contract. Inari would define message types, request correlation, auth, reconnect, event replay, and Client Grant checks. The C library needs CMake or vendoring. The official Rust binding [`nng-rs`](https://github.com/nanomsg/nng-rs) builds NNG through FFI and its [current crate metadata](https://raw.githubusercontent.com/nanomsg/nng-rs/main/nng/Cargo.toml) labels the main `nng` crate as a v2 prerelease, while the official NNG repository’s stable line is v1.10. That version and packaging gap is a material risk. NNG deserves a short proof of concept only if its Rust binding exposes the IPC security and peer options needed by Inari.

### ZeroMQ and zmq.rs

[ZeroMQ’s socket API](https://zeromq.org/socket-api/) gives request/reply, publish/subscribe, pipeline, and high-water-mark limits. The reference [IPC transport](https://libzmq.readthedocs.io/en/zeromq3-x/zmq_ipc.html) is Unix-domain-socket based and therefore does not supply a Windows local transport. The native Rust project [`zmq.rs`](https://github.com/zeromq/zmq.rs) supports Tokio and Unix-only IPC but states that it does not implement the full ZeroMQ feature set. The older [`rust-zmq`](https://github.com/erickt/rust-zmq) binding depends on the C library.

ZeroMQ gives useful messaging patterns, but Inari would still own schema, peer authorization, reconnect, and durable event semantics. Windows support and Rust maintenance make it lower priority than NNG.

### ipc-channel

[`ipc-channel`](https://docs.rs/ipc-channel/latest/ipc_channel/) gives Rust processes Serde channels over native primitives: Unix file descriptors, macOS Mach ports, and Windows named pipes. Its documented channels are unbounded, so `send()` does not provide backpressure. Its one-shot server accepts one client connection. These properties leave service admission and resource policy to Inari. The library supplies no RPC contract or reconnect protocol. A fixed helper pair is a better fit than the proposed multi-client Agent service.

### Native platform RPC

**macOS XPC.** Apple documents XPC as a bidirectional IPC and RPC mechanism integrated with `launchd`. [`NSXPCConnection`](https://developer.apple.com/documentation/foundation/nsxpcconnection) exposes peer process identifiers, effective user and group identifiers, audit session information, code-signing requirements, interruption handlers, and invalidation handlers. XPC services are restarted by `launchd`; the C API has message replies, event handlers, and cancellation. Apple’s [XPC guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingXPCServices.html) also states that the encoding is opaque and is not an ABI contract, so messages must not be persisted as bytes. XPC interfaces are Objective-C or C property-list/object APIs. Rust needs FFI and macOS-specific packaging. Use XPC only as a macOS adapter if native code-signing and launchd policy are required.

**Windows ncalrpc.** Microsoft documents [`ncalrpc`](https://learn.microsoft.com/en-us/windows/win32/rpc/protocol-sequence-constants) as the local RPC protocol sequence and recommends it for local calls ([selection guidance](https://learn.microsoft.com/en-us/windows/win32/rpc/choosing-a-protocol-sequence)). It supports Windows `RPC_C_AUTHN_WINNT` authentication. String bindings can request identification, impersonation, and security options ([string binding](https://learn.microsoft.com/en-us/windows/win32/rpc/string-binding)); an RPC interface can use a Windows security descriptor and a security callback through [`RpcServerRegisterIf3`](https://learn.microsoft.com/en-us/windows/win32/api/rpcdce/nf-rpcdce-rpcserverregisterif3). Microsoft also documents that `RpcServerRegisterIf3`'s `MaxRpcSize` limit has no effect on ncalrpc, so Inari still needs an application-level size limit. The Windows Rust projection exposes these APIs as low-level unsafe FFI ([`RpcServerRegisterIf3`](https://microsoft.github.io/windows-docs-rs/doc/windows/Win32/System/Rpc/fn.RpcServerRegisterIf3.html)). This is a Windows-only IDL/stub and security stack. It is useful for a native Windows adapter, but it cannot be the Inari contract across all three OSes.

### iceoryx2

[`iceoryx2`](https://github.com/eclipse-iceoryx/iceoryx2) provides publish/subscribe, request/response, and events over zero-copy shared memory. Its current platform table lists Windows, Linux, and macOS as tier 2. Its FAQ has a direct cross-user limitation: service access rights management is not implemented, so processes under a different user receive an insufficient-permissions error. The `dev_permissions` feature makes resources globally accessible and is marked for development only. The FAQ also restricts payloads to shared-memory-safe, self-contained types; ordinary `String`, `Vec`, and `HashMap` payloads are not valid.

iceoryx2 is a high-volume data transport, not a complete RPC/auth boundary. It does not solve the different-user Agent/Device Center admission problem. Keep it out of the ordinary command and event API unless a measured large-payload need appears and a secure permission design is available.

## Practical ranking

1. **Local stream adapter plus one typed RPC contract.** This is the smallest cross-platform architecture. Select the transport after the parent audit of Tokio, `interprocess`, Unix peer credentials, and Windows named-pipe ACLs. Select Tonic/Protobuf when cross-language contract reuse matters. Select tarpc when the contract is intentionally Rust-only after the Agent migration.
2. **Tonic/Protobuf over that adapter.** It has the clearest unary, streaming, and future client story. Budget for a Windows connector, explicit OS admission, frame/message limits, deadlines, cancellation tests, and reconnect/replay code.
3. **NNG proof of concept.** It is attractive when bounded queues, pub/sub, native IPC permissions, and peer credentials are more valuable than standard IDL. First verify nng-rs version, Tokio behavior, Windows security descriptor access, and shutdown/reconnect behavior.
4. **Cap’n Proto RPC or tarpc for the Rust future.** Both can work over the same local stream. Cap’n Proto provides a capability RPC model; tarpc uses Rust service definitions. Neither solves OS admission.
5. **Platform adapters.** zbus is useful for Linux desktop/system-bus integration; XPC and ncalrpc are useful only when native platform security or lifecycle is worth a second implementation.

Reject `ipc-channel` for the Agent service. Keep ZeroMQ below NNG because of Windows IPC and Rust implementation limits. Keep iceoryx2 as an optional measured data path, never as the initial command/control channel.

## Contract and security requirements for the selected stack

- The Agent owns the local listener and remains authoritative for Client Pairing and Client Grant checks.
- The transport adapter reports OS peer identity when available. The Agent rejects an unknown or revoked process before serving methods.
- The RPC layer enforces a maximum request and event size, bounded per-client queues, explicit deadlines, cancellation, and a policy for slow subscribers.
- Device Center reconnects after Agent restart. Events that affect durable state need a sequence or snapshot/replay rule; transient UI events can be dropped.
- A transport reconnect does not create a new Client Pairing. Iroh peer identity and remote grants stay in their own trust store.
- One contract file or generated crate must be authoritative. Do not add a second schema only to make a library fit.
