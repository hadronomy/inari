//! Live parameters, declared where they are read.
//!
//! A story reads its parameters from a [`Dial`]:
//!
//! ```ignore
//! let radius = dial.range("Radius", 12.0, 0.0..=32.0);
//! let disabled = dial.flag("Disabled", false);
//! ```
//!
//! Each call declares the knob, gives it a default, and returns its current
//! value, all at once. The panel then shows the knobs this frame's render
//! actually read, in the order it read them — so a knob cannot drift from its
//! use, deleting the read deletes the knob, and a knob read only inside an `if`
//! appears only when that branch runs.
//!
//! The ordering is exact rather than lucky. `Window::draw` prepaints the root
//! before it prepaints the inspector, so by the time the panel renders, this
//! frame's schema is already recorded.
//!
//! The idea is DialKit's (joshpuckett/dialkit): bind the control to the value at
//! the point of use and let the panel follow. DialKit infers the control from
//! the shape of a JavaScript literal; here the method carries that information,
//! which is both cheaper and harder to get wrong.

use std::{collections::HashMap, ops::RangeInclusive};

use gpui::{App, BorrowAppContext as _, Global, SharedString};

/// A value a knob can hold. Small and `Clone` — a story reads a handful of
/// these per frame.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Flag(bool),
    Number(f32),
    Text(SharedString),
    /// An index into the declaring type's [`Choice::VARIANTS`].
    Choice(usize),
}

/// What control the panel draws. Decided by the method the story called, never
/// by the value.
#[derive(Clone, Debug, PartialEq)]
pub enum Kind {
    Flag,
    Range { lo: f32, hi: f32, step: f32 },
    Count { lo: usize, hi: usize },
    Text,
    Pick { labels: Vec<&'static str> },
    Press,
    /// A heading. Carries no value; splits a long panel into named runs.
    Group,
}

/// One knob, as recorded by the last render.
#[derive(Clone, Debug)]
pub struct Knob {
    pub label: &'static str,
    pub kind: Kind,
    pub value: Value,
    /// What the story passed as the default, so the panel can offer a reset
    /// without the story restating it.
    pub default: Value,
}

impl Knob {
    /// Whether the knob has been moved off what the story asked for.
    pub fn is_moved(&self) -> bool {
        !matches!(self.kind, Kind::Group | Kind::Press) && self.value != self.default
    }
}

/// An enum a knob can pick from.
///
/// One list, beside the type it describes. Deliberately not the shape Zed's
/// `storybook` uses, where the list of things lives away from the things.
pub trait Choice: Copy + PartialEq + 'static {
    const VARIANTS: &'static [(Self, &'static str)];
}

/// Every knob value, keyed by story and label, plus the schema the last render
/// recorded.
#[derive(Default)]
pub struct Store {
    values: HashMap<(&'static str, &'static str), Value>,
    /// The knobs the active story read last frame, in declaration order.
    schema: Vec<Knob>,
    /// Which story owns `schema`. The panel shows nothing when this is not the
    /// story on the stage.
    story: Option<&'static str>,
    /// Armed by the panel, spent by the next render that asks for it.
    pressed: Option<&'static str>,
}

impl Global for Store {}

/// The knobs the last render recorded, or nothing outside the Bench.
pub fn schema(cx: &App) -> Vec<Knob> {
    cx.try_global::<Store>()
        .map(|store| store.schema.clone())
        .unwrap_or_default()
}

/// The story the recorded schema belongs to.
pub fn active_story(cx: &App) -> Option<&'static str> {
    cx.try_global::<Store>().and_then(|store| store.story)
}

/// Move a knob. Called by the panel; a story never writes.
pub fn set(story: &'static str, label: &'static str, value: Value, cx: &mut App) {
    cx.update_global(|store: &mut Store, _| {
        store.values.insert((story, label), value.clone());
        if let Some(knob) = store.schema.iter_mut().find(|knob| knob.label == label) {
            knob.value = value;
        }
    });
}

