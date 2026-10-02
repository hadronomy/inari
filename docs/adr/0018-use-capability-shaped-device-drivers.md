---
status: accepted
---

# Use capability-shaped device drivers

The Device Driver Interface exposes discovery, capabilities, execution, stop
requests, and health. Platform Backends contain operating-system submission
details. Drivers contain direct transport details.

Device Work uses stable `device_id` values. Printer names and default-printer
selection remain outside this Interface.

This change replaces the current transport-shaped printer methods. It gives
printers, scales, scanners, displays, and later device kinds one runtime seam.

The Interface is asynchronous and returns stable execution, stop, and Device
Health results. Each operation has a bounded deadline.

The Agent persists identity aliases independently of Driver keys. A different
physical identity on the same port requires Device Manager approval.

Each immutable Driver Profile has a stable identity and digest. A Binding
Revision pins one tested digest. Revocation blocks Device Work.

The Hardware Certification Matrix records exact Device, firmware, Driver,
Platform Backend, connection, media, and operating-system combinations. A
physical Driver Profile stays inactive until its exact combination passes the
lab gate.

Each immutable Matrix Row identifies its Release Set, signed test evidence,
effective date, status, and revocation date. Device-kind rules mark unused
dimensions as `not_applicable`.

Generic protocol-family tests can use emulated devices. They never authorize a
physical combination. Each Release Set lists the tested lab inventory.

Printer profiles record integer-dot width, length, origin, margins,
orientation, and action mapping. Certified hardware and media determine these
values. The system never infers physical support from inches alone.

A missing or revoked Matrix Row blocks activation and Device I/O. Firmware or
host drift marks the Binding Revision `Needs attention` and reports
`capability_changed`.

The Agent accepts Driver health events and runs bounded probes. Three missed
probe intervals make Device Health stale.

The Controller signs each canonical Driver Profile. The Agent checks its
signature, digest, compatibility, and offline age. Revocation blocks queued and
new work that uses the profile.

Each Platform Backend stores a complete Platform Job Identity. Generic Windows
and CUPS states provide only their qualified `spooler` or `transport` evidence.

Only a physically tested end-of-job signal can provide `device` evidence. A
missing platform job or uncertain cancellation never proves success.
