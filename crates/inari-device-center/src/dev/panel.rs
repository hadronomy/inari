//! The docked tool surface.
//!
//! GPUI reserves a fixed 30rem strip on the right of any window that has an
//! inspector and prepaints whatever `App::set_inspector_renderer` returns into
//! it (`window.rs:2019-2031`, `window.rs:4582-4596`). That strip is the panel.
//!
//! Hosting the panel there rather than floating it costs one constraint — the
//! width is not ours — and removes three problems: the panel never occludes the
//! surfaces being judged, it needs no drag or z-order model of its own, and the
//! inspector's own picking mode is already wired to it. Browser devtools dock
//! for the same reasons.
//!
//! The Bench and the application window get the same panel and the same tools.
//! Only what the tools have to say differs.

use std::{cell::OnceCell, collections::HashMap, collections::HashSet, time::Duration};

/// One 60Hz frame, the floor the sparkline scales against so an idle window
/// does not draw its two lazy renders as a full-height wall.
const SIXTY_HZ: Duration = Duration::from_millis(16);
/// How many of the most recent gaps the sparkline draws.
const SPARKLINE: usize = 60;

use gpui::{
    AnyElement, App, AppContext as _, BorrowAppContext as _, Context, DivInspectorState, Entity,
    Global, InteractiveElement as _, IntoElement, ParentElement as _, SharedString,
    StatefulInteractiveElement as _, Styled as _, Subscription, Window, WindowId, div,
    prelude::FluentBuilder as _, px,
};
use gpui_component::{
    IconName, Selectable as _, Sizable as _, StyledExt as _,
    button::{Button, ButtonVariants as _},
    input::{InputEvent, InputState},
    switch::Switch,
};

use crate::{
    dev::{
        control,
        dial::{self, Kind, Knob, Value},
        element, frames,
    },
    ui::{
        content::Typography as _,
        material,
        motion,
        theme::{ActiveTheme as _, Appearance, Theme},
    },
};

/// One screen of the panel.
///
/// A screen has something to read. That is the whole membership test, and it is
/// why picking an element and outlining every div are not screens: they change
/// how the *window* behaves and have nothing of their own to show. Giving them
/// tabs cost a click on the way in and left a screen that said nothing on the
/// way out — the selection went one place and its report went another.
///
/// Modes live on the bar beside the tabs, and picking hands the panel straight
/// to [`Screen::Element`], which is the only screen that has anything to say
/// about what was picked.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum Screen {
    /// The current story's live parameters. Empty outside the Bench.
    #[default]
    Knobs,
    /// The selected element: geometry, the box model, and the live style
    /// editors.
    Element,
    /// How often the window is rebuilding itself.
    Frames,
    /// Appearance, material, motion, and the size the stage is judged at.
    Stage,
}

impl Screen {
    pub const ALL: [Self; 4] = [Self::Knobs, Self::Element, Self::Frames, Self::Stage];

    pub fn title(self) -> &'static str {
        match self {
            Self::Knobs => "Knobs",
            Self::Element => "Element",
            Self::Frames => "Frames",
            Self::Stage => "Stage",
        }
    }

    pub fn icon(self) -> IconName {
        match self {
            Self::Knobs => IconName::Settings2,
            Self::Element => IconName::Inspector,
            Self::Frames => IconName::ChartPie,
            Self::Stage => IconName::Palette,
        }
    }
}

/// What the panel shows and what the floating layer draws. One value, read by
/// both surfaces, so a toggle flipped in the panel shows up in the overlay
/// without either knowing about the other.
#[derive(Clone, Copy, Debug, Default)]
pub struct Deck {
    pub screen: Screen,
    /// GPUI's own `DebugBelow`: a red outline around every div, with no
    /// instrumentation in any component. A mode, not a screen.
    pub outline_all: bool,
    /// The width the stage is held at, or the full stage when `None`.
    pub stage_width: Option<f32>,
    /// Bumped when knobs are reset, so the panel drops the control entities it
    /// caches and rebuilds them at the new values.
    pub generation: usize,
    /// A request to arm the picker, spent by the next panel render.
    ///
    /// The launcher has no way to reach the window's `Inspector`, and opening
    /// the dock arms the picker by itself, so this only carries the case where
    /// the dock was already open.
    pub arm: bool,
}

