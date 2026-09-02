//! The Bench: the catalog on the left, one story on the stage.
//!
//! The Bench is an ordinary window, which is the whole reason the two systems
//! share as much as they do: inspection is a window facility, not an
//! application one, so the panel, the picker, the overlay and the appearance
//! controls all work here without a second implementation.

use gpui::{
    AnyElement, App, AppContext as _, Context, Entity, FocusHandle, Focusable,
    InteractiveElement as _, IntoElement, ParentElement as _, Render, SharedString,
    StatefulInteractiveElement as _, Styled as _, Window, actions, div,
    prelude::FluentBuilder as _, px,
};
use gpui_component::{
    Icon, IconName, StyledExt as _,
    input::InputState,
};

use crate::{
    dev::{
        control,
        dial::Dial,
        panel,
        story::{self, Scope, Story},
    },
    ui::{
        content::Typography as _,
        motion, readout,
        theme::{ActiveTheme as _, Theme},
        titlebar::WindowChrome,
    },
};

actions!(bench, [NextStory, PreviousStory, FocusFilter]);

pub const KEY_CONTEXT: &str = "Bench";

/// The filter's chrome key, and the fade the Bench reports focus against.
const FILTER_KEY: &str = "bench-filter";
const FILTER_FOCUS: &str = "bench-filter-focus";

/// Wide enough that the 30rem panel and a 560px stage both fit without the
/// catalog collapsing.
pub const WINDOW_SIZE: (f32, f32) = (1480.0, 900.0);
const RAIL_WIDTH: f32 = 208.0;

pub struct Bench {
    /// The story id, not its index: filtering moves indices, and a selection
    /// that jumps when you type is worse than no filter at all.
    selected: SharedString,
    filter: Entity<InputState>,
    focus_handle: FocusHandle,
}

impl Bench {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let filter = cx.new(|cx| InputState::new(window, cx).placeholder("Filter stories"));
        cx.subscribe(&filter, |_, _, event: &gpui_component::input::InputEvent, cx| {
            // Focus is reported so the field's chrome can ease, the way the
            // enrollment field's does. A filter is still an input, and an input
            // that does not answer the caret is the one everybody notices.
            if matches!(event, gpui_component::input::InputEvent::Focus)
                || matches!(event, gpui_component::input::InputEvent::Blur)
            {
                let focused =
                    matches!(event, gpui_component::input::InputEvent::Focus);
                if motion::hover_set(FILTER_FOCUS, focused) {
                    cx.refresh_windows();
                }
            }
            cx.notify();
        })
        .detach();
        let focus_handle = cx.focus_handle();
        focus_handle.focus(window);
        let selected = story::catalog()
            .first()
            .map(|story| SharedString::new_static(story.id))
            .unwrap_or_default();
        Self { selected, filter, focus_handle }
    }

    fn matching(&self, cx: &App) -> Vec<&'static Story> {
        let needle = self.filter.read(cx).value().to_lowercase();
        story::catalog()
            .into_iter()
            .filter(|story| {
                needle.is_empty()
                    || story.name.to_lowercase().contains(&needle)
                    || story.id.contains(&needle)
                    || story.scope.title().to_lowercase().contains(&needle)
            })
            .collect()
    }

    fn active(&self, cx: &App) -> Option<&'static Story> {
        let matching = self.matching(cx);
        matching
            .iter()
            .find(|story| story.id == self.selected.as_ref())
            .copied()
            // A filter that hides the selection shows the first thing it kept,
            // rather than an empty stage that looks like a crash.
            .or_else(|| matching.first().copied())
    }

    fn step(&mut self, delta: isize, cx: &mut Context<Self>) {
        let matching = self.matching(cx);
        if matching.is_empty() {
            return;
        }
        let current = matching
            .iter()
            .position(|story| story.id == self.selected.as_ref())
            .unwrap_or(0) as isize;
        let next = (current + delta).rem_euclid(matching.len() as isize) as usize;
        self.selected = SharedString::new_static(matching[next].id);
        cx.notify();
    }

    fn select(&mut self, id: &'static str, cx: &mut Context<Self>) {
        self.selected = SharedString::new_static(id);
        cx.notify();
    }
}

impl Focusable for Bench {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus_handle.clone()
    }
}

impl Render for Bench {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        // The Bench is for judging how the real components feel, so it owes
        // them the frame loop the application's own root keeps. Without it a
        // hover wash never eases here and the Bench reports a stiffness the
        // shipped screen does not have.
        if motion::fades_live() || readout::acknowledgements_live() {
            window.request_animation_frame();
        }

        let theme = cx.inari().clone();
        let active = self.active(cx);
        let stage = match active {
            Some(story) => self.stage(story, window, cx),
            None => div()
                .flex_1()
                .flex()
                .items_center()
                .justify_center()
                .text_caption()
                .text_color(theme.text_tertiary)
                .child("Nothing matches that filter.")
                .into_any_element(),
        };

