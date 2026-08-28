//! The credential field: the one input a screen may center its question on.
//!
//! An invitation link is a credential, and the field that takes it is the
//! moment of highest attention in the whole app. It is styled accordingly —
//! not as one more bordered rectangle, but as a small instrument: a lit fill,
//! an edge that warms to the accent while the field is live, and a ring that
//! arrives over the same 150 ms every other wash in the app uses.
//!
//! The state table follows glassy-ui's `field_chrome`: rest, hover, focus,
//! and disabled each named and painted as one decision, with the material
//! constants — the inset catch-light, the radius — shared across all of them.
//! Comet's lesson holds here too: no drop shadow behind a translucent fill,
//! where it reads through as an inner glow.
//!
//! Editing itself belongs to GPUI Component's [`Input`] with its appearance
//! off; this component owns only the chrome around it. Focus and hover both
//! run through the motion module's fade store — the owner reports focus flips
//! against [`FADE_KEY_FOCUS`] — so the ring, like every other wash, is a pure
//! function of the clock.

use gpui::{
    AnimationExt as _, BoxShadow, Entity, Hsla, InteractiveElement as _, IntoElement,
    ParentElement as _, RenderOnce, StatefulInteractiveElement as _, Styled, Window, div, point,
    prelude::FluentBuilder as _, px,
};
use gpui_component::input::{Input, InputState};
use gpui_component::{Icon, IconName};

use super::{
    icon::{Glyph, Symbol},
    motion,
    theme::{ActiveTheme as _, Theme},
};

/// The fade keys this chrome drives. The focus key is reported by the window
/// that owns the input, on the input's Focus and Blur events; the invalid key
/// is aimed at a prop each render, since an invalid link arrives with data
/// rather than with a pointer event.
pub const FADE_KEY_FOCUS: &str = "credential-focus";
const FADE_KEY_HOVER: &str = "credential-hover";
const FADE_KEY_INVALID: &str = "credential-invalid";

/// One driven input: GPUI Component's editor inside this component's chrome.
#[derive(IntoElement)]
pub struct CredentialField {
    input: Entity<InputState>,
    valid: bool,
    invalid: bool,
    disabled: bool,
}

impl CredentialField {
    pub fn new(input: Entity<InputState>) -> Self {
        Self { input, valid: false, invalid: false, disabled: false }
    }

    /// Whether the current contents parse as the credential the field asks
    /// for. Drives the quiet check on the trailing edge — positive feedback
    /// only: a half-typed link is never scolded, it just has not earned the
    /// check yet.
    pub fn valid(mut self, valid: bool) -> Self {
        self.valid = valid;
        self
    }

    /// Whether a submitted attempt failed on the text itself. The field answers
    /// in its own paint — an edge and a wash in the danger tone — while the
    /// banner above carries the words. A network failure with a well-formed
    /// link is not the field's to report, so the caller passes this only when
    /// the text is what failed.
    pub fn invalid(mut self, invalid: bool) -> Self {
        self.invalid = invalid;
        self
    }

    pub fn disabled(mut self, disabled: bool) -> Self {
        self.disabled = disabled;
        self
    }
}

