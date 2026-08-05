# Device Center typeface research

Date: 2026-08-03

## Decision

Use **Atkinson Hyperlegible Next** as the Device Center typeface. Bundle the
static Regular and SemiBold upright OTF files with the desktop application.
Use the platform text system for scripts that this family does not cover.

The Braille Institute designed this family to distinguish similar letters and
numbers. This research infers that it fits device names, identifiers, status
text, and compact controls. The files give Device Center one face on Windows,
macOS, and Linux.
The license permits redistribution, and the two files add about 79 KiB.

Do not use the variable font as the first desktop integration. GPUI 0.2.2 can
load font bytes, but its public text styling exposes family, weight, and style.
It does not expose arbitrary variation axes or an optical-size setting.

Source Sans 3 remains the first alternative if future localization work finds
a width problem.

## Requirements

Device Center is an operational desktop UI. It must remain clear in light and
dark themes, at small text sizes, and at the 780 × 560 px minimum window size.
It must also give equal treatment to keyboard users and mouse users.

The selected typeface needs all of these properties:

- Reliable self-hosting on Windows, macOS, and Linux.
- Clear Latin text and digits in dense device and activity views.
- A small, known binary cost.
- Permissive redistribution terms.
- Static files that work without variation-axis control.

## Implemented state

Device Center embeds and registers the static Atkinson Regular and SemiBold OTF
files before it creates the window. The root theme selects Atkinson once.
[Device Center assets](../crates/inari-device-center/src/assets.rs) show this
boundary.

The revised type scale stops at 0.75 rem, or 12 px with the 16 px root size.
[Microsoft sets 12 px Regular and 14 px SemiBold as its minimum readable
Windows UI values](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography)
for some languages.

