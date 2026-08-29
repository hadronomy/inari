//! The technical readout: the facts an administrator asks for, and one press
//! that hands them over.
//!
//! Support exists for a moment that is going badly. Something is broken,
//! somebody is on a call, and the question is always the same: what version,
//! what address, what did it actually say. A screen that only *shows* those
//! answers has done the easy half of the job — the operator still has to
//! retype an endpoint and three lines of error text into a ticket, by hand,
//! out of a window they cannot select from. So every fact here is one press
//! from the clipboard, and the whole set is one more.
//!
//! Three decisions carry the look.
//!
//! **The label is language and the value is machine.** Labels stay in the sans
//! face at the caption size; values are set in the technical face on its own
//! pixel grid. That is the entire distinction the readout exists to draw, and
//! the two faces draw it for free.
//!
//! **The values share one left edge.** Labels sit in a fixed column, so an eye
//! hunting for the endpoint runs down a single ruled edge instead of a ragged
//! one. It is the oldest trick in a printed table and still the reason a
//! readout reads as an instrument rather than as a paragraph.
//!
//! **Nothing is drawn between the rows.** Five hairlines inside one small card
//! is more structure than five facts need, and the label column has already
//! separated them. What marks a row instead is the pointer: the wash arrives,
//! the copy glyph arrives with it, and both leave together.

use std::{
    cell::RefCell,
    collections::HashMap,
    time::{Duration, Instant},
};

use gpui::{
    AnyElement, App, ClipboardItem, InteractiveElement as _, IntoElement, KeyDownEvent,
    ParentElement as _, RenderOnce, SharedString, StatefulInteractiveElement as _, Styled, Window,
    canvas, div, prelude::FluentBuilder as _, px,
};
use gpui_component::{Icon, IconName, StyledExt as _};

use super::{
    button::Button,
    chrome::is_activation,
    content::Typography as _,
    focus, motion,
    surface::card,
    theme::{ActiveTheme as _, Theme},
};

/// The label column.
///
/// Sized to the longest label the app's readouts use at the caption size, with
/// room to spare, and held as a constant rather than measured: a column that
/// hugged the longest label of whichever screen is open would move every value
/// sideways the moment a fact was added.
const LABEL_COLUMN: f32 = 112.0;

/// How long a copy stays acknowledged. Long enough to notice after the eye has
/// moved to the paste target, short enough to be gone before the operator
/// wonders whether the row is stuck.
const ACKNOWLEDGED: Duration = Duration::from_millis(1400);

// ---- the acknowledgement clock ----
//
// A copy answers for a moment and then stops answering, which is per-element
// state with a deadline — the same shape the hover fades have, so it is kept
// the same way: a thread-local store keyed by element, read as a pure function
// of the wall clock, with the owning view asking for frames while any entry is
// still live.
//
// `Window::use_keyed_state` would also hold it, but that reads element state
// from inside `RenderOnce::render` and hands back an entity to observe. A
// deadline read off the clock needs neither, and this way the readout stays a
// plain value with no state of its own to thread through.

thread_local! {
    static COPIES: RefCell<HashMap<SharedString, Instant>> = RefCell::new(HashMap::default());
}

/// Record that `key` just went to the clipboard.
fn acknowledge(key: impl Into<SharedString>) {
    let key = key.into();
    COPIES.with(|copies| {
        let mut copies = copies.borrow_mut();
        // One acknowledgement at a time per readout: two ticks on screen would
        // claim the clipboard holds both values. The key's prefix is the
        // readout's id, so clearing by prefix retires only its own siblings.
        let readout = readout_of(&key);
        copies.retain(|other, _| readout_of(other) != readout);
        copies.insert(key, Instant::now());
    });
}

/// Whether `key`'s copy is still being acknowledged, retiring it once it is not.
fn acknowledged(key: impl Into<SharedString>) -> bool {
    let key = key.into();
    COPIES.with(|copies| {
        let mut copies = copies.borrow_mut();
        let Some(at) = copies.get(&key) else {
            return false;
        };
        if at.elapsed() < ACKNOWLEDGED {
            true
        } else {
            copies.remove(&key);
            false
        }
    })
}

/// Whether any acknowledgement is still running, and so the window owes itself
/// another frame. A readout with nothing copied schedules nothing at all.
pub fn acknowledgements_live() -> bool {
    COPIES.with(|copies| {
        copies
            .borrow()
            .values()
            .any(|at| at.elapsed() < ACKNOWLEDGED)
    })
}

/// The readout id a target key belongs to.
fn readout_of(key: &str) -> &str {
    key.split_once('/')
        .map(|(readout, _)| readout)
        .unwrap_or(key)
}

