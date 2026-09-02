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

use std::{cell::OnceCell, collections::HashMap, time::Duration};

use gpui::{
    AnyElement, App, AppContext as _, BorrowAppContext as _, Context, DivInspectorState, Entity,
    Global, InteractiveElement as _, IntoElement, ParentElement as _, SharedString,
    StatefulInteractiveElement as _, Styled as _, Subscription, Window, div, prelude::FluentBuilder as _,
    px,
};
use gpui_component::{
    Disableable as _, IconName, Selectable as _, Sizable as _, StyledExt as _,
    button::{Button, ButtonVariants as _},
    input::{Input, InputEvent, InputState},
    slider::{Slider, SliderEvent, SliderState},
    switch::Switch,
};

use crate::{
    dev::{
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

/// One tab of the panel.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum Tool {
    /// The current story's live parameters. Empty outside the Bench.
    #[default]
    Knobs,
    /// The selected element: geometry, the box model, and the live style
    /// editors.
    Element,
    /// Outlines and the box-model overlay.
    Layout,
    /// How often the window is rebuilding itself.
    Frames,
    /// Appearance, material, motion, and the size the stage is judged at.
    Stage,
}

impl Tool {
    pub const ALL: [Self; 5] =
        [Self::Knobs, Self::Element, Self::Layout, Self::Frames, Self::Stage];

    pub fn title(self) -> &'static str {
        match self {
            Self::Knobs => "Knobs",
            Self::Element => "Element",
            Self::Layout => "Layout",
            Self::Frames => "Frames",
            Self::Stage => "Stage",
        }
    }

    pub fn icon(self) -> IconName {
        match self {
            Self::Knobs => IconName::Settings2,
            Self::Element => IconName::Inspector,
            Self::Layout => IconName::Frame,
            Self::Frames => IconName::ChartPie,
            Self::Stage => IconName::Palette,
        }
    }
}

/// What the panel shows and what the floating layer draws. One value, read by
/// both surfaces, so a toggle flipped in the panel shows up in the overlay
/// without either knowing about the other.
#[derive(Clone, Copy, Debug)]
pub struct Deck {
    pub tool: Tool,
    /// GPUI's own `DebugBelow`: a red outline around every div, with no
    /// instrumentation in any component.
    pub outline_all: bool,
    /// The selected element's bounds, border, padding and content box.
    pub box_model: bool,
    /// The width the stage is held at, or the full stage when `None`.
    pub stage_width: Option<f32>,
    /// Bumped when knobs are reset, so the panel drops the control entities it
    /// caches and rebuilds them at the new values.
    pub generation: usize,
}

