# Document preflight

Document preflight rejects invalid Report PDFs and Label Documents before the
Agent creates a Print Job. Device authorization still checks the signed
Binding Revision, Device Test, capability observation, and options digest.

## Report PDF

The Linux worker requires `libseccomp`. It reads the source PDF from a pipe
and opens it in memory through pikepdf and qpdf. It creates no plaintext file.
After library initialization, the worker denies filesystem opens, network
access, child processes, and executable launches.

The worker limits address space to 384 MiB and CPU time to 20 seconds. The
supervisor limits wall time to 30 seconds and result data to 64 KiB. An
unavailable isolation mechanism returns `service_unavailable`.

The policy checks syntax without repair. Any qpdf warning rejects the document.
It rejects encryption, actions, annotations, attachments, forms, and rich media.
An empty annotation array contains no annotations and is permitted.

The limits are 10 MiB of input, 50 pages, 50,000 indirect objects, 64 levels of
object nesting, 64 MiB per decoded stream, and 256 MiB of decoded streams.
Each page must fit within 17 by 17 inches and 24 million pixels. The complete
document must fit within 200 million pixels. Geometry and pixel limits apply
independently, after CropBox, UserUnit, and rotation.

The current font policy checks embedded TrueType and OpenType programs with
fontTools. It checks each selected glyph, including fonts in Form XObjects
and tiling patterns. Composite fonts require Identity-H or Identity-V and
CIDFontType2. Simple fonts support StandardEncoding, WinAnsiEncoding,
MacRomanEncoding, and explicit Differences. Unsupported font programs,
encodings, Type 3 fonts, and inline images are rejected. System-font fallback
is never permitted.

The signed options contain exactly one field:

```json
{"dpi": 300}
```

The supported resolutions are 150, 203, and 300 DPI. These options select one
physical copy. A separate Print Intent requests each additional copy.

The current supervisor starts the confined worker on Linux. Other platforms
return `service_unavailable` until their worker isolation is installed.
Preflight success alone does not certify a Platform Backend or physical Device.

## Label Document

The Agent and Odoo share the `inari-print-contracts` parser. A Label Document
contains exactly one complete ZPL envelope. A rendered Odoo report can contain
at most 500 envelopes and 2 MiB before the addon splits it.

Each envelope requires `^CI28`. Each field requires an explicit origin, bounded
font or barcode geometry, and `^FH_` immediately before `^FD`. The parser
rejects commands outside Contract Major 1, invalid UTF-8, controls, invalid
escapes, extra copies, and overflow. It never repairs or truncates business
data.

The signed options contain exactly one `layout` object. For example:

```json
{
  "layout": {
    "profile_id": "odoo_stock_zpl_203_4x6_v1",
    "width_dots": 812,
    "height_dots": 1218,
    "dpi": 203
  }
}
```

The profile identity must match the Report Binding. The binding must use
`zpl_v1` and identify its Hardware Certification Matrix digest. The authority
checks the complete options digest before acceptance. The example does not
activate or certify a Device.

Optional layout limits can reduce `max_fields`, `max_field_bytes`, and
`max_barcode_bytes` from their maxima of 128, 1024, and 256. Width and height
cannot exceed 812 and 1218 dots. Every layout uses 203 DPI.

## Implementation references

- [pikepdf syntax checks and in-memory input](https://pikepdf.readthedocs.io/en/latest/api/main.html)
- [fontTools program and glyph inspection](https://fonttools.readthedocs.io/en/latest/ttLib/ttFont.html)
- [Zebra command reference](https://docs.zebra.com/us/en/printers/software/zpl-pg.html)
- [GS1 Data Matrix capacity tables](https://gs1.ee/doc/download/GS1_DataMatrix_Guideline.pdf)