impl Global for Deck {}

pub fn deck(cx: &App) -> Deck {
    cx.try_global::<Deck>().copied().unwrap_or_default()
}

pub fn adjust(cx: &mut App, change: impl FnOnce(&mut Deck)) {
    let mut deck = deck(cx);
    change(&mut deck);
    cx.set_global(deck);
    cx.refresh_windows();
}

/// Whether GPUI is reserving the strip for us.
///
/// `Window::inspector` is private and there is no public reader, so the panel
/// reports its own existence instead: it raises `drawn` every time GPUI renders
/// it, and the floating layer lowers the flag at the start of each root render.
/// Root prepaints before inspector (`window.rs:2035-2042`), so `open` answers
/// for the previous frame — one frame stale, and self-correcting, which is
/// enough to decide whether a click should open the dock or only switch tools.
/// Per window, because the Bench and the application window each have their
/// own dock and their own answer.
#[derive(Default)]
pub struct Dock {
    drawn: HashSet<WindowId>,
    open: HashSet<WindowId>,
}

impl Global for Dock {}

/// Called from the panel each time GPUI renders it.
fn mark_drawn(window: &Window, cx: &mut App) {
    let id = window.window_handle().window_id();
    if !cx.has_global::<Dock>() {
        cx.set_global(Dock::default());
    }
    cx.update_global(|dock: &mut Dock, _| dock.drawn.insert(id));
}

/// Called from the floating layer at the top of every root render.
pub fn observe(window: &Window, cx: &mut App) {
    let id = window.window_handle().window_id();
    if !cx.has_global::<Dock>() {
        cx.set_global(Dock::default());
    }
    cx.update_global(|dock: &mut Dock, _| {
        if dock.drawn.remove(&id) {
            dock.open.insert(id);
        } else {
            dock.open.remove(&id);
        }
    });
}

pub fn is_open(window: &Window, cx: &App) -> bool {
    let id = window.window_handle().window_id();
    cx.try_global::<Dock>()
        .is_some_and(|dock| dock.open.contains(&id))
}

/// Show `screen`, opening the dock if it is closed.
pub fn show(screen: Screen, window: &mut Window, cx: &mut App) {
    // A second press on the screen already showing closes the dock, so one
    // control both opens and dismisses.
    let dismiss = is_open(window, cx) && deck(cx).screen == screen;
    adjust(cx, |deck| deck.screen = screen);
    if dismiss || !is_open(window, cx) {
        window.toggle_inspector(cx);
    }
}

/// Arm GPUI's picker, and hand the panel to the screen that will have something
/// to say the moment anything is picked.
pub fn pick(inspector: &mut gpui::Inspector, window: &mut Window, cx: &mut App) {
    adjust(cx, |deck| deck.screen = Screen::Element);
    inspector.start_picking();
    window.refresh();
}

/// The same, from somewhere with no `Inspector` to hand — the launcher.
///
/// One press: the dock opens on the Element screen with the picker armed, so
/// the click that lands on something is the same click that shows its report.
pub fn start_pick(window: &mut Window, cx: &mut App) {
    adjust(cx, |deck| {
        deck.screen = Screen::Element;
        deck.arm = true;
    });
    // Opening the dock arms the picker by itself; the flag covers the case
    // where it was already open.
    if !is_open(window, cx) {
        window.toggle_inspector(cx);
    }
}

