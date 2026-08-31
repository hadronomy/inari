//! The Inari design tokens.
//!
//! One source of truth. [`Theme`] holds every semantic role the Device Center
//! paints with, and [`Theme::install`] derives the GPUI Component palette from
//! it so the two never drift. Views read roles (`theme.text_secondary`), never
//! raw colors, so a palette change lands everywhere at once.
//!
//! Tones come from a fixed neutral ramp rather than hand-picked hex values.
//! The ramp carries a faint green cast that ties the surfaces to the vermilion
//! brand mark: a pure grey next to vermilion reads cold and slightly blue.

use gpui::{App, Global, Hsla, SharedString, Window, WindowAppearance, hsla, px};
use gpui_component::Theme as ComponentTheme;

use super::material::Material;

/// Light or dark. Resolved from the OS; the Device Center does not offer a
/// separate appearance preference because it is a companion to the desktop.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Appearance {
    Light,
    Dark,
}

impl Appearance {
    pub fn from_window(appearance: WindowAppearance) -> Self {
        match appearance {
            WindowAppearance::Dark | WindowAppearance::VibrantDark => Self::Dark,
            WindowAppearance::Light | WindowAppearance::VibrantLight => Self::Light,
        }
    }

    pub fn is_dark(self) -> bool {
        self == Self::Dark
    }
}

/// Semantic paint, geometry, and type roles for one appearance.
#[derive(Clone, Debug)]
pub struct Theme {
    pub appearance: Appearance,
    pub material: Material,

    // ---- surfaces ----
    /// The shell: the plane the titlebar, the rail, and the panel gutters
    /// share. Translucent when the window is glass, so the blurred desktop
    /// reads through it.
    pub chrome: Hsla,
    /// The content panel. Glass uses a thin tonal step over the shell. Opaque
    /// mode uses a solid content plane.
    pub surface: Hsla,
    /// A card lifted off `surface`: nested detail, code, and inert fills. Glass
    /// uses a light wash instead of another dark veil.
    pub surface_raised: Hsla,
    /// Menus, popovers, and tooltips. These float above content and must stay
    /// legible over an unknown backdrop, so they never thin as far as `surface`.
    pub surface_overlay: Hsla,
    /// The modal backdrop.
    pub scrim: Hsla,

    // ---- lines ----
    /// The default edge: the surface's own tone at low opacity, so it reads as
    /// a boundary you feel rather than a drawn grey line.
    pub hairline: Hsla,
    /// A boundary that must survive over glass or a busy backdrop.
    pub hairline_strong: Hsla,

    // ---- text ----
    pub text: Hsla,
    pub text_secondary: Hsla,
    /// Timestamps, units, and other text that must recede without vanishing.
    pub text_tertiary: Hsla,
    /// Text on a filled accent or semantic plate.
    pub text_on_accent: Hsla,

    // ---- interaction ----
    /// Hover wash for chrome and rows. A tone-flipped translucent wash, not a
    /// solid fill: over glass a solid hover plate reads as a hole punched in
    /// the frost.
    pub wash_hover: Hsla,
    /// Selected wash. Pairs with an accent indicator; the fill alone never
    /// carries selection, so the state survives Differentiate Without Color.
    pub wash_selected: Hsla,
    pub wash_pressed: Hsla,
    pub focus_ring: Hsla,

    // ---- accent ----
    /// Inari vermilion — the torii gate. The only chromatic accent in the
    /// interface, so it always means "this is the live path".
    pub accent: Hsla,
    /// Vermilion as a fill behind [`Self::text_on_accent`].
    pub accent_fill: Hsla,
    pub accent_fill_hover: Hsla,
    pub accent_fill_active: Hsla,
    /// A vermilion wash for accent-tinted rows and chips.
    pub accent_wash: Hsla,

    // ---- semantic ----
    pub success: Hsla,
    pub success_wash: Hsla,
    pub warning: Hsla,
    pub warning_wash: Hsla,
    pub info: Hsla,
    pub info_wash: Hsla,
    pub danger: Hsla,
    pub danger_wash: Hsla,
    pub danger_fill: Hsla,

    // ---- type ----
    pub font_sans: SharedString,
    pub font_mono: SharedString,
}

impl Global for Theme {}

