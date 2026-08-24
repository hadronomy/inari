# Device Center glass and frosted-surface research

Date: 2026-08-24

## Outcome

Device Center asks GPUI for a blurred macOS window. The request reaches the
native backend. The attached Comet reference narrows the target: it uses one
neutral tint over a blurred window, then a small number of low-alpha tonal
surfaces. Its look does not depend on macOS 26 Liquid Glass.

The original Device Center build missed that target for three reasons:

1. `gpui-component::Root` and `DeviceCenter` both paint a full-window tint.
   The content panel and cards add more large fills above them.
2. Those fills leave about 12.6% of the blurred backdrop in the dark shell and
   5.7% under its content panel. Raised cards are almost opaque.
3. Stock GPUI 0.2.2 only exposes whole-window blur. Comet pins a custom GPUI
   fork with a Metal `BackdropBlur` primitive for its composer and floating
   menus.

The first fix is now implemented. `gpui-component::Root` owns the base window
tint. The app root no longer paints a second veil. Secondary panes use a 40%
neutral tint, and raised surfaces use a 4% white or black wash with quiet
borders. The complete glass stack leaves 11.5% of the native blurred backdrop
visible. The previous stack left 2.8% under a dark raised card.

A custom renderer primitive is still needed for true within-window frost on
floating surfaces. This is a separate product choice. It is not required for
the quieter, modern surface hierarchy shown in the Comet reference.

