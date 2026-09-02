//! The house button.
//!
//! GPUI Component ships a button, and this app used it until the difference
//! became the thing you notice. Its wash lands in one frame while every other
//! surface in the window — rail items, device rows, the health chip, the
//! credential field — eases over 150 ms, so the one control an operator is
//! most likely to be pointing at was the one that felt least considered. It
//! also keeps the arrow cursor on a control that is unambiguously clickable,
//! and it carries a shadow recipe this design system does not use.
//!
//! So the button is ours. It is built from the same three parts as the
//! credential field, in the same order: a fill, an edge, and a catch-light
//! along the top lip. What changes between variants is how much of each — a
//! primary earns a filled plate, an outline earns a plate and an edge, and a
//! ghost earns neither until the pointer arrives.
//!
//! One rule holds all three together: **the fill is the only thing that moves
//! on hover.** No lift, no scale, no growing shadow. A control that jumps away
//! from the pointer is harder to hit than one that does not, and on a
//! translucent window a shadow behind a translucent fill reads through it as
//! an inner glow rather than as depth.

use std::rc::Rc;

use gpui::{
    App, Hsla, InteractiveElement as _, IntoElement, KeyDownEvent, ParentElement as _, RenderOnce,
    SharedString, StatefulInteractiveElement as _, Styled, Window, div,
    prelude::FluentBuilder as _, px,
};
use gpui_component::Icon;

use super::{
    chrome::is_activation,
    content::Typography as _,
    focus,
    icon::Symbol,
    motion, swap,
    theme::{ActiveTheme as _, Theme, flatten, mix},
};

/// Control height. One step under the rail item's 34px: a button sits inside
/// content rather than being the surface content sits on.
const HEIGHT: f32 = 30.0;
/// Glyph size. Sized to the cap height of the label beside it, not to the
/// button, so the two read as one word rather than as a picture and a caption.
const ICON: f32 = 14.0;

/// How much of a button's job the variant is doing.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Emphasis {
    /// The one action a screen is asking for. Vermilion plate, white label.
    Primary,
    /// A real action that is not the point of the screen. A plate and an edge.
    Outline,
    /// An action that should stay quiet until it is wanted. No chrome at rest.
    Ghost,
}

type Handler = Rc<dyn Fn(&mut Window, &mut App)>;

#[derive(IntoElement)]
pub struct Button {
    id: SharedString,
    label: Option<SharedString>,
    icon: Option<Symbol>,
    emphasis: Emphasis,
    disabled: bool,
    handler: Option<Handler>,
    /// What the glyph and the label become while the button is reporting, and
    /// whether it is reporting now. A button that answers a press by changing
    /// what it says is the one case where its contents move.
    reporting_icon: Option<Symbol>,
    reporting_label: Option<SharedString>,
    reporting: bool,
}

impl Button {
    pub fn new(id: impl Into<SharedString>) -> Self {
        Self {
            id: id.into(),
            label: None,
            icon: None,
            emphasis: Emphasis::Outline,
            disabled: false,
            handler: None,
            reporting_icon: None,
            reporting_label: None,
            reporting: false,
        }
    }

    pub fn label(mut self, label: impl Into<SharedString>) -> Self {
        self.label = Some(label.into());
        self
    }

    pub fn icon(mut self, icon: impl Into<Symbol>) -> Self {
        self.icon = Some(icon.into());
        self
    }

    pub fn primary(mut self) -> Self {
        self.emphasis = Emphasis::Primary;
        self
    }

    pub fn ghost(mut self) -> Self {
        self.emphasis = Emphasis::Ghost;
        self
    }

    pub fn disabled(mut self, disabled: bool) -> Self {
        self.disabled = disabled;
        self
    }

    /// The glyph this button shows while it is reporting a result, swapped in
    /// over [`motion::SWAP`] rather than cut to.
    pub fn reports_icon(mut self, icon: impl Into<Symbol>, reporting: bool) -> Self {
        self.reporting_icon = Some(icon.into());
        self.reporting = self.reporting || reporting;
        self
    }

    /// The words this button shows while it is reporting a result. The resting
    /// label holds the width, so the button does not resize and the controls
    /// beside it stay put.
    pub fn reports_label(mut self, label: impl Into<SharedString>, reporting: bool) -> Self {
        self.reporting_label = Some(label.into());
        self.reporting = self.reporting || reporting;
        self
    }

    pub fn on_click(mut self, handler: impl Fn(&mut Window, &mut App) + 'static) -> Self {
        self.handler = Some(Rc::new(handler));
        self
    }

    /// Dispatch `action` when the button is pressed.
    ///
    /// The form most buttons here take: naming the action rather than closing
    /// over the work keeps the same behaviour reachable from the keymap and the
    /// tray, so a screen and a menu item cannot drift apart.
    pub fn action(self, action: impl gpui::Action) -> Self {
        let action = Box::new(action);
        self.on_click(move |window, cx| {
            window.dispatch_action(action.boxed_clone(), cx);
        })
    }