        div()
            .id("bench")
            .key_context(KEY_CONTEXT)
            .track_focus(&self.focus_handle)
            .on_action(cx.listener(|this, _: &NextStory, _, cx| this.step(1, cx)))
            .on_action(cx.listener(|this, _: &PreviousStory, _, cx| this.step(-1, cx)))
            .on_action(cx.listener(|this, _: &FocusFilter, window, cx| {
                this.filter.focus_handle(cx).focus(window);
            }))
            .size_full()
            .v_flex()
            .font_family(theme.font_sans.clone())
            .text_color(theme.text)
            .child(WindowChrome::new("bench-drag"))
            .child(
                div()
                    .h_flex()
                    .flex_1()
                    .min_h(px(0.0))
                    .w_full()
                    .child(self.catalog(window, cx))
                    .child(stage),
            )
            .child(crate::dev::attach(window, cx))
    }
}

impl Bench {
    fn catalog(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let theme = cx.inari().clone();
        let matching = self.matching(cx);
        let selected = self
            .active(cx)
            .map(|story| story.id)
            .unwrap_or_default();

        let mut list = div()
            .id("bench-catalog")
            .v_flex()
            .flex_1()
            .min_h(px(0.0))
            .overflow_y_scroll()
            .px(px(Theme::SPACE_SM))
            .pb(px(Theme::SPACE_MD));

        for scope in Scope::ALL {
            let stories: Vec<&Story> = matching
                .iter()
                .copied()
                .filter(|story| story.scope == scope)
                .collect();
            if stories.is_empty() {
                continue;
            }
            list = list.child(
                div()
                    .pt(px(Theme::SPACE_MD))
                    .pb(px(Theme::SPACE_XS))
                    .px(px(Theme::SPACE_SM))
                    .text_caption()
                    .text_color(theme.text_tertiary)
                    .child(scope.title()),
            );
            for story in stories {
                let id = story.id;
                let chosen = id == selected;
                list = list.child(
                    div()
                        .id(SharedString::new_static(id))
                        .w_full()
                        .px(px(Theme::SPACE_SM))
                        .py(px(5.0))
                        .rounded(px(Theme::RADIUS_CONTROL))
                        .text_size(px(13.0))
                        .when(chosen, |row| {
                            row.bg(theme.surface_raised).text_color(theme.text)
                        })
                        .when(!chosen, |row| {
                            row.text_color(theme.text_secondary)
                                .hover(|style| style.bg(theme.wash_hover))
                        })
                        .child(story.name)
                        .on_click(cx.listener(move |this, _, _, cx| this.select(id, cx))),
                );
            }
        }

        div()
            .v_flex()
            .w(px(RAIL_WIDTH))
            .flex_none()
            .h_full()
            .bg(theme.chrome)
            .border_r_1()
            .border_color(theme.hairline)
            .child(
                div()
                    .p(px(Theme::SPACE_SM))
                    .child(
                        control::field(&theme, FILTER_KEY.into(), &self.filter)
                            .child(
                                Icon::from(IconName::Search)
                                    .size(px(13.0))
                                    .flex_none()
                                    .text_color(theme.text_tertiary),
                            )
                            .child(control::editor(&theme, &self.filter)),
                    ),
            )
            .child(list)
            .into_any_element()
    }

    fn stage(
        &mut self,
        story: &'static Story,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let theme = cx.inari().clone();
        let width = panel::deck(cx).stage_width;

        // The knob lifecycle. `Window::draw` prepaints the root before the
        // inspector, so the schema this render records is the schema the panel
        // shows in the same frame.
        let mut dial = Dial::begin(story.id, cx);
        let content = (story.render)(&mut dial, window, cx);
        dial.end(cx);

        div()
            .id("bench-stage")
            .v_flex()
            .flex_1()
            .min_w(px(0.0))
            .h_full()
            .overflow_y_scroll()
            .bg(theme.surface)
            .child(
                div()
                    .v_flex()
                    .gap(px(2.0))
                    .px(px(Theme::SPACE_XL))
                    .pt(px(Theme::SPACE_XL))
                    .pb(px(Theme::SPACE_MD))
                    .child(div().text_heading().child(story.name))
                    .child(
                        div()
                            .text_caption()
                            .text_color(theme.text_tertiary)
                            .child(story.about),
                    ),
            )
            .child(
                div()
                    .flex()
                    .justify_center()
                    .px(px(Theme::SPACE_XL))
                    .pb(px(Theme::SPACE_XL))
                    .child(
                        div()
                            .v_flex()
                            .when_some(width, |frame, width| frame.w(px(width)))
                            .when(width.is_none(), |frame| frame.w_full())
                            .p(px(Theme::SPACE_LG))
                            .rounded(px(Theme::RADIUS_PANEL))
                            .border_1()
                            .border_color(theme.hairline)
                            .child(content),
                    ),
            )
            .into_any_element()
    }
}
