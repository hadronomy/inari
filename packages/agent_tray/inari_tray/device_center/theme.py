from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette


@dataclass(frozen=True)
class DeviceCenterTheme:
    is_dark: bool
    background: str
    surface: str
    surface_alt: str
    surface_raised: str
    border: str
    border_soft: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    accent_foreground: str
    selection_background: str
    selection_border: str
    input_background: str
    input_hover: str
    input_focus: str
    success_background: str
    success_foreground: str
    offline_background: str
    offline_foreground: str
    warning_background: str
    warning_foreground: str
    code_background: str
    code_border: str
    splitter: str

    def color(self, value: str) -> QColor:
        return QColor(value)

    def code_font(self) -> QFont:
        return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

    def application_style_sheet(self) -> str:
        return (
            "QMainWindow#deviceCenterWindow {"
            f"background: {self.background};"
            f"color: {self.text_primary};"
            "}"
            "QWidget#deviceCenterRoot {"
            f"background: {self.background};"
            f"color: {self.text_primary};"
            "}"
            "QFrame#toolbarCard, QFrame#inspectorCard, QFrame#emptyStateCard {"
            f"background: {self.surface};"
            f"border: 1px solid {self.border};"
            "border-radius: 16px;"
            "}"
            "QFrame#toolbarCard, QFrame#inspectorCard {"
            f"background: {self.surface_alt};"
            "}"
            "QFrame#tableShell, QFrame#factsShell, QFrame#codeShell {"
            f"border: 1px solid {self.border_soft};"
            "border-radius: 12px;"
            "}"
            "QFrame#overviewSectionShell {"
            f"background: {self.surface_alt};"
            f"border: 1px solid {self.border_soft};"
            "border-radius: 12px;"
            "}"
            "QFrame#detailFieldCard {"
            f"background: {self.surface};"
            f"border: 1px solid {self.border};"
            "border-radius: 10px;"
            "}"
            "QFrame#overviewMetricCard {"
            f"background: {self.surface};"
            f"border: 1px solid {self.border_soft};"
            "border-radius: 12px;"
            "}"
            "QFrame#tableShell, QFrame#factsShell, QFrame#codeShell {"
            f"background: {self.surface_alt};"
            "}"
            "QFrame#codeShell {"
            f"border-color: {self.border_soft};"
            "}"
            "QFrame#activityOverlay {"
            f"background: {self.surface_raised};"
            f"border: 1px solid {self.border};"
            "border-top-left-radius: 0px;"
            "border-top-right-radius: 12px;"
            "border-bottom-right-radius: 0px;"
            "border-bottom-left-radius: 0px;"
            "}"
            'QFrame#activityOverlay[statusMode="busy"] {'
            f"border-color: {self.border_soft};"
            "}"
            'QFrame#activityOverlay[statusMode="ready"] {'
            f"border-color: {self.border};"
            "}"
            'QFrame#activityOverlay[statusMode="offline"] {'
            f"border-color: {self.offline_foreground};"
            "}"
            "QLabel {"
            f"color: {self.text_primary};"
            "background: transparent;"
            "}"
            "QLabel#panelEyebrow {"
            f"color: {self.accent};"
            "font-size: 12px;"
            "font-weight: 700;"
            "}"
            "QLabel#activityMessage {"
            f"color: {self.text_secondary};"
            "font-size: 12px;"
            "font-weight: 600;"
            "}"
            "QLabel#panelTitle {"
            f"color: {self.text_primary};"
            "font-size: 15px;"
            "font-weight: 700;"
            "}"
            "QLabel#pageTitle {"
            f"color: {self.text_primary};"
            "font-size: 18px;"
            "font-weight: 700;"
            "}"
            "QLabel#toolbarMeta, QLabel#panelHint, QLabel#summaryLabel, QLabel#eventsSummaryLabel, QLabel#deviceMeta {"
            f"color: {self.text_muted};"
            "font-size: 12px;"
            "}"
            "QLabel#toolbarStatusChip, QLabel#deviceChip {"
            "padding: 5px 10px;"
            "border-radius: 10px;"
            f"border: 1px solid {self.border_soft};"
            f"background: {self.surface_raised};"
            f"color: {self.text_secondary};"
            "font-size: 12px;"
            "font-weight: 600;"
            "}"
            'QLabel#toolbarStatusChip[tone="online"], QLabel#deviceChip[tone="online"] {'
            f"background: {self.success_background};"
            f"border-color: {self.success_foreground};"
            f"color: {self.success_foreground};"
            "}"
            'QLabel#toolbarStatusChip[tone="busy"], QLabel#deviceChip[tone="busy"] {'
            f"background: {self.accent_soft};"
            f"border-color: {self.accent};"
            f"color: {self.accent};"
            "}"
            'QLabel#toolbarStatusChip[tone="offline"], QLabel#deviceChip[tone="offline"] {'
            f"background: {self.offline_background};"
            f"border-color: {self.offline_foreground};"
            f"color: {self.offline_foreground};"
            "}"
            'QLabel#toolbarStatusChip[tone="default"], QLabel#deviceChip[tone="default"] {'
            f"background: {self.accent_soft};"
            f"border-color: {self.border_soft};"
            f"color: {self.text_primary};"
            "}"
            "QLabel#panelMeta {"
            f"color: {self.text_muted};"
            "font-size: 12px;"
            "font-weight: 600;"
            "}"
            "QLabel#sectionLabel {"
            f"color: {self.text_secondary};"
            "font-weight: 700;"
            "font-size: 13px;"
            "}"
            "QLabel#overviewSectionTitle {"
            f"color: {self.text_secondary};"
            "font-size: 12px;"
            "font-weight: 700;"
            "letter-spacing: 0.04em;"
            "}"
            "QLabel#overviewMetricLabel {"
            f"color: {self.text_muted};"
            "font-size: 11px;"
            "font-weight: 700;"
            "letter-spacing: 0.04em;"
            "}"
            "QLabel#overviewMetricValue {"
            f"color: {self.text_primary};"
            "font-size: 16px;"
            "font-weight: 700;"
            "}"
            "QLabel#detailFieldLabel {"
            f"color: {self.text_muted};"
            "font-size: 11px;"
            "font-weight: 700;"
            "letter-spacing: 0.04em;"
            "text-transform: uppercase;"
            "}"
            "QLabel#detailFieldValue {"
            f"color: {self.text_primary};"
            "font-size: 13px;"
            "font-weight: 600;"
            "}"
            "QLabel#deviceTitle {"
            f"color: {self.text_primary};"
            "font-size: 22px;"
            "font-weight: 700;"
            "}"
            "QLabel#emptyStateLabel {"
            f"color: {self.text_secondary};"
            "font-size: 14px;"
            "padding: 44px;"
            "}"
            "QLabel#connectionBanner {"
            f"background: {self.warning_background};"
            f"color: {self.warning_foreground};"
            f"border: 1px solid {self.border};"
            "border-radius: 14px;"
            "padding: 12px 14px;"
            "font-weight: 600;"
            "}"
            "QLineEdit#searchInput {"
            f"background: {self.input_background};"
            f"border: 1px solid {self.border};"
            "border-radius: 10px;"
            f"color: {self.text_primary};"
            "padding: 8px 12px;"
            f"selection-background-color: {self.selection_background};"
            "}"
            "QLineEdit#searchInput:hover {"
            f"background: {self.input_hover};"
            f"border-color: {self.border_soft};"
            "}"
            "QLineEdit#searchInput:focus {"
            f"border: 1px solid {self.input_focus};"
            f"background: {self.surface_raised};"
            "}"
            "QLineEdit#searchInput::placeholder {"
            f"color: {self.text_muted};"
            "}"
            "QPushButton {"
            f"background: {self.surface_raised};"
            f"color: {self.text_secondary};"
            f"border: 1px solid {self.border};"
            "border-radius: 10px;"
            "padding: 7px 12px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            f"border-color: {self.accent};"
            f"color: {self.text_primary};"
            "}"
            "QPushButton:pressed {"
            f"background: {self.input_background};"
            f"border-color: {self.accent_pressed};"
            "}"
            "QPushButton:checked {"
            f"background: {self.accent_soft};"
            f"border-color: {self.accent};"
            f"color: {self.accent};"
            "}"
            "QPushButton:disabled {"
            f"background: {self.surface};"
            f"border-color: {self.border};"
            f"color: {self.text_muted};"
            "}"
            'QPushButton[buttonRole="accent"] {'
            f"background: {self.accent};"
            f"border-color: {self.accent};"
            f"color: {self.accent_foreground};"
            "}"
            'QPushButton[buttonRole="accent"]:hover {'
            f"background: {self.accent_hover};"
            f"border-color: {self.accent_hover};"
            "}"
            'QPushButton[buttonRole="accent"]:pressed {'
            f"background: {self.accent_pressed};"
            f"border-color: {self.accent_pressed};"
            "}"
            'QPushButton[buttonRole="filter"] {'
            f"background: {self.surface};"
            f"border-color: {self.border_soft};"
            f"color: {self.text_muted};"
            "padding: 6px 12px;"
            "}"
            'QPushButton[buttonRole="filter"]:checked {'
            f"background: {self.accent_soft};"
            f"border-color: {self.accent};"
            f"color: {self.accent};"
            "}"
            'QPushButton[buttonRole="subtle"] {'
            "background: transparent;"
            "border: 0;"
            f"color: {self.text_secondary};"
            "padding: 0px;"
            "}"
            'QPushButton[buttonRole="subtle"]:hover {'
            f"color: {self.text_primary};"
            "border: 0;"
            "}"
            'QPushButton[buttonRole="subtle"]:checked {'
            "background: transparent;"
            "border: 0;"
            f"color: {self.accent};"
            "}"
            "QTabWidget::pane {"
            "background: transparent;"
            "border: 0;"
            "margin-top: 10px;"
            "}"
            "QTabBar::tab {"
            "background: transparent;"
            "border: 0;"
            "border-bottom: 2px solid transparent;"
            f"color: {self.text_muted};"
            "padding: 8px 2px 10px 2px;"
            "margin-right: 16px;"
            "font-weight: 600;"
            "}"
            "QTabBar::tab:hover {"
            f"color: {self.text_primary};"
            "}"
            "QTabBar::tab:selected {"
            f"color: {self.text_primary};"
            f"border-bottom: 2px solid {self.accent};"
            "}"
            "QPlainTextEdit {"
            "background: transparent;"
            "border: 0;"
            f"color: {self.text_secondary};"
            "padding: 10px;"
            f"selection-background-color: {self.selection_background};"
            "}"
            "QSplitter::handle {"
            f"background: {self.background};"
            "width: 12px;"
            "}"
            "QSplitter::handle:hover {"
            f"background: {self.splitter};"
            "border-radius: 4px;"
            "}"
        )

    def table_style_sheet(self, *, compact: bool) -> str:
        header_padding = "10px 12px" if compact else "13px 14px"
        cell_padding = "8px 12px" if compact else "11px 14px"
        return (
            "QTableView {"
            f"background: {self.surface_alt};"
            f"alternate-background-color: {self.surface};"
            "border: 0;"
            f"selection-background-color: {self.selection_background};"
            f"selection-color: {self.text_primary};"
            f"color: {self.text_primary};"
            "outline: 0;"
            "}"
            "QTableView::item {"
            f"padding: {cell_padding};"
            "border: 0;"
            f"border-bottom: 1px solid {self.border};"
            "}"
            "QTableView::item:hover {"
            f"background: {self.surface_raised};"
            "}"
            "QTableView::item:selected {"
            f"background: {self.selection_background};"
            f"color: {self.text_primary};"
            f"border-top: 1px solid {self.selection_border};"
            f"border-bottom: 1px solid {self.selection_border};"
            "}"
            "QHeaderView::section {"
            "background: transparent;"
            f"color: {self.text_secondary};"
            f"padding: {header_padding};"
            "border: 0;"
            f"border-bottom: 1px solid {self.border};"
            "font-weight: 700;"
            "}"
        )

    def context_menu_style_sheet(self) -> str:
        return (
            "QMenu {"
            f"background: {self.surface_raised};"
            f"border: 1px solid {self.border_soft};"
            "border-radius: 12px;"
            "padding: 8px;"
            f"color: {self.text_primary};"
            "}"
            "QMenu::item {"
            "padding: 8px 12px;"
            "border-radius: 8px;"
            "margin: 2px 0;"
            "}"
            "QMenu::item:selected {"
            f"background: {self.selection_background};"
            f"color: {self.text_primary};"
            "}"
            "QMenu::separator {"
            f"height: 1px;"
            f"background: {self.border};"
            "margin: 6px 4px;"
            "}"
        )