/// Arm a press. The next render of `story` sees it once.
pub fn press(story: &'static str, label: &'static str, cx: &mut App) {
    cx.update_global(|store: &mut Store, _| {
        if store.story == Some(story) {
            store.pressed = Some(label);
        }
    });
}

/// Put every knob of `story` back to the default its render passed.
pub fn reset(story: &'static str, cx: &mut App) {
    cx.update_global(|store: &mut Store, _| {
        store
            .values
            .retain(|(owner, _), _| *owner != story);
        for knob in &mut store.schema {
            knob.value = knob.default.clone();
        }
    });
}

/// The object a story reads its parameters from.
///
/// Holds a working copy of the values for one render, so a story can read knobs
/// while it also holds `&mut App` to build elements. Reads never touch the
/// global; the panel's writes do.
pub struct Dial {
    story: &'static str,
    values: HashMap<&'static str, Value>,
    schema: Vec<Knob>,
    pressed: Option<&'static str>,
}

impl Dial {
    /// Take a working copy for one render of `story`.
    pub fn begin(story: &'static str, cx: &mut App) -> Self {
        if !cx.has_global::<Store>() {
            cx.set_global(Store::default());
        }
        let store = cx.global::<Store>();
        let values = store
            .values
            .iter()
            .filter(|((owner, _), _)| *owner == story)
            .map(|((_, label), value)| (*label, value.clone()))
            .collect();
        let pressed = store.pressed;
        Self { story, values, schema: Vec::new(), pressed }
    }

    /// Publish the schema this render recorded and spend the armed press.
    pub fn end(self, cx: &mut App) {
        cx.update_global(|store: &mut Store, _| {
            store.schema = self.schema;
            store.story = Some(self.story);
            store.pressed = None;
        });
    }

    /// A heading in the panel. Carries no value.
    pub fn group(&mut self, title: &'static str) {
        self.schema.push(Knob {
            label: title,
            kind: Kind::Group,
            value: Value::Flag(false),
            default: Value::Flag(false),
        });
    }

    /// A switch.
    pub fn flag(&mut self, label: &'static str, default: bool) -> bool {
        let current = match self.values.get(label) {
            Some(Value::Flag(flag)) => *flag,
            _ => default,
        };
        self.record(label, Kind::Flag, Value::Flag(current), Value::Flag(default));
        current
    }

    /// A slider over a continuous span.
    pub fn range(&mut self, label: &'static str, default: f32, span: RangeInclusive<f32>) -> f32 {
        let (lo, hi) = (*span.start(), *span.end());
        let current = match self.values.get(label) {
            Some(Value::Number(number)) => number.clamp(lo, hi),
            _ => default.clamp(lo, hi),
        };
        // A hundred stops across the span: fine enough that dragging feels
        // continuous, coarse enough that the readout does not chase decimals.
        let step = ((hi - lo) / 100.0).max(f32::EPSILON);
        self.record(
            label,
            Kind::Range { lo, hi, step },
            Value::Number(current),
            Value::Number(default),
        );
        current
    }

    /// A whole number, stepped one at a time.
    pub fn count(
        &mut self,
        label: &'static str,
        default: usize,
        span: RangeInclusive<usize>,
    ) -> usize {
        let (lo, hi) = (*span.start(), *span.end());
        let current = match self.values.get(label) {
            Some(Value::Number(number)) => (*number as usize).clamp(lo, hi),
            _ => default.clamp(lo, hi),
        };
        self.record(
            label,
            Kind::Count { lo, hi },
            Value::Number(current as f32),
            Value::Number(default as f32),
        );
        current
    }

    /// A text field. The way to test long, empty, and malformed content without
    /// editing the story.
    pub fn text(&mut self, label: &'static str, default: &'static str) -> SharedString {
        let current = match self.values.get(label) {
            Some(Value::Text(text)) => text.clone(),
            _ => SharedString::new_static(default),
        };
        self.record(
            label,
            Kind::Text,
            Value::Text(current.clone()),
            Value::Text(SharedString::new_static(default)),
        );
        current
    }