/// Install the renderer. Must run after `gpui_component::init`, which registers
/// its own; the last writer wins and we host its editors inside ours.
pub fn install(cx: &mut App) {
    let editors = OnceCell::new();
    cx.register_inspector_element(move |id, state: &DivInspectorState, window, cx| {
        // Refreshed every frame, whatever the panel is showing. The overlay
        // draws from this, and a box that is only refreshed while its own
        // screen is open is a box that lies the moment you look away.
        element::remember(&id, state, window, cx);
        if deck(cx).screen != Screen::Element {
            return gpui::Empty.into_any_element();
        }
        let editors = editors
            .get_or_init(|| cx.new(|cx| gpui_component::DivInspector::new(window, cx)));
        editors.update(cx, |div_inspector, cx| {
            div_inspector.update_inspected_element(id.clone(), state.clone(), window, cx);
        });
        element::tool(&id, editors, cx)
    });

    let panel = OnceCell::new();
    cx.set_inspector_renderer(Box::new(move |inspector, window, cx| {
        mark_drawn(window, cx);
        if deck(cx).arm {
            inspector.start_picking();
            adjust(cx, |deck| deck.arm = false);
        }
        let panel = panel.get_or_init(|| cx.new(|_| Panel::default()));
        // Always run, whatever the screen: the closure above is what keeps the
        // selection's geometry current, and it is cheap when nothing is asking
        // for the editors.
        let states = inspector.render_inspector_states(window, cx);
        let states = if deck(cx).screen == Screen::Element { states } else { Vec::new() };
        let picking = inspector.is_picking();
        let body = panel.update(cx, |panel, cx| panel.body(states, window, cx));
        chrome(body, picking, window, cx)
    }));
}

/// The control entities the panel keeps between frames.
///
/// Sliders and text fields own their own drag and caret state, so they have to
/// outlive one render. They are keyed by knob label and dropped whenever the
/// story changes or the knobs are reset — the store stays authoritative.
#[derive(Default)]
struct Panel {
    story: Option<&'static str>,
    generation: usize,
    fields: HashMap<&'static str, Entity<InputState>>,
    subscriptions: Vec<Subscription>,
}

impl Panel {
    /// The selected tool's content. The chrome around it is built by the
    /// renderer, which holds the `Inspector` the pick button needs.
    fn body(
        &mut self,
        states: Vec<AnyElement>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let theme = cx.inari().clone();
        let deck = deck(cx);
        // A slider mid-travel and a press mid-drag both need the next frame.
        if control::animating() {
            window.request_animation_frame();
        }

        match deck.screen {
            Screen::Knobs => self.knobs(window, cx),
            Screen::Element => {
                if states.is_empty() {
                    hint(&theme, "Pick an element to inspect it.")
                } else {
                    div()
                        .v_flex()
                        .gap(px(Theme::SPACE_MD))
                        .children(states)
                        .into_any_element()
                }
            },
            Screen::Frames => frames_tool(&theme, cx),
            Screen::Stage => stage_tool(&theme, deck, cx),
        }
    }

    /// The knobs the last story render recorded, in the order it read them.
    fn knobs(&mut self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let theme = cx.inari().clone();
        let Some(story) = dial::active_story(cx) else {
            return hint(&theme, "Open the Bench to tune a story.");
        };
        let generation = deck(cx).generation;
        if self.story != Some(story) || self.generation != generation {
            self.story = Some(story);
            self.generation = generation;
            self.fields.clear();
            control::forget();
            self.subscriptions.clear();
        }

        let schema = dial::schema(cx);
        if schema.is_empty() {
            return hint(&theme, "This story declares no knobs.");
        }

        let moved = schema.iter().any(Knob::is_moved);
        let rows: Vec<AnyElement> = schema
            .iter()
            .map(|knob| self.row(story, knob, window, cx))
            .collect();

        div()
            .v_flex()
            // DialKit sets its rows four pixels apart. Any more and the panel
            // stops reading as one instrument.
            .gap(px(4.0))
            .w_full()
            .children(rows)
            .child(
                Button::new("dev-knobs-reset")
                    .label("Reset")
                    .ghost()
                    .small()
                    .disabled(!moved)
                    .on_click(move |_, _, cx| {
                        dial::reset(story, cx);
                        adjust(cx, |deck| deck.generation += 1);
                    }),
            )
            .into_any_element()
    }

