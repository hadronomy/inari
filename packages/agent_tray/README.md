# IoT Agent Tray

Windows tray companion for the `iot-agent` package.

The tray app is intentionally separate from the headless agent service:

- it runs in the user session, so it can own the Windows notification area icon
- it talks to the agent over the existing local HTTP and WebSocket API
- it can either monitor an external agent, manage a local background process, or control a Windows service

The tray is now WebSocket-first for live state:

- it bootstraps and reconciles with HTTP
- it keeps queue and device state fresh from the snapshot-backed `WS /events` stream
- it only falls back to slower HTTP reconciliation instead of polling `/system/status` after every runtime event

## Run

From the repository root:

```powershell
uv run --directory packages/agent_tray iot-agent-tray
```

## Environment

```env
IOT_AGENT_TRAY_AGENT_API_BASE_URL=http://127.0.0.1:7310
IOT_AGENT_TRAY_CONTROL_MODE=spawn
IOT_AGENT_TRAY_SERVICE_NAME=IoT Agent
IOT_AGENT_TRAY_AUTO_START_AGENT=true
IOT_AGENT_TRAY_STATUS_RECONCILE_INTERVAL_SECONDS=30
IOT_AGENT_TRAY_EVENT_RECONNECT_DELAY_SECONDS=3
IOT_AGENT_TRAY_LOG_LEVEL=INFO
IOT_AGENT_TRAY_LOG_DIR=./logs
```

Control modes:

- `spawn`: the tray starts and stops a local `iot-agent` background process, and auto-starts it on tray launch by default
- `service`: the tray controls a Windows service through the Service Control Manager
- `monitor`: the tray only observes an already-running agent

When `spawn` mode cannot boot the local agent, the tray writes the launcher output to `logs/agent-launch.log` so early startup failures are visible even before the agent itself configures logging.

By default, quitting the tray also stops the tray-managed local agent process in `spawn` mode.