This note combines source inspection with a glass-versus-opaque runtime
comparison. The repository pins `gpui = "=0.2.2"` and
`gpui-component = "=0.5.1"` in
[`Cargo.toml`](../Cargo.toml#L51-L52), and the lockfile records the exact GPUI
package at [`Cargo.lock`](../Cargo.lock#L3776-L3780). The installed SDK is
Xcode 26.2 (build 17C52), `MacOSX26.2.sdk`. Apple documentation and the Zed
source snapshot below were checked on 2026-08-24.

The registry source roots used for local source checks are on Yggdrasil:
`/Volumes/Yggdrasil/rust/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/gpui-0.2.2/src`
and
`/Volumes/Yggdrasil/rust/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/gpui-component-0.5.1/src`.
The links below point to the matching docs.rs source files and line ranges.

## What the Comet reference actually does

The attached reference is Comet. Its public repository had 1,106 stars and
124 forks when checked on 2026-08-24. The exact source is more useful than a
visual guess because it records the surface values and its GPUI changes.

Comet's dark palette uses neutral surfaces. Its main background is `#060606`,
and its shell is `#0d0d0d`. On macOS, the shell tint has alpha `0.80`:
[Comet theme](https://github.com/zeronsh/comet/blob/3f4b731c29198a309e61572a840d568397193a50/crates/ui/src/theme.rs#L740-L760),
[dark tokens](https://github.com/zeronsh/comet/blob/3f4b731c29198a309e61572a840d568397193a50/crates/ui/src/theme.rs#L946-L973).
The purple character in the screenshot comes mainly from the desktop behind
that neutral tint. It is not a purple app background.

Comet paints that tint once on its shell root. Its main workspace does not add
another full-area base fill. The right diff pane adds `bg.opacity(0.4)`, which
creates a controlled tonal step while it still shares the window atmosphere:
[shell root](https://github.com/zeronsh/comet/blob/3f4b731c29198a309e61572a840d568397193a50/crates/ui/src/shell.rs#L7320-L7415),
[right pane](https://github.com/zeronsh/comet/blob/3f4b731c29198a309e61572a840d568397193a50/crates/ui/src/shell.rs#L6058-L6082).

Its dark composer fill is a 3% white wash with an 8% white border. It omits a
drop shadow while frost is active. The custom `frosted` element first paints a
rounded backdrop blur, then paints the complete child subtree in one scene
layer. The composer uses blur radius 16; menus use 44:
[composer surface](https://github.com/zeronsh/comet/blob/3f4b731c29198a309e61572a840d568397193a50/crates/ui/src/composer.rs#L6127-L6140),
[frost element](https://github.com/zeronsh/comet/blob/3f4b731c29198a309e61572a840d568397193a50/crates/ui/src/frost.rs#L1-L98).

That local blur is not an upstream GPUI feature. Comet pins
`wingleeio/zed@e2ddcc6`. Its fork adds the scene primitive and Metal blur pass,
bounds the temporary GPU textures, fixes destination-alpha compositing for
transparent windows, and changes the macOS 26 material from `Selection` to
`UnderWindowBackground`:
[Comet GPUI pin](https://github.com/zeronsh/comet/blob/3f4b731c29198a309e61572a840d568397193a50/Cargo.toml#L42-L69),
[BackdropBlur commit](https://github.com/wingleeio/zed/commit/a6c1ad501f90c9437d2553bde691958f150364c5),
[alpha fix](https://github.com/wingleeio/zed/commit/f596cde4c56447a565a600fddef43dc505a6bfc7),
[macOS 26 material fix](https://github.com/wingleeio/zed/commit/8a8954c7234f2261d13b72568ff09e4a5136d39f).

The reference combines two techniques. Whole-window frost supplies the shared
atmosphere. Within-window blur is reserved for floating controls. The surface
hierarchy and single base tint account for most of the visible difference from
Device Center.

## What GPUI 0.2.2 actually does

### `Blurred` is a window-level backdrop

GPUI defines `WindowBackgroundAppearance` as the appearance used when the
window has no content or its content is transparent. `Opaque` tells the window
manager that the area behind the window need not be drawn. `Transparent` gives
plain alpha transparency. `Blurred` asks the platform to blur content behind
the window, and GPUI warns that this is not always supported. See the exact
enum in [`platform.rs`](https://docs.rs/crate/gpui/0.2.2/source/src/platform.rs#L1307-L1325).

`WindowOptions.window_background` carries that value into window creation, and
`Window::new` applies it before the first render. A later change uses the same
platform method through `Window::set_background_appearance`:
[`WindowOptions`](https://docs.rs/crate/gpui/0.2.2/source/src/platform.rs#L1087-L1122),
[`Window::new`](https://docs.rs/crate/gpui/0.2.2/source/src/window.rs#L929-L999),
and [`Window::set_background_appearance`](https://docs.rs/crate/gpui/0.2.2/source/src/window.rs#L1788-L1792).

This is not a CSS-style `backdrop-filter`. GPUI renders its element tree into
one Metal view. A panel in that tree cannot ask the platform to blur an earlier
GPUI panel or an earlier part of the same Metal frame.

### The macOS backend inserts one native blur view below GPUI

The exact 0.2.2 macOS backend registers `BlurredView` as an
`NSVisualEffectView` subclass. It sets the material to
`NSVisualEffectMaterialSelection` and the state to `NSVisualEffectStateActive`:
[`window.rs`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/window.rs#L2500-L2508).

For macOS 12 and later, `set_background_appearance` creates one view the size
of the native content view and inserts it below the GPUI native view. It
removes that view for every value other than `Blurred`. For older systems it
uses the legacy WindowServer blur-radius API:
[`set_background_appearance`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/window.rs#L1257-L1310).

The native view is created as a sibling of the Metal view, then the Metal view
is added to the content view. The renderer is therefore above the blur view:
[`MacWindow::open`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/window.rs#L680-L698)
and [`addSubview`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/window.rs#L761-L776).
Only pixels that the GPUI renderer leaves transparent reveal the blurred
desktop.

The backend also removes the effect layer's desktop tint and saturation filter
after AppKit updates it:
[`blurred_view_update_layer`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/window.rs#L2511-L2555).
That makes the result less like the default AppKit material. The backend's
hard-coded `Active` state also differs from AppKit's documented default of
following the containing window's active state. Compare the GPUI source above
with the [`NSVisualEffectView` header](https://developer.apple.com/documentation/appkit/nsvisualeffectview)
and the installed SDK header at
`/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.2.sdk/System/Library/Frameworks/AppKit.framework/Headers/NSVisualEffectView.h:80-105`.

### The renderer is transparent, but the toggle is not a renderer switch

The macOS Metal layer in GPUI 0.2.2 is created with `opaque = false`.
`new_renderer` ignores its `_transparent` argument, and
`update_transparency` is a no-op. The draw pass clears with alpha zero when
the layer is non-opaque:
[`metal_renderer.rs`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/metal_renderer.rs#L44-L52),
[`MetalLayer::set_opaque`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/metal_renderer.rs#L130-L146),
[`update_transparency`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/metal_renderer.rs#L345-L347),
and [`draw`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/metal_renderer.rs#L426-L438).

The window's `Opaque` flag and background color still change in the native
window code. That is why setting `Blurred` is necessary, but it is not enough:
the GPUI tree must also leave useful pixels transparent.

### A transparent titlebar is a layout option

`TitlebarOptions.appears_transparent` is documented as hiding the default
system titlebar so the app can draw a custom titlebar. On macOS 0.2.2 it adds
`NSFullSizeContentViewWindowMask` and sets the titlebar transparent and hidden;
it does not select a material or create a blur view:
[`TitlebarOptions`](https://docs.rs/crate/gpui/0.2.2/source/src/platform.rs#L1246-L1257)
and [`MacWindow::open`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/window.rs#L599-L618).

## The local composition

### The window request is present

The app opens the window with `appears_transparent: true` and passes
`material::resolve().window_background()`:
[`main.rs`](../crates/inari-device-center/src/main.rs#L49-L78).

`Material::Glass` maps to `WindowBackgroundAppearance::Blurred`, and
`Theme::sync` reapplies the value on startup, appearance changes, and the
manual translucency action:
[`material.rs`](../crates/inari-device-center/src/ui/material.rs#L28-L44),
[`theme.rs`](../crates/inari-device-center/src/ui/theme.rs#L336-L355),
and [`app.rs`](../crates/inari-device-center/src/app.rs#L278-L287).

The window can still be deliberately opaque. `INARI_MATERIAL=opaque` and the
Support action set the process preference, so a flat screenshot must first be
checked against that environment variable and the current material state:
[`material.rs`](../crates/inari-device-center/src/ui/material.rs#L57-L95)
and [`support.rs`](../crates/inari-device-center/src/features/support.rs#L140-L156).

### The original alpha stack hid the backdrop

`gpui-component`'s `Root` paints `ComponentTheme::background` across its full
content area. Its window border is transparent, but the root background is not
automatically transparent:
[`Root::render`](https://docs.rs/crate/gpui-component/0.5.1/source/src/root.rs#L396-L413)
and [`WindowBorder::render`](https://docs.rs/crate/gpui-component/0.5.1/source/src/window_border.rs#L66-L175).

Before this change, `ComponentTheme::background` was `theme.surface`, and the
`DeviceCenter` root then painted `theme.chrome` across the full window. The
glass alpha values were:

- Dark: root surface `0.55`, then full-window chrome `0.72`.
- Light: root surface `0.70`, then full-window chrome `0.80`.
- The inset content panel adds surface alpha `0.55` or `0.70`.
- Cards add raised-surface alpha `0.78` or `0.86`.

For one pixel covered by both full-area layers, standard source-over alpha
left this much of the blurred backdrop visible:

```text
dark:  (1 - 0.55) * (1 - 0.72) = 0.126  -> 12.6%
light: (1 - 0.70) * (1 - 0.80) = 0.060  ->  6.0%
```

Where the content panel covers that pixel, the remaining backdrop falls to
about 5.7% in dark mode and 1.8% in light mode. Raised cards reduce it again.
These are composition estimates, not a screenshot measurement. They explain
why the native blur can be present while the window looks nearly solid.

The implementation now gives the full-window tint one owner. GPUI Component's
root paints `theme.chrome`; `DeviceCenter` has no background fill. Both glass
themes use a root alpha of `0.80`, a panel alpha of `0.40`, and a card wash
alpha of `0.04`. The remaining backdrop under all three layers is:

```text
(1 - 0.80) * (1 - 0.40) * (1 - 0.04) = 0.1152 -> 11.5%
```

The opaque theme keeps all three base surfaces fully opaque. Theme tests hold
the glass stack to a minimum 10% backdrop budget and keep the panel and raised
surface alphas within their intended limits. See
[`theme.rs`](../crates/inari-device-center/src/ui/theme.rs) and
[`app.rs`](../crates/inari-device-center/src/app.rs).

The panel and card helpers confirm that these are large painted surfaces, not
small glass controls:
[`surface.rs`](../crates/inari-device-center/src/ui/surface.rs#L13-L48).
The titlebar and navigation rail are transparent in their own views. They sit
on the single full-window `chrome` plane:
[`chrome.rs`](../crates/inari-device-center/src/ui/chrome.rs#L1-L7)
and [`app.rs`](../crates/inari-device-center/src/app.rs#L470-L497).

### The app's effect is not macOS Liquid Glass

Apple describes Liquid Glass as a dynamic material for controls and navigation
that lets underlying content remain visible. Apple also says standard blur and
vibrancy materials serve the content layer, that material choice must be
semantic rather than based on apparent color, and that thicker materials give
better fine-detail contrast while thinner materials preserve more context:
[Apple HIG Materials](https://developer.apple.com/design/human-interface-guidelines/materials).

GPUI 0.2.2 has no `NSGlassEffectView` symbol or bridge. Its native effect is the
older `NSVisualEffectView` path above, with the selection material, forced active
state, and stripped tint/saturation. This is a real frosted desktop backdrop,
but it is not the macOS 26 Liquid Glass material.

## `NSVisualEffectView` versus `NSGlassEffectView`

### `NSVisualEffectView`

`NSVisualEffectView` adds translucency and vibrancy. Its material and blending
mode are semantic. AppKit defines `BehindWindow` for the desktop or other
windows and `WithinWindow` for content inside the same window. The SDK header
states that the default blending mode is `BehindWindow`, and that materials can
fall back when a material does not support both modes:
[NSVisualEffectView documentation](https://developer.apple.com/documentation/appkit/nsvisualeffectview)
and `NSVisualEffectView.h:18-105` in the installed 26.2 SDK.

This is the correct primitive for the GPUI 0.2.2 whole-window fallback. It does
not turn a single Metal view into a hierarchy of native, per-element materials.
The current Zed macOS backend uses the same architecture: it creates one
`NSVisualEffectView` subclass, inserts it below the GPUI view for `Blurred`, and
uses the selection material. The current `main` snapshot was commit
`6e2fae619c45ffc90e5bcf5cfbfcef8bb693fbe1` on 2026-08-24:
[Zed macOS window backend](https://github.com/zed-industries/zed/blob/6e2fae619c45ffc90e5bcf5cfbfcef8bb693fbe1/crates/gpui_macos/src/window.rs#L1656-L1702)
and [Zed blur-view setup](https://github.com/zed-industries/zed/blob/6e2fae619c45ffc90e5bcf5cfbfcef8bb693fbe1/crates/gpui_macos/src/window.rs#L3428-L3446).

### `NSGlassEffectView`

`NSGlassEffectView` is the macOS 26 Liquid Glass API. The installed SDK marks
the class and its style enum as available from macOS 26.0 and defines
`contentView`, `cornerRadius`, `tintColor`, and regular/clear styles at
`NSGlassEffectView.h:14-38`. Apple's class reference describes it as a view
that embeds its content in a dynamic glass effect:
[NSGlassEffectView](https://developer.apple.com/documentation/appkit/nsglasseffectview)
and [its styles](https://developer.apple.com/documentation/appkit/nsglasseffectview/style-swift.enum).

`NSGlassEffectContainerView` groups descendant glass views that are near each
other. Apple states that it elevates the descendants, merges eligible glass
views, and processes similar views as a batch for performance. The SDK header
defines that behavior at `NSGlassEffectView.h:40-56`; see the
[container reference](https://developer.apple.com/documentation/appkit/nsglasseffectcontainerview).

This API is not a drop-in setting for GPUI 0.2.2. A bridge must own AppKit
availability checks, main-thread view lifetime, containment, resizing,
coordinate conversion, z-order, input routing, and the fallback. Apple's SDK
header also warns that `NSGlassEffectView` only guarantees placement of its
`contentView`; arbitrary subviews do not have a guaranteed z-order relative to
the glass. A bridge that places the GPUI Metal view beside or inside native glass
must choose that containment deliberately.

## Text rendering trade-off

The exact GPUI 0.2.2 macOS text system requests grayscale antialiasing from
font-kit, then enables Core Graphics subpixel *positioning* and disables font
subpixel quantization. GPUI also creates fractional-origin glyph variants:
[`text_system.rs`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/text_system.rs#L330-L360),
[`Core Graphics glyph drawing`](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/text_system.rs#L399-L420),
and [`Window::paint_glyph`](https://docs.rs/crate/gpui/0.2.2/source/src/window.rs#L2948-L2989).

This version has no `should_use_subpixel_rendering` or platform capability gate.
Do not read its positional variants as RGB subpixel text rendering: the raster
mode is grayscale. Still, transparent surfaces need review for edge halos and
contrast because the glyphs are composited over changing backdrops.

Current Zed has made the policy explicit. Its GPUI window code disables
subpixel rendering whenever the window background is not opaque, and its macOS
platform implementation reports that subpixel rendering is unsupported:
[Zed GPUI text policy](https://github.com/zed-industries/zed/blob/6e2fae619c45ffc90e5bcf5cfbfcef8bb693fbe1/crates/gpui/src/window.rs#L4339-L4356),
[Zed platform capability](https://github.com/zed-industries/zed/blob/6e2fae619c45ffc90e5bcf5cfbfcef8bb693fbe1/crates/gpui/src/platform.rs#L861-L866),
and [Zed macOS implementation](https://github.com/zed-industries/zed/blob/6e2fae619c45ffc90e5bcf5cfbfcef8bb693fbe1/crates/gpui_macos/src/window.rs#L1696-L1702).
That newer policy is a useful validation target if the app later adds a native
glass bridge. It is not present in the pinned 0.2.2 source.

## Accessibility and fallback behavior

Apple's AppKit contract is explicit: when
`NSWorkspace.accessibilityDisplayShouldReduceTransparency` is true, an app
must avoid semitransparent backgrounds, for example by using opaque windows.
AppKit also posts `accessibilityDisplayOptionsDidChangeNotification` when the
setting changes:
[reduce-transparency property](https://developer.apple.com/documentation/appkit/nsworkspace/accessibilitydisplayshouldreducetransparency)
and [display-options notification](https://developer.apple.com/documentation/appkit/nsworkspace/accessibilitydisplayoptionsdidchangenotification).

The local material policy documents that GPUI 0.2.2 cannot read this setting
and offers only `INARI_MATERIAL=opaque` plus the in-app session toggle:
[`material.rs`](../crates/inari-device-center/src/ui/material.rs#L57-L74).
That is an incomplete native accessibility integration. A macOS bridge needs
to read the workspace property at startup, observe the shared workspace
notification, switch to `Opaque`, and repaint the theme. It also needs to test
Increase Contrast, Differentiate Without Color, light/dark appearance, and
active/inactive windows. The app already has explicit status shapes and a
reduced-motion preference, but those do not replace the transparency fallback:
[`status.rs`](../crates/inari-device-center/src/ui/status.rs#L1-L12),
[`app.rs`](../crates/inari-device-center/src/app.rs#L289-L295).

Apple's HIG also says that material appearance changes with system settings,
that vibrant foreground colors help legibility, and that custom Liquid Glass
effects should be limited to important functional elements. The app's manually
alpha-tinted HSL surfaces therefore need contrast review over real desktop
wallpapers, not only against the test shell:
[HIG Materials](https://developer.apple.com/design/human-interface-guidelines/materials),
[HIG Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility),
and [`theme.rs` contrast tests](../crates/inari-device-center/src/ui/theme.rs#L540-L579).

## Local mismatch summary

| Area | Local code | Evidence-backed mismatch |
| --- | --- | --- |
| Window request | `Blurred` plus transparent titlebar | Correct for GPUI whole-window blur, but not a Liquid Glass request. [`main.rs`](../crates/inari-device-center/src/main.rs#L49-L64) |
| Root transparency | GPUI Component's root owns the only full-window tint | The duplicate app-root veil is removed. [`root.rs`](https://docs.rs/crate/gpui-component/0.5.1/source/src/root.rs#L396-L413), [`app.rs`](../crates/inari-device-center/src/app.rs) |
| Surface scale | Panels use a 40% neutral tint; cards use a 4% wash | The complete stack leaves 11.5% of the native backdrop, guarded by a regression test. [`theme.rs`](../crates/inari-device-center/src/ui/theme.rs), [`surface.rs`](../crates/inari-device-center/src/ui/surface.rs#L18-L48) |
| Native primitive | GPUI `NSVisualEffectView(Selection)` below Metal | This is the GPUI/Zed fallback, not `NSGlassEffectView`. [GPUI backend](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/window.rs#L2500-L2508), [Apple glass API](https://developer.apple.com/documentation/appkit/nsglasseffectview) |
| Native appearance | Forced active state; desktop tint/saturation removed | It cannot match AppKit's normal active-state and desktop-tint behavior. [GPUI layer cleanup](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/window.rs#L2511-L2555), [AppKit header](https://developer.apple.com/documentation/appkit/nsvisualeffectview) |
| Accessibility | Manual opaque toggle and environment override | It does not follow Reduce Transparency or its change notification. [Local policy](../crates/inari-device-center/src/ui/material.rs#L63-L74), [AppKit property](https://developer.apple.com/documentation/appkit/nsworkspace/accessibilitydisplayshouldreducetransparency) |
| Text | GPUI 0.2.2 grayscale raster plus positional variants | No current-Zed non-opaque text gate exists in the pinned version. Validate halos and contrast, then consider the current Zed policy. [GPUI text](https://docs.rs/crate/gpui/0.2.2/source/src/platform/mac/text_system.rs#L330-L420), [Zed policy](https://github.com/zed-industries/zed/blob/6e2fae619c45ffc90e5bcf5cfbfcef8bb693fbe1/crates/gpui/src/window.rs#L4339-L4356) |

## Evidence gaps

- Glass and opaque variants were launched on macOS 15.7.3 during diagnosis.
  The first comparison confirmed that the native blur view was present and
  that broad GPUI fills hid it. After the surface change, the glass launch
  showed clearer shared atmosphere through the shell and a calmer panel and
  card hierarchy. A macOS keychain dialog blocked a clean final opaque capture.
  No keychain action was taken. These checks do not provide a controlled pixel
  measurement.
- The runtime machine cannot test `NSGlassEffectView` because that API starts
  at macOS 26. A native bridge still needs a macOS 26 test if Liquid Glass later
  becomes a separate product target.
- The alpha calculation assumes source-over compositing over the same pixels.
  Rounded corners, clipping, shadows, and AppKit's dynamic material can change
  the measured result. Use screenshots over several wallpapers for the final
  visual decision.

## Remaining verification

1. Capture controlled glass and opaque screenshots over several wallpapers
   after the keychain dialog is resolved by the user.
2. Test active and inactive windows, light and dark appearance, Reduce
   Transparency, Increase Contrast, and a pre-macOS-26 system. The expected
   fallback for Reduce Transparency is an opaque theme and opaque window.
3. Decide separately whether floating controls need real within-window frost.
   Stock GPUI 0.2.2 cannot provide it. Review and upstream the minimum renderer
   change instead of taking Comet's whole fork without an ownership plan.
4. Prototype `NSGlassEffectView` only if macOS 26 Liquid Glass becomes an
   explicit target. It is not required to match the attached Comet reference.
