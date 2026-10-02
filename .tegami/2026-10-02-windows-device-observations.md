---
packages:
  "group:edge": patch
---

### Report current Windows printer facts

The Agent reads the Windows queue port, driver, media width, and readiness on
each discovery pass. A missing or blocked queue cannot claim readiness.
Firmware remains unavailable until the Device reports it. The Agent does not
use the Windows driver version as Device firmware.
