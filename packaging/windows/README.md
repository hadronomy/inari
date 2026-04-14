# Windows Packaging

This directory contains the Windows packaging workflow for the IoT Agent stack.

The current packaging model builds:

- `IoT Agent Tray.exe`
- `IoT Agent Service.exe`
- an MSIX package that contains both executables, the tray startup task metadata, and the packaged service metadata

The workflow is designed around:

- `uv` for workspace builds
- `pyapp` for self-bootstrapping Python launchers
- `MSIX` for install, uninstall, signing, and Windows shell integration

## Required Tooling

- Rust and Cargo
- a checked out copy of `pyapp`
- a standalone CPython distribution archive for Windows
- Windows SDK tools:
  - `makepri`
  - `makeappx`
  - `signtool` if you want signing

## Config

Start by copying the example config:

```powershell
uv run --directory packages/windows_installer iot-agent-windows-installer init-config
```

That writes:

- [iot-agent-windows.toml](./iot-agent-windows.toml)

Adjust at least:

- `identity.package_id`
- `identity.publisher`
- `identity.publisher_display_name`
- `paths.pyapp_source_dir`
- `paths.python_distribution_path`
- `paths.certificate_path` if signing is enabled

The example config is in [iot-agent-windows.example.toml](./iot-agent-windows.example.toml).

## Stage

To build the launchers and generate the MSIX layout:

```powershell
uv run --directory packages/windows_installer iot-agent-windows-installer stage
```

That workflow:

1. builds fresh `iot-agent` and `iot-agent-tray` wheels with `uv`
2. exports third-party dependencies for the tray stack
3. vendors those wheels into a local wheelhouse
4. builds two PyApp launchers
5. generates MSIX icons, `AppxManifest.xml`, and `priconfig.xml`

Generated staging content lands under `build/windows/`.

## Package

To produce the `.msix`:

```powershell
uv run --directory packages/windows_installer iot-agent-windows-installer package
```

To force signing for a single run:

```powershell
uv run --directory packages/windows_installer iot-agent-windows-installer package --sign
```

The package output lands under `dist/windows/`.

## Packaging Notes

- The tray is the visible packaged application.
- The agent runs as a packaged Windows service through `IoT Agent Service.exe`.
- The tray startup task is controlled by `tray.startup_task_enabled`.
- The packaged service defaults to `localService` for a calmer least-privilege baseline.
- The launcher bootstrap is self-contained for workspace-local packages, and by default it vendors transitive wheels as well.
