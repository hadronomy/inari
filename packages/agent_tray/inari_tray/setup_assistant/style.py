from __future__ import annotations

from ..device_center.theme import DeviceCenterTheme


def setup_style_sheet(theme: DeviceCenterTheme) -> str:
    return f"""
    QMainWindow#setupAssistantWindow, QWidget#setupRoot {{
        background: {theme.background};
        color: {theme.text_primary};
    }}
    QFrame#setupProgressPanel {{
        background: {theme.surface_alt};
        border-right: 1px solid {theme.border};
    }}
    QLabel#setupMark {{
        background: {theme.accent};
        color: {theme.accent_foreground};
        border-radius: 8px;
        font-size: 18px;
        font-weight: 800;
    }}
    QLabel#setupPanelTitle {{ font-size: 15px; font-weight: 700; }}
    QLabel#setupTitle {{ font-size: 25px; font-weight: 750; }}
    QLabel#setupBody {{ color: {theme.text_secondary}; font-size: 14px; }}
    QLabel#setupFieldLabel {{
        color: {theme.text_secondary};
        font-size: 12px;
        font-weight: 700;
    }}
    QLabel#setupPrivacy, QLabel#setupDeviceEmpty {{
        color: {theme.text_muted};
        font-size: 11px;
    }}
    QLabel#setupProgressItem {{
        color: {theme.text_muted};
        padding: 7px 0;
        font-size: 12px;
    }}
    QLabel#setupProgressItem[state="active"] {{
        color: {theme.text_primary};
        font-weight: 700;
    }}
    QLabel#setupProgressItem[state="done"] {{
        color: {theme.success_foreground};
        font-weight: 650;
    }}
    QLabel#setupStatus {{
        color: {theme.text_primary};
        font-size: 14px;
        font-weight: 650;
    }}
    QLabel#setupError {{
        background: {theme.offline_background};
        color: {theme.offline_foreground};
        border: 1px solid {theme.offline_foreground};
        border-radius: 6px;
        padding: 10px;
    }}
    QLabel#setupDetailLine {{
        color: {theme.text_secondary};
        font-family: monospace;
        font-size: 11px;
    }}
    QLabel#setupReadyMark {{
        background: {theme.success_background};
        color: {theme.success_foreground};
        border: 1px solid {theme.success_foreground};
        border-radius: 8px;
        font-size: 28px;
        font-weight: 800;
    }}
    QLabel#setupDeviceState {{ color: {theme.text_muted}; font-size: 11px; }}
    QPlainTextEdit#setupInvitationInput, QLineEdit#setupServerInput,
    QFrame#setupDetails, QFrame#setupDeviceRow {{
        background: {theme.input_background};
        color: {theme.text_primary};
        border: 1px solid {theme.border};
        border-radius: 7px;
    }}
    QLineEdit {{
        min-height: 34px;
        padding: 0 10px;
        background: {theme.input_background};
        color: {theme.text_primary};
        border: 1px solid {theme.border};
        border-radius: 6px;
    }}
    QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {theme.input_focus}; }}
    QToolButton#setupDisclosure {{
        color: {theme.text_secondary};
        background: transparent;
        border: none;
        padding: 5px 0;
        font-weight: 600;
    }}
    QPushButton {{
        min-height: 36px;
        padding: 0 16px;
        border-radius: 6px;
        font-weight: 700;
    }}
    QPushButton#setupPrimaryButton {{
        background: {theme.accent};
        color: {theme.accent_foreground};
        border: 1px solid {theme.accent};
    }}
    QPushButton#setupPrimaryButton:hover {{ background: {theme.accent_hover}; }}
    QPushButton#setupPrimaryButton:pressed {{ background: {theme.accent_pressed}; }}
    QPushButton#setupSecondaryButton, QPushButton#setupTertiaryButton {{
        background: {theme.surface};
        color: {theme.text_primary};
        border: 1px solid {theme.border};
    }}
    QPushButton#setupTertiaryButton {{ min-height: 30px; padding: 0 12px; }}
    QPushButton:disabled {{ color: {theme.text_muted}; }}
    QProgressBar {{
        background: {theme.border_soft};
        border: none;
        border-radius: 3px;
    }}
    QProgressBar::chunk {{ background: {theme.accent}; border-radius: 3px; }}
    QScrollArea#setupDeviceScroll {{ background: transparent; }}
    """
