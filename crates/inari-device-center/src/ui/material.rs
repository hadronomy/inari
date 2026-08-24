//! Window material policy.
//!
//! GPUI 0.2.2 can blur the content *behind* a window, and nothing else. There
//! is no per-element backdrop filter, so the Device Center never claims one:
//! chrome is translucent over one real window blur, content surfaces use thin
//! tonal washes, and floating overlays keep a denser legibility tint.
//!
//! | Platform | Blur behind window | Notes |
//! |---|---|---|
//! | macOS | `NSVisualEffectView` | Real vibrancy |
//! | Windows | Acrylic blur-behind | The production packaging target |
//! | Linux | Wayland only, compositor-dependent | Not assumed; opaque by default |

use std::sync::atomic::{AtomicBool, Ordering};

use gpui::WindowBackgroundAppearance;

/// How the window composites against the desktop.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Material {
    /// Translucent chrome over a real window blur.
    Glass,
    /// Every surface paints solid. The fallback for platforms without a blur
    /// guarantee, and the answer for anyone who wants less transparency.
    Opaque,
}

impl Material {
    pub fn is_glass(self) -> bool {
        self == Self::Glass
    }

    /// How the platform should composite the window behind our paint.
    ///
    /// Re-apply this after every theme change, not just at startup: GPUI's
    /// macOS backend removes its `NSVisualEffectView` whenever the value is
    /// anything but `Blurred`, so a round trip through `Opaque` leaves the
    /// window flat until something sets it again.
    pub fn window_background(self) -> WindowBackgroundAppearance {
        match self {
            Self::Glass => WindowBackgroundAppearance::Blurred,
            Self::Opaque => WindowBackgroundAppearance::Opaque,
        }
    }
}

/// Whether this build can blur behind its window at all.
///
/// Linux is excluded deliberately. Blur there needs a Wayland session *and* a
/// compositor that honors the protocol; on X11 or a compositor that declines,
/// a transparent window shows the raw desktop through the rail instead of a
/// blur, which is worse than an honest solid surface.
pub const fn platform_supports_glass() -> bool {
    cfg!(any(target_os = "macos", target_os = "windows"))
}

/// The session's material preference.
///
/// Held in a process global rather than on the root entity because the window
/// background is applied during window construction, before any entity exists.
static PREFER_OPAQUE: AtomicBool = AtomicBool::new(false);

/// Resolve the material from platform capability and the current preference.
///
/// GPUI 0.2.2 reports no OS accessibility preferences, so "Reduce
/// transparency" cannot be detected. [`set_prefer_opaque`] is the manual path,
/// seeded from `INARI_MATERIAL=opaque` for anyone who wants it from launch.
pub fn resolve() -> Material {
    if !platform_supports_glass() || PREFER_OPAQUE.load(Ordering::Relaxed) {
        Material::Opaque
    } else {
        Material::Glass
    }
}

/// Read the launch-time material override. Accepts `opaque`/`solid` and
/// `glass`/`translucent`; anything else leaves the platform default.
pub fn init_from_environment() {
    let Ok(value) = std::env::var("INARI_MATERIAL") else {
        return;
    };
    match value
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "opaque" | "solid" => set_prefer_opaque(true),
        "glass" | "translucent" => set_prefer_opaque(false),
        other => tracing::warn!(value = other, "ignoring unknown INARI_MATERIAL value"),
    }
}

pub fn set_prefer_opaque(prefer_opaque: bool) {
    PREFER_OPAQUE.store(prefer_opaque, Ordering::Relaxed);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opaque_preference_wins_over_platform_capability() {
        set_prefer_opaque(true);
        assert_eq!(resolve(), Material::Opaque);
        set_prefer_opaque(false);

        let expected = if platform_supports_glass() { Material::Glass } else { Material::Opaque };
        assert_eq!(resolve(), expected);
    }

    #[test]
    fn opaque_material_never_asks_the_platform_to_blur() {
        assert_eq!(Material::Opaque.window_background(), WindowBackgroundAppearance::Opaque);
        assert_eq!(Material::Glass.window_background(), WindowBackgroundAppearance::Blurred);
    }
}