GPUI 0.2.2 accepts in-memory font bytes through
[`TextSystem::add_fonts`](https://docs.rs/gpui/0.2.2/gpui/struct.TextSystem.html#method.add_fonts).
Its Windows backend adds those bytes to a DirectWrite font collection. The
public [`TextStyle`](https://docs.rs/gpui/0.2.2/gpui/struct.TextStyle.html)
has family, weight, and style fields. This version has no public variable-axis
or optical-size control. Static OTF files give predictable Regular and
SemiBold selection.

## Comparison

| Typeface | Legibility and coverage | Distribution and cost | GPUI fit | Result |
| --- | --- | --- | --- | --- |
| **Atkinson Hyperlegible Next** | The Braille Institute designed it for letterform distinction. It has seven weights, a variable version, 370 glyphs per style, and support for more than 150 languages. | SIL OFL 1.1. Static Regular is 38,232 bytes. SemiBold is 42,180 bytes. The upright pair is about 79 KiB. | Static files fit GPUI. | **Select.** |
| **Source Sans 3** | Adobe designed Source Sans for UI environments. It supports Latin, Greek, and Cyrillic text. | SIL OFL 1.1. Static Regular is 334,924 bytes. SemiBold is 339,636 bytes. The upright pair is about 659 KiB. | Static files avoid the current variation-axis gap. | Keep as the fallback for a tighter UI. |
| **Instrument Sans** | The current brand face supports 389 languages. Its official project provides width, weight, and italic axes. | SIL OFL 1.1. Static Regular is 59,996 bytes. SemiBold is 61,564 bytes. The upright pair is about 119 KiB. | Use static Regular and SemiBold. | Keep for brand surfaces, not dense operational text. |
| **Segoe UI Variable** | Windows 11 uses this system UI face. Microsoft states that it supports a weight axis and automatic optical sizing for small text. | Installed with Windows 11. Its `SegUIVar.ttf` is listed as 2.00 MB. Microsoft does not license it for non-Windows use. | Excellent for a Windows-only build. It cannot give Device Center one face on every target. | Do not select as the only family. |

No primary source shows a special dark-theme advantage for one candidate.
Use Regular and SemiBold in both themes. Review the rendered text on each
desktop target before release.

## Evidence behind the comparison

### Atkinson Hyperlegible Next

The [Braille Institute font page](https://www.brailleinstitute.org/freefont/)
states that Atkinson Hyperlegible Next is an everyday reading face. It has
seven upright and italic weights, a variable version, and support for more
than 150 languages. The Institute also states that it is free for commercial
applications.

The [official source repository](https://github.com/googlefonts/atkinson-hyperlegible-next)
ships the static and variable font files under the
[SIL Open Font License 1.1](https://github.com/googlefonts/atkinson-hyperlegible-next/blob/main/OFL.txt).
The upstream [Regular OTF](https://raw.githubusercontent.com/googlefonts/atkinson-hyperlegible-next/main/fonts/otf/AtkinsonHyperlegibleNext-Regular.otf)
and [SemiBold OTF](https://raw.githubusercontent.com/googlefonts/atkinson-hyperlegible-next/main/fonts/otf/AtkinsonHyperlegibleNext-SemiBold.otf)
have the sizes listed above.

### Source Sans 3

The [Adobe source repository](https://github.com/adobe-fonts/source-sans)
states that Source Sans was designed for UI environments. It provides TTF,
OTF, and variable formats under SIL OFL 1.1. The
[official specimen](https://adobe-fonts.github.io/source-sans/) shows Latin,
Greek, and Cyrillic text. Its
[Regular OTF](https://raw.githubusercontent.com/adobe-fonts/source-sans/release/OTF/SourceSans3-Regular.otf)
and [SemiBold OTF](https://raw.githubusercontent.com/adobe-fonts/source-sans/release/OTF/SourceSans3-Semibold.otf)
have the sizes listed above.

### Instrument Sans

The [Instrument Sans source repository](https://github.com/Instrument/instrument-sans)
defines it as a variable family. It lists width, weight, and italic axes,
389-language support, and SIL OFL 1.1 licensing. The existing asset in this
repository is a Latin WOFF2 subset, not a desktop TTF.

The [Regular OTF](https://raw.githubusercontent.com/Instrument/instrument-sans/master/fonts/otf/InstrumentSans-Regular.otf)
and [SemiBold OTF](https://raw.githubusercontent.com/Instrument/instrument-sans/master/fonts/otf/InstrumentSans-SemiBold.otf)
have the sizes listed above. These upstream files give desktop targets the same
font data without relying on the web subset.

### Segoe UI Variable

Microsoft recommends [Segoe UI Variable for Windows apps](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography).
Microsoft states that it uses weight and optical-size axes to preserve
legibility at small sizes. The [Windows 11 font list](https://learn.microsoft.com/en-us/typography/fonts/windows_11_font_list)
lists `SegUIVar.ttf` at 2.00 MB. The Microsoft
[font FAQ](https://learn.microsoft.com/en-my/typography/fonts/font-faq) states
that Segoe UI Variable is not available for licensing or use outside Microsoft
products or non-Windows platforms.

## Integration

The brand build script fetches the selected static OTF files from a pinned HTTPS
source. It verifies each SHA-256 digest. The application registers both files
with `TextSystem::add_fonts` before it creates text. The root theme sets the
family once.

Use Regular for body text and SemiBold for headings, selected controls, and
focus-related labels. Keep body text at 12 px or more.

## Evidence gaps

- Release checks must still cover 100%, 125%, and 150% platform scaling on each
  supported operating system.
- The current GPUI version accepts font bytes. This research did not run a
  Windows executable with a WOFF2 input. Use OTF for the selected files.
- Language requirements for Device Center are not yet defined. Bundle a script
  fallback only after localization scope or product telemetry identifies it.
- This note does not replace accessibility checks for contrast, focus, or text
  scaling.

## Ranked recommendation

1. **Atkinson Hyperlegible Next:** best character distinction and smallest
   static bundle. Sources: [Braille Institute](https://www.brailleinstitute.org/freefont/)
   and [official repository](https://github.com/googlefonts/atkinson-hyperlegible-next).
2. **Source Sans 3:** provisional fallback for a width failure in product review.
   Source: [Adobe repository](https://github.com/adobe-fonts/source-sans).
3. **Instrument Sans:** strongest brand continuity, but it has no equivalent
   primary evidence for small operational text. Source:
   [Instrument repository](https://github.com/Instrument/instrument-sans).
4. **Segoe UI Variable:** strongest Windows-native option, but Microsoft does
   not permit cross-platform use. Sources: [Windows typography](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography)
   and [Microsoft font FAQ](https://learn.microsoft.com/en-my/typography/fonts/font-faq).