impl RenderOnce for CredentialField {
    fn render(self, _: &mut Window, cx: &mut gpui::App) -> impl IntoElement {
        let theme = cx.inari();
        // Aim the invalid fade at the prop before reading it: the state
        // arrives with data, so there is no event to listen for.
        let invalid_target = f32::from(self.invalid);
        if motion::fade_target(FADE_KEY_INVALID) != invalid_target {
            motion::hover_set(FADE_KEY_INVALID, self.invalid);
        }
        let focus = motion::fade_fraction(FADE_KEY_FOCUS);
        let hover = motion::fade_fraction(FADE_KEY_HOVER);
        let invalid = motion::fade_fraction(FADE_KEY_INVALID);

        // The edge rests on the hairline, warms to the accent while the field
        // is live, and hands itself to the danger tone when the text failed —
        // in that order, each fade layered over the last.
        let border = super::theme::mix(theme.hairline, Hsla { a: 0.55, ..theme.accent }, focus);
        let border = super::theme::mix(border, Hsla { a: 0.65, ..theme.danger }, invalid);
        let fill = super::theme::flatten(
            Hsla { a: theme.wash_hover.a * hover, ..theme.wash_hover },
            theme.surface_raised,
        );
        let fill = super::theme::flatten(Hsla { a: 0.30 * invalid, ..theme.danger_wash }, fill);
        // The glassy ring: a soft accent halo, 3px out, no blur — presence
        // without glow. It stands down while invalid owns the edge.
        let ring = (focus > 0.004 && invalid < 0.5).then(|| BoxShadow {
            color: Hsla { a: 0.16 * focus, ..theme.accent },
            offset: point(px(0.0), px(0.0)),
            blur_radius: px(0.0),
            spread_radius: px(3.0),
        });
        // The material's own catch-light, constant across states: one pixel of
        // light along the top lip, the way every raised surface in the app
        // catches it.
        let catch_light = theme.is_dark().then(|| BoxShadow {
            color: Hsla { a: 0.06, ..gpui::white() },
            offset: point(px(0.0), px(1.0)),
            blur_radius: px(0.0),
            spread_radius: px(0.0),
        });
        let shadows: Vec<BoxShadow> = catch_light
            .into_iter()
            .chain(ring)
            .collect();

        div()
            .id("credential-field")
            .flex()
            .items_center()
            .w_full()
            .h(px(40.0))
            .px(px(Theme::SPACE_MD))
            .gap(px(Theme::SPACE_SM))
            .rounded(px(Theme::RADIUS_CONTROL))
            .border_1()
            .border_color(border)
            .bg(fill)
            .shadow(shadows)
            .when(self.disabled, |field| field.opacity(0.5))
            .when(!self.disabled, |field| {
                field
                    .cursor(gpui::CursorStyle::IBeam)
                    .on_hover(|hovered, window, _| {
                        if motion::hover_set(FADE_KEY_HOVER, *hovered) {
                            window.refresh();
                        }
                    })
            })
            .child(
                Icon::from(Symbol::House(Glyph::Link))
                    .size(px(15.0))
                    .flex_none()
                    .text_color(theme.text_tertiary),
            )
            .child(
                div()
                    .flex_1()
                    .min_w(px(0.0))
                    // The editor's own clear control overflows its flex box
                    // when the chrome owns the border, so the field carries
                    // none and the one below is ours — placed, sized, and
                    // faded by the same hand as the rest of the chrome.
                    .child(
                        Input::new(&self.input)
                            .appearance(false)
                            .disabled(self.disabled),
                    ),
            )
            .children((!self.disabled && !self.input.read(cx).value().is_empty()).then(|| {
                div()
                    .id("credential-clear")
                    .flex()
                    .items_center()
                    .justify_center()
                    .size(px(22.0))
                    .flex_none()
                    .rounded_full()
                    .text_color(theme.text_tertiary)
                    .on_hover(|hovered, window, _| {
                        if motion::hover_set("credential-clear", *hovered) {
                            window.refresh();
                        }
                    })
                    .bg(motion::hover_blend("credential-clear", gpui::transparent_black()))
                    .on_click({
                        let input = self.input.clone();
                        move |_, window, cx| {
                            input.update(cx, |state, cx| {
                                state.set_value("", window, cx);
                            });
                        }
                    })
                    .child(Icon::from(Symbol::Component(IconName::CircleX)).size(px(13.0)))
            }))
            .children(self.valid.then(|| {
                // The link parses. It arrives with the settle ease, keyed on
                // the flag so a corrected link earns the check again.
                Icon::from(Symbol::Component(IconName::CircleCheck))
                    .size(px(14.0))
                    .flex_none()
                    .text_color(theme.success)
                    .with_animation(
                        ("credential-valid", usize::from(self.valid)),
                        motion::settle(),
                        |icon, delta| icon.opacity(delta),
                    )
            }))
    }
}
