---
status: accepted
---

# Isolate platform-specific PDF printing

The `report_pdf` Interface has separate Windows and Linux Platform Backends.
Windows uses a signed PDFium worker. Linux uses typed CUPS submission with an
explicit `application/pdf` format.

Every Report PDF passes a disposable `qpdf` worker first. The worker rejects
encrypted, active, attached, form, and rich-media content. Resource limits
bound bytes, pages, objects, nesting, decoded streams, pixels, memory, CPU
time, and wall time.

The qpdf policy runs before `Accepted`. Contract Major 1 permits embedded fonts
only. The qpdf worker has a 384 MiB memory limit. The PDFium worker has a 256
MiB limit, and the separate Windows Platform Backend has a 160 MiB limit.

Windows disables PDFium JavaScript and XFA. Linux requires an advertised and
physically tested CUPS PDF capability.

The Windows worker holds one BGRA page buffer and streams it to a separate GDI
Platform Backend. It does not access printers or persist a rasterized report.

The Windows Platform Backend uses `StartDoc` for Platform Job Identity. Linux submits
the checked source PDF through typed CUPS calls.

Linux pins the CUPS filter, backend, queue, media, resolution, and capability
digests. Any change returns `capability_changed`.

The same source can produce different spool bytes on each platform. Physical
tests require the same geometry and document content.

SumatraPDF, Ghostscript, and AGPL MuPDF are not production Agent dependencies.