// ---- the measured height ----
//
// A card holding wrapped text does not report its own height to the section
// above it. GPUI shapes the value once while the section measures — at one
// line — and again at the real width when it paints, at three; the card paints
// correctly, but the section keeps the first answer and the next section on
// the page starts two line-heights too early.
//
// Seven structural variants did not move it: the value off the flex line, the
// row as a column, the label absolutely positioned, the padding moved onto the
// card, the actions lifted out of the card, `flex_none`, `line_clamp`. It is
// not the wrapper and not the face — a plain sans value with no break points
// measures short too.
//
// So the height is taken from the paint pass instead of asked for in the
// measure pass. A canvas inset over the card reports the bounds it was
// actually given, and the wrapper around the card takes that height on the
// next frame — the same instrument Comet uses to hand its composer's measured
// height to the transcript. The card itself stays auto-sized, so a rewrap
// after a resize is measured again rather than clipped to a stale number.

thread_local! {
    static HEIGHTS: RefCell<HashMap<SharedString, f32>> = RefCell::new(HashMap::default());
}

/// The height this readout painted at, once it has painted once.
fn measured(id: &str) -> Option<f32> {
    HEIGHTS.with(|heights| heights.borrow().get(id).copied())
}

/// Record what the card actually measured. Returns whether it changed, so the
/// paint pass asks for one more frame only when the answer is new.
fn record(id: SharedString, height: f32) -> bool {
    HEIGHTS.with(|heights| {
        let mut heights = heights.borrow_mut();
        match heights.get(&id) {
            // Sub-pixel churn is not a change worth a frame.
            Some(known) if (known - height).abs() < 0.5 => false,
            _ => {
                heights.insert(id, height);
                true
            },
        }
    })
}

/// One fact, and how it should be set.
struct Fact {
    label: SharedString,
    value: SharedString,
    /// Whether the value is a sentence that wraps rather than a token that does
    /// not. Prose takes the secondary tone: a three-line error is the longest
    /// thing in the card and the least often the thing being looked for, so it
    /// must not also be the loudest.
    prose: bool,
}

/// A card of machine facts, each one press from the clipboard.
#[derive(IntoElement)]
pub struct Readout {
    id: &'static str,
    facts: Vec<Fact>,
    /// Actions the owning screen adds to the card's footer, after the readout
    /// has placed its own.
    actions: Vec<AnyElement>,
}

pub fn readout(id: &'static str) -> Readout {
    Readout { id, facts: Vec::new(), actions: Vec::new() }
}

impl Readout {
    /// A single-line fact: a version, an address, a path.
    pub fn fact(mut self, label: impl Into<SharedString>, value: impl Into<SharedString>) -> Self {
        self.facts
            .push(Fact { label: label.into(), value: value.into(), prose: false });
        self
    }

    /// A fact that is a sentence, and so wraps.
    pub fn diagnostic(
        mut self,
        label: impl Into<SharedString>,
        value: impl Into<SharedString>,
    ) -> Self {
        self.facts
            .push(Fact { label: label.into(), value: value.into(), prose: true });
        self
    }

    /// An action for the card's footer.
    pub fn action(mut self, action: impl IntoElement) -> Self {
        self.actions
            .push(action.into_any_element());
        self
    }

    /// Every fact as one block of text, for the clipboard.
    ///
    /// Written the way it will be read: a stamp, then a column of values the
    /// same distance in, so a paste into a ticket or a chat window still lines
    /// up. The stamp lives here rather than in a row of its own because a
    /// displayed timestamp is stale the moment it is painted, while a copied
    /// one is true about the copy.
    fn transcript(&self) -> String {
        let width = self
            .facts
            .iter()
            .map(|fact| fact.label.chars().count())
            .max()
            .unwrap_or(0);
        let mut text = format!(
            "Inari Device Center — collected {}\n\n",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S %z")
        );
        for fact in &self.facts {
            // Padded as `&str`, not as the `SharedString` itself: that type's
            // `Display` writes straight through and drops the width, which
            // silently gives every line its own column.
            let label: &str = fact.label.as_ref();
            text.push_str(&format!("{label:width$}  {}\n", fact.value, width = width));
        }
        text
    }
}

