# Rust implementation craft

**Status:** Required standard for the connectivity redesign. This document does not change runtime code.

Read [the target glossary](CONTEXT.md), [design reference](README.md), and
[implementation stages](implementation.md) before work. Apply the installed
`hadronomy-skills:rust-craft` skill. Use its API, errors, workspace, async,
performance, testing, and dependency references as the task requires.
Apply `codebase-design` when designing a module boundary.

Repository instructions take precedence over skill defaults. Inspect the active
checkout and neighboring code before adopting an upstream pattern. Record
the source revision for any pattern that affects the design.

## Design the caller experience first

Before implementation, write a short design record beside the changed boundary.
Include:

- The caller's operation and a complete usage example, including failure and recovery.
- The owning module, hidden mechanisms, and types that cross its interface.
- Each public item's purpose, invariant, ownership, and resource lifetime.
- Typed failures, authorization, ordering, deadlines, cancellation, and finite limits.
- The data access pattern and the reason for each important collection or shared owner.
- Acceptance scenarios and the commands that will validate the implemented behavior.

For Device Center, demonstrate readiness, an event gap, and recovery without
caller-owned transport machinery. For an integration, demonstrate saved pairing,
Device Work submission, Outcome Unknown, and Job Reconciliation.
Use these examples to reject APIs that expose unnecessary setup or recovery steps.

The examples describe the proposed interface. Compile them against the completed
implementation during validation. Generated Tonic methods alone do not establish
the SDK's caller experience.

## Build deep modules

A deep module provides substantial behavior through a small, clear interface.
Its interface includes guarantees and failure behavior, as well as signatures.
Keep each invariant with the module that owns the state needed to enforce it.

The client boundary owns connection authentication, reconnect, subscription
recovery, and typed transport failures. Callers receive domain results and
connection state. Agent application services own authorization and Device Work
semantics. Drivers own Device protocol behavior. The composition root assembles
these modules and their platform dependencies.

Keep generated Protobuf models, Tonic channels, Iroh framing, and OS handles
inside their owning boundaries. Map wire values into validated domain types.
Keep Device Adapter business fields outside Agent authority and core SDK types.

Use seams where behavior actually varies: platform authentication, storage,
Drivers, or adopted transports. Choose a trait only when it expresses that
variation or a useful test boundary. Record static or dynamic dispatch and any
required `Send`, `Sync`, and future bounds. Avoid a trait for every struct.

Review locality: a change to a wire codec must stay within its transport boundary.
A change to a grant rule must stay with authority and its contract tests.
If callers coordinate several low-level methods to maintain one invariant,
move that behavior into its owner.

Use the deletion test during review. If deleting a wrapper removes only
forwarding, simplify it. If deletion scatters authentication, recovery, or
ordering into callers, the module earns its place. Create a crate only for a
real dependency, ownership, or distribution boundary.

## Express constraints through Rust types

- Use distinct types for Device identity, Client identity, Client Grant, and Idempotency Key.
- Validate untrusted wire values once at admission. Keep invariant-bearing fields private.
- Use data-bearing enums for outcomes and lifecycle states. Avoid contradictory booleans and optional fields.
- Use fallible constructors and `TryFrom` for fallible conversion. Keep `From` infallible.
- Accept borrowed strings, paths, and slices when ownership transfer is unnecessary.
- Make copying, allocation, and shared ownership costs clear at the call site.
- Use concrete library errors that callers can match. Keep display text separate from recovery decisions.
- Document cancellation, event ordering, gap recovery, and whether an operation has already accepted durable Device Work.
- Implement standard traits when their meaning is valid. Redact secrets from `Debug` and tracing.
- Use `#[must_use]` when ignoring a returned value loses required work or evidence.
- Keep visibility private, then widen it only for an actual caller.

Use typestate when it prevents important misuse through a simple interface.
Use runtime enums for states driven by network events, restart, or durable storage.
Use a builder when real configuration combinations justify it.
Do not add typestate, a builder dependency, or generic parameters by reflex.

Keep internal enums exhaustive. Introduce extensibility only for an explicit
external API commitment. Follow the repository's clean replacement policy;
remove obsolete interfaces at the completed boundary.

## Choose data structures from access patterns

Record expected counts, lifetime, lookup keys, iteration order, mutation,
contention, and admission limits before choosing storage.

| Access pattern | Starting point | Review obligation |
| --- | --- | --- |
| Sequential processing or snapshots | `Vec<T>`; `Box<[T]>` for fixed retained data | Capacity, traversal cost, and retained memory |
| Keyed mutable state | `HashMap` with an appropriate hasher | Untrusted keys, memory bounds, and explicit output ordering |
| Ordered keys or range access | `BTreeMap` | Required ordering and cost against the actual workload |
| Reused entries with stale references | Typed generational handles | Handle lifetime, deletion, and distinction from persistent Device identity |
| FIFO scheduling | `VecDeque` or a bounded channel | Fairness, overload, per-Client isolation, and shutdown |
| Shared immutable content | Owned buffers or existing `bytes` types | Copy count, retained backing storage, and content limits |

Begin with safe standard collections and existing workspace libraries.
Keep frequently accessed data contiguous where the workload benefits.
Separate infrequent metadata only when measurements justify the added layout.
Avoid one heap allocation or shared lock per small record without a reason.

Data-oriented design does not require an ECS, arena, structure of arrays, or
lock-free collection. Introduce these only for a demonstrated access pattern.
An arena needs a clear lifetime and destructor policy. A faster hasher needs
an explicit trust assessment. A small-buffer optimization needs measured sizes.

Validate representative Device counts, content sizes, concurrent Clients,
slow subscribers, restart, and overload. Measure latency distributions,
allocations, retained memory, and throughput where relevant.
Record the baseline and result before making a performance claim.
Set resource budgets during design, then validate them after implementation.

