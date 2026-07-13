## inari-tray@1.20.0-alpha.2

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
