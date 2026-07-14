# Inari Device Center

Device Center is the desktop companion for the Inari Agent. It owns the tray
icon, local setup, service controls, device views, and enrollment handoff. It is
not the agent service and does not talk to hardware or Zenoh directly.

## Run from source

Start a desktop session with a system tray, then run:

```sh
mise exec -- just sync
uv run --directory packages/agent_tray inari-tray
```

The development default is `spawn` mode: Device Center starts a child agent and
stops that child when it exits. Installed systems use `service` mode, where the
operating system owns the agent and quitting the tray leaves it running.

## Control modes

| Mode | Ownership |
| --- | --- |
| `spawn` | Device Center owns a development child process |
| `service` | Windows Service Control Manager, systemd, or launchd owns the agent |
| `monitor` | Device Center observes an agent managed elsewhere |

The tray connects to the local HTTP API, completes local pairing when needed,
and obtains short-lived access tokens. It receives state through the
snapshot-backed WebSocket and uses a slower HTTP reconciliation interval as a
safety net.

Useful development overrides include:

```env
INARI_TRAY_AGENT_API_BASE_URL=http://127.0.0.1:7310
INARI_TRAY_CONTROL_MODE=spawn
INARI_TRAY_SERVICE_SCOPE=system
INARI_TRAY_AUTO_START_AGENT=true
INARI_TRAY_LOG_LEVEL=INFO
INARI_TRAY_LOG_DIR=./logs
```

The default service names are `InariService` on Windows, `inari.service` on
Linux, and `io.inari.service` on macOS. Early spawn failures are written to
`logs/agent-launch.log` before the child has configured its own logging.

## Installed Windows behavior

The signed MSIX installs **Inari Device Center** as the Start Menu and sign-in
application and **Inari Agent** as a packaged `LocalService`. The package also
registers `inari://` invitation links. A running instance receives links over
Qt local IPC instead of opening a second tray.

Trust deployment and installation are documented in the
[Windows guide](../../docs/windows.md).

## Tests

```sh
uv run --directory packages/agent_tray --group dev pytest tests -q
```

Use `mise exec -- just check` before submitting changes that cross package
boundaries.