impl Default for Deck {
    fn default() -> Self {
        Self {
            tool: Tool::default(),
            outline_all: false,
            box_model: true,
            stage_width: None,
            generation: 0,
        }
    }
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
#[derive(Default)]
pub struct Dock {
    drawn: bool,
    open: bool,
}

impl Global for Dock {}

/// Called from the panel each time GPUI renders it.
fn mark_drawn(cx: &mut App) {
    if !cx.has_global::<Dock>() {
        cx.set_global(Dock::default());
    }
    cx.update_global(|dock: &mut Dock, _| dock.drawn = true);
}

/// Called from the floating layer at the top of every root render. Returns
/// whether the dock was open on the previous frame.
pub fn observe(cx: &mut App) -> bool {
    if !cx.has_global::<Dock>() {
        cx.set_global(Dock::default());
    }
    cx.update_global(|dock: &mut Dock, _| {
        dock.open = dock.drawn;
        dock.drawn = false;
        dock.open
    })
}

pub fn is_open(cx: &App) -> bool {
    cx.try_global::<Dock>()
        .map(|dock| dock.open)
        .unwrap_or(false)
}

/// Show `tool`, opening the dock if it is closed.
pub fn show(tool: Tool, window: &mut Window, cx: &mut App) {
    let already = is_open(cx) && deck(cx).tool == tool;
    adjust(cx, |deck| deck.tool = tool);
    // A second press on the tool already showing closes the dock, so one
    // control both opens and dismisses.
    if already || !is_open(cx) {
        window.toggle_inspector(cx);
    }
}

/// Install the renderer. Must run after `gpui_component::init`, which registers
/// its own; the last writer wins and we host its editors inside ours.
pub fn install(cx: &mut App) {
    let editors = OnceCell::new();
    cx.register_inspector_element(move |id, state: &DivInspectorState, window, cx| {
        // Cached whatever the tool is, so the overlay can draw the box model
        // while another tool is on screen.
        element::remember(&id, state, window, cx);
        if deck(cx).tool != Tool::Element {
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
        mark_drawn(cx);
        let panel = panel.get_or_init(|| cx.new(|_| Panel::default()));
        let states = if deck(cx).tool == Tool::Element {
            inspector.render_inspector_states(window, cx)
        } else {
            Vec::new()
        };
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
    sliders: HashMap<&'static str, Entity<SliderState>>,
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

        match deck.tool {
            Tool::Knobs => self.knobs(window, cx),
            Tool::Element => {
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
            Tool::Layout => layout_tool(&theme, deck, cx),
            Tool::Frames => frames_tool(&theme, cx),
            Tool::Stage => stage_tool(&theme, deck, cx),
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
            self.sliders.clear();
            self.fields.clear();
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
            .gap(px(Theme::SPACE_SM))
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

        match &knob.kind {
            Kind::Group => div()
                .w_full()
                .pt(px(Theme::SPACE_MD))
                .pb(px(Theme::SPACE_XS))
                .text_caption()
                .text_color(theme.text_tertiary)
                .child(label)
                .into_any_element(),

            Kind::Flag => {
                let checked = matches!(knob.value, Value::Flag(true));
                field(&theme, knob, Switch::new(label).checked(checked).on_click(
                    move |checked, _, cx| {
                        dial::set(story, label, Value::Flag(*checked), cx);
                        cx.refresh_windows();
                    },
                ))
            },

            Kind::Range { lo, hi, step } => {
                let current = number(&knob.value);
                let state = self.slider(story, knob, *lo, *hi, *step, current, cx);
                stacked(
                    &theme,
                    knob,
                    &format!("{current:.2}"),
                    Slider::new(&state).into_any_element(),
                )
            },

            Kind::Count { lo, hi } => {
                let current = number(&knob.value) as usize;
                let (lo, hi) = (*lo, *hi);
                let step = move |delta: i64| {
                    move |_: &gpui::ClickEvent, _: &mut Window, cx: &mut App| {
                        let next = (current as i64 + delta).clamp(lo as i64, hi as i64);
                        dial::set(story, label, Value::Number(next as f32), cx);
                        cx.refresh_windows();
                    }
                };
                field(
                    &theme,
                    knob,
                    div()
                        .h_flex()
                        .items_center()
                        .gap(px(Theme::SPACE_XS))
                        .child(
                            Button::new(SharedString::from(format!("{label}-down")))
                                .icon(IconName::Minus)
                                .ghost()
                                .xsmall()
                                .disabled(current <= lo)
                                .on_click(step(-1)),
                        )
                        .child(
                            div()
                                .w(px(28.0))
                                .text_size(px(11.0))
                                .font_family(theme.font_mono.clone())
                                .text_color(theme.text)
                                .child(current.to_string()),
                        )
                        .child(
                            Button::new(SharedString::from(format!("{label}-up")))
                                .icon(IconName::Plus)
                                .ghost()
                                .xsmall()
                                .disabled(current >= hi)
                                .on_click(step(1)),
                        ),
                )
            },

            Kind::Text => {
                let state = self.field_state(story, knob, window, cx);
                stacked(&theme, knob, "", Input::new(&state).small().into_any_element())
            },

            Kind::Pick { labels } => {
                let selected = match knob.value {
                    Value::Choice(index) => index,
                    _ => 0,
                };
                let choices: Vec<AnyElement> = labels
                    .iter()
                    .enumerate()
                    .map(|(index, name)| {
                        segment(&theme, label, index, name, index == selected, story)
                    })
                    .collect();
                stacked(
                    &theme,
                    knob,
                    "",
                    div()
                        .h_flex()
                        .gap(px(2.0))
                        .p(px(2.0))
                        .rounded(px(Theme::RADIUS_CONTROL))
                        .bg(theme.surface_raised)
                        .children(choices)
                        .into_any_element(),
                )
            },

            Kind::Press => div()
                .w_full()
                .child(
                    Button::new(label)
                        .label(label)
                        .ghost()
                        .small()
                        .on_click(move |_, _, cx| {
                            dial::press(story, label, cx);
                            cx.refresh_windows();
                        }),
                )
                .into_any_element(),
        }
    }

    fn slider(
        &mut self,
        story: &'static str,
        knob: &Knob,
        lo: f32,
        hi: f32,
        step: f32,
        current: f32,
        cx: &mut Context<Self>,
    ) -> Entity<SliderState> {
        let label = knob.label;
        if let Some(state) = self.sliders.get(label) {
            return state.clone();
        }
        let state = cx.new(|_| {
            SliderState::new()
                .min(lo)
                .max(hi)
                .step(step)
                .default_value(current)
        });
        self.subscriptions.push(cx.subscribe(
            &state,
            move |_, _, event: &SliderEvent, cx| {
                let SliderEvent::Change(value) = event;
                dial::set(story, label, Value::Number(value.start()), cx);
                cx.refresh_windows();
            },
        ));
        self.sliders.insert(label, state.clone());
        state
    }

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
        self.subscriptions.push(cx.subscribe(
            &state,
            move |_, state, event: &InputEvent, cx| {
                if matches!(event, InputEvent::Change) {
                    let text = state.read(cx).value().to_string();
                    dial::set(story, label, Value::Text(text.into()), cx);
                    cx.refresh_windows();
                }
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
    let active = deck(cx).tool;
    let tab = |tool: Tool| {
        Button::new(SharedString::from(format!("dev-tool-{}", tool.title())))
            .icon(tool.icon())
            .ghost()
            .small()
            .selected(tool == active)
            .tooltip(tool.title())
            .on_click(move |_, _, cx| adjust(cx, |deck| deck.tool = tool))
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
                .children(Tool::ALL.map(tab)),
        )
        .child(
            div()
                .h_flex()
                .gap(px(2.0))
                .child(
                    Button::new("dev-pick")
                        .icon(IconName::Search)
                        .ghost()
                        .small()
                        .selected(picking)
                        .tooltip("Pick an element — scroll to walk up its ancestors")
                        .on_click(cx.listener(|inspector: &mut gpui::Inspector, _, window, _| {
                            inspector.start_picking();
                            window.refresh();
                        })),
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

/// A knob whose control fits beside its label.
fn field(theme: &Theme, knob: &Knob, control: impl IntoElement) -> AnyElement {
    div()
        .h_flex()
        .items_center()
        .justify_between()
        .gap(px(Theme::SPACE_MD))
        .w_full()
        .min_h(px(26.0))
        .child(name(theme, knob))
        .child(control)
        .into_any_element()
}

/// A knob whose control wants the full width, with an optional readout pinned
/// to the label row.
fn stacked(theme: &Theme, knob: &Knob, readout: &str, control: AnyElement) -> AnyElement {
    div()
        .v_flex()
        .gap(px(Theme::SPACE_XS))
        .w_full()
        .child(
            div()
                .h_flex()
                .items_baseline()
                .justify_between()
                .w_full()
                .child(name(theme, knob))
                .child(
                    div()
                        .text_size(px(11.0))
                        .font_family(theme.font_mono.clone())
                        .text_color(theme.text_tertiary)
                        .child(readout.to_string()),
                ),
        )
        .child(control)
        .into_any_element()
}

/// The label, marked when the knob has been moved off the story's default. A
/// tuning session is only useful if you can see what you have touched.
fn name(theme: &Theme, knob: &Knob) -> impl IntoElement {
    div()
        .h_flex()
        .items_center()
        .gap(px(Theme::SPACE_XS))
        .child(
            div()
                .text_size(px(12.0))
                .text_color(theme.text_secondary)
                .child(knob.label),
        )
        .when(knob.is_moved(), |row| {
            row.child(
                div()
                    .size(px(4.0))
                    .rounded_full()
                    .bg(theme.accent),
            )
        })
}

fn segment(
    theme: &Theme,
    label: &'static str,
    index: usize,
    name: &'static str,
    selected: bool,
    story: &'static str,
) -> AnyElement {
    div()
        .id(SharedString::from(format!("{label}-{index}")))
        .flex_1()
        .h(px(20.0))
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
        .child(name)
        .on_click(move |_, _, cx| {
            dial::set(story, label, Value::Choice(index), cx);
            cx.refresh_windows();
        })
        .into_any_element()
}

// ---- the tools that need no state ----

fn layout_tool(theme: &Theme, deck: Deck, cx: &App) -> AnyElement {
    let selection = cx.try_global::<element::Selection>();
    div()
        .v_flex()
        .gap(px(Theme::SPACE_SM))
        .w_full()
        .child(toggle(
            theme,
            "dev-outline-all",
            "Outline every element",
            deck.outline_all,
            |checked, cx| {
                adjust(cx, |deck| deck.outline_all = *checked);
            },
        ))
        .child(toggle(
            theme,
            "dev-box-model",
            "Box model on the selection",
            deck.box_model,
            |checked, cx| {
                adjust(cx, |deck| deck.box_model = *checked);
            },
        ))
        .when_some(selection, |tool, selection| {
            // Which element the overlay is drawing. Without it the box on
            // screen is a shape with no name, and the answer is one tab away.
            tool.child(
                div()
                    .w_full()
                    .px(px(Theme::SPACE_SM))
                    .py(px(Theme::SPACE_XS))
                    .rounded(px(Theme::RADIUS_CONTROL))
                    .bg(theme.surface_raised)
                    .text_size(px(11.0))
                    .font_family(theme.font_mono.clone())
                    .text_color(theme.text_secondary)
                    .child(format!("{}", selection.id.path.source_location)),
            )
        })
        .child(
            div()
                .text_caption()
                .text_color(theme.text_tertiary)
                .child(
                    "Bounds, border, padding and content box. Margin is reported in the Element \
                     tool but never drawn: GPUI resolves it during layout and no margin rectangle \
                     reaches paint.",
                ),
        )
        .into_any_element()
}

fn frames_tool(theme: &Theme, cx: &App) -> AnyElement {
    let cadence = frames::cadence(cx);
    let gaps = frames::gaps(cx);
    let worst = gaps
        .iter()
        .copied()
        .max()
        .unwrap_or(Duration::from_millis(16))
        .max(Duration::from_millis(16));

    let bars: Vec<gpui::AnyElement> = gaps
        .iter()
        .map(|gap| {
            let share = gap.as_secs_f32() / worst.as_secs_f32();
            div()
                .flex_1()
                .h(px(1.0 + share * 31.0))
                .bg(if *gap < Duration::from_millis(12) {
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
        .child(reading(theme, "Renders in the last second", &cadence.rate.to_string()))
        .child(reading(theme, "Since the previous render", &millis(cadence.last)))
        .child(reading(theme, "Longest gap on record", &millis(cadence.longest)))
        .child(
            div()
                .h_flex()
                .items_end()
                .gap(px(1.0))
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