## Own async execution and physical effects

Use Tokio for the selected local stack. Keep pure domain decisions synchronous
when they need no I/O. Assign an owner to every task, queue, connection, and
subscription. Bound concurrency and queued bytes as well as message counts.

Use existing Tokio and `tokio-util` facilities when they meet the requirement.
Specify cancellation propagation, task supervision, draining, and shutdown.
Keep blocking Device operations outside async executor threads, with bounded
execution and a documented shutdown policy.

Check cancellation safety for each actual future used in `select!`.
Keep partial frame state with an owner that survives cancellation.
Do not hold a synchronous lock or OS impersonation context across an `await`.

RPC cancellation does not prove cancellation of physical Device I/O.
Keep durable acceptance, Idempotency Key checks, Output Evidence, and
Outcome Unknown explicit. Reconnect cannot silently resubmit physical output.
Event lag must lead to documented snapshot or replay recovery.

## Author code and configuration deliberately

Read `Cargo.toml`, `rust-toolchain.toml`, `rustfmt.toml`, any `clippy.toml`,
repository instructions, and two neighboring files before editing Rust.
Use the repository's shared manifest and formatting conventions.

The inspected checkout uses edition 2024, a `stable` toolchain, and a root
`rustfmt.toml`. It has no declared workspace MSRV or shared workspace lint table.
Recheck these facts in stage 0. Establish an explicit minimum supported Rust
version, toolchain update policy, inherited workspace lints, and CI checks.
Select policies for Inari's platform and dependency requirements.

Preserve the existing formatting configuration unless an intentional repository
decision changes it. Prefer stable rustfmt options and one checked configuration.
Run `mbx fmt --all -- --check`. Never format generated code by hand.
Review build profiles with measurements; do not copy a compiler project's profile.

Keep imports at the top, names explicit, and related implementations easy to find.
Comments explain reasons, contracts, synchronization, and safety invariants.
Keep `panic!`, `unreachable!`, `unwrap`, `expect`, and `todo!` out of production code.
Every `unsafe` block needs a precise `SAFETY` invariant and validation of its caller obligations.

Refactor lint failures. An exceptional `#[expect(..., reason = "...")]` needs
the smallest scope and a reviewable justification. Do not add broad suppression.
Remove dead code, forwarding wrappers, temporary stubs, and unused dependencies.

Before adding a library, inspect existing dependencies, current documentation,
types, features, and source when necessary. Record maintenance, license,
MSRV, platform support, API fit, build cost, and the complexity it removes.
Keep features additive and validate documented feature combinations.
Pin selected protocol tooling and reproduce schema generation.

## Stage completion gate

Every stage follows **design → implement → validate → review → complete**.
Plan validation before implementation. Execute the completion validation against
the implemented stage. Test and refine small increments during implementation.
Stage 7 adds packaged release evidence to the evidence from earlier stages.

Do not start the next stage until every applicable condition passes:

1. The stage's behavior works end to end under its required account and platform boundaries.
2. Caller examples compile. Public items have a purpose, documented invariants, and usable typed failures.
3. Module review finds no unnecessary caller coordination, leaked transport details, or speculative abstraction.
4. Ownership, collections, queue limits, and cancellation match the recorded access patterns and resource budgets.
5. Formatting, compiler, Clippy, relevant tests, doctests, and reproducible generation pass without new warnings.
6. Contract tests exercise real seams, including acceptance, rejection, recovery, and isolation where the stage changes them.
7. Documentation, schemas, configuration, migrations, packaging, and release notes match the changed behavior where applicable.
8. Known defects in the stage are resolved. Obsolete paths and temporary scaffolding are removed at the completed boundary.
9. The handoff records commands, results, tested targets, source revisions, and all unexecuted scenarios.

Use the checkout's required workflows and `mbx` for Rust commands.
The current `just check` includes Rust tests; inspect its actual commands again
at implementation time. Add focused package tests and doctests as needed.
Validate feature and MSRV configurations promised by the changed crates.

Use property tests or fuzzing for untrusted parsing and important state laws.
Use compile-fail tests for important compile-time API constraints.
Use Miri for compatible unsafe code and platform tests for native OS obligations.
Use Loom only when a custom concurrency protocol needs it.
Regenerate snapshots through their tooling and review every changed snapshot.

A source audit cannot replace execution evidence for an implemented behavior.
Record a justified non-applicable gate. An unexecuted required scenario blocks
completion. If the required host or Device is unavailable, leave the stage open
with its precise blocker. Do not defer a known defect to the next stage.

## Primary references

These sources guide specific decisions. Recheck moving upstream code before use.
Inari's requirements determine which patterns fit.

- [Rust API Guidelines](https://rust-lang.github.io/api-guidelines/checklist.html): naming, conversions, type safety, errors, documentation, and caller control.
- [rust-analyzer architecture](https://rust-analyzer.github.io/book/contributing/architecture.html): explicit API boundaries, domain representations, protocol mappings, and boundary tests.
- [Ruff contributor rules](https://github.com/astral-sh/ruff/blob/main/AGENTS.md): narrow visibility, typed constraints, reasoned lint exceptions, and reviewed generated snapshots.
- [Oxc workspace manifest](https://github.com/oxc-project/oxc/blob/main/Cargo.toml): shared metadata, lint policy, dependency declarations, and explicit tradeoffs.
- [The Rust Performance Book](https://nnethercote.github.io/perf-book/heap-allocations.html): allocation behavior and collection storage tradeoffs.
- [Tokio cancellation safety](https://docs.rs/tokio/latest/tokio/macro.select.html#cancellation-safety): method-specific obligations when futures are dropped.