impl RenderOnce for Readout {
    fn render(self, _: &mut Window, cx: &mut App) -> impl IntoElement {
        let theme = cx.inari().clone();
        let transcript = self.transcript();
        let rows = self
            .facts
            .iter()
            .enumerate()
            .map(|(index, fact)| row(self.id, index, fact, &theme))
            .collect::<Vec<_>>();

        let id = SharedString::from(self.id);
        let card = card(&theme)
            .w_full()
            .py(px(Theme::SPACE_SM))
            .children(rows)
            .child(
                div()
                    .h_flex()
                    .flex_wrap()
                    .items_center()
                    .gap(px(Theme::SPACE_SM))
                    .w_full()
                    .mt(px(Theme::SPACE_SM))
                    // The footer's own inset plus a button's padding lands its
                    // first glyph on the same edge as the labels above it.
                    .px(px(Theme::SPACE_XS))
                    .pt(px(Theme::SPACE_MD))
                    .pb(px(Theme::SPACE_XS))
                    .border_t_1()
                    .border_color(theme.hairline)
                    .child(copy_all(self.id, transcript))
                    .children(self.actions),
            )
            // Inset over the card, so the bounds it is handed are the card's
            // own padding box: the height the card really painted at.
            .child(
                canvas(|_, _, _| (), {
                    let id = id.clone();
                    move |bounds, _, window, _| {
                        if record(id.clone(), f32::from(bounds.size.height)) {
                            window.refresh();
                        }
                    }
                })
                .absolute()
                .inset_0(),
            );

        // The wrapper is what the section measures. Until the card has painted
        // once there is nothing to tell it, so the first frame sizes to content
        // the way it always did and the second one corrects it.
        div()
            .w_full()
            .when_some(measured(&id), |wrapper, height| wrapper.h(px(height)))
            .child(card)
    }
}

/// One fact, as a row that copies itself.
///
/// The whole row is the target rather than the glyph on its trailing edge. A
/// 16px square at the far end of a card is something you aim at; a row is
/// something you land on, and the glyph is left with the easier job of saying
/// that the row is a control at all.
fn row(id: &'static str, index: usize, fact: &Fact, theme: &Theme) -> AnyElement {
    let key = SharedString::from(format!("{id}/row-{index}"));
    let hover = motion::fade_fraction(key.clone());
    let copied = acknowledged(key.clone());
    let ring = theme.focus_ring;
    let wash = theme.wash_hover;
    let copy = {
        let key = key.clone();
        let value = fact.value.clone();
        move |window: &mut Window, cx: &mut App| {
            cx.write_to_clipboard(ClipboardItem::new_string(value.to_string()));
            acknowledge(key.clone());
            window.refresh();
        }
    };
    let activate = copy.clone();

    div()
        .id(key.clone())
        // Not `h_flex`, which centres: the label of a wrapping diagnostic
        // belongs beside the first line of its value, not halfway down it.
        .flex()
        .flex_row()
        .items_start()
        .gap(px(Theme::SPACE_LG))
        .w_full()
        .px(px(Theme::SPACE_LG))
        .py(px(Theme::SPACE_SM))
        // Square, deliberately. The wash runs the card's full width, so it is
        // a band across the readout rather than a pill sitting on it, and a
        // second radius inside the card's own only reads as a shape that did
        // not quite line up with the one around it.
        // A transparent edge at rest, so the focus ring cannot change the
        // row's height when it appears.
        .border_1()
        .border_color(gpui::transparent_black())
        .cursor_pointer()
        .focusable()
        .tab_stop(true)
        .when(focus::visible(), |row| row.focus(move |style| style.border_color(ring)))
        .on_hover({
            let key = key.clone();
            move |hovered, window, _| {
                if motion::hover_set(key.clone(), *hovered) {
                    // Refresh, never request_animation_frame: this runs in
                    // event dispatch, where that call panics.
                    window.refresh();
                }
            }
        })
        .bg(motion::hover_blend(key, wash))
        .on_click(move |_, window, cx| copy(window, cx))
        .on_key_down(move |event: &KeyDownEvent, window, cx| {
            if is_activation(event) {
                activate(window, cx);
                cx.stop_propagation();
            }
        })
        .child(
            div()
                .flex_none()
                .w(px(LABEL_COLUMN))
                .text_caption()
                .text_color(theme.text_tertiary)
                .child(fact.label.clone()),
        )
        .child(
            div()
                .flex_1()
                .min_w(px(0.0))
                .font_family(theme.font_mono.clone())
                .text_technical()
                .text_color(if fact.prose { theme.text_secondary } else { theme.text })
                .child(breakable(&fact.value)),
        )
        .child(marker(hover, copied, theme))
        .into_any_element()
}

/// The trailing glyph: a copy hint that arrives with the pointer, and a tick
/// that replaces it once the clipboard holds the value.
///
/// The tick ignores the hover fade and paints at full strength. An
/// acknowledgement that dims as the pointer leaves is one the operator can
/// walk away from before it has finished telling them anything.
fn marker(hover: f32, acknowledged: bool, theme: &Theme) -> impl IntoElement {
    div()
        .flex()
        .flex_none()
        .items_center()
        .justify_center()
        .size(px(16.0))
        // On the first line's optical centre, like the label opposite, rather
        // than in the middle of a value that may run to three lines.
        .mt(px(1.0))
        .when(acknowledged, |slot| {
            slot.text_color(theme.success)
                .child(Icon::new(IconName::Check).size(px(13.0)))
        })
        .when(!acknowledged, |slot| {
            slot.opacity(hover)
                .text_color(theme.text_tertiary)
                .child(Icon::new(IconName::Copy).size(px(13.0)))
        })
}

