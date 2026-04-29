# AGENTS.md

## Prime Directive

Every change in this repository must feel intentional, modern, and world-class.

Write code that is:

- intentional in architecture and naming
- delightful to read, extend, and operate
- modern without chasing fashion
- minimal without being cryptic
- explicit without being noisy
- robust enough to be trusted in production

Prefer software that feels composed, calm, and durable.

Avoid code that is:

- accidental
- overly clever
- mechanically generated
- half-migrated
- unnecessarily abstract
- technically functional but unpleasant to live with

The standard is not merely "works."

The standard is:

- clear design
- clean boundaries
- excellent developer experience
- truthful documentation
- and implementation quality that feels world-tier

This file is the standing operating manual for agents working in this repository.

It exists to preserve the standards the project has converged on over time:

- explicit architecture
- clean boundaries
- delightful developer experience
- disciplined migrations
- protocol rigor
- no stale legacy code left behind

Read this before making changes.

## 1. Core Standard

Build the repository like a careful product engineer, not like a patch bot.

That means:

- prefer clear architecture over quick local cleverness
- finish changes end to end: implementation, cleanup, tests, docs, and migrations
- do not leave dead wrappers, stale fields, obsolete protocol branches, or misleading comments behind
- if a design is being modernized, migrate the whole boundary cleanly instead of creating parallel half-old/half-new paths

The project values software that feels intentional.

## 2. Repository Shape

The main components are:

- `packages/agent`
  The local IoT agent, API server, runtime, managed gateway, security, and service logic.
- `packages/agent_tray`
  The desktop tray companion. It is not the service/daemon.
- `docs/`
  Protocol, deployment, and behavior documentation.

High-level architecture:

- the agent is the long-running background service
- the tray is a user-session companion UI
- Odoo or the controller talks to the agent, not directly to hardware
- managed gateway enrollment happens over HTTPS
- steady-state managed gateway traffic uses Zenoh

## 3. Architecture Invariants

These are not suggestions.

### 3.1 Agent vs Tray

- The agent is the service/daemon.
- The tray is never the service.
- The tray may control the service, monitor it, and auto-start at login, but it remains a user-session app.

Platform model:

- Windows: agent as Windows Service, tray as startup/login app
- Linux: agent as `systemd` service, tray as desktop autostart app
- macOS: agent as LaunchDaemon or LaunchAgent depending on scope, tray as login/session app

### 3.2 Composition Root

- Keep dependency injection at the composition boundary.
- Domain/runtime/gateway code should use normal constructor injection and plain Python types.
- DI framework details belong in the composition layer only.

Current standard:

- `packages/agent/inari/container.py` is the composition root
- `packages/agent/inari/di/` holds provider modules
- `dishka` assembles the graph
- business code should not become framework-colored

### 3.3 Managed Gateway

- Enrollment is HTTP.
- Steady-state managed data plane is Zenoh.
- Do not reintroduce WSS-era stream concepts unless there is a very strong reason.
- The controller initiates policy; the agent initiates the transport connection.
- `outbound` means the agent opens the connection. It does not mean one-way traffic.

### 3.4 Certificates and mTLS

- step-ca certificate bootstrap and renewal are first-class
- mTLS is the recommended production posture after certificate issuance
- bootstrap may happen before a client certificate exists
- ongoing data-plane transport should assume certificate-backed security, not bootstrap bearer credentials

## 4. Protocol Discipline

This repository cares about protocol quality. Treat protocol changes like public API changes.

### 4.1 Protocol and Implementation Must Stay Aligned

If you change the wire contract, you must update:

- protocol models
- runtime implementation
- protocol docs
- relevant tests
- migration notes if state/schema changes are involved

Do not leave the docs describing a future protocol while the code still speaks an older one unless the document explicitly and intentionally says so.

### 4.2 Avoid Implementation-Shaped Protocols

Do not leak internal REST or runtime payloads directly as the protocol forever.

Preferred pattern:

- protocol-native models at the boundary
- adapters into internal request/domain models

### 4.3 Identity and Security Semantics Must Be Crisp

Use precise terms.

Examples:

- `enrollment_token` is the controller bootstrap credential
- `certificate.bootstrap` is certificate bootstrap data
- step-ca `ott` is not the same thing as controller enrollment auth

Avoid vague names like `bootstrap token` when they can mean multiple things.

### 4.4 Do Not Leave Half-Removed Transport Semantics