impl Theme {
    // ---- geometry ----
    /// The spacing ladder. Every gap and inset is one of these, so optical
    /// rhythm survives edits that a per-call-site `rems(0.375)` would not.
    pub const SPACE_XS: f32 = 4.0;
    pub const SPACE_SM: f32 = 8.0;
    pub const SPACE_MD: f32 = 12.0;
    pub const SPACE_LG: f32 = 16.0;
    pub const SPACE_XL: f32 = 24.0;
    pub const SPACE_2XL: f32 = 32.0;

    /// Radii, coarsest surface outward to the smallest control.
    pub const RADIUS_CONTROL: f32 = 7.0;
    pub const RADIUS_CARD: f32 = 12.0;
    pub const RADIUS_PANEL: f32 = 14.0;

    /// The unified titlebar. Its centerline aligns the brand lockup and the
    /// macOS traffic lights.
    pub const TITLEBAR_HEIGHT: f32 = 44.0;
    /// The navigation rail. Sized to the longest destination label at the
    /// rail's type size, not to a round number.
    pub const RAIL_WIDTH: f32 = 216.0;
    /// The reading measure for body copy. Beyond this, long-form guidance in
    /// Support and Setup gets hard to track back to the next line.
    pub const MEASURE: f32 = 660.0;
    /// The widest a screen's content column grows.
    ///
    /// Wider than [`Self::MEASURE`] because lists, rows, and the gate are not
    /// prose and read better with room; prose inside them stays capped at the
    /// measure, so nothing gets a line length it cannot be read at.
    pub const CONTENT_WIDTH: f32 = 960.0;

    pub fn is_dark(&self) -> bool {
        self.appearance.is_dark()
    }

    /// Whether chrome paints translucently over a blurred backdrop. Every
    /// glass-only recipe gates on this, never on the platform directly: the
    /// user can force the opaque path, and unsupported platforms fall back.
    pub fn is_glass(&self) -> bool {
        self.material.is_glass()
    }

    pub fn dark(material: Material) -> Self {
        let glass = material.is_glass();
        Self {
            appearance: Appearance::Dark,
            material,
            chrome: veil(neutral_dark(0.082), glass, 0.80),
            surface: if glass {
                Hsla { a: 0.40, ..neutral_dark(0.055) }
            } else {
                neutral_dark(0.115)
            },
            surface_raised: if glass { white(0.04) } else { neutral_dark(0.175) },
            surface_overlay: veil(neutral_dark(0.135), glass, 0.88),
            scrim: hsla(0.0, 0.0, 0.0, 0.55),

            hairline: white(0.08),
            hairline_strong: white(0.16),

            text: neutral_dark_text(0.955),
            text_secondary: neutral_dark_text(0.735),
            text_tertiary: neutral_dark_text(0.560),
            text_on_accent: hsla(0.0, 0.0, 1.0, 1.0),

            wash_hover: white(0.06),
            wash_selected: white(0.10),
            wash_pressed: white(0.14),
            focus_ring: VERMILION_DARK,

            accent: VERMILION_DARK,
            accent_fill: hex(0xd4432c),
            accent_fill_hover: hex(0xe04f37),
            accent_fill_active: hex(0xbc3a26),
            accent_wash: Hsla { a: 0.14, ..VERMILION_DARK },

            success: hex(0x7ec79f),
            success_wash: hex(0x1c3227),
            warning: hex(0xf0c36b),
            warning_wash: hex(0x382d18),
            info: hex(0x8db8d5),
            info_wash: hex(0x1d2d36),
            danger: hex(0xff8b78),
            danger_wash: hex(0x3b211c),
            danger_fill: hex(0xa8321f),

            font_sans: FONT_SANS.into(),
            font_mono: FONT_MONO.into(),
        }
    }

