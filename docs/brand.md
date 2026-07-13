# Inari identity

Inari is the quiet guardian at the threshold between physical devices and software. The identity is deliberately operational rather than mystical: it should feel vigilant, composed, intelligent, humane, and just mischievous enough to be memorable.

The symbol combines a fox brow with the structure of a torii gate. Its central opening is the important idea—the trusted passage through which a physical device becomes a managed software capability. Foxes are treated as messengers of Inari, not as the deity itself. This distinction follows the history and terminology published by [Fushimi Inari Taisha](https://inari.jp/vr_en/) and its guidance on the [Senbon Torii](https://inari.jp/en/map/spot_07/).

Do not add decorative kanji, kitsune masks, rising suns, faux calligraphy, shrine illustrations, eyes, or tails. The mark carries its personality through geometry, not costume.

## Voice

Write “Inari” in prose and use the lowercase `inari` wordmark in identity lockups.

Inari speaks in concise operational truth. Prefer a clear state and a useful next action over flourish. The voice is confident without being grandiose, humane without becoming cute, and technically precise without exposing implementation language to operators.

The preferred product descriptor is **Private device operations.** The positioning statement is **The trusted threshold between physical devices and software.**

## Source of truth

The editable design source is the [Inari Brand System in Paper](https://app.paper.design/file/01KJNSRTZW7338XY38FF83JG42/3-0). Approved production assets live in [`packages/brand/inari_brand/assets`](../packages/brand/inari_brand/assets). That directory is authoritative for every product surface.

The Python `inari-brand` workspace package provides typed access to packaged vectors. cargo-leptos ships the same asset directory directly. Documentation, Helm metadata, installers, and release material link to or derive from those sources. Never paste the path geometry into another component or redraw the mark for a platform.

| Asset | Intended use |
| --- | --- |
| `inari-mark.svg` | Sumi mark on light surfaces |
| `inari-mark-reversed.svg` | White mark on dark surfaces |
| `inari-mark-torii.svg` | Expressive brand moment, never body text |
| `inari-mark-micro.svg` | Symbol-only use at 16–20 px |
| `inari-lockup*.svg` | Horizontal identity lockups |
| `inari-app-icon.svg` | Canonical application tile |
| `inari-tray-icon.svg` | Theme-independent tray base before one status overlay |
| `favicon-development.svg` | Blue browser-tab mark for local development |
| `favicon-preview.svg` | Green browser-tab mark for preview and staging controllers |
| `favicon.svg` and `inari-icon-*.png` | Canonical vermilion production and installed-web surfaces |
| `inari.ico` and `inari.icns` | Windows and macOS packaging |
| `readme-header.webp` and `social-preview.svg` | Repository and release presentation |

Run `python packages/brand/tools/build_assets.py` after an approved vector change. The script regenerates exact-size PNG, ICO, and ICNS outputs and verifies the digests of the self-hosted font files. Do not resize an existing raster by hand.

## Mark usage

Preserve clear space equal to the width of one upright around a standalone mark. Use the full mark at 24 px or larger and the optical micro variant at 16–20 px. The horizontal lockup should not be rendered below 96 px wide.

Use Sumi on Porcelain or Stone, Porcelain on Sumi, and the Torii variant only for a deliberate passage or brand moment. Do not add gradients, shadows, outlines, rotations, distortions, extra ear cuts, or independently colored pieces to the canonical logo.

Supporting graphics may use gate frames, one directional path, or a single fox-ear cut. They should not combine all three indiscriminately. Show the passage, not a literal shrine scene.

## Foundations

| Token | Value | Role |
| --- | --- | --- |
| Sumi | `#0F1110` | Primary ink and dark ground |
| Porcelain | `#FFFFFF` | Primary light surface |
| Stone mist | `#F2F2EF` | Secondary canvas |
| Torii vermilion | `#E23D28` | Brand accent and passage graphics |
| Lacquer shadow | `#A62B20` | Accessible brand text and actions on light surfaces |
| Fox amber | `#E8A63B` | Rare highlight and origin point |
| Signal blue | `#2563EB` | Development-environment tab cue only |
| Relay green | `#18794E` | Preview-environment tab cue only |

Instrument Sans 400–700 is the product and communication face. IBM Plex Mono 400–500 is reserved for identifiers, protocol values, commands, and other machine-oriented content. Noto Sans JP should be introduced only alongside genuine Japanese localization and native-language review.

Semantic success, warning, failure, and information colors remain independently named. Vermilion does not mean “error,” and no status may rely on color alone. Light and dark surfaces must preserve the same hierarchy and visible focus treatment.

Browser tabs use environment color as a quiet safety cue: signal blue for development, relay green for preview, and canonical Torii vermilion for production. The mark, silhouette, and application identity do not change. Configure `server.environment` rather than replacing files manually; each environment uses a distinct URL so browser favicon caches cannot blur the distinction.

## Review checklist

Before publishing a new use, verify it at 16, 20, 24, 32, 48, and 256 px as relevant; on light and dark backgrounds; in grayscale; and at native platform scale. Confirm clear space, contrast, keyboard focus, accessible naming, and the absence of duplicated or redrawn assets.

The visual system should feel like a lacquered instrument panel: crisp black-and-white infrastructure, one intentional vermilion passage, and personality carried by shape rather than decoration.