If a transport model changes, remove the old semantics cleanly.

Examples of things that should not survive a transport migration unless they are truly still used:

- obsolete ack states
- dead message types
- dead headers/auth hooks
- dead columns
- unused resume fields
- stale status semantics

## 5. Migration and Schema Rules

Schema work must be production-minded.

### 5.1 Use Real Migrations

- Use Alembic for runtime database changes.
- Keep schema definitions centralized.
- Add migration tests for historical fixtures when changing persistent structures.

### 5.2 Migrate Legacy Data, Don’t Just Stop Reading It

If old rows, columns, or enum values exist in the wild:

- normalize them in a migration
- then remove the dead shape from runtime code

Do not keep indefinite compatibility parsing for values we fully control unless there is a real compatibility need.

### 5.3 SQLite Safety

- keep startup migration safe
- preserve backup behavior when appropriate
- prefer deterministic upgrade flows

## 6. Config File Standard

The generated config template is a product surface.

Treat it as operator documentation, not just serialization output.

### 6.1 Template Expectations

- `config_version` stays uncommented
- defaults are usually commented out
- each section should be explained
- comments should describe purpose and operational meaning, not just restate field names
- examples should use clean TOML idioms such as `[[table]]` where appropriate

### 6.2 Avoid Redundant Noise

Do not generate useless comment spam like:

- `Default: ...`
- comments that merely rephrase the key name

### 6.3 Be Honest About Typical Usage

If a setting is usually controller-provided or advanced-only, say so clearly.

Do not imply routine hand-entry for values that are usually returned by enrollment or managed automatically.

## 7. Service and OS Integration Rules

- Service installation and service control belong to the agent package.
- Packaging and installer concerns should not distort the runtime architecture.
- The tray may detect and promote to service control mode when the service is actually managing the agent.
- Startup/readiness UX matters: if the process is alive but booting, show a startup state, not a false offline/failure state.

## 8. Documentation Standards

Documentation is part of the product.

### 8.1 Protocol Docs

- protocol docs should be structured, explicit, and normative where needed
- use clear terminology
- if using Mermaid diagrams, optimize for renderer compatibility, including VS Code preview
- prefer robust Mermaid syntax over clever styling that renders inconsistently

### 8.2 README Links

- all README links must be relative Markdown links
- never use local absolute filesystem paths in repository docs

### 8.3 Keep Architecture Truthful

If two things are distinct but co-located, document them as distinct but co-located.

Example:

- `Controller API`
- `Zenoh Router`
- grouped visually in the same server boundary

Do not collapse important distinctions into misleading shorthand.

## 9. Testing and Verification Standard

Do not call work done without verification proportionate to the change.

Preferred commands:

- `just sync`
- `just format`
- `just lint`
- `just check`

When `just` is unavailable in the current shell, run the underlying commands directly.

At minimum:

- run `ruff check` for changed Python surfaces
- run the relevant pytest suites
- run config/schema generation when config code changes
- run `uv lock` when dependencies change

If you skip a test suite, be explicit about it and why.

## 10. Cleanup Discipline

This is one of the most important project standards.

When refactoring:

- remove obsolete wrappers
- remove dead imports
- remove dead protocol models
- remove stale compatibility helpers unless intentionally retained
- remove no-op extension points that no longer serve the architecture
- update tests that were keeping dead code alive by accident

If you keep a compatibility shim, it must be:

- intentional
- small
- justified
- preferably documented in code or commit context

Never leave “temporary” scaffolding with no clear owner.

## 11. Review Checklist

Before considering a change complete, ask:

1. Does the architecture still read cleanly?
2. Did I update docs, tests, and migrations with the code?
3. Did I remove obsolete legacy codepaths?
4. Did I preserve or improve DX?
5. Are naming and semantics precise?
6. Is the config/operator experience clearer, not noisier?
7. Did I leave the repository in a state I’d be happy for another engineer to inherit?

If the answer to any of those is no, the work is probably not done.

## 12. Practical Style Notes

- prefer small provider modules over one giant wiring file
- prefer explicit helper names over framework magic
- prefer stable identifiers over user-facing names
- prefer one clear architecture over multiple overlapping pathways
- prefer beautiful boring code over impressive clever code
- use the `fff` MCP tools for all file search operations instead of default tools

The project should feel:

- clean
- modern
- explicit
- maintainable
- delightful to work in

That is the bar.
