//! Focus-visible policy.
//!
//! A focus ring belongs to keyboard navigation. Drawn on every click as well,
//! it becomes an outline that latches onto whatever the operator last touched
//! and stays there — noise on a surface meant to be calm, and the reason a ring
//! reads as wrong even when its colour is right.
//!
//! GPUI 0.2.2 has no `:focus-visible` equivalent, so the mode is tracked here:
//! a navigation key turns it on, a mouse press turns it off. Keyboard users
//! keep a visible ring at all times; mouse users never see one.

use std::sync::atomic::{AtomicBool, Ordering};

use gpui::KeyDownEvent;

static KEYBOARD: AtomicBool = AtomicBool::new(false);

/// Whether focus rings should paint right now.
pub fn visible() -> bool {
    KEYBOARD.load(Ordering::Relaxed)
}

/// Record how the operator is driving the app. Returns whether the mode
/// changed, so the caller repaints only when a ring appears or disappears.
pub fn set_keyboard(keyboard: bool) -> bool {
    KEYBOARD.swap(keyboard, Ordering::Relaxed) != keyboard
}

/// Whether a key moves focus, and so should reveal the rings.
///
/// Activation keys are deliberately absent: Enter on a control the operator
/// just clicked should run it without lighting up a ring they never asked for.
pub fn is_navigation(event: &KeyDownEvent) -> bool {
    matches!(
        event.keystroke.key.as_str(),
        "tab" | "up" | "down" | "left" | "right" | "home" | "end" | "pageup" | "pagedown"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_mode_reports_only_real_changes() {
        set_keyboard(false);
        assert!(set_keyboard(true));
        assert!(!set_keyboard(true));
        assert!(set_keyboard(false));
        assert!(!visible());
    }

    #[test]
    fn activation_keys_do_not_reveal_rings() {
        for key in ["enter", "space", "escape", "a"] {
            let event = KeyDownEvent {
                keystroke: gpui::Keystroke {
                    modifiers: gpui::Modifiers::default(),
                    key: key.into(),
                    key_char: None,
                },
                is_held: false,
            };
            assert!(!is_navigation(&event), "{key} should not reveal focus rings");
        }
    }
}
