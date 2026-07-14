# Inari brand guide

Inari is the quiet guardian at the threshold between physical devices and
software. The identity should feel composed, precise, humane, and alert—not
nostalgic, mystical, or ornamental.

The symbol joins a fox brow with the structure of a torii gate. The opening in
the center is the idea that matters: a trusted passage from physical hardware
to software. Foxes are described as Inari’s messengers, not as the deity. This
follows the terminology used by [Fushimi Inari Taisha](https://inari.jp/vr_en/)
and its account of the [Senbon Torii](https://inari.jp/en/map/spot_07/).

## Writing

Use **Inari** in prose and the lowercase `inari` wordmark in logo lockups. The
product descriptor is **Private device operations.**

Write with calm operational clarity. Tell people what is happening and what
they can do next. Avoid grand claims, cute fox language, unexplained protocol
jargon, and badges that merely restate an obvious implementation detail.

## Assets

The editable source is the [Inari Brand System in Paper](https://app.paper.design/file/01KJNSRTZW7338XY38FF83JG42/3-0).
Approved production assets live in
[`packages/brand/inari_brand/assets`](../packages/brand/inari_brand/assets).
Every product surface consumes that directory or a derived asset; do not copy
path geometry into components or redraw the mark for a platform.

| Asset | Use |
| --- | --- |
| `inari-mark.svg` | Sumi mark on light surfaces |
| `inari-mark-reversed.svg` | White mark on dark surfaces |
| `inari-mark-torii.svg` | Deliberate vermilion brand moment |
| `inari-mark-micro.svg` | 16–20 px symbol use |
| `inari-lockup*.svg` | Horizontal lockups |
| `inari-app-icon.svg` | Application and installer tile |
| `inari-tray-icon.svg` | Tray base before one status overlay |
| `favicon-development.svg` | Development environment cue |
| `favicon-preview.svg` | Preview environment cue |
| `favicon.svg` | Production browser icon |
| `readme-header.webp` | Repository header |

After changing an approved vector, run:

```sh
python packages/brand/tools/build_assets.py
```

The script creates the exact-size raster, ICO, and ICNS variants and verifies
the bundled fonts.

## Color and type

| Token | Value | Role |
| --- | --- | --- |
| Sumi | `#0F1110` | Ink and dark ground |
| Porcelain | `#FFFFFF` | Primary light surface |
| Stone mist | `#F2F2EF` | Secondary surface |
| Torii vermilion | `#E23D28` | Brand accent |
| Lacquer shadow | `#A62B20` | Accessible brand action on light surfaces |
| Fox amber | `#E8A63B` | Rare highlight |
| Signal blue | `#2563EB` | Development environment cue |
| Relay green | `#18794E` | Preview environment cue |

Instrument Sans is the product and communication face. IBM Plex Mono is for
identifiers, commands, and protocol values. Introduce Japanese type only with
real localization and native-language review.

Vermilion is a brand color, not an error state. Success, warning, failure, and
information keep their own semantic tokens, visible labels, and accessible
contrast.

## Using the mark

Leave clear space equal to one upright around the symbol. Use the full mark at
24 px and above, the optical micro mark at 16–20 px, and the horizontal lockup
at 96 px or wider.

Do not add eyes, tails, masks, kanji, rising suns, faux calligraphy, gradients,
shadows, outlines, rotations, or independently colored pieces. Supporting
graphics may use a gate frame, one directional path, or one fox-ear cut. Use
them sparingly: show the passage, not a shrine scene.

Review new applications at their actual sizes, on light and dark surfaces, in
grayscale, and with platform scaling enabled. Check contrast, clear space,
accessible names, keyboard focus, and whether a canonical asset already exists.