def resolve_device_center_theme() -> DeviceCenterTheme:
    app = QGuiApplication.instance()
    palette = app.palette() if app is not None else QPalette()
    return _dark_theme() if _is_dark_palette(palette) else _light_theme()


def _is_dark_palette(palette: QPalette) -> bool:
    window = palette.color(QPalette.ColorRole.Window)
    text = palette.color(QPalette.ColorRole.WindowText)
    return window.lightnessF() < text.lightnessF()


def _dark_theme() -> DeviceCenterTheme:
    return DeviceCenterTheme(
        is_dark=True,
        background="#0F0F10",
        surface="#151516",
        surface_alt="#19191A",
        surface_raised="#1D1C1B",
        border="#292827",
        border_soft="#393735",
        text_primary="#F4EFE9",
        text_secondary="#D8D0C7",
        text_muted="#A29A93",
        accent="#E06A3A",
        accent_hover="#EB7950",
        accent_pressed="#C95A30",
        accent_soft="#30211B",
        accent_foreground="#FFF3EA",
        selection_background="#25201C",
        selection_border="#8A563E",
        input_background="#121213",
        input_hover="#171717",
        input_focus="#E06A3A",
        success_background="#1A2C22",
        success_foreground="#90D7A7",
        offline_background="#3B2328",
        offline_foreground="#ECA1AE",
        warning_background="#3A2B1D",
        warning_foreground="#E9C27A",
        code_background="#101011",
        code_border="#232221",
        splitter="#1E1C1A",
    )


def _light_theme() -> DeviceCenterTheme:
    return DeviceCenterTheme(
        is_dark=False,
        background="#F4F1EC",
        surface="#FFFFFF",
        surface_alt="#FAF7F3",
        surface_raised="#FFFDFC",
        border="#E4DBD1",
        border_soft="#D3C7BA",
        text_primary="#2C241E",
        text_secondary="#5C4F43",
        text_muted="#887A6E",
        accent="#C65D31",
        accent_hover="#D26B41",
        accent_pressed="#AE4F27",
        accent_soft="#F7E4DB",
        accent_foreground="#FFF8F4",
        selection_background="#F0E5DE",
        selection_border="#E2D2C8",
        input_background="#FFFCF9",
        input_hover="#FFFFFF",
        input_focus="#C65D31",
        success_background="#E2F4E8",
        success_foreground="#2F7D50",
        offline_background="#FBE4E8",
        offline_foreground="#A5465A",
        warning_background="#FBF0D9",
        warning_foreground="#8A6420",
        code_background="#FBF8F5",
        code_border="#E8E0D7",
        splitter="#DED5CB",
    )
