//! The development environment: a component Bench, and devtools for the running
//! application.
//!
//! Two systems, one set of parts. See `docs/device-center-dev-environment.md`
//! for the research behind the shape; the short version:
//!
//! - GPUI already carries a complete element inspector — picking, ancestor
//!   walking, source locations, per-element state — and `gpui_component::init`
//!   already binds it in this application. Nothing here rebuilds that. The
//!   panel replaces its *renderer* and hosts its style editors inside ours.
//! - A story registers itself from the file that owns its component, through
//!   `inventory`. There is no central list to keep in step.
//! - A story reads its live parameters from a [`dial::Dial`] at the point of
//!   use, so the panel shows exactly the knobs this frame's render asked for.
//!
//! Compiled only under `debug_assertions`; a release build carries none of it.

pub mod bench;
pub mod bubble;
pub mod control;
pub mod dial;
pub mod element;
pub mod frames;
pub mod panel;
pub mod story;
mod stories;

use gpui::{
    AnyElement, App, AppContext as _, Global, IntoElement as _, KeyBinding, Window, WindowOptions,
    actions, point, px, size,
};
use gpui_component::Root;

use crate::{
    dev::panel::Tool,
    ui::{material, theme::Theme},
};

pub use dial::Choice;
pub use stories::{note_center, note_onboarding};
pub use story::Scope;

actions!(dev, [ToggleBench, ToggleTools, PickElement]);

/// The window the Bench lives in, if it is open.
struct BenchWindow(gpui::AnyWindowHandle);

impl Global for BenchWindow {}

/// Bind the shortcuts and install the panel. Called from `main` on debug
/// builds, after `gpui_component::init` — the inspector renderer is
/// last-writer-wins, and ours hosts theirs.
pub fn init(cx: &mut App) {
    stories::init(cx);
    panel::install(cx);
    cx.bind_keys([
        KeyBinding::new("cmd-alt-d", ToggleBench, None),
        KeyBinding::new("ctrl-alt-d", ToggleBench, None),
        KeyBinding::new("cmd-alt-i", ToggleTools, None),
        KeyBinding::new("ctrl-alt-i", ToggleTools, None),
        KeyBinding::new("down", bench::NextStory, Some(bench::KEY_CONTEXT)),
        KeyBinding::new("up", bench::PreviousStory, Some(bench::KEY_CONTEXT)),
        KeyBinding::new("cmd-f", bench::FocusFilter, Some(bench::KEY_CONTEXT)),
        KeyBinding::new("ctrl-f", bench::FocusFilter, Some(bench::KEY_CONTEXT)),
    ]);
    cx.on_action(|_: &ToggleBench, cx| open_bench(cx));
    cx.on_action(|_: &ToggleTools, cx| {
        let Some(active) = cx.active_window() else {
            return;
        };
        // Deferred because the window is already leased by the dispatch that
        // brought us here.
        cx.defer(move |cx| {
            active
                .update(cx, |_, window, cx| {
                    panel::show(panel::deck(cx).tool, window, cx);
                })
                .ok();
        });
    });
}

/// The floating layer, mounted once at each window root.
///
/// This is the single integration point: it records the render for the Frames
/// tool, reports whether the dock is open, applies GPUI's `DebugBelow` global
/// for the outline-everything toggle, and returns the launcher and overlay.
/// Ordinary components carry no debugging code at all.
pub fn attach(window: &mut Window, cx: &mut App) -> AnyElement {
    frames::tick(cx);
    panel::observe(window, cx);

    // One global, and every div in the window outlines itself
    // (`gpui/src/style.rs:612-618`). Nothing else has to know.
    //
    // `remove_global` panics on a global that was never added, so the check is
    // load-bearing rather than defensive: the first frame of every run reaches
    // here with the toggle off and nothing set.
    if panel::deck(cx).outline_all {
        cx.set_global(gpui::DebugBelow);
    } else if cx.has_global::<gpui::DebugBelow>() {
        cx.remove_global::<gpui::DebugBelow>();
    }

    // While the picker is armed GPUI gives *every* div a hitbox
    // (`elements/div.rs:1711`), and this layer is `deferred`, so its hitbox
    // would sit on top of the whole window and be the only thing anyone could
    // ever pick. Standing down for the duration is both the fix and the right
    // behaviour: picking is not a moment when the launcher is wanted.
    if window.is_inspector_picking(cx) {
        return gpui::Empty.into_any_element();
    }

    bubble::render(window, cx)
}

/// Open the Bench, or raise it when it is already open.
fn open_bench(cx: &mut App) {
    if let Some(existing) = cx.try_global::<BenchWindow>() {
        // A closed window leaves its handle behind — the global outlives the
        // window it names, and `update` is what discovers that. Falling through
        // opens a fresh one, and the `set_global` below replaces the stale
        // handle.
        let raised = existing
            .0
            .update(cx, |_, window, _| window.activate_window())
            .is_ok();
        if raised {
            return;
        }
    }

    let (width, height) = bench::WINDOW_SIZE;
    let bounds = gpui::Bounds::centered(None, size(px(width), px(height)), cx);
    let handle = cx
        .open_window(
            WindowOptions {
                window_bounds: Some(gpui::WindowBounds::Windowed(bounds)),
                window_min_size: Some(size(px(900.0), px(600.0))),
                titlebar: Some(gpui::TitlebarOptions {
                    title: Some("Inari Bench".into()),
                    appears_transparent: true,
                    traffic_light_position: Some(point(
                        px(20.0),
                        px((Theme::TITLEBAR_HEIGHT - 12.0) / 2.0),
                    )),
                }),
                window_background: material::resolve().window_background(),
                app_id: Some("dev.inari.device-center".into()),
                show: false,
                ..WindowOptions::default()
            },
            |window, cx| {
                Theme::sync(window, cx);
                let bench = cx.new(|cx| bench::Bench::new(window, cx));
                // The Bench opens with the panel already docked: knobs are the
                // primary control here, not an extra someone has to find.
                panel::show(Tool::Knobs, window, cx);
                cx.new(|cx| Root::new(bench, window, cx))
            },
        )
        .expect("failed to open the Bench");
    handle
        .update(cx, |_, window, cx| {
            crate::infrastructure::platform::show_window(window, cx);
        })
        .ok();
    cx.set_global(BenchWindow(handle.into()));
}
