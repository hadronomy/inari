---
status: accepted
---

# Use Odoo-rendered receipt images

The first POS Device Adapter keeps Odoo `OrderReceipt` and
`OrderChangeReceipt` as the only receipt renderers. It submits their JPEG output
to Inari as `receipt_image`.

This boundary preserves Odoo layout and extension behavior. A second structured
receipt renderer creates duplicate business and presentation rules, so it is
outside the first release.

One Document Imaging Module checks, orients, scales, converts to grayscale,
and dithers the image for the Driver profile. Device-specific raster work stays
outside Odoo.

The first release accepts JPEG input up to 2 MiB and 32 megapixels. The same
input, Driver Profile, and imaging version produce identical raster bytes.

Each Print Job records the imaging version and Driver Profile digest. Odoo
cannot send raw printer bytes or arbitrary device commands.

Odoo renders at the Driver printable width. The Document Imaging Module can
downscale or center and cannot crop or upscale.

The Agent encrypts the original JPEG and derived raster in its private spool.
Internal retries reuse the raster, and terminal retention deletes both files.

Each Print Job uses a random AES-256-GCM data key. A master key from the
protected operating-system store protects that key. Production rejects
content-bearing work when the protected store is unavailable.

Master keys are versioned. Rotation rewraps only the small job keys. The Agent
retains an old master key until no job references it.

The Agent rotates master keys every 90 days and after suspected exposure. An
interrupted rewrap resumes idempotently.

A missing referenced key blocks new content work. Pre-I/O work fails with
public code `service_unavailable`.

Restricted diagnostics record `spool_key_unavailable`. Active work becomes
`outcome_unknown`.

The Agent raises a security alert and deletes unreadable ciphertext only after
it commits the terminal state. The product has no receipt-key escrow.

An Agent Administrator uses Device Center and operating-system authentication
to repair the store. Odoo roles cannot reset a local key.

The Agent creates a new key only after the store passes its self-test. The new
key permits new work and cannot recover old ciphertext.
