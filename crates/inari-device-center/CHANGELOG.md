## inari-device-center@1.20.0-alpha.8

### Fix Device Center setup after installation

Device Center now reaches the installed agent with valid request paths. Agent
outages show clear recovery steps and keep Support available.

### Improve Device Center clarity

The revised interface has consistent spacing, accessible light and dark color
tokens, clearer status messages, keyboard-ready navigation, and a readable
desktop typeface.

### Fix the macOS application icon

The macOS icon now uses the full canvas and lets the system apply its mask. The
mark also stays sharp at every bundle size.

## inari-device-center@1.20.0-alpha.7

### Make Device Center a native Rust application

Device Center and its tray now run on GPUI, with one coherent setup and
operations shell backed by a typed local-agent client. Setup resumes from the
agent’s durable checkpoint, invitation links are forwarded to the running
instance without touching disk, and local identity from earlier installations
continues to work. The new device directory makes hardware easy to search and
keeps stable integration identifiers close at hand.

The Windows package now combines the native Device Center with the existing
Python agent service. Device Center reports the service’s actual Windows state
and offers start or restart only when either action is useful. Closing or
quitting the client still leaves device work running in the background.