    /// Whether this button has room for a label, or is a bare glyph in a
    /// square. A square button is padded to its own height so the glyph sits on
    /// both centre lines rather than on the horizontal one alone.
    fn is_square(&self) -> bool {
        self.label.is_none()
    }
}

impl RenderOnce for Button {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let theme = cx.inari().clone();
        let fade_key = SharedString::from(format!("button:{}", self.id));
        // A disabled control does not answer the pointer, so it must not carry
        // a stale wash from before it was turned off either.
        let hover = if self.disabled { 0.0 } else { motion::fade_fraction(fade_key.clone()) };
        let paint = Paint::resolve(self.emphasis, &theme, hover);
        let square = self.is_square();
        let handler = self.handler;
        let ring = theme.focus_ring;

        div()
            .id(self.id.clone())
            // Positioned, so the catch-light resolves against this button's own
            // box rather than against whichever ancestor happens to be.
            .relative()
            .flex()
            .flex_none()
            .items_center()
            .justify_center()
            .gap(px(Theme::SPACE_SM - 1.0))
            .h(px(HEIGHT))
            .when(square, |button| button.w(px(HEIGHT)))
            .when(!square, |button| button.px(px(Theme::SPACE_MD)))
            .rounded(px(Theme::RADIUS_CONTROL))
            .bg(paint.fill)
            // Always bordered, transparent where the variant wants no edge, so
            // the focus ring cannot change the button's size when it appears.
            .border_1()
            .border_color(paint.border)
            .text_color(paint.text)
            .children(catch_light(self.emphasis, &theme))
            .when(self.disabled, |button| button.opacity(0.45))
            .when(!self.disabled, |button| {
                button
                    .cursor_pointer()
                    .focusable()
                    .tab_stop(true)
                    .when(focus::visible(), |button| {
                        button.focus(move |style| style.border_color(ring))
                    })
                    .on_hover(move |hovered, window, _| {
                        if motion::hover_set(fade_key.clone(), *hovered) {
                            // Refresh, never request_animation_frame: this runs
                            // in event dispatch, where that call panics.
                            window.refresh();
                        }
                    })
                    .active(move |button| button.bg(paint.pressed))
                    .when_some(handler, |button, handler| {
                        let pressed = handler.clone();
                        button
                            .on_click(move |_, window, cx| pressed(window, cx))
                            .on_key_down(move |event: &KeyDownEvent, window, cx| {
                                if is_activation(event) {
                                    handler(window, cx);
                                    cx.stop_propagation();
                                }
                            })
                    })
            })
            .children(self.icon.map(|icon| {
                match self.reporting_icon {
                    Some(reporting) => swap::icon(
                        SharedString::from(format!("swap-icon:{}", self.id)),
                        icon,
                        reporting,
                        self.reporting,
                    )
                    .size(ICON)
                    .tones(paint.text, paint.text)
                    .into_any_element(),
                    None => Icon::from(icon)
                        .size(px(ICON))
                        .flex_none()
                        .into_any_element(),
                }
            }))
            .children(self.label.map(|label| {
                match self.reporting_label {
                    Some(reporting) => swap::label(
                        SharedString::from(format!("swap-label:{}", self.id)),
                        label,
                        reporting,
                        self.reporting,
                    )
                    .into_any_element(),
                    None => div()
                        .text_body()
                        .font_weight(gpui::FontWeight::MEDIUM)
                        .child(label)
                        .into_any_element(),
                }
            }))
    }
}

/// The four colours one variant paints with at one moment in its hover fade.
#[derive(Clone, Copy)]
struct Paint {
    fill: Hsla,
    border: Hsla,
    text: Hsla,
    pressed: Hsla,
}

impl Paint {
    /// Resolve a variant at `hover`, the eased 0..1 fraction of the way the
    /// pointer has arrived.
    ///
    /// Primary moves between two solid vermilions, so it interpolates. The
    /// other two move between a wash's absence and its presence, so they scale
    /// one colour's alpha instead — premultiplied by construction, which is
    /// what keeps a wash rising out of transparency from passing through grey.
    fn resolve(emphasis: Emphasis, theme: &Theme, hover: f32) -> Self {
        match emphasis {
            Emphasis::Primary => Self {
                fill: mix(theme.accent_fill, theme.accent_fill_hover, hover),
                border: gpui::transparent_black(),
                text: theme.text_on_accent,
                pressed: theme.accent_fill_active,
            },
            Emphasis::Outline => Self {
                fill: flatten(
                    Hsla { a: theme.wash_hover.a * hover, ..theme.wash_hover },
                    theme.surface_raised,
                ),
                border: theme.hairline,
                // The label warms to full strength as the pointer arrives, so
                // the button answers with more than its plate.
                text: mix(theme.text_secondary, theme.text, hover),
                pressed: flatten(theme.wash_pressed, theme.surface_raised),
            },
            Emphasis::Ghost => Self {
                fill: Hsla { a: theme.wash_hover.a * hover, ..theme.wash_hover },
                border: gpui::transparent_black(),
                text: mix(theme.text_secondary, theme.text, hover),
                pressed: theme.wash_pressed,
            },
        }
    }
}