    fn row(
        &mut self,
        story: &'static str,
        knob: &Knob,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let theme = cx.inari().clone();
        let label = knob.label;
        let key = SharedString::from(format!("{story}/{label}"));

        match &knob.kind {
            Kind::Group => control::heading(&theme, label).into_any_element(),

            Kind::Flag => control::Toggle::new(
                key,
                label,
                matches!(knob.value, Value::Flag(true)),
                move |checked, _, cx| {
                    dial::set(story, label, Value::Flag(checked), cx);
                    cx.refresh_windows();
                },
            )
            .into_any_element(),

            Kind::Range { lo, hi, step } => control::Slider::new(
                key,
                label,
                number(&knob.value),
                *lo..=*hi,
                *step,
                move |value, _, cx| {
                    dial::set(story, label, Value::Number(value), cx);
                    cx.refresh_windows();
                },
            )
            .into_any_element(),

            Kind::Count { lo, hi } => control::labelled(
                &theme,
                label,
                control::stepper(
                    &theme,
                    key,
                    number(&knob.value) as usize,
                    *lo..=*hi,
                    move |value, _, cx| {
                        dial::set(story, label, Value::Number(value as f32), cx);
                        cx.refresh_windows();
                    },
                ),
            )
            .into_any_element(),

            Kind::Text => {
                let state = self.field_state(story, knob, window, cx);
                control::text_row(&theme, key, label, &state).into_any_element()
            },

            Kind::Pick { labels } => {
                let selected = match knob.value {
                    Value::Choice(index) => index,
                    _ => 0,
                };
                control::labelled(
                    &theme,
                    label,
                    control::Segmented::new(
                        key,
                        labels
                            .iter()
                            .map(|name| SharedString::new_static(name))
                            .collect(),
                        selected,
                        move |index, _, cx| {
                            dial::set(story, label, Value::Choice(index), cx);
                            cx.refresh_windows();
                        },
                    ),
                )
                .into_any_element()
            },

            Kind::Press => control::Action::new(key, label, move |_, cx| {
                dial::press(story, label, cx);
                cx.refresh_windows();
            })
            .into_any_element(),
        }
    }

