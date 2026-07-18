use gpui::{App, Hsla};
use gpui_component::{ActiveTheme as _, Colorize as _, Theme};

#[derive(Clone, Copy)]
pub struct Palette {
    pub canvas: Hsla,
    pub sidebar: Hsla,
    pub surface: Hsla,
    pub surface_raised: Hsla,
    pub border: Hsla,
    pub text: Hsla,
    pub text_muted: Hsla,
    pub vermilion: Hsla,
    pub green: Hsla,
    pub blue: Hsla,
    pub blue_wash: Hsla,
    pub blue_text: Hsla,
}

impl Palette {
    pub fn current(cx: &App) -> Self {
        let theme = cx.theme();
        let vermilion = brand_vermilion(theme.is_dark());
        Self {
            canvas: theme.muted.mix(theme.background, 0.55),
            sidebar: theme.sidebar,
            surface: theme.background,
            surface_raised: theme.secondary,
            border: theme.border,
            text: theme.foreground,
            text_muted: theme.muted_foreground,
            vermilion,
            green: theme.success,
            blue: theme.info,
            blue_wash: theme.background.mix(theme.info, 0.88),
            blue_text: if theme.is_dark() {
                theme.info
            } else {
                Hsla { h: 0.579_889_8, s: 0.733_333_3, l: 0.323_529_4, a: 1.0 }
            },
        }
    }
}

pub fn apply_brand(cx: &mut App) {
    let theme = Theme::global_mut(cx);
    let vermilion = brand_vermilion(theme.is_dark());
    theme.primary = vermilion;
    theme.primary_hover = Hsla { l: (vermilion.l + 0.05).min(1.0), ..vermilion };
    theme.primary_active = Hsla { l: (vermilion.l - 0.04).max(0.0), ..vermilion };
    theme.primary_foreground = if theme.is_dark() {
        Hsla { h: 0.333_333_3, s: 0.0625, l: 0.062_745_1, a: 1.0 }
    } else {
        Hsla { h: 0.0, s: 0.0, l: 1.0, a: 1.0 }
    };
}

fn brand_vermilion(dark: bool) -> Hsla {
    if dark {
        Hsla { h: 0.021_978, s: 0.92, l: 0.676_470_6, a: 1.0 }
    } else {
        Hsla { h: 0.015_873, s: 0.676_470_6, l: 0.388_235_3, a: 1.0 }
    }
}
