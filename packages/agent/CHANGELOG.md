## inari@1.20.0-alpha.7

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

## inari@1.20.0-alpha.6

### Resume setup safely after an interruption

Inari Device Center now asks the local agent whether setup actually finished before opening the main window. Closing an invalid, failed, or interrupted invitation no longer skips first-time setup on the next launch. The assistant resumes at the saved step, offers a clean start-over path after a failure, and can finish setup before any devices are attached.

## inari@1.20.0-alpha.5

### Fix the Device Center icon on Windows

Device Center now keeps its intended transparent icon on the Windows taskbar
and Start menu instead of appearing inside a pale system-generated square.

Windows releases now publish provenance for every included file and bind the
installer to its SPDX SBOM with a GitHub attestation.

## inari@1.20.0-alpha.4

### Fix Windows installation and first launch

App Installer now presents a single, clear installation action. The Windows package also carries the TLS runtime that matches its embedded Python interpreter, preventing Device Center from failing on first launch.

## inari@1.20.0-alpha.3

### Keep published artifacts immutable

Completed release plans are now retired before another version is prepared, so
later changes can never rebuild an already published Device Center version.

### Fix Windows publisher trust

Windows installation now deploys the complete Inari signing chain and verifies
the MSIX through the same machine certificate stores used by App Installer.
The installation guide includes a direct recovery path when Windows shows an
unknown publisher.

## inari@1.20.0-alpha.2

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