    /// The slider entity for `knob`, built once and kept until the story or the
    /// generation changes.
    fn field_state(
        &mut self,
        story: &'static str,
        knob: &Knob,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Entity<InputState> {
        let label = knob.label;
        if let Some(state) = self.fields.get(label) {
            return state.clone();
        }
        let initial = match &knob.value {
            Value::Text(text) => text.to_string(),
            _ => String::new(),
        };
        let state = cx.new(|cx| InputState::new(window, cx).default_value(initial));
        let focus_key = SharedString::from(format!("{story}/{label}-focus"));
        self.subscriptions.push(cx.subscribe(
            &state,
            move |_, state, event: &InputEvent, cx| match event {
                InputEvent::Change => {
                    let text = state.read(cx).value().to_string();
                    dial::set(story, label, Value::Text(text.into()), cx);
                    cx.refresh_windows();
                },
                // A field cannot see its own focus from inside the row, so the
                // owner reports the flip and the chrome eases off the same
                // clock as every other wash.
                InputEvent::Focus | InputEvent::Blur => {
                    if motion::hover_set(
                        focus_key.clone(),
                        matches!(event, InputEvent::Focus),
                    ) {
                        cx.refresh_windows();
                    }
                },
                _ => {},
            },
        ));
        self.fields.insert(label, state.clone());
        state
    }
}

fn number(value: &Value) -> f32 {
    match value {
        Value::Number(number) => *number,
        _ => 0.0,
    }
}

// ---- panel chrome ----

/// The panel frame: the tool tabs, the picker, and the body under them.
fn chrome(
    body: AnyElement,
    picking: bool,
    _window: &mut Window,
    cx: &mut Context<gpui::Inspector>,
) -> AnyElement {
    let theme = cx.inari().clone();
    let deck = deck(cx);
    let tab = |screen: Screen| {
        Button::new(SharedString::from(format!("dev-screen-{}", screen.title())))
            .icon(screen.icon())
            .ghost()
            .small()
            .selected(screen == deck.screen)
            .tooltip(screen.title())
            .on_click(move |_, _, cx| adjust(cx, |deck| deck.screen = screen))
    };

    let bar = div()
        .h_flex()
        .items_center()
        .justify_between()
        .h(px(Theme::TITLEBAR_HEIGHT))
        .px(px(Theme::SPACE_SM))
        .border_b_1()
        .border_color(theme.hairline)
        .child(
            div()
                .h_flex()
                .gap(px(2.0))
                .children(Screen::ALL.map(tab)),
        )
        .child(
            div()
                .h_flex()
                .gap(px(2.0))
                // Two modes, not two screens. They change how the window
                // behaves and have nothing of their own to read, so they sit on
                // the bar rather than taking a tab that would open onto
                // something empty.
                .child(
                    Button::new("dev-outline")
                        .icon(IconName::Frame)
                        .ghost()
                        .small()
                        .selected(deck.outline_all)
                        .tooltip("Outline every element")
                        .on_click(|_, _, cx: &mut App| {
                            adjust(cx, |deck| deck.outline_all = !deck.outline_all)
                        }),
                )
                .child(
                    Button::new("dev-pick")
                        .icon(IconName::Search)
                        .ghost()
                        .small()
                        .selected(picking)
                        .tooltip("Pick an element — scroll to walk up its ancestors")
                        .on_click(cx.listener(
                            |inspector: &mut gpui::Inspector, _, window, cx| {
                                pick(inspector, window, cx);
                            },
                        )),
                )
                .child(
                    Button::new("dev-close")
                        .icon(IconName::Close)
                        .ghost()
                        .small()
                        .on_click(|_, window: &mut Window, cx: &mut App| {
                            window.toggle_inspector(cx);
                        }),
                ),
        );

    div()
        .size_full()
        .v_flex()
        .bg(theme.chrome)
        .border_l_1()
        .border_color(theme.hairline)
        .font_family(theme.font_sans.clone())
        .text_color(theme.text)
        .child(bar)
        .child(
            div()
                .id("dev-panel-body")
                .flex_1()
                .min_h(px(0.0))
                .overflow_y_scroll()
                .p(px(Theme::SPACE_MD))
                .child(body),
        )
        // A press that leaves the track it started on still belongs to that
        // slider, and the rubber band is travel past the track by definition.
        .children(control::capture_sheet())
        .into_any_element()
}

fn hint(theme: &Theme, message: &'static str) -> AnyElement {
    div()
        .w_full()
        .py(px(Theme::SPACE_LG))
        .text_caption()
        .text_color(theme.text_tertiary)
        .child(message)
        .into_any_element()
}

// ---- the tools that need no state ----

fn frames_tool(theme: &Theme, cx: &App) -> AnyElement {
    let cadence = frames::cadence(cx);
    let gaps = frames::gaps(cx);
    let worst = gaps
        .iter()
        .copied()
        .max()
        .unwrap_or(SIXTY_HZ)
        .max(SIXTY_HZ);

    // The panel is 30rem wide and the history is 120 samples deep, so drawing
    // all of it leaves under a pixel per bar. Half a second of history, drawn
    // wide enough to read, says more than a full second drawn as a smear.
    let shown = gaps.len().saturating_sub(SPARKLINE);
    let bars: Vec<gpui::AnyElement> = gaps[shown..]
        .iter()
        .map(|gap| {
            let share = gap.as_secs_f32() / worst.as_secs_f32();
            div()
                .flex_1()
                .h(px(1.0 + share * 31.0))
                // A gap shorter than a 120Hz frame means something asked for
                // this render before the display could have used the last one.
                .bg(if *gap < Duration::from_millis(9) {
                    theme.warning
                } else {
                    theme.accent
                })
                .into_any_element()
        })
        .collect();

    div()
        .v_flex()
        .gap(px(Theme::SPACE_SM))
        .w_full()
        .child(reading(theme, "Renders per second", &cadence.rate.to_string()))
        .child(reading(theme, "Since the last", &millis(cadence.last)))
        .child(reading(theme, "Longest gap", &millis(cadence.longest)))
        .child(
            div()
                .h_flex()
                .items_end()
                .h(px(32.0))
                .w_full()
                .children(bars),
        )
        .child(div().text_caption().text_color(theme.text_tertiary).child(
            "A tall, even wall means something is asking for every frame. An idle window should \
             be almost flat — GPUI redraws when it is asked to, not on a clock.",
        ))
        .into_any_element()
}

fn stage_tool(theme: &Theme, deck: Deck, cx: &App) -> AnyElement {
    let width = |label: &'static str, value: Option<f32>| {
        let selected = deck.stage_width == value;
        div()
            .id(label)
            .flex_1()
            .h(px(24.0))
            .flex()
            .items_center()
            .justify_center()
            .rounded(px(Theme::RADIUS_CONTROL - 2.0))
            .text_size(px(11.0))
            .when(selected, |chip| chip.bg(theme.surface_overlay).text_color(theme.text))
            .when(!selected, |chip| {
                chip.text_color(theme.text_tertiary)
                    .hover(|style| style.bg(theme.wash_hover))
            })
            .child(label)
            .on_click(move |_, _, cx| adjust(cx, |deck| deck.stage_width = value))
    };

