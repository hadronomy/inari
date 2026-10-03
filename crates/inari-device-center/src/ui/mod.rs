//! The Inari design system: tokens, materials, motion, and the component set
//! every feature screen is assembled from.

pub mod banner;
pub mod button;
pub mod chrome;
pub mod content;
pub mod effect;
pub mod field;
pub mod focus;
pub mod gate;
pub mod icon;
pub mod material;
pub mod motion;
pub mod pixel_bloom;
pub mod readout;
pub mod status;
pub mod surface;
pub mod swap;
pub mod theme;
pub mod titlebar;

/// Register a story beside the component it previews.
///
/// ```ignore
/// crate::story! {
///     id: "control.button",
///     name: "Button",
///     scope: Scope::Controls,
///     about: "Every emphasis, with the reporting swap.",
///     render: |dial, _window, cx| { ... },
/// }
/// ```
///
/// The `#[cfg]` is inside the macro so a story never needs to remember that a
/// release build carries no dev surfaces.
#[macro_export]
macro_rules! story {
    (
        id: $id:expr,
        name: $name:expr,
        scope: $scope:expr,
        about: $about:expr,
        render: $render:expr $(,)?
    ) => {
        #[cfg(debug_assertions)]
        $crate::dev::story::__inventory::submit! {
            $crate::dev::story::Story {
                id: $id,
                name: $name,
                scope: $scope,
                about: $about,
                render: $render,
            }
        }
    };
}
