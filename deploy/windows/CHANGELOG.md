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

## inari-device-center@1.20.0-alpha.6

### Resume setup safely after an interruption

Inari Device Center now asks the local agent whether setup actually finished before opening the main window. Closing an invalid, failed, or interrupted invitation no longer skips first-time setup on the next launch. The assistant resumes at the saved step, offers a clean start-over path after a failure, and can finish setup before any devices are attached.

## inari-device-center@1.20.0-alpha.5

### Fix the Device Center icon on Windows

Device Center now keeps its intended transparent icon on the Windows taskbar
and Start menu instead of appearing inside a pale system-generated square.

Windows releases now publish provenance for every included file and bind the
installer to its SPDX SBOM with a GitHub attestation.

## inari-device-center@1.20.0-alpha.4

### Fix Windows installation and first launch

App Installer now presents a single, clear installation action. The Windows package also carries the TLS runtime that matches its embedded Python interpreter, preventing Device Center from failing on first launch.

## inari-device-center@1.20.0-alpha.3

### Keep published artifacts immutable

Completed release plans are now retired before another version is prepared, so
later changes can never rebuild an already published Device Center version.

### Fix Windows publisher trust

Windows installation now deploys the complete Inari signing chain and verifies
the MSIX through the same machine certificate stores used by App Installer.
The installation guide includes a direct recovery path when Windows shows an
unknown publisher.

## inari-device-center@1.20.0-alpha.2

### Introduce Inari Device Center for Windows

The first Windows distribution packages Device Center as the user-session tray application and the Inari agent as its own delayed-start service. The signed MSIX includes protocol activation, protected local pairing, native credential storage, canonical brand assets, checksums, an SBOM, and installation guidance for managed environments.

### Add recoverable Windows publication

Tegami now versions the complete edge distribution as one synchronized release. Signed Windows artifacts attach to the corresponding GitHub release with checksums and provenance, and interrupted uploads can safely resume from verified remote state.

### Refresh the security baseline

The edge distribution now ships with patched releases of its authentication, cryptography, HTTP, configuration, and internationalized-domain dependencies. The release test toolchain also uses the corrected temporary-directory handling in Pytest 9.

### Establish the Windows publisher identity

Inari Device Center packages now carry Pablo Hernández Jiménez as their
publisher identity. A publisher-owned code-signing root delegates to a
project-scoped Inari issuing authority, giving managed Windows deployments a
clear and truthful trust boundary without coupling the root identity to one
application.
