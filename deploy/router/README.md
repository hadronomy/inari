# Router image

The image packages `inari-router` with the official Zenoh 1.9 executable.
The Supervisor owns the stock Router process. It starts no data plane until a
current signed policy passes validation. Management requires dedicated mTLS.

The image runs as UID and GID 65532 with a read-only root filesystem.
Mount its TOML config and TLS files read-only. Give the state directory a writable
persistent volume owned by that user. The policy generation must survive Pod
replacement. A temporary directory is sufficient only for isolated tests.

The config sets `zenoh_executable = "/usr/local/bin/zenohd"`.
The entry point accepts `--config /path/to/router.toml`. See the
[policy contract](../../docs/router_policy.md) for authority and acknowledgment
requirements.

## Verification and publication

The [Router image workflow](../../.github/workflows/router-image.yaml) builds the
Supervisor through `mbx`, verifies the official Zenoh archive's SHA-256 digest,
and packages both executables. Its container contract runs the built image with
separate management and data-plane CAs. It checks absent-policy denial, exact
acknowledgment, a real data-plane mTLS connection, retry, expiry, generation
rollback rejection, and recovery with a higher generation.

Only a successful build on `main` publishes an immutable source tag to
`ghcr.io/hadronomy/inari-router`. Publication includes provenance, an SBOM, and
a Cosign signature for the image digest. Deployment must verify the workflow
identity and select that exact digest.
