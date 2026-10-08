# Local IPC alternatives for Inari

> Supporting research. The [connectivity reference](connectivity/README.md)
> defines the consolidated target. Follow its requirements and implementation
> stages. Candidate rankings and rollout orders here are historical evidence.

Checked 2026-10-08. This is a proposed architecture, not an implementation report.

## Recommendation

Use **interprocess with Tokio for local transport, plus Tonic and Protobuf for
typed RPC**. Windows uses named pipes. Linux and macOS use filesystem Unix
domain sockets. Keep OS access checks inside a small local transport module.

This is my design recommendation from the source evidence. It is not a measured
performance ranking. Inari needs reliable service access, typed calls, event
subscriptions, and simple installation. Shared-memory throughput alone does
not establish the best fit.

Direct Tokio local streams are the strongest alternative. They use an existing
dependency and expose native controls. They require separate listener and
connection implementations for Windows and Unix. Choose one transport
implementation after the platform trial; do not ship both as overlapping paths.

Tonic adds HTTP/2 encoding inside the local stream. It does not require a TCP
port, public HTTP server, certificate enrollment, broker, or Controller. A
Windows named-pipe connector and authenticated connection metadata still need
Inari code. Tonic accepts custom asynchronous streams on its
[server](https://docs.rs/tonic/latest/tonic/transport/struct.Server.html) and
[client](https://docs.rs/tonic/latest/tonic/transport/channel/struct.Endpoint.html).

## Inari requirements

Device Center is a GPUI user-session application. Agent owns Devices and runs
as a background service. They can run under different OS accounts. The target
Agent is Rust; existing Python implementation details do not set this choice.

The current `main` glossary is Odoo-specific. This proposal retains Agent,
Device Center, Device Work, Device Capability, Agent Administrator, and Job
Reconciliation. It proposes generic Client Pairing and Client Grant semantics.
It does not silently change the current glossary or wire contract.

The default installation needs:

- No Controller, local broker, manually selected port, or local certificate setup.
- Automatic Device Center connection under an authorized OS account.
- OS access control across the service and desktop accounts.
- Concurrent clients and bounded request, event, and memory use.
- Typed errors, deadlines, disconnect handling, and event subscriptions.
- Agent restart without another pairing flow.
- Windows, Linux, and macOS packaging and lifecycle support.
- One generic device contract, with integration details outside that contract.

## Separate the layers

| Layer | Responsibility | Proposed choice |
| --- | --- | --- |
| Local transport | Private connection between processes | interprocess local sockets with Tokio |
| Local admission | OS permissions and authenticated peer identity | Unix credentials; Windows DACL and connected-pipe token |
| Contract and RPC | Typed methods, errors, event streams, message limits | Protobuf, prost, Tonic |
| Inari authority | Administrative permissions and Client Grants | Agent-owned authorization |
| Remote transport | Connections from external integrations | Iroh candidate, evaluated separately |
| Persistent state | Grants, Device Work, reconciliation | Agent-owned storage |

`interprocess` and Tonic complement each other. One carries bytes; the other
defines RPC. Iceoryx2, NNG, and Zenoh also supply messaging behavior, so a flat
comparison of these names hides part of the engineering cost.

## Candidate comparison

| Candidate | Strongest use | Material cost or limit | Inari decision |
| --- | --- | --- | --- |
| **interprocess + Tokio** | Portable private local byte streams | RPC and OS-specific authorization remain separate | Preferred transport |
| **Direct Tokio** | Explicit Unix socket and Windows pipe control | More platform code; no RPC contract | Close alternative using an existing dependency |
| **Tonic + Protobuf** | Typed calls and event streams with language-neutral schemas | Code generation, HTTP/2, custom Windows connector | Preferred RPC layer |
| **tarpc** | Rust-defined service contracts and cancellation | Event feeds need a separate protocol; Rust-first schema | Best smaller Rust-only RPC alternative |
| **Cap’n Proto RPC** | Object capabilities and pipelined calls | Different API model and schema tooling | Strong option when capability RPC is a deliberate product choice |
| **NNG** | Brokerless request/reply and pub/sub with IPC controls | C/FFI packaging and Rust binding version risk | Messaging finalist, below native streams |
| **Zenoh, including SHM** | Existing pub/sub and query infrastructure; large data | Local identity design remains; SHM API is unstable | Reuse for a defined data feature, not default local administration |
| **iceoryx2** | Zero-copy shared-memory data distribution | Current cross-user service permissions are unavailable | Exclude from the default service/UI boundary |
| **ipc-channel** | Typed channels for a fixed helper-process pair | Unbounded channels and one-shot bootstrap | Exclude from the multi-client Agent service |
| **D-Bus / zbus** | Linux system policy, signals, and activation | Usual bus deployment adds a broker and platform policy | Linux integration option |
| **XPC / ncalrpc** | Deep native OS security and lifecycle | Separate macOS and Windows RPC implementations | Add only for a required native capability |
| **ZeroMQ** | Established messaging patterns | Reviewed Rust IPC path is Unix-only; incomplete native Rust implementation | Lower priority than NNG |

See the [RPC and messaging source audit](local-ipc-rpc-candidates-research.md)
for the detailed evidence on each higher-level candidate.

## interprocess source audit

The inspected package is **2.4.4**, with source commit
`e27f397daebff9054f8e2f7b3dc034bae36b2867`. The `opensrc` CLI was unavailable.
I inspected the official crates.io source archive instead. This did not change
repository dependencies.

The [local socket API](https://docs.rs/interprocess/latest/interprocess/local_socket/index.html)
maps Windows to named pipes and Unix to Unix domain sockets. Its Tokio feature
supplies asynchronous streams. It inserts no framing or private metadata, so
other languages can implement the same byte contract.

The Windows listener accepts a custom security descriptor through
[`ListenerOptionsExt::security_descriptor`](https://github.com/kotauskas/interprocess/blob/e27f397daebff9054f8e2f7b3dc034bae36b2867/src/os/windows/local_socket.rs).
The local Tokio listener passes that descriptor to the pipe listener.
[Source](https://github.com/kotauskas/interprocess/blob/e27f397daebff9054f8e2f7b3dc034bae36b2867/src/os/windows/named_pipe/local_socket/tokio/listener.rs).

The underlying listener defaults to rejecting remote clients and disabling
handle inheritance. Its first instance sets `FILE_FLAG_FIRST_PIPE_INSTANCE`.
These are useful production controls, not missing library capabilities.
[Defaults](https://github.com/kotauskas/interprocess/blob/e27f397daebff9054f8e2f7b3dc034bae36b2867/src/os/windows/named_pipe/listener/options.rs),
[pipe creation](https://github.com/kotauskas/interprocess/blob/e27f397daebff9054f8e2f7b3dc034bae36b2867/src/os/windows/named_pipe/listener/create_instance.rs).

The portable [`PeerCreds`](https://docs.rs/interprocess/latest/interprocess/local_socket/struct.PeerCreds.html)
API exposes Unix effective user identity. On Windows it exposes a PID, not a
verified SID or access token. A PID alone is insufficient for authorization.
The concrete Windows Tokio stream exposes its native handle. Inari can use
Windows token APIs through that handle without abandoning the portable
transport. [Stream source](https://github.com/kotauskas/interprocess/blob/e27f397daebff9054f8e2f7b3dc034bae36b2867/src/os/windows/named_pipe/local_socket/tokio/stream.rs).

The library helps with portable I/O. Inari still owns the permission policy,
connection limits, peer authorization, and restart behavior.

## Security determines the default

### Windows

Create the pipe with an explicit DACL. Microsoft documents that the default
descriptor grants read access to Everyone and anonymous users. Do not use
that default for Inari. Also avoid granting clients `FILE_GENERIC_WRITE`:
it includes permission to create pipe instances. Grant the required individual
rights instead.
[Microsoft named-pipe security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights).

Capture a connected client's token through native named-pipe authentication,
then authorize its SID and administrative rights. Handle authentication errors
by rejecting access. Windows impersonation applies to a thread. Keep token
capture and reversion inside one synchronous scope; do not keep impersonation
active across a Tokio `.await`.
[Microsoft impersonation API](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-impersonatenamedpipeclient).

First-instance protection prevents the real Agent from silently opening another
listener at an occupied name. Device Center still needs to establish that it
reached the trusted Agent. Include this direction of authentication in the
platform trial; a pipe name alone is not identity.

### Linux and macOS

Use a filesystem socket in an Agent-owned directory. Apply the installation's
user/group access policy and obtain kernel peer credentials. Validate the
server identity as well as the client identity. Linux abstract sockets do not
inherit filesystem permissions. POSIX also does not guarantee that socket-file
permissions provide the same protection on every Unix system.
[Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html).

macOS supplies effective peer user and group identity through `getpeereid`.
Do not assume that Linux-specific credential fields exist on macOS.
[Apple getpeereid manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/getpeereid.3.html).

These identities establish an OS-account boundary. They do not prove that every
process under an allowed user is Device Center. Define that trust boundary
explicitly. Add application identity controls only for a requirement that OS
account authorization does not meet.

## Why the other finalists rank lower

**tarpc** has a compact Rust service definition, configurable deadlines,
propagation, and cancellation. It accepts pluggable transports. Current source
also includes Serde transport helpers for Unix and TCP. Its RPC model does not
give a transparent server-streaming return API for the event feed. Inari needs
an explicit event protocol and a Windows pipe transport.
[Project](https://github.com/google/tarpc),
[transport source](https://github.com/google/tarpc/blob/main/tarpc/src/serde_transport.rs).
I prefer Tonic because typed event streams and portable schema tooling are
useful beyond the current Python Agent. A smaller all-Rust local API is a valid
reason to choose tarpc instead.

**Cap’n Proto RPC** supports capability references and call pipelining over
ordered byte streams. This can express restricted remote objects well. Inari
does not currently need that object model to show Device Health, approve access,
or reconcile Device Work. Choosing it creates an API design commitment, beyond
choosing a fast encoding.
[Rust RPC project](https://github.com/capnproto/capnproto-rust/tree/master/capnp-rpc).

**NNG** has unusually complete native IPC controls, including Windows security
descriptors and platform peer credentials. It also supplies messaging patterns
and queue controls. Its official Rust binding's main crate is currently a v2
prerelease, while the reviewed stable NNG manual is v1.10. Confirm the exact
binding/version combination before adoption. C/FFI packaging and a separate
Inari schema remain costs.
[IPC options](https://nng.nanomsg.org/man/v1.10.0/nng_ipc_options.5.html),
[Rust binding metadata](https://raw.githubusercontent.com/nanomsg/nng-rs/main/nng/Cargo.toml).

**Zenoh** deserves consideration because Inari already uses it. Recent versions
provide transport-managed SHM and subscriber recovery features. The current
Rust SHM API remains unstable. Existing use does not prove that its local
transport enforces the required desktop/service identity policy. Introducing
sessions and key-expression routing for local administration needs a specific
benefit over a private connection.
[Zenoh 1.10 release](https://zenoh.io/blog/2026-08-17-zenoh-1.10/),
[SHM API](https://docs.rs/zenoh/latest/zenoh/shm/index.html).

**iceoryx2** currently lacks service access management across OS users.
`dev_permissions` exposes resources globally and is explicitly excluded from
production use. Its shared-memory data also needs self-contained layouts.
Those limits matter more than a vendor latency comparison for this boundary.
[Official FAQ](https://github.com/eclipse-iceoryx/iceoryx2/blob/main/FAQ.md#accessing-services-from-multiple-users).

**ipc-channel** documents unbounded channels and one-shot bootstrap. Its native
primitives are useful for a known helper pair. They leave too much service
admission and resource policy to Inari.
[Official API](https://docs.rs/ipc-channel/latest/ipc_channel/).

## Pairing and the contract

Device Center connects automatically after installation establishes OS access.
It does not run the external integration QR flow. Administrative methods
require an Agent Administrator; ordinary connection does not grant that role.

An external integration proves its durable identity, requests access, and
receives Agent-owned Client Grants after approval. It keeps that association
across reconnects. Local IPC and Iroh each report a principal to the same
authorization service. They do not share identical credential mechanisms.

Define one authoritative domain schema. Keep Odoo origin, POS configuration,
company, and business identifiers in its Device Adapter. Do not make them
required properties of generic Device Work or Client Grants. Tonic code
generation does not itself remove the current Odoo coupling.

Protobuf models can be shared with a future Iroh protocol. Tonic's gRPC wire
protocol does not automatically become an Iroh application protocol. Design
that remote encoding separately and avoid two independently maintained copies
of the domain schema.

## Adoption proof

Run one small end-to-end trial before replacing the current client boundary:

1. Install Agent under its real service account on each OS. Connect Device
   Center under an allowed non-service account. Reject an unauthorized account.
2. Validate Windows DACL rights, connected-token identity, pipe squatting, remote
   rejection, and client authentication of the Agent. Validate Unix directory
   ownership, permissions, peer identity, and stale socket handling.
3. Exercise a typed state call, a Pairing Request approval, and an event stream
   with two concurrent clients. Validate administrative permissions separately.
4. Stop a subscriber and send oversized messages. Memory must remain bounded;
   each slow-client outcome must be explicit. Define limits from actual messages.
5. Restart Agent. Device Center reconnects and obtains a fresh state snapshot.
   Agent Boot Identity distinguishes event sequences from different starts.
6. Disconnect during Device Work. Use Idempotency Key and Job Reconciliation;
   do not automatically repeat physical output. RPC cancellation does not prove
   that Device I/O stopped or that output failed.
7. Measure user-visible reconnect time, latency distribution, CPU, and peak
   memory on representative calls and event rates. Record payloads, OS,
   hardware, and build settings. Compare direct Tokio only if an unresolved
   transport concern justifies it.

No runtime trial or performance measurement accompanies this note. Source
inspection establishes API capabilities and current limits. Platform execution
must establish the final permission and lifecycle behavior.

The earlier [pairing and SDK research](pairing-and-sdk-technologies-research.md)
covers the broader connection experience. This note supplies its current local
IPC recommendation.
