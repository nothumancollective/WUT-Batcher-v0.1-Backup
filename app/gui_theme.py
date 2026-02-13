"""Central GUI theme tokens and QSS."""

from __future__ import annotations

TOKENS = {
    "bg": "#121212",
    "surface": "#1B1B1B",
    "sidebar": "#0D0D0D",
    "border": "#373737",
    "text": "#F1F1F1",
    "muted": "#B6B6B6",
    "selection": "#EBEBEB",
    "accent": "#8D8D8D",
    "button_bg": "#FFFFFF",
    "button_text": "#1A1A1A",
    "button_border": "#CFCFCF",
    "button_hover": "#F2F2F2",
    "button_pressed": "#E4E4E4",
    "button_disabled": "#CCCCCC",
    "success": "#6CB080",
    "warning": "#D8B868",
    "danger": "#C86A6A",
}


def build_stylesheet() -> str:
    return f"""
    QWidget {{
        background-color: {TOKENS['bg']};
        color: {TOKENS['text']};
        font-size: 13px;
    }}
    QMainWindow {{
        background-color: {TOKENS['bg']};
    }}
    #Sidebar {{
        background-color: {TOKENS['sidebar']};
        border-right: 1px solid {TOKENS['border']};
    }}
    #ContentArea {{
        background-color: {TOKENS['bg']};
    }}
    QLabel#SidebarTitle {{
        font-size: 19px;
        font-weight: 800;
    }}
    QLabel#PageTitle {{
        font-size: 28px;
        font-weight: 800;
    }}
    QLabel#SectionTitle {{
        font-size: 18px;
        font-weight: 800;
    }}
    QLabel#MutedText {{
        color: {TOKENS['muted']};
    }}
    QFrame#Card {{
        background-color: {TOKENS['surface']};
        border: 1px solid {TOKENS['border']};
        border-radius: 12px;
    }}
    QGroupBox {{
        background-color: {TOKENS['surface']};
        border: 1px solid {TOKENS['border']};
        border-radius: 10px;
        margin-top: 12px;
        padding-top: 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }}
    QLineEdit,
    QPlainTextEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox {{
        background-color: {TOKENS['bg']};
        color: {TOKENS['text']};
        border: 1px solid {TOKENS['border']};
        border-radius: 8px;
        padding: 6px 8px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {TOKENS['surface']};
        color: {TOKENS['text']};
        border: 1px solid {TOKENS['border']};
        selection-background-color: #2B2B2B;
    }}
    QPushButton {{
        background-color: {TOKENS['button_bg']};
        color: {TOKENS['button_text']};
        border: 1px solid {TOKENS['button_border']};
        border-radius: 8px;
        padding: 8px 12px;
    }}
    QPushButton:hover {{
        background-color: {TOKENS['button_hover']};
        border-color: #BEBEBE;
    }}
    QPushButton:pressed {{
        background-color: {TOKENS['button_pressed']};
    }}
    QPushButton:disabled {{
        background-color: {TOKENS['button_disabled']};
        color: #666666;
        border-color: #B8B8B8;
    }}
    QPushButton#PrimaryButton {{
        background-color: {TOKENS['button_bg']};
        border-color: {TOKENS['button_border']};
        color: {TOKENS['button_text']};
        font-weight: 600;
    }}
    QPushButton#NavButton {{
        text-align: left;
        padding: 10px 12px;
        border-radius: 8px;
        border: 1px solid {TOKENS['button_border']};
        background: {TOKENS['button_bg']};
        color: {TOKENS['button_text']};
        font-weight: 800;
    }}
    QPushButton#NavButton:checked {{
        background-color: {TOKENS['selection']};
        border-color: #A8A8A8;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 1px solid #F1F1F1;
        border-radius: 4px;
        background-color: #FFFFFF;
    }}
    QCheckBox::indicator:checked {{
        border: 1px solid #FFFFFF;
        background-color: #FFFFFF;
    }}
    QListWidget {{
        background-color: {TOKENS['surface']};
        border: 1px solid {TOKENS['border']};
        border-radius: 10px;
    }}
    QStatusBar {{
        background-color: {TOKENS['sidebar']};
        border-top: 1px solid {TOKENS['border']};
    }}
    """
