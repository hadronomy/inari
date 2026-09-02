//! The story catalog.
//!
//! A story registers itself from the file that owns the component it previews,
//! the way a `#[cfg(test)] mod tests` does. There is no central enum, no `ALL`
//! array, and no per-story action: the catalog is whatever was compiled in.
//!
//! Zed learned this the hard way. Its `crates/storybook` needs a new variant in
//! `ComponentStory` *and* a new arm in the `match` that builds it — two lists to
//! keep in step for one preview — and its replacement, `crates/component`, drops
//! both in favour of `inventory`. We start where they finished.

use gpui::{AnyElement, App, Window};

use super::dial::Dial;

/// Where a story sits in the catalog. Ordering here is the ordering the rail
/// shows, so it runs from the smallest pieces to whole screens.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub enum Scope {
    /// Colour, type, spacing: the things every other story is made of.
    Foundations,
    /// Planes that hold content — cards, panels, banners.
    Surfaces,
    /// Things a person operates.
    Controls,
    /// Status, progress, and the rest of what the app says back.
    Feedback,
    /// Transitions and the curves under them.
    Motion,
    /// Shader work, judged at the size it ships at and again enlarged.
    Effects,
    /// Whole screens against scripted data.
    Screens,
}

impl Scope {
    pub const ALL: [Self; 7] = [
        Self::Foundations,
        Self::Surfaces,
        Self::Controls,
        Self::Feedback,
        Self::Motion,
        Self::Effects,
        Self::Screens,
    ];

    pub fn title(self) -> &'static str {
        match self {
            Self::Foundations => "Foundations",
            Self::Surfaces => "Surfaces",
            Self::Controls => "Controls",
            Self::Feedback => "Feedback",
            Self::Motion => "Motion",
            Self::Effects => "Effects",
            Self::Screens => "Screens",
        }
    }
}

/// One registered preview.
///
/// `render` receives a [`Dial`] and reads its live parameters from it. The
/// signature takes `&mut App` rather than a `Context<T>` because a story owns no
/// entity: whatever state it needs either comes from a knob or is built on the
/// spot.
pub struct Story {
    /// Dotted and stable — it keys the knob store, so renaming it resets the
    /// knobs and nothing else.
    pub id: &'static str,
    pub name: &'static str,
    pub scope: Scope,
    /// One line under the title. What the story is for, not what it contains.
    pub about: &'static str,
    pub render: fn(&mut Dial, &mut Window, &mut App) -> AnyElement,
}

inventory::collect!(Story);

/// Every registered story, ordered by scope and then by name.
pub fn catalog() -> Vec<&'static Story> {
    let mut stories: Vec<&'static Story> = inventory::iter::<Story>().collect();
    stories.sort_by_key(|story| (story.scope, story.name));
    stories
}

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

/// Reached only through [`story!`], so a caller never needs the dependency in
/// scope.
#[doc(hidden)]
pub use inventory as __inventory;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_catalog_is_not_empty() {
        assert!(!catalog().is_empty(), "no story registered; inventory did not link");
    }

    #[test]
    fn story_ids_are_unique() {
        let mut ids: Vec<&str> = catalog().iter().map(|story| story.id).collect();
        ids.sort_unstable();
        let count = ids.len();
        ids.dedup();
        assert_eq!(ids.len(), count, "two stories share an id, so they would share knobs");
    }

    #[test]
    fn the_catalog_runs_from_pieces_to_screens() {
        let scopes: Vec<Scope> = catalog().iter().map(|story| story.scope).collect();
        let mut sorted = scopes.clone();
        sorted.sort();
        assert_eq!(scopes, sorted);
    }
}
