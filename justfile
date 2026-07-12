set windows-shell := ["powershell", "-NoLogo", "-NoProfile", "-Command"]

# Show the available repository workflows.
default:
    @just --list --unsorted

# Sync every workspace package into the current environment.
sync:
    uv sync --all-packages

# Format every language surface.
format:
    cargo fmt
    uv run --no-sync --group dev ruff format packages/agent packages/agent_tray

# Lint the Python packages with Ruff and every Rust target with Clippy.
lint:
    cargo clippy --workspace --all-targets --all-features -- -D warnings
    cargo clippy -p inari-web --no-default-features --features ssr -- -D warnings
    uv run --no-sync --group dev ruff check packages/agent packages/agent_tray

# Validate the hydration crate for the browser target.
lint-wasm:
    cargo clippy -p inari-web-frontend --target wasm32-unknown-unknown -- -D warnings

# Type check the Python packages with ty.
typecheck:
    uvx ty check

# Build the server binary and hydrated WASM/CSS bundle.
build-web:
    cargo leptos build --release

# Run the repository verification suite.
check: lint lint-wasm typecheck
    cargo fmt --check
    cargo test --workspace
    uv run --directory packages/agent --group dev pytest tests -q
    uv run --directory packages/agent_tray --group dev pytest tests -q
