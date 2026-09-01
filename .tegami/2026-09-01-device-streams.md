---
packages:
  "group:edge": patch
---

### Add secure POS Device Streams

The Agent now provides signed, DPoP-protected Scale Reading and Barcode Event
streams. Agent-owned fencing leases prevent stale browser contexts from using
device input.