/// A pixel of light along the top lip, the way every raised surface in the app
/// catches it.
///
/// A drawn child rather than a shadow: an inset `BoxShadow` under a fill this
/// thin paints the light onto almost nothing. Dark appearances only — on a
/// light palette the plate is already the brightest thing near it, and white on
/// its top edge reads as a seam instead of as lift.
fn catch_light(emphasis: Emphasis, theme: &Theme) -> Option<gpui::Div> {
    let alpha = match emphasis {
        // The vermilion plate is dark enough to take a firmer highlight than
        // the neutral surfaces do before the light reads as a drawn line.
        Emphasis::Primary => 0.16,
        Emphasis::Outline => 0.06,
        // A ghost has no plate for light to land on.
        Emphasis::Ghost => return None,
    };
    theme.is_dark().then(|| {
        div()
            .absolute()
            .top_0()
            .left(px(Theme::RADIUS_CONTROL))
            .right(px(Theme::RADIUS_CONTROL))
            .h(px(1.0))
            .bg(Hsla { a: alpha, ..gpui::white() })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ui::material::Material;

    fn dark() -> Theme {
        Theme::dark(Material::Glass)
    }

    #[test]
    fn a_hover_only_ever_moves_the_fill() {
        // The geometry a button paints must be identical at rest and under the
        // pointer: a control that changes size is a control that moves away
        // from the click aimed at it.
        for emphasis in [Emphasis::Primary, Emphasis::Outline, Emphasis::Ghost] {
            let rest = Paint::resolve(emphasis, &dark(), 0.0);
            let hovered = Paint::resolve(emphasis, &dark(), 1.0);
            assert_ne!(rest.fill, hovered.fill, "{emphasis:?} does not answer the pointer");
            assert_eq!(rest.border, hovered.border, "{emphasis:?} moved its edge");
        }
    }

    #[test]
    fn a_wash_rises_out_of_transparency_rather_than_out_of_grey() {
        // Only the alpha climbs, so the blend is premultiplied by construction
        // and never passes through a grey midpoint.
        let theme = dark();
        let rest = Paint::resolve(Emphasis::Ghost, &theme, 0.0);
        let half = Paint::resolve(Emphasis::Ghost, &theme, 0.5);
        assert_eq!(rest.fill.a, 0.0);
        assert_eq!(half.fill.l, rest.fill.l);
        assert!(half.fill.a > 0.0 && half.fill.a < theme.wash_hover.a);
    }

    #[test]
    fn a_ghost_carries_no_chrome_until_it_is_pointed_at() {
        let theme = dark();
        let rest = Paint::resolve(Emphasis::Ghost, &theme, 0.0);
        assert_eq!(rest.fill.a, 0.0);
        assert_eq!(rest.border.a, 0.0);
        assert!(catch_light(Emphasis::Ghost, &theme).is_none());
    }

    #[test]
    fn only_a_labelled_button_is_wider_than_it_is_tall() {
        assert!(Button::new("id").is_square());
        assert!(
            !Button::new("id")
                .label("Open logs")
                .is_square()
        );
    }
}

#[cfg(debug_assertions)]
impl crate::dev::Choice for Emphasis {
    const VARIANTS: &'static [(Self, &'static str)] = &[
        (Self::Primary, "Primary"),
        (Self::Outline, "Outline"),
        (Self::Ghost, "Ghost"),
    ];
}

crate::story! {
    id: "control.button",
    name: "Button",
    scope: crate::dev::Scope::Controls,
    about: "One button under every knob, and then all three emphases at once.",
    render: |dial, _window, _cx| {
        use gpui::{ParentElement as _, Styled as _};
        use gpui_component::StyledExt as _;

        let emphasis = dial.pick("Emphasis", Emphasis::Primary);
        let label = dial.text("Label", "Copy all details");
        let icon = dial.flag("Icon", true);
        let disabled = dial.flag("Disabled", false);
        dial.group("Reporting");
        // The swap is a transition, so a still frame cannot show it. The button
        // holds the reported state for as long as the flag is on, which is the
        // only way to judge the resting half of a two-state control.
        let reporting = dial.flag("Reported", false);

        let build = |emphasis: Emphasis| {
            let mut button = Button::new(SharedString::from(format!("story-{emphasis:?}")))
                .label(label.clone())
                .disabled(disabled)
                .reports_label("Copied", reporting);
            if icon {
                button = button
                    .icon(gpui_component::IconName::Copy)
                    .reports_icon(gpui_component::IconName::Check, reporting);
            }
            match emphasis {
                Emphasis::Primary => button.primary(),
                Emphasis::Outline => button,
                Emphasis::Ghost => button.ghost(),
            }
        };

        gpui::div()
            .v_flex()
            .gap(gpui::px(24.0))
            .child(build(emphasis))
            .child(
                gpui::div()
                    .h_flex()
                    .gap(gpui::px(12.0))
                    .child(build(Emphasis::Primary))
                    .child(build(Emphasis::Outline))
                    .child(build(Emphasis::Ghost)),
            )
            .into_any_element()
    },
}
