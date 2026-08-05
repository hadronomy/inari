use gpui::{App, Hsla, rgb};
use gpui_component::{ActiveTheme as _, Colorize as _, Theme};

#[derive(Clone, Copy)]
pub struct Palette {
    pub canvas: Hsla,
    pub sidebar: Hsla,
    pub surface: Hsla,
    pub surface_raised: Hsla,
    pub separator: Hsla,
    pub border: Hsla,
    pub text: Hsla,
    pub text_muted: Hsla,
    pub vermilion: Hsla,
    pub primary_foreground: Hsla,
    pub success: Hsla,
    pub success_wash: Hsla,
    pub warning: Hsla,
    pub warning_wash: Hsla,
    pub info: Hsla,
    pub info_wash: Hsla,
    pub danger: Hsla,
    pub danger_wash: Hsla,
}

impl Palette {
    pub fn current(cx: &App) -> Self {
        Self::for_dark(cx.theme().is_dark())
    }

    fn for_dark(dark: bool) -> Self {
        if dark {
            Self {
                canvas: color(0x121411),
                sidebar: color(0x181a16),
                surface: color(0x1d201b),
                surface_raised: color(0x292d27),
                separator: color(0x353a32),
                border: color(0x70776b),
                text: color(0xf2f3ef),
                text_muted: color(0xb5bbb1),
                vermilion: color(0xfa7862),
                primary_foreground: color(0x1c0e0b),
                success: color(0x7ec79f),
                success_wash: color(0x1c3227),
                warning: color(0xf0c36b),
                warning_wash: color(0x382d18),
                info: color(0x8db8d5),
                info_wash: color(0x1d2d36),
                danger: color(0xff8b78),
                danger_wash: color(0x3b211c),
            }
        } else {
            Self {
                canvas: color(0xf4f4f1),
                sidebar: color(0xedede9),
                surface: color(0xffffff),
                surface_raised: color(0xe6e6e1),
                separator: color(0xd7d9d2),
                border: color(0x777d74),
                text: color(0x1b1d1a),
                text_muted: color(0x555b54),
                vermilion: color(0xb43b29),
                primary_foreground: color(0xffffff),
                success: color(0x1f6b45),
                success_wash: color(0xdcede3),
                warning: color(0x785100),
                warning_wash: color(0xf3e7c8),
                info: color(0x365d7d),
                info_wash: color(0xdce8ef),
                danger: color(0xa83525),
                danger_wash: color(0xf4ddd8),
            }
        }
    }
}

pub fn apply_brand(cx: &mut App) {
    let palette = Palette::current(cx);
    let theme = Theme::global_mut(cx);
    theme.font_family = "Atkinson Hyperlegible Next".into();
    theme.background = palette.surface;
    theme.foreground = palette.text;
    theme.muted = palette.surface_raised;
    theme.muted_foreground = palette.text_muted;
    theme.sidebar = palette.sidebar;
    theme.sidebar_foreground = palette.text;
    theme.sidebar_accent = palette.surface_raised;
    theme.sidebar_accent_foreground = palette.text;
    theme.sidebar_border = palette.separator;
    theme.border = palette.separator;
    theme.input = palette.border;
    theme.accent = palette.surface_raised;
    theme.accent_foreground = palette.text;
    theme.secondary = palette.surface_raised;
    theme.secondary_foreground = palette.text;
    theme.secondary_hover = palette.separator;
    theme.secondary_active = palette.separator;
    theme.primary = palette.vermilion;
    theme.primary_hover = palette
        .vermilion
        .mix(palette.text, 0.08);
    theme.primary_active = palette
        .vermilion
        .mix(palette.text, 0.14);
    theme.primary_foreground = palette.primary_foreground;
    theme.ring = palette.vermilion;
    theme.info = palette.info;
    theme.info_foreground = palette.canvas;
    theme.success = palette.success;
    theme.success_foreground = palette.canvas;
    theme.warning = palette.warning;
    theme.warning_foreground = palette.canvas;
    theme.danger = palette.danger;
    theme.danger_foreground = palette.canvas;
}

fn color(value: u32) -> Hsla {
    rgb(value).into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpui::Rgba;

    #[test]
    fn text_and_semantic_colors_meet_wcag_aa_in_both_themes() {
        for palette in [Palette::for_dark(false), Palette::for_dark(true)] {
            for (foreground, background) in [
                (palette.text, palette.canvas),
                (palette.text_muted, palette.canvas),
                (palette.text, palette.surface),
                (palette.text_muted, palette.surface),
                (palette.primary_foreground, palette.vermilion),
                (palette.success, palette.success_wash),
                (palette.warning, palette.warning_wash),
                (palette.info, palette.info_wash),
                (palette.danger, palette.danger_wash),
            ] {
                assert!(contrast(foreground, background) >= 4.5);
            }
            assert!(contrast(palette.border, palette.surface) >= 3.0);
        }
    }

    fn contrast(a: Hsla, b: Hsla) -> f32 {
        let a = luminance(a.into());
        let b = luminance(b.into());
        (a.max(b) + 0.05) / (a.min(b) + 0.05)
    }

    fn luminance(color: Rgba) -> f32 {
        0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)
    }

    fn channel(value: f32) -> f32 {
        if value <= 0.04045 { value / 12.92 } else { ((value + 0.055) / 1.055).powf(2.4) }
    }
}
