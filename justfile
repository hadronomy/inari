set windows-shell := ["powershell", "-NoLogo", "-NoProfile", "-Command"]

# Show the available repository workflows.
default:
    @just --list --unsorted

# Sync every workspace package into the current environment.
sync:
    uv sync --all-packages

# Format the Python packages with Ruff.
format:
    uv run --no-sync --group dev ruff format packages/agent packages/agent_tray

# Lint the Python packages with Ruff.
lint:
    uv run --no-sync --group dev ruff check packages/agent packages/agent_tray

# Type check the Python packages with ty. TODO: Install ty and add it to the dev group in pyproject.toml.
typecheck:
    uvx ty check

# Run the repository verification suite.
check: lint typecheck
    uv run --directory packages/agent --group dev pytest tests -q
    uv run --directory packages/agent_tray --group dev pytest tests -q
