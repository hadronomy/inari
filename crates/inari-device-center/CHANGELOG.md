## inari-device-center@1.20.0-alpha.9

### Rebuild Device Center around a translucent shell

Device Center now opens with one glass tint across the window. The titlebar and
the navigation rail share this plane. The brand sits beside the native window
controls on their centerline. The content panel adds a thin tonal step, and
cards use quiet light or dark washes. The desktop remains visible through the
surfaces on macOS and Windows.

Translucency is a preference, not a fixed style. Support has a Display section
that switches the window to solid surfaces and stops the connection pulse and
the navigation slide. `INARI_MATERIAL=opaque` and `INARI_REDUCED_MOTION` apply
the same settings from launch. Linux keeps solid surfaces, because a blur
behind the window is not guaranteed there.

### Show the path from this computer to the devices

Overview opens with the connection path: this computer, the local agent, and
the devices it operates. A broken segment shows where the path stops, so the
first question an operator has is answered before they read anything else.

Items that need a person are now listed one by one with their state and what to
do, in place of a count that sent you to another screen to learn what it meant.

### Act on a problem from where you find it

Items in Needs attention are now controls. Selecting a device opens the device
directory with that device already selected, and selecting failed work opens
Activity. Reading a problem no longer means finding the same device again by
hand on another screen.

### Reach every control from the keyboard

The navigation rail, the agent health indicator, the device list, and the
attention items are all focus stops. Tab moves between them, Enter and Space
activate them, and Up and Down move the selection through the device list.

Focus rings follow the input device. They appear when you move with the
keyboard and stay hidden while you use the pointer, so selecting something with
the mouse no longer leaves an outline behind on it.

### Stop the repeated credential prompts

Device Center read this computer's stored identity on every attempt to reach
the agent. With the agent stopped, the reconnect loop turned that into a system
credential prompt every few seconds. The identity is now read once. A read that
fails is held, and Device Center reports that it stopped asking rather than
implying it is still trying. Select "Check again" in Support to allow access and
retry.

### Report agent health on every screen

The titlebar carries the agent state on all screens and opens Support when you
select it. A service that runs but does not answer now reports as "Not
responding" instead of "Running", and Support offers only the recovery action
that matches the current state.

### Make device and job states readable without color

Devices, jobs, and the agent service share one set of states. Each state has
its own label and its own icon, so the interface stays readable with
Differentiate Without Color, in high contrast, and in a grayscale screenshot.

Device kinds now have their own icons for printers, scales, and scanners.
Identifiers, endpoints, and error text are set in a monospace face.

### Start the Windows agent service

Windows can now start the packaged agent under the `LocalService` account. The
service uses production defaults when no custom config path exists.

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