    let appearance = |label: &'static str, pick: Option<Appearance>| {
        div()
            .id(label)
            .flex_1()
            .h(px(24.0))
            .flex()
            .items_center()
            .justify_center()
            .rounded(px(Theme::RADIUS_CONTROL - 2.0))
            .text_size(px(11.0))
            .text_color(theme.text_tertiary)
            .hover(|style| style.bg(theme.wash_hover))
            .child(label)
            .on_click(move |_, window: &mut Window, cx: &mut App| match pick {
                // Pinned against the OS. The next appearance event writes over
                // it, which is right: this is a preview control, not a
                // preference.
                Some(appearance) => {
                    Theme::resolve(appearance, material::resolve()).install(cx);
                    window.set_background_appearance(material::resolve().window_background());
                    cx.refresh_windows();
                },
                None => Theme::sync(window, cx),
            })
    };

    let _ = cx;
    div()
        .v_flex()
        .gap(px(Theme::SPACE_SM))
        .w_full()
        .child(group(theme, "Appearance"))
        .child(
            div()
                .h_flex()
                .gap(px(2.0))
                .p(px(2.0))
                .rounded(px(Theme::RADIUS_CONTROL))
                .bg(theme.surface_raised)
                .child(appearance("Light", Some(Appearance::Light)))
                .child(appearance("Dark", Some(Appearance::Dark)))
                .child(appearance("Follow OS", None)),
        )
        .child(toggle(
            theme,
            "dev-translucent",
            "Translucent window",
            material::resolve().is_glass(),
            |checked, cx| {
                material::set_prefer_opaque(!checked);
                cx.refresh_windows();
            },
        ))
        .child(toggle(
            theme,
            "dev-reduced-motion",
            "Reduced motion",
            motion::reduced(),
            |checked, cx| {
                motion::set_reduced(*checked);
                cx.refresh_windows();
            },
        ))
        .child(group(theme, "Stage width"))
        .child(
            div()
                .h_flex()
                .gap(px(2.0))
                .p(px(2.0))
                .rounded(px(Theme::RADIUS_CONTROL))
                .bg(theme.surface_raised)
                .child(width("Fill", None))
                .child(width("360", Some(360.0)))
                .child(width("560", Some(560.0)))
                .child(width("880", Some(880.0))),
        )
        .into_any_element()
}

