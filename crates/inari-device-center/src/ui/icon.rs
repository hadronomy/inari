//! The house icon set.
//!
//! Every glyph is a brand asset under `icons/`, drawn on one 24px grid with a
//! 2px round stroke. GPUI Component resolves its own [`IconName`] variants
//! against the same folder, so the two sets are one visual family rather than
//! a vendored pack sitting next to a bespoke one.
//!
//! Glyphs the design needs beyond GPUI Component's set live here, typed, so a
//! renamed asset breaks a test instead of silently rendering nothing.

use gpui_component::{Icon, IconName};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Glyph {
    Printer,
    Scale,
    Scanner,
    Device,
    Activity,
    Support,
    /// This computer.
    Computer,
    /// The local agent service.
    Agent,
    /// A credential carried by a link.
    Link,
    /// Present in the directory, not reachable.
    Offline,
}

impl Glyph {
    pub fn path(self) -> &'static str {
        match self {
            Self::Printer => "icons/printer.svg",
            Self::Scale => "icons/scale.svg",
            Self::Scanner => "icons/scan-line.svg",
            Self::Device => "icons/cpu.svg",
            Self::Activity => "icons/activity.svg",
            Self::Support => "icons/life-buoy.svg",
            Self::Computer => "icons/monitor.svg",
            Self::Agent => "icons/server.svg",
            Self::Link => "icons/link.svg",
            Self::Offline => "icons/circle-dashed.svg",
        }
    }

    pub const ALL: [Self; 10] = [
        Self::Printer,
        Self::Scale,
        Self::Scanner,
        Self::Device,
        Self::Activity,
        Self::Support,
        Self::Computer,
        Self::Agent,
        Self::Link,
        Self::Offline,
    ];
}

impl From<Glyph> for Icon {
    fn from(glyph: Glyph) -> Self {
        Icon::default().path(glyph.path())
    }
}

/// A glyph from either set, so a component can take one icon parameter.
#[derive(Clone)]
pub enum Symbol {
    House(Glyph),
    Component(IconName),
}

impl From<Glyph> for Symbol {
    fn from(glyph: Glyph) -> Self {
        Self::House(glyph)
    }
}

impl From<IconName> for Symbol {
    fn from(name: IconName) -> Self {
        Self::Component(name)
    }
}

impl Symbol {
    /// The embedded asset this symbol resolves to.
    pub fn path(&self) -> gpui::SharedString {
        match self {
            Self::House(glyph) => glyph.path().into(),
            Self::Component(name) => gpui_component::IconNamed::path(name.clone()),
        }
    }
}

impl From<Symbol> for Icon {
    fn from(symbol: Symbol) -> Self {
        match symbol {
            Symbol::House(glyph) => glyph.into(),
            Symbol::Component(name) => Icon::new(name),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assets::BrandAssets;

    #[test]
    fn every_house_glyph_resolves_to_an_embedded_asset() {
        for glyph in Glyph::ALL {
            assert!(
                BrandAssets::get(glyph.path()).is_some(),
                "missing brand icon: {}",
                glyph.path()
            );
        }
    }

    #[test]
    fn house_glyphs_are_all_distinct() {
        for (index, glyph) in Glyph::ALL.iter().enumerate() {
            for other in &Glyph::ALL[index + 1..] {
                assert_ne!(glyph.path(), other.path());
            }
        }
    }
}
