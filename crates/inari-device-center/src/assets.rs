use std::borrow::Cow;

use gpui::{App, AssetSource, SharedString};
use rust_embed::RustEmbed;

#[derive(RustEmbed)]
#[folder = "../../packages/brand/inari_brand/assets"]
pub struct BrandAssets;

/// The faces the interface is set in, embedded rather than resolved from the
/// system.
///
/// Shipping the mono face matters as much as shipping the sans one: the
/// technical readouts are set on Departure Mono's pixel grid, and a platform
/// fallback would put those measurements on a face that does not share it.
const EMBEDDED_FONTS: [&str; 3] = [
    "fonts/atkinson-hyperlegible-next-regular.otf",
    "fonts/atkinson-hyperlegible-next-semibold.otf",
    "fonts/departure-mono-regular.otf",
];

pub fn install_fonts(cx: &App) -> gpui::Result<()> {
    let fonts = EMBEDDED_FONTS
        .into_iter()
        .map(|path| {
            BrandAssets::get(path)
                .unwrap_or_else(|| panic!("missing embedded font: {path}"))
                .data
        })
        .collect();
    cx.text_system().add_fonts(fonts)
}

impl AssetSource for BrandAssets {
    fn load(&self, path: &str) -> gpui::Result<Option<Cow<'static, [u8]>>> {
        Ok(Self::get(path).map(|asset| asset.data))
    }

    fn list(&self, path: &str) -> gpui::Result<Vec<SharedString>> {
        Ok(Self::iter()
            .filter(|asset| asset.starts_with(path))
            .map(|asset| SharedString::from(asset.into_owned()))
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_interface_face_is_embedded() {
        for path in EMBEDDED_FONTS {
            assert!(BrandAssets::get(path).is_some(), "missing {path}");
        }
    }

    #[test]
    fn windows_titlebar_icons_are_embedded() {
        for path in [
            "icons/window-close.svg",
            "icons/window-maximize.svg",
            "icons/window-minimize.svg",
            "icons/window-restore.svg",
        ] {
            assert!(BrandAssets::get(path).is_some(), "missing {path}");
        }
    }
}