    pub fn light(material: Material) -> Self {
        let glass = material.is_glass();
        Self {
            appearance: Appearance::Light,
            material,
            // A near-white tint controls wallpaper contrast while it keeps the
            // same backdrop budget as dark frost. macOS light sidebars use the
            // same pale treatment for legibility.
            chrome: veil(neutral_light(0.980), glass, 0.80),
            surface: if glass { white(0.40) } else { white(1.0) },
            surface_raised: if glass { black(0.04) } else { neutral_light(0.915) },
            surface_overlay: veil(hsla(0.0, 0.0, 1.0, 1.0), glass, 0.93),
            scrim: hsla(0.0, 0.0, 0.0, 0.32),

            hairline: black(0.10),
            hairline_strong: black(0.20),

            text: neutral_light_text(0.105),
            text_secondary: neutral_light_text(0.335),
            text_tertiary: neutral_light_text(0.455),
            text_on_accent: hsla(0.0, 0.0, 1.0, 1.0),

            wash_hover: black(0.045),
            wash_selected: black(0.070),
            wash_pressed: black(0.095),
            focus_ring: VERMILION_LIGHT,

            accent: VERMILION_LIGHT,
            accent_fill: VERMILION_LIGHT,
            accent_fill_hover: hex(0xa33526),
            accent_fill_active: hex(0x8f2e21),
            accent_wash: Hsla { a: 0.10, ..VERMILION_LIGHT },

            success: hex(0x1f6b45),
            success_wash: hex(0xdcede3),
            warning: hex(0x785100),
            warning_wash: hex(0xf3e7c8),
            info: hex(0x365d7d),
            info_wash: hex(0xdce8ef),
            danger: hex(0xa83525),
            danger_wash: hex(0xf4ddd8),
            danger_fill: hex(0xa83525),

            font_sans: FONT_SANS.into(),
            font_mono: FONT_MONO.into(),
        }
    }

    pub fn resolve(appearance: Appearance, material: Material) -> Self {
        match appearance {
            Appearance::Dark => Self::dark(material),
            Appearance::Light => Self::light(material),
        }
    }

    /// Install `self` as the global theme and derive the GPUI Component
    /// palette from it.
    ///
    /// The derived half matters: buttons, inputs, and menus come from GPUI
    /// Component and read *its* palette. Deriving here keeps one editable
    /// source instead of two palettes that agree until someone edits one.
    pub fn install(self, cx: &mut App) {
        let component = ComponentTheme::global_mut(cx);
        component.font_family = self.font_sans.clone();
        component.mono_font_family = self.font_mono.clone();
        component.font_size = px(14.0);
        component.radius = px(Self::RADIUS_CONTROL);
        component.radius_lg = px(Self::RADIUS_CARD);
        component.shadow = false;

        // GPUI Component's Root paints this token across the complete window.
        // It owns the shell tint so DeviceCenter does not stack a second veil.
        component.background = self.chrome;
        component.foreground = self.text;
        component.border = self.hairline;
        component.input = self.hairline_strong;
        component.ring = self.focus_ring;
        component.selection = self.accent_wash;
        component.caret = self.accent;

        component.muted = self.surface_raised;
        component.muted_foreground = self.text_secondary;
        component.accent = self.wash_hover;
        component.accent_foreground = self.text;
        component.secondary = self.surface_raised;
        component.secondary_foreground = self.text;
        component.secondary_hover = self.wash_hover;
        component.secondary_active = self.wash_pressed;

        component.primary = self.accent_fill;
        component.primary_hover = self.accent_fill_hover;
        component.primary_active = self.accent_fill_active;
        component.primary_foreground = self.text_on_accent;

        component.popover = self.surface_overlay;
        component.popover_foreground = self.text;
        component.overlay = self.scrim;
        component.title_bar = self.chrome;
        component.title_bar_border = self.hairline;

        component.sidebar = self.chrome;
        component.sidebar_foreground = self.text;
        component.sidebar_border = self.hairline;
        component.sidebar_accent = self.wash_selected;
        component.sidebar_accent_foreground = self.text;
        component.sidebar_primary = self.accent;
        component.sidebar_primary_foreground = self.text_on_accent;

        component.list = self.surface;
        component.list_hover = self.wash_hover;
        component.list_active = self.wash_selected;
        component.list_active_border = self.accent;

        component.scrollbar = gpui::transparent_black();
        component.scrollbar_thumb = self.hairline_strong;
        component.scrollbar_thumb_hover = self.text_tertiary;

        component.info = self.info;
        component.info_foreground = self.text_on_accent;
        component.success = self.success;
        component.success_foreground = self.text_on_accent;
        component.warning = self.warning;
        component.warning_foreground = self.text_on_accent;
        component.danger = self.danger_fill;
        component.danger_foreground = self.text_on_accent;

        cx.set_global(self);
    }

