---
packages:
  "group:edge": patch
---

### Add secure browser Device Streams

The Odoo POS browser can now hold and renew Agent Device Stream leases. It
verifies signed Scale Readings and Barcode Events, coordinates open tabs, and
reconciles device state before it accepts live input.
