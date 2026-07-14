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
    uv run --no-sync --group dev ruff format packages/agent packages/agent_tray deploy/windows/build.py
    bun run format

# Lint the Python packages with Ruff and every Rust target with Clippy.
lint:
    cargo clippy --workspace --all-targets --all-features -- -D warnings
    cargo clippy -p inari-web --no-default-features --features ssr -- -D warnings
    uv run --no-sync --group dev ruff check packages/agent packages/agent_tray deploy/windows/build.py
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
check: lint lint-wasm typecheck check-release check-docs
    cargo fmt --check
    cargo test --workspace
    uv run --no-sync --group dev python -m pytest deploy/windows/tests -q
    uv run --directory packages/agent --group dev pytest tests -q
    uv run --directory packages/agent_tray --group dev pytest tests -q
    just check-kubernetes
