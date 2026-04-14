# IoT Agent Tray

Cross-platform tray companion for the `iot-agent` package.

The tray app is intentionally separate from the headless agent service:

- it runs in the user session, so it can own the desktop notification area icon
- it talks to the agent over the existing local HTTP and WebSocket API
- it can either monitor an external agent, manage a local background process, or control a platform service
- it now uses a Qt-based tray shell through `PySide6`, which gives us a more consistent cross-platform tray experience than the old `pystray` backend

The tray is now WebSocket-first for live state:

- it bootstraps and reconciles with HTTP
- it keeps queue and device state fresh from the snapshot-backed `WS /events` stream
- it only falls back to slower HTTP reconciliation instead of polling `/system/status` after every runtime event
- it obtains and refreshes a short-lived local bearer token automatically before calling protected agent endpoints

## Run

From the repository root:

```powershell
uv run --directory packages/agent_tray iot-agent-tray
```

The tray expects a desktop session with a visible system tray available to Qt.

## Environment

```env
IOT_AGENT_TRAY_AGENT_API_BASE_URL=http://127.0.0.1:7310
IOT_AGENT_TRAY_CONTROL_MODE=spawn
IOT_AGENT_TRAY_SERVICE_SCOPE=system
IOT_AGENT_TRAY_AUTO_START_AGENT=true
IOT_AGENT_TRAY_AUTH_CLIENT_NAME=iot-agent-tray
IOT_AGENT_TRAY_STATUS_RECONCILE_INTERVAL_SECONDS=30
IOT_AGENT_TRAY_EVENT_RECONNECT_DELAY_SECONDS=3
IOT_AGENT_TRAY_LOG_LEVEL=INFO
IOT_AGENT_TRAY_LOG_DIR=./logs
```

Control modes:

- `spawn`: the tray starts and stops a local `iot-agent` background process, and auto-starts it on tray launch by default
- `service`: the tray controls a platform service
  - Windows: Service Control Manager
  - Linux: `systemctl`
  - macOS: `launchctl`
- `monitor`: the tray only observes an already-running agent

In `service` mode, the tray now defaults to the same platform-native service identifier that the agent CLI installs:

- Windows: `IoTAgentService`
- Linux: `iot-agent.service`
- macOS: `io.iot-agent.service`

When `spawn` mode cannot boot the local agent, the tray writes the launcher output to `logs/agent-launch.log` so early startup failures are visible even before the agent itself configures logging.

By default, quitting the tray also stops the tray-managed local agent process in `spawn` mode.

## Platform Notes

- Windows service mode uses the configured service name directly.
- Linux service mode expects a `systemd` unit name such as `iot-agent.service`.
- macOS service mode expects a `launchd` label such as `io.iot-agent.service`.
- Opening the log directory uses the platform-native opener when available: `start`, `xdg-open`, or `open`.