    #[inline]
    pub fn of(cx: &App) -> &Theme {
        cx.global::<Theme>()
    }

    /// Re-resolve the theme from the window's appearance and the current
    /// material preference, then re-apply the window background.
    ///
    /// Call this at startup and on every appearance or preference change. The
    /// window background is not optional here: GPUI's macOS backend drops its
    /// blur view whenever the value is not `Blurred`, so a theme swap that
    /// skips this step leaves the shell flat until the next one.
    pub fn sync(window: &mut Window, cx: &mut App) {
        ComponentTheme::sync_system_appearance(Some(window), cx);

        let appearance = Appearance::from_window(window.appearance());
        let material = super::material::resolve();
        Theme::resolve(appearance, material).install(cx);

        window.set_background_appearance(material.window_background());
        // Colors are read imperatively at paint time, so no view knows its
        // palette went stale. Refreshing every window is what forces already
        // laid-out elements to repaint with the new one.
        cx.refresh_windows();
    }
}

/// Read the active theme. Mirrors GPUI Component's `ActiveTheme` so call sites
/// read the same way regardless of which palette they need.
pub trait ActiveTheme {
    fn inari(&self) -> &Theme;
}

impl ActiveTheme for App {
    #[inline]
    fn inari(&self) -> &Theme {
        Theme::of(self)
    }
}

// ---- ramp helpers ----

/// The brand vermilion. Dark mode lifts it well above the light value: the
/// #b43b29 that reads as a deep gate red on white turns muddy on near-black.
const VERMILION_DARK: Hsla = Hsla { h: 0.0244, s: 0.94, l: 0.685, a: 1.0 };
const VERMILION_LIGHT: Hsla = Hsla { h: 0.0244, s: 0.63, l: 0.435, a: 1.0 };

const FONT_SANS: &str = "Atkinson Hyperlegible Next";

/// Surface hue: a green cast just strong enough to keep neutrals from reading
/// blue beside vermilion, and weak enough to still read as grey on its own.
const SURFACE_HUE: f32 = 96.0 / 360.0;

fn neutral_dark(lightness: f32) -> Hsla {
    hsla(SURFACE_HUE, 0.055, lightness, 1.0)
}

fn neutral_light(lightness: f32) -> Hsla {
    hsla(SURFACE_HUE, 0.070, lightness, 1.0)
}

/// Text carries less of the surface cast than the surfaces do; at text sizes a
/// visible hue reads as a printing error rather than as warmth.
fn neutral_dark_text(lightness: f32) -> Hsla {
    hsla(SURFACE_HUE, 0.030, lightness, 1.0)
}

fn neutral_light_text(lightness: f32) -> Hsla {
    hsla(SURFACE_HUE, 0.040, lightness, 1.0)
}

/// Thin `color` to `alpha` when the window is glass, leave it opaque otherwise.
fn veil(color: Hsla, glass: bool, alpha: f32) -> Hsla {
    if glass { Hsla { a: alpha, ..color } } else { color }
}

/// Composite `top` over `base`, as the renderer would.
pub(crate) fn flatten(top: Hsla, base: Hsla) -> Hsla {
    let mix = |over: f32, under: f32| over * top.a + under * (1.0 - top.a);
    Hsla {
        h: if top.a > 0.0 { top.h } else { base.h },
        s: if top.a > 0.0 { top.s } else { base.s },
        l: mix(top.l, base.l),
        a: mix(top.a, base.a),
    }
}

/// Transition between two colors the way CSS interpolation would.
///
/// Used across tiny distances — a hairline towards an accent, a rest fill
/// towards its hover — where a straight ramp in HSLA reads the same as the
/// browser's sRGB one and costs nothing.
pub(crate) fn mix(from: Hsla, to: Hsla, t: f32) -> Hsla {
    let t = t.clamp(0.0, 1.0);
    Hsla {
        h: from.h + (to.h - from.h) * t,
        s: from.s + (to.s - from.s) * t,
        l: from.l + (to.l - from.l) * t,
        a: from.a + (to.a - from.a) * t,
    }
}

fn white(alpha: f32) -> Hsla {
    hsla(0.0, 0.0, 1.0, alpha)
}

fn black(alpha: f32) -> Hsla {
    hsla(0.0, 0.0, 0.0, alpha)
}

