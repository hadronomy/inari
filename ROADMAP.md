# Roadmap

Inari is still in alpha. The near-term work is about making the existing
architecture dependable across real device fleets, not adding parallel ways to
do the same job.

## Local trust on desktop systems

The local HTTP pairing flow already uses short-lived secrets, signed client
challenges, origin-bound tokens, and a persistent tray identity. Installed
Windows packages add a named-pipe bootstrap tied to the package family.

The remaining design work is to decide where native clients on Linux and macOS
should prefer local IPC over loopback HTTP. Any implementation should preserve
the same authorization model, keep platform details behind a small boundary,
and remain optional for browser-based POS clients.

## Device coverage

Printers are the most mature device family. Scale and scanner support should
follow the same interface/driver split, stable device identity, approval state,
durable jobs, and observable failure model rather than growing ad-hoc transport
paths.

## Production readiness

Before a stable release, the project needs sustained upgrade testing across the
controller, agent, Windows package, and Kubernetes chart; a production Windows
signing service with durable timestamping; broader hardware fixtures; and
documented disaster-recovery exercises for PostgreSQL, certificates, and edge
state.

Concrete work belongs in GitHub issues. This file records direction and the
architectural constraints that should survive individual implementations.