fn group(theme: &Theme, title: &'static str) -> impl IntoElement {
    div()
        .w_full()
        .pt(px(Theme::SPACE_SM))
        .text_caption()
        .text_color(theme.text_tertiary)
        .child(title)
}

fn toggle(
    theme: &Theme,
    id: &'static str,
    label: &'static str,
    checked: bool,
    change: impl Fn(&bool, &mut App) + 'static,
) -> impl IntoElement {
    div()
        .h_flex()
        .items_center()
        .justify_between()
        .gap(px(Theme::SPACE_MD))
        .w_full()
        .min_h(px(26.0))
        .child(
            div()
                .text_size(px(12.0))
                .text_color(theme.text_secondary)
                .child(label),
        )
        .child(
            Switch::new(id)
                .checked(checked)
                .on_click(move |checked, _, cx| change(checked, cx)),
        )
}

fn reading(theme: &Theme, label: &'static str, value: &str) -> impl IntoElement {
    div()
        .h_flex()
        .items_baseline()
        .justify_between()
        .gap(px(Theme::SPACE_MD))
        .w_full()
        .child(
            div()
                .text_size(px(12.0))
                .text_color(theme.text_secondary)
                .child(label),
        )
        .child(
            div()
                .text_size(px(12.0))
                .font_family(theme.font_mono.clone())
                .text_color(theme.text)
                .child(value.to_string()),
        )
}

fn millis(duration: Duration) -> String {
    format!("{:.1} ms", duration.as_secs_f32() * 1000.0)
}

#[cfg(test)]
mod tests {
    use gpui_component::IconNamed as _;

    use super::*;
    use crate::assets::BrandAssets;

    /// Every glyph the panel names, whether it is a tab or a control.
    ///
    /// GPUI draws a missing SVG as nothing at all — no warning, no placeholder,
    /// no log line. Two tool tabs shipped blank before this test existed, and
    /// the only way anyone found out was by looking at the pixels.
    const GLYPHS: [IconName; 7] = [
        IconName::Settings2,
        IconName::Inspector,
        IconName::Frame,
        IconName::ChartPie,
        IconName::Palette,
        IconName::Search,
        IconName::Close,
    ];

    #[test]
    fn every_glyph_the_panel_names_is_embedded() {
        for glyph in GLYPHS {
            let path = glyph.path();
            assert!(
                BrandAssets::get(path.as_ref()).is_some(),
                "missing {path}; the panel would draw nothing there"
            );
        }
    }

    #[test]
    fn every_screen_tab_has_an_embedded_glyph() {
        for screen in Screen::ALL {
            let path = screen.icon().path();
            assert!(
                BrandAssets::get(path.as_ref()).is_some(),
                "{} has no embedded glyph at {path}",
                screen.title()
            );
        }
    }

    #[test]
    fn the_stepper_arrows_are_embedded() {
        for glyph in [IconName::Minus, IconName::Plus] {
            let path = glyph.path();
            assert!(BrandAssets::get(path.as_ref()).is_some(), "missing {path}");
        }
    }

    #[test]
    fn no_two_screens_share_a_glyph() {
        let mut paths: Vec<SharedString> = Screen::ALL
            .iter()
            .map(|screen| screen.icon().path())
            .collect();
        paths.sort();
        let count = paths.len();
        paths.dedup();
        assert_eq!(paths.len(), count, "two tabs would be indistinguishable");
    }

    #[test]
    fn a_mode_never_takes_a_tab() {
        // Outlining and picking change how the window behaves and have nothing
        // to read. A tab for either would open onto an empty screen.
        for screen in Screen::ALL {
            assert!(
                !matches!(screen.title(), "Layout" | "Pick"),
                "{} is a mode, not a screen",
                screen.title()
            );
        }
    }
}