fn hex(value: u32) -> Hsla {
    gpui::rgb(value).into()
}

/// The technical face, for identifiers, endpoints, and diagnostics.
///
/// Departure Mono, embedded, so the readouts look the same on every platform
/// instead of inheriting SF Mono, Cascadia Mono, and whatever `monospace`
/// resolves to on a given Linux box.
///
/// It is a pixel face: every outline sits on a grid of 50 units in a 550-unit
/// em, so it is only truly sharp at sizes where that grid lands on whole
/// device pixels. [`super::content::Typography::text_technical`] carries the
/// size that follows from that.
const FONT_MONO: &str = "Departure Mono";

#[cfg(test)]
mod tests {
    use gpui::Rgba;

    use super::*;
    use crate::ui::material::Material;

    fn themes() -> [Theme; 4] {
        [
            Theme::dark(Material::Glass),
            Theme::dark(Material::Opaque),
            Theme::light(Material::Glass),
            Theme::light(Material::Opaque),
        ]
    }

    /// Text must clear WCAG AA against the surface it actually lands on.
    ///
    /// Translucent surfaces are flattened over `canvas` first. That is the
    /// honest worst case we control: over glass the real backdrop is the
    /// user's desktop, which no palette can guarantee, so the tokens are tuned
    /// so the frost alone already carries the text.
    #[test]
    fn body_text_meets_wcag_aa_on_every_surface_in_both_appearances() {
        for theme in themes() {
            for background in actual_surfaces(&theme) {
                assert!(
                    contrast(theme.text, background) >= 4.5,
                    "primary text failed on {:?}: {:.2}",
                    theme.appearance,
                    contrast(theme.text, background)
                );
                assert!(
                    contrast(theme.text_secondary, background) >= 4.5,
                    "secondary text failed on {:?}: {:.2}",
                    theme.appearance,
                    contrast(theme.text_secondary, background)
                );
            }
        }
    }

    /// Tertiary text is metadata, so it is held to the AA large-text ratio
    /// rather than the body ratio it would fail by design.
    #[test]
    fn tertiary_text_stays_above_the_large_text_ratio() {
        for theme in themes() {
            let [_, _, background] = actual_surfaces(&theme);
            assert!(
                contrast(theme.text_tertiary, background) >= 3.0,
                "tertiary text failed on {:?}: {:.2}",
                theme.appearance,
                contrast(theme.text_tertiary, background)
            );
        }
    }

    #[test]
    fn every_semantic_tone_is_readable_on_its_own_wash() {
        for theme in themes() {
            for (foreground, wash) in [
                (theme.success, theme.success_wash),
                (theme.warning, theme.warning_wash),
                (theme.info, theme.info_wash),
                (theme.danger, theme.danger_wash),
            ] {
                assert!(
                    contrast(foreground, flatten(wash, opaque_shell(&theme))) >= 4.5,
                    "semantic tone failed on {:?}: {:.2}",
                    theme.appearance,
                    contrast(foreground, flatten(wash, opaque_shell(&theme)))
                );
            }
        }
    }

    #[test]
    fn accent_fills_carry_readable_labels() {
        for theme in themes() {
            assert!(
                contrast(theme.text_on_accent, flatten(theme.accent_fill, opaque_shell(&theme)))
                    >= 4.5,
                "accent fill failed on {:?}: {:.2}",
                theme.appearance,
                contrast(theme.text_on_accent, flatten(theme.accent_fill, opaque_shell(&theme)))
            );
            assert!(
                contrast(theme.text_on_accent, flatten(theme.danger_fill, opaque_shell(&theme)))
                    >= 4.5
            );
        }
    }

    /// A keyboard user has to find the focus ring, so it is held to the
    /// non-text 3:1 ratio even though the subtle hairlines are not.
    #[test]
    fn the_focus_ring_separates_from_every_surface_it_can_land_on() {
        for theme in themes() {
            for surface in actual_surfaces(&theme) {
                assert!(
                    contrast(theme.focus_ring, surface) >= 3.0,
                    "focus ring failed on {:?}",
                    theme.appearance
                );
            }
        }
    }

