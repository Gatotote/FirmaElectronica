"""Hoja de estilo y colores (negro y gris con acentos morados, como el ícono; al estilo de GeniusIQ)."""

from PySide6.QtGui import QColor, QPalette

ACENTO = "#9b6bff"
ACENTO_OSCURO = "#6d3fd9"
ACENTO_CLARO = "#c7b2ff"
FONDO = "#0e0e10"
TARJETA = "#19191c"
CONTROL = "#242428"
BORDE = "#3a3a40"
TEXTO = "#eeebf5"
SUAVE = "#a6a2b3"
MESA = "#2b2b30"     # fondo detrás de la hoja en la vista del documento
OK = "#6ee7a0"
MAL = "#ff6b6b"

QSS = f"""
QWidget {{ background: {FONDO}; color: {TEXTO}; font-size: 14px; }}
QLabel#titulo {{ font-size: 30px; font-weight: 800; color: {ACENTO}; }}
QLabel#subtitulo {{ font-size: 20px; font-weight: 700; color: {TEXTO}; }}
QLabel#suave {{ color: {SUAVE}; }}
QLabel#credito {{ color: #6f6b7a; font-size: 12px; }}
QLabel#aviso {{ background: #24183d; border: 1px solid #5b3fa0; border-radius: 8px; padding: 10px;
                color: {ACENTO_CLARO}; }}
QFrame#tarjeta {{ background: {TARJETA}; border: 1px solid {BORDE}; border-radius: 12px; }}
QFrame#tarjeta QLabel, QFrame#tarjeta QCheckBox, QFrame#tarjeta QSlider {{ background: transparent; }}
QFrame#tarjeta QLabel#aviso {{ background: #24183d; }}
QPushButton {{ background: {CONTROL}; border: 1px solid {BORDE}; border-radius: 8px; padding: 8px 16px; }}
QPushButton:hover {{ border-color: {ACENTO}; background: #241c33; }}
QPushButton:disabled {{ color: #5f5b68; border-color: #2c2c31; }}
QPushButton#principal {{ background: {ACENTO}; color: #0e0e10; border: none; font-weight: 800;
                          padding: 12px 28px; font-size: 16px; }}
QPushButton#principal:hover {{ background: {ACENTO_CLARO}; }}
QPushButton#principal:disabled {{ background: #3b2d5c; color: #6f6b7a; }}
QPushButton#peligro {{ color: {MAL}; }}
QPushButton#peligro:hover {{ border-color: {MAL}; background: #2d1717; }}
QListWidget, QComboBox, QLineEdit {{
    background: {CONTROL}; border: 1px solid {BORDE}; border-radius: 8px; padding: 6px; }}
QListWidget {{ outline: 0; }}
QComboBox QAbstractItemView {{ background: {CONTROL}; selection-background-color: {ACENTO};
                               selection-color: #0e0e10; }}
QListWidget::item {{ padding: 10px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {ACENTO}; color: #0e0e10; font-weight: 700; }}
QListWidget::item:hover:!selected {{ background: #2c2540; }}
QListWidget#menu {{ background: {TARJETA}; font-size: 16px; padding: 8px; border-radius: 12px; }}
QListWidget#menu::item {{ padding: 14px 12px; margin: 2px 0; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACENTO}; }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDE}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACENTO}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACENTO_CLARO}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QGraphicsView {{ background: {MESA}; border: 1px solid {BORDE}; border-radius: 10px; }}
QSplitter::handle {{ background: {FONDO}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {FONDO}; width: 10px; height: 10px; }}
QScrollBar::handle {{ background: {BORDE}; border-radius: 5px; min-height: 30px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QMessageBox, QDialog, QInputDialog {{ background: {FONDO}; }}
QToolTip {{ background: {TARJETA}; color: {TEXTO}; border: 1px solid {ACENTO}; }}
"""


def paleta() -> QPalette:
    """Paleta oscura para lo que no cubre la hoja de estilo (diálogos de archivos, menús)."""
    p = QPalette()
    for rol, color in [
        (QPalette.ColorRole.Window, TARJETA), (QPalette.ColorRole.WindowText, TEXTO),
        (QPalette.ColorRole.Base, CONTROL), (QPalette.ColorRole.AlternateBase, TARJETA),
        (QPalette.ColorRole.Text, TEXTO), (QPalette.ColorRole.Button, CONTROL),
        (QPalette.ColorRole.ButtonText, TEXTO), (QPalette.ColorRole.Highlight, ACENTO),
        (QPalette.ColorRole.HighlightedText, "#0e0e10"), (QPalette.ColorRole.ToolTipBase, TARJETA),
        (QPalette.ColorRole.ToolTipText, TEXTO), (QPalette.ColorRole.PlaceholderText, SUAVE),
        (QPalette.ColorRole.Link, ACENTO_CLARO),
    ]:
        p.setColor(rol, QColor(color))
    return p
