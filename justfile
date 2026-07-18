set windows-shell := ["powershell", "-NoLogo", "-NoProfile", "-Command"]

# Show the available repository workflows.
default:
    @just --list --unsorted

# Sync every workspace package into the current environment.
sync:
    uv sync --all-packages
    bun install --frozen-lockfile

# Format every language surface.
format:
    cargo fmt
    uv run --no-sync --group dev ruff format packages/agent deploy/windows
    bun run format

# Lint the Python packages with Ruff and every Rust target with Clippy.
lint:
    cargo clippy --workspace --all-targets --all-features -- -D warnings
    cargo clippy -p inari-web --no-default-features --features ssr -- -D warnings
    uv run --no-sync --group dev ruff check packages/agent deploy/windows
    bun run lint

# Validate the hydration crate for the browser target.
lint-wasm:
    cargo clippy -p inari-web-frontend --target wasm32-unknown-unknown -- -D warnings

# Type check the Python packages with ty.
typecheck:
    uvx ty check
    bun run typecheck

# Validate and build the Tegami release packages.
check-release:
    bun run format:check
    bun run lint
    bun run typecheck
    bun test
    bun run build:release

# Lint the maintained Markdown documentation.
check-docs:
    bun run docs:check

# Regenerate and verify the local-agent contract consumed by the Rust client.
check-contracts:
    uv run --directory packages/agent python -m inari.local_api.openapi ../../contracts/local-agent.openapi.json
    git diff --exit-code -- contracts/local-agent.openapi.json

# Show the release changes Tegami would version without writing them.
release-preview:
    bun run release:preview

# Build and sign Inari Device Center on a Windows release host.
build-windows:
    powershell -NoLogo -NoProfile -File deploy/windows/build.ps1

# Build the server binary and hydrated WASM/CSS bundle.
build-web:
    cargo leptos build --release

# Lint, render, schema-check, and package the Kubernetes distribution.
check-kubernetes:
    scripts/validate-kubernetes.sh

# Validate rendered manifests through a real Kubernetes API server in kind.
check-kubernetes-server:
    scripts/validate-kubernetes-server.sh

# Run the repository verification suite.
check: lint lint-wasm typecheck check-release check-docs check-contracts
    cargo fmt --check
    cargo test --workspace
    uv run --no-sync --group dev python -m pytest deploy/windows/tests -q
    uv run --directory packages/agent --group dev pytest tests -q
    just check-kubernetes