    /// A segmented control over an enum.
    pub fn pick<T: Choice>(&mut self, label: &'static str, default: T) -> T {
        let fallback = T::VARIANTS
            .iter()
            .position(|(variant, _)| *variant == default)
            .unwrap_or(0);
        let current = match self.values.get(label) {
            Some(Value::Choice(index)) if *index < T::VARIANTS.len() => *index,
            _ => fallback,
        };
        self.record(
            label,
            Kind::Pick { labels: T::VARIANTS.iter().map(|(_, name)| *name).collect() },
            Value::Choice(current),
            Value::Choice(fallback),
        );
        T::VARIANTS[current].0
    }

    /// A button. True on the one render that follows the press, so a story can
    /// restart a transition or reseed a shader.
    pub fn press(&mut self, label: &'static str) -> bool {
        let fired = self.pressed == Some(label);
        self.record(label, Kind::Press, Value::Flag(fired), Value::Flag(false));
        fired
    }

    fn record(&mut self, label: &'static str, kind: Kind, value: Value, default: Value) {
        debug_assert!(
            !self.schema.iter().any(|knob| knob.label == label),
            "story `{}` reads the knob `{label}` twice; knobs are keyed by label",
            self.story
        );
        self.schema.push(Knob { label, kind, value, default });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone, Copy, Debug, PartialEq)]
    enum Weight {
        Light,
        Heavy,
    }

    impl Choice for Weight {
        const VARIANTS: &'static [(Self, &'static str)] =
            &[(Self::Light, "Light"), (Self::Heavy, "Heavy")];
    }

    fn fresh() -> Dial {
        Dial { story: "test", values: HashMap::new(), schema: Vec::new(), pressed: None }
    }

    #[test]
    fn a_knob_returns_its_default_until_it_is_moved() {
        let mut dial = fresh();
        assert_eq!(dial.range("Radius", 12.0, 0.0..=32.0), 12.0);
        dial.values
            .insert("Radius", Value::Number(20.0));
        let mut dial = Dial { schema: Vec::new(), ..dial };
        assert_eq!(dial.range("Radius", 12.0, 0.0..=32.0), 20.0);
    }

    #[test]
    fn a_moved_knob_is_clamped_to_the_span_the_story_asks_for() {
        let mut dial = fresh();
        dial.values
            .insert("Radius", Value::Number(900.0));
        assert_eq!(dial.range("Radius", 12.0, 0.0..=32.0), 32.0);
    }

    #[test]
    fn the_schema_keeps_the_order_the_story_read_in() {
        let mut dial = fresh();
        dial.flag("Disabled", false);
        dial.group("Shape");
        dial.range("Radius", 12.0, 0.0..=32.0);
        let labels: Vec<&str> = dial
            .schema
            .iter()
            .map(|knob| knob.label)
            .collect();
        assert_eq!(labels, ["Disabled", "Shape", "Radius"]);
    }

    #[test]
    fn a_press_fires_once() {
        let mut dial = Dial { pressed: Some("Replay"), ..fresh() };
        assert!(dial.press("Replay"));
        // `end` clears the arm, so the following render builds with none set.
        let mut next = fresh();
        assert!(!next.press("Replay"));
    }

    #[test]
    fn a_choice_survives_a_variant_being_removed() {
        let mut dial = fresh();
        dial.values
            .insert("Weight", Value::Choice(7));
        assert_eq!(dial.pick("Weight", Weight::Heavy), Weight::Heavy);
    }

    #[test]
    fn a_knob_at_its_default_does_not_read_as_moved() {
        let mut dial = fresh();
        dial.range("Radius", 12.0, 0.0..=32.0);
        assert!(!dial.schema[0].is_moved());
        dial.schema[0].value = Value::Number(20.0);
        assert!(dial.schema[0].is_moved());
    }
}
