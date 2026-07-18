use std::borrow::Cow;

use gpui::{AssetSource, ImageSource, Img, Resource, SharedString, img};
use rust_embed::RustEmbed;

#[derive(RustEmbed)]
#[folder = "../../packages/brand/inari_brand/assets"]
pub struct BrandAssets;

pub fn image(path: &'static str) -> Img {
    img(ImageSource::Resource(Resource::Embedded(path.into())))
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
