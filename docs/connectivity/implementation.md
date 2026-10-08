# Implement the connectivity redesign

**Status:** Future implementation procedure. No stage is complete from research alone.

Read [the target glossary](CONTEXT.md), [reference](README.md), and
[Rust craft standard](rust-craft.md) first.
Use these stages for an implementation task explicitly authorized by the user.
This document is not authorization to deploy, publish, or replace a live service.

## Stage discipline

Each stage follows **design → implement → validate → review → complete**.
Record the interface, ownership, data access patterns, and acceptance scenarios
before implementation. Use the [craft standard](rust-craft.md#design-the-caller-experience-first)
to design the API from complete caller workflows.

Execute validation against the implemented stage. Test small increments during
implementation, then run its full completion validation and craft review.
Do not advance until the stage's completion criterion and the
[craft gate](rust-craft.md#stage-completion-gate) pass.
Fix defects in the current stage. Record an unavailable required target as a blocker.

Every completed runtime stage leaves working behavior for the next stage to extend.
Local IPC implements the selected stack without a comparison experiment.
Stage 7 validates packaged release artifacts in addition to each stage's evidence.

## 0. Establish the implementation baseline

### Produce the baseline

1. Read the active checkout's `AGENTS.md`, glossary, architecture, and release configuration.
2. Record the branch, commit, supported OS architectures, and relevant contract versions.
3. Compare the inspected research revisions with the actual implementation.
4. Inventory required Controller gates and Odoo fields at each public authorization boundary.
5. Map each field to generic authority or the owning Device Adapter.
6. Record the shipped browser workflow and its offline requirements.
7. Record the optional managed features that need retention or removal decisions.
8. Select one bounded end-to-end slice and its acceptance scenarios.
9. Inspect Rust manifests, toolchain, formatting, lints, and neighboring module interfaces.
10. Record module owners, caller examples, data access patterns, and resource budgets for the first slice.
11. Establish the Rust MSRV, toolchain, inherited lint, formatting, and CI policy described in the craft standard.

### Validate the baseline

1. Trace each coupled field and setup gate to current code and its target owner.
2. Review the first slice's caller API and invariants before implementation starts.
3. Run the relevant baseline checks, including any changed authoring configuration.
4. Record existing failures and resolve those that block the first slice's completion criteria.

**Complete when:** Every coupled field and setup gate has an owner and a target
disposition. Browser requirements and shipped platforms are explicit. The first
slice has a concrete observable result. No runtime decision relies on an old
research revision as proof of current code. Authoring policies are explicit and
validated. The applicable [craft gate](rust-craft.md#stage-completion-gate) passes.

## 1. Implement protected local IPC

### Design and implement

1. Read the [local IPC audit](../local-ipc-alternatives-research.md).
2. Pin interprocess, Tokio, Tonic, prost, and schema tooling for implementation.
3. Define installer account policy and authentication of both peers.
4. Design the local client API, typed failures, task ownership, and subscription recovery.
5. Implement one typed readiness call and one bounded event subscription.
6. Keep OS authentication and Tonic connection details within the local client and service boundaries.
7. Implement reconnect, finite limits, peer rejection, and endpoint lifecycle behavior.

### Validate after implementation

1. Compile readiness, event subscription, and recovery examples through the caller API.
2. Run Agent under each real service account and Device Center under another authorized account.
3. Reject an unauthorized account and an impersonated Agent endpoint.
4. Validate Windows DACL rights, token capture, first-instance protection, and remote rejection.
5. Validate Unix directory ownership, peer credentials, permissions, and stale socket handling.
6. Validate request limits, slow subscribers, reconnect, and Agent restart.
7. Measure queue and memory bounds under the recorded workload. Review API depth and ownership.

**Complete when:** interprocess and Tonic/Protobuf work on every shipped OS architecture under its
real account boundary. Unauthorized access fails. Memory remains bounded.
Device Center reconnects without external pairing. The Windows connector and
both-peer authentication have execution evidence. Typed calls and event streams
use the selected stack. The [craft gate](rust-craft.md#stage-completion-gate) passes.

## 2. Define generic contracts and authority

### Design and implement

1. Create one authoritative schema for Device messages and local administrative methods.
2. Specify Contract Major, typed errors, finite limits, deadlines, and operation permissions.
3. Specify Client identity, grant scope, key binding, expiry, and revocation rules.
4. Specify Idempotency Key scope, work fingerprints, and conflicting-submission behavior.
5. Specify event identity, gap behavior, snapshot or replay, and Job Reconciliation.
6. Keep generated wire types behind domain mappings and the client boundary.
7. Implement validated domain types, matchable errors, authority checks, and a reference Client without Odoo fields.
8. Document the SDK API through complete submission, event recovery, and reconciliation examples.
9. Implement contract fixtures and reproducible generation. Update the glossary and protocol documents with the new boundary.

### Validate after implementation

1. Compile caller examples and run typed acceptance, rejection, event, and reconciliation scenarios.
2. Exercise two independent Clients against the same application services.
3. Validate grant isolation, conflicting submissions, ordering, finite limits, and schema generation.
4. Review public types and module boundaries against the API Guidelines and the craft standard.

**Complete when:** Contract fixtures cover typed acceptance, rejection, and events.
Two Clients have isolated grants, execution state, content, and Idempotency Keys.
The generic authority contains no required Odoo business field. Schema generation
is reproducible. Every exposed operation has a documented permission and limit.
The [craft gate](rust-craft.md#stage-completion-gate) passes.

## 3. Implement Agent-owned pairing and SDK recovery

### Design and implement

1. Read the pairing and identity sections of [the reference](README.md#pairing-identity-and-revocation).
2. Design pairing state transitions, persistence transactions, typed errors, and the SDK recovery interface.
3. Persist protected Agent identity and Client associations under one storage owner.
4. Implement bounded Pairing Requests with key proof, expiry, and atomic consumption.
5. Implement local review, approval, decline, grant expiry, and revocation.
6. Expose administrative methods only through authorized local access.
7. Implement SDK identity storage, saved pairing, reconnect, and Job Reconciliation.
8. Implement explicit lost-key, key-rotation, backup, and restore behavior.

### Validate after implementation

1. Compile first-pairing, saved-pairing, and reconciliation caller examples.
2. Validate concurrent approvals, replay, decline, expiry, restart, and active revocation.
3. Validate storage failure, key replacement, backup, and restore against the authority rules.
4. Review transaction ownership, credential lifetimes, and bounded request state.

**Complete when:** One approved association survives restart. A repeated or
expired request creates no authority. One revoked Client loses access while
another continues. A lost response creates no extra physical output. Device
Center itself requires no external QR flow. Key replacement cannot bypass approval.
The [craft gate](rust-craft.md#stage-completion-gate) passes.

## 4. Implement and validate remote and browser paths

### Design and implement

1. Read the [network research](../inari-connectivity-alternatives-research.md).
2. Pin native Iroh and the required Python and browser bindings.
3. Specify the Iroh application protocol, framing, limits, and domain-schema mappings.
4. Persist the endpoint key and configure production relay and lookup candidates.
5. Restrict unknown peers to the bounded pairing surface during an active pairing window.
6. Authorize every Device operation through the same Agent application services.
7. Keep remote transport state and generated binding details behind the SDK interface.
8. Implement the browser workflow and Python Client packaging for their recorded requirements.
9. Record relay availability, metadata, abuse, operating cost, and support ownership.

### Validate after implementation

1. Exercise the generic caller workflows through the remote transport and bindings.
2. Validate direct connection, blocked UDP, relay use, network change, and restart.
3. Validate the browser path against the recorded offline and installation requirements.
4. Validate Python Client packaging on every shipped target that needs it.
5. Measure connection recovery, frame limits, memory, and slow-peer behavior.
6. Review protocol framing, cancellation, and domain mappings before recording adoption.

**Complete when:** Native, Python, and browser Clients complete their required
flows without a Controller or Odoo assertion issuer. Network changes preserve
trusted identity. Production relay and lookup ownership is explicit. Required
offline behavior has execution evidence. Unsupported browser behavior is resolved
before adoption. Tailcat comparison is required only for a concrete Iroh failure.
The [craft gate](rust-craft.md#stage-completion-gate) passes.

## 5. Deliver the pairing experience and integrations

### Design and implement

1. Design complete user flows and Device Adapter APIs using the established client boundary.
2. Implement the connection states from [the reference](README.md#pairing-experience-and-connection-states).
3. Implement Connect Inari, invitation links, QR review, and the authenticated headless CLI.
4. Show Client identity, requested Device access, expiry, and one clear approval action.
5. Keep manual IP, port, certificate, company, and Controller fields outside the default flow.
6. Add native discovery only if it removes a demonstrated manual step.
7. Move Odoo business scope, rendering, and workflow rules into its Device Adapter.

### Validate after implementation

1. Exercise clean installation, slow startup, first pairing, saved pairing, and decline through the UI and CLI.
2. Run Odoo and a plain integration concurrently through the generic contract.
3. Revoke one Client and validate the other Client's continued access.
4. Measure manual fields, approvals, first Device Test time, and unexpected repeat pairing.
5. Review caller code for duplicated lifecycle work and Odoo fields in core APIs.

**Complete when:** Clean installation reaches useful Device state without a
Controller. Startup never appears as a false failure. Both integrations work
concurrently and one revocation leaves the other intact. User effort has recorded
measurements. Optional discovery does not introduce another authority service.
The [craft gate](rust-craft.md#stage-completion-gate) passes.

## 6. Complete the Rust runtime and storage cutover

### Design and implement

1. Record the current Drivers, platform behavior, database revisions, and protected state.
2. Design runtime interfaces, state transitions, scheduling data, and bounded Driver execution.
3. Implement the Rust ownership boundary around the existing durable execution semantics.
4. Keep service installation and control in Agent packaging.
5. Implement one owner for schema migration and protected storage at cutover.
6. Implement Shared Device scheduling and authoritative Exclusive Device leases.
7. Preserve Output Evidence, Outcome Unknown, and explicit recovery decisions.
8. Migrate Device Center, SDKs, and Device Adapters to the adopted contract.
9. Remove obsolete Python authority, transport branches, schema fields, and setup gates at the completed boundary.
10. Resolve retention or removal of each optional managed feature.

### Validate after implementation

1. Validate historical database fixtures, backup, restore, key continuity, and revoked grants.
2. Validate real Devices, Shared Device scheduling, and Exclusive Device leases.
3. Validate failure during Device I/O, restart, shutdown, and Job Reconciliation.
4. Profile representative scheduling and event workloads against the recorded resource budgets.
5. Review state ownership, collection layout, Driver APIs, migrations, and completed-boundary cleanup.

**Complete when:** The Rust Agent owns one functioning runtime and authoritative
store. Device execution and recovery pass under real service packaging. Historical
state has a tested disposition. No permanent old/new authority paths remain.
Retained managed features are optional and cannot block local readiness or pairing.
The [craft gate](rust-craft.md#stage-completion-gate) passes.

## 7. Validate the release

### Assemble the release

1. Finish contract fixtures, generated models, migrations, installer docs, and configuration templates together.
2. Add the release notes required by the repository's Tegami workflow.
3. Build the candidate artifacts and record their versions and supported target matrix.

### Validate after implementation

Validate these scenarios against the shipped artifacts, not only development processes:

| Scenario | Required result |
| --- | --- |
| Clean install without infrastructure | Agent and Device Center reach local readiness |
| Close Device Center | Agent and accepted Device Work continue |
| Start Agent slowly | UI shows Starting until readiness |
| Two integrations and one busy Client | Grants, queues, content, and events remain isolated and bounded |
| Shared and Exclusive Devices | Safe scheduling and authoritative lease enforcement |
| Declined, expired, replayed pairing | No Client Grant appears |
| Restart and changed network | Saved identities reconnect without another pairing |
| Revoked Client and active subscription | Further access stops while other Clients continue |
| Response lost during Device I/O | Reconciliation preserves one work identity and physical evidence |
| Event gap or changed Agent Boot Identity | Authorized snapshot or replay restores state without invented Device events |
| Blocked UDP and relay failure | Documented recovery and delivery outcomes |
| Invalid local peer or fake Agent listener | Access fails under the real OS account policy |
| Oversized request or stalled reader | Finite resource use and typed failure or recovery |
| Database restore or lost identity | Explicit recovery preserves revocation and work safety |
| Diagnostics export | No secrets or business Device Work content |

1. Run the repository's required validation commands for every changed surface.
2. Run Rust commands through `mbx` and use the repository's package and worktree tools.
3. Record platform results, remaining limits, and measured UX and resource behavior.
4. Review the complete shipped caller APIs and module boundaries against the craft standard.
5. Update this directory's decision statuses and the current architecture after the implementation passes.

**Complete when:** Every required scenario has a passing result for its shipped
target. The documented platform matrix matches tested artifacts. No production
readiness claim rests on a source audit or demonstration alone.
The [craft gate](rust-craft.md#stage-completion-gate) passes.

## Handoff between implementation tasks

Record the stage, branch and commit, adopted versions, and contract and database revisions.
Record passing scenarios, failures, open decisions, and the next completion criterion.
Link the interface design record, code, tests, and source evidence.
Record craft review findings and their resolution, collection choices, and resource measurements.
Record validation commands and results. Mark an unexecuted scenario as unexecuted.
Keep this reference authoritative for design changes; keep measurements with
the implementation evidence. A completed task never means the whole redesign
is complete unless every required stage has evidence.
An incomplete craft gate keeps the current stage open and blocks the next stage.