    /// Elevation is carried by tone, not by the hairline, so the step from a
    /// panel to a card on it has to be visible.
    ///
    /// Measured as a contrast ratio rather than a luminance difference: near
    /// black, two clearly distinct tones are only thousandths apart in
    /// absolute luminance, so a fixed delta would pass light mode and reject a
    /// dark palette that reads fine.
    #[test]
    fn each_elevation_step_is_a_visible_tonal_change() {
        for theme in themes() {
            let [_, surface, raised] = actual_surfaces(&theme);
            let ratio = contrast_between(surface, raised);
            assert!(
                ratio >= 1.08,
                "surface and surface_raised are too close on {:?} ({:?}): {ratio:.3}",
                theme.appearance,
                theme.material
            );
        }
    }

    /// A glass theme must leave enough of the native blur visible after every
    /// standard surface is painted. This budget prevents another accidental
    /// stack of broad, nearly opaque fills.
    #[test]
    fn glass_surface_stack_preserves_the_backdrop() {
        for theme in [Theme::dark(Material::Glass), Theme::light(Material::Glass)] {
            let backdrop =
                (1.0 - theme.chrome.a) * (1.0 - theme.surface.a) * (1.0 - theme.surface_raised.a);
            assert!(
                backdrop >= 0.10,
                "glass stack leaves only {:.1}% of the backdrop on {:?}",
                backdrop * 100.0,
                theme.appearance
            );
            assert!(theme.surface.a <= 0.40);
            assert!(theme.surface_raised.a <= 0.08);
        }
    }

    #[test]
    fn opaque_material_leaves_every_surface_fully_opaque() {
        for theme in [Theme::dark(Material::Opaque), Theme::light(Material::Opaque)] {
            assert!(!theme.is_glass());
            for surface in [theme.chrome, theme.surface, theme.surface_raised] {
                assert_eq!(surface.a, 1.0);
            }
        }
    }

    #[test]
    fn glass_material_thins_chrome_and_keeps_an_opaque_test_shell() {
        for theme in [Theme::dark(Material::Glass), Theme::light(Material::Glass)] {
            assert!(theme.is_glass());
            assert!(theme.chrome.a < 1.0);
            assert_eq!(opaque_shell(&theme).a, 1.0);
        }
    }

    #[test]
    fn appearance_resolution_follows_the_window() {
        assert!(Appearance::from_window(WindowAppearance::VibrantDark).is_dark());
        assert!(!Appearance::from_window(WindowAppearance::Light).is_dark());
        assert!(Theme::resolve(Appearance::Dark, Material::Opaque).is_dark());
    }

    /// The shell tone at full coverage.
    ///
    /// Under glass the real backdrop is the user's desktop, which no palette
    /// can guarantee. This is the worst case the palette does control, and it
    /// is what every contrast check measures against.
    fn opaque_shell(theme: &Theme) -> Hsla {
        Hsla { a: 1.0, ..theme.chrome }
    }

    /// Return the shell, panel, and card colors after their real nesting order.
    fn actual_surfaces(theme: &Theme) -> [Rgba; 3] {
        let shell = opaque_shell(theme).into();
        let panel = flatten_over(theme.surface, shell);
        let raised = flatten_over(theme.surface_raised, panel);
        [shell, panel, raised]
    }

    /// Composite `color` over `base`, so a translucent token is measured as it
    /// is actually seen.
    fn flatten(color: Hsla, base: Hsla) -> Rgba {
        flatten_over(color, base.into())
    }

    fn flatten_over(color: Hsla, base: Rgba) -> Rgba {
        let top: Rgba = color.into();
        let mix = |over: f32, under: f32| over * color.a + under * (1.0 - color.a);
        Rgba { r: mix(top.r, base.r), g: mix(top.g, base.g), b: mix(top.b, base.b), a: 1.0 }
    }

    fn contrast(foreground: Hsla, background: Rgba) -> f32 {
        contrast_between(foreground.into(), background)
    }

    fn contrast_between(one: Rgba, other: Rgba) -> f32 {
        let a = luminance(one);
        let b = luminance(other);
        (a.max(b) + 0.05) / (a.min(b) + 0.05)
    }

    fn luminance(color: Rgba) -> f32 {
        0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)
    }

    fn channel(value: f32) -> f32 {
        if value <= 0.04045 { value / 12.92 } else { ((value + 0.055) / 1.055).powf(2.4) }
    }
}
