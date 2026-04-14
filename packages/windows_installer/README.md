# IoT Agent Windows Installer

Workspace package that stages and packages the Windows installer artifacts for:

- `iot-agent-tray`
- `iot-agent` as a packaged Windows service host
- an MSIX bundle layout with generated assets and manifest metadata

Primary command:

```powershell
uv run --directory packages/windows_installer iot-agent-windows-installer --help
```

For the end-to-end workflow and example config, see [packaging/windows/README.md](../../packaging/windows/README.md).