/// The one control that answers the whole question at once.
///
/// The house button in its quietest variant, so the footer reads as one row of
/// controls rather than as a special case beside two ordinary ones.
///
/// The glyph carries the acknowledgement and the label never changes. Swapping
/// the words to "Copied" is the clearer sentence, but it also makes the button
/// two thirds narrower for 1.4 seconds and slides the two controls beside it
/// along with it — a row of buttons that rearranges itself under the pointer
/// that just used it. The rows in this same card already say a copy landed by
/// swapping one glyph, so the footer says it the same way.
fn copy_all(id: &'static str, transcript: String) -> impl IntoElement {
    let key = SharedString::from(format!("{id}/all"));
    let copied = acknowledged(key.clone());
    Button::new(key.clone())
        .ghost()
        .icon(if copied { IconName::Check } else { IconName::Copy })
        .label("Copy all details")
        .on_click(move |window, cx| {
            cx.write_to_clipboard(ClipboardItem::new_string(transcript.clone()));
            acknowledge(key.clone());
            window.refresh();
        })
}

/// Insert zero-width spaces after the punctuation that URLs, endpoints, and
/// paths are built from, so a long value wraps inside its card instead of
/// forcing the card wider than the panel.
///
/// Applied at paint and nowhere else. What the row holds — and so what it
/// copies — stays exactly what the agent said: a URL pasted into a browser
/// with invisible breaks in it does not resolve.
fn breakable(message: &str) -> SharedString {
    let mut wrapped = String::with_capacity(message.len());
    for character in message.chars() {
        wrapped.push(character);
        // Backslash included for Windows paths, which is where the longest
        // value on this screen — the log folder — comes from.
        if matches!(character, '/' | '\\' | ':' | '?' | '&' | '=' | ')' | ']') {
            wrapped.push('\u{200b}');
        }
    }
    wrapped.into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_value_in_the_transcript_starts_on_one_column() {
        let text = readout("t")
            .fact("Agent API", "http://127.0.0.1:7310/")
            .fact("Platform", "windows x86_64")
            .transcript();
        let starts: Vec<_> = ["http", "windows"]
            .iter()
            .map(|needle| {
                text.lines()
                    .find(|line| line.contains(needle))
                    .and_then(|line| line.find(needle))
            })
            .collect();
        assert!(starts[0].is_some(), "{text}");
        assert_eq!(starts[0], starts[1], "{text}");
    }

    #[test]
    fn the_transcript_stamps_when_it_was_collected() {
        // A ticket that arrives without a time is a ticket somebody has to ask
        // about, so the stamp is not optional.
        let text = readout("t")
            .fact("Platform", "windows")
            .transcript();
        assert!(text.starts_with("Inari Device Center — collected 20"), "{text}");
    }

    #[test]
    fn a_copied_value_carries_no_invisible_characters() {
        // The wrap points exist for the paint pass alone.
        let url = "http://127.0.0.1:7310/auth/local-challenge";
        let text = readout("t")
            .diagnostic("Latest error", url)
            .transcript();
        assert!(!text.contains('\u{200b}'));
        assert!(text.contains(url));
        // The painted form is the one that carries them.
        assert!(breakable(url).contains('\u{200b}'));
    }

    #[test]
    fn a_windows_path_can_wrap_inside_its_card() {
        // Backslashes are the only break opportunity a Windows path offers,
        // and the log folder is the longest value the screen shows.
        let path = r"C:\Users\pablo\AppData\Local\Inari\logs";
        assert!(breakable(path).contains('\u{200b}'));
    }

    #[test]
    fn an_acknowledgement_expires_on_its_own_clock() {
        acknowledge("expiry/row-0");
        assert!(acknowledged("expiry/row-0"));
        COPIES.with(|copies| {
            *copies
                .borrow_mut()
                .get_mut("expiry/row-0")
                .unwrap() = Instant::now() - ACKNOWLEDGED;
        });
        assert!(!acknowledged("expiry/row-0"));
        // Reading it retires it, so an idle readout schedules no frames.
        assert!(COPIES.with(|copies| {
            !copies
                .borrow()
                .contains_key("expiry/row-0")
        }));
    }

    #[test]
    fn copying_a_second_row_retires_the_first_tick() {
        // Two ticks in one card would claim the clipboard holds both values.
        acknowledge("card/row-0");
        acknowledge("card/row-1");
        assert!(!acknowledged("card/row-0"));
        assert!(acknowledged("card/row-1"));
    }

    #[test]
    fn one_readout_does_not_retire_another_readouts_tick() {
        acknowledge("first/row-0");
        acknowledge("second/row-0");
        assert!(acknowledged("first/row-0"));
        assert!(acknowledged("second/row-0"));
    }
}
