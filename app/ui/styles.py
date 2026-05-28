"""
Folha de estilos global da aplicação Omie Label Automation.
Design premium com tema escuro (dark mode), glassmorphism e micro-animações.
"""

STYLESHEET = """
/* ============================================================
   ESTILOS GLOBAIS - OMIE LABEL AUTOMATION
   ============================================================ */

QMainWindow, QDialog {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: "Segoe UI", Arial, sans-serif;
}

QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-size: 13px;
}

/* ---- MENU BAR ---- */
QMenuBar {
    background-color: #161b22;
    color: #e6edf3;
    border-bottom: 1px solid #30363d;
    padding: 2px 4px;
}
QMenuBar::item:selected {
    background-color: #1f6feb;
    border-radius: 4px;
}

/* ---- TOOLBAR ---- */
QToolBar {
    background-color: #161b22;
    border-bottom: 1px solid #30363d;
    spacing: 6px;
    padding: 4px 8px;
}

/* ---- STATUS BAR ---- */
QStatusBar {
    background-color: #161b22;
    color: #8b949e;
    border-top: 1px solid #30363d;
    font-size: 11px;
}

/* ---- GROUPBOX ---- */
QGroupBox {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    margin-top: 12px;
    padding: 8px 10px;
    font-weight: bold;
    color: #58a6ff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 12px;
    top: -6px;
}

/* ---- LABELS ---- */
QLabel {
    color: #e6edf3;
    background: transparent;
}
QLabel#labelTitle {
    font-size: 20px;
    font-weight: bold;
    color: #58a6ff;
}
QLabel#labelSubtitle {
    font-size: 12px;
    color: #8b949e;
}
QLabel#statusDot {
    font-size: 12px;
    font-weight: bold;
}

/* ---- BUTTONS ---- */
QPushButton {
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 3px 10px;
    font-size: 13px;
    min-height: 24px;
}
QPushButton:hover {
    background-color: #30363d;
    border-color: #58a6ff;
    color: #58a6ff;
}
QPushButton:pressed {
    background-color: #0d419d;
    border-color: #1f6feb;
}
QPushButton:disabled {
    background-color: #161b22;
    color: #484f58;
    border-color: #21262d;
}

QPushButton#btnStart {
    background-color: #196c2e;
    border-color: #2ea043;
    color: #ffffff;
    font-weight: bold;
}
QPushButton#btnStart:hover {
    background-color: #2ea043;
}

QPushButton#btnStop {
    background-color: #6e1a1a;
    border-color: #da3633;
    color: #ffffff;
    font-weight: bold;
}
QPushButton#btnStop:hover {
    background-color: #da3633;
}

QPushButton#btnRefresh {
    background-color: #0d419d;
    border-color: #1f6feb;
    color: #ffffff;
    font-weight: bold;
}
QPushButton#btnRefresh:hover {
    background-color: #1f6feb;
}

QPushButton#btnPrint {
    background-color: #5a3e00;
    border-color: #d29922;
    color: #ffffff;
    font-weight: bold;
}
QPushButton#btnPrint:hover {
    background-color: #d29922;
}

/* ---- INPUTS ---- */
QLineEdit, QSpinBox, QComboBox, QDateEdit, QAbstractSpinBox {
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #21262d;
    border-radius: 4px;
    padding: 2px 6px;
    min-height: 22px;
    selection-background-color: #1f6feb;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus, QAbstractSpinBox:focus {
    border-color: #58a6ff;
    outline: none;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid #8b949e;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #30363d;
    selection-background-color: #1f6feb;
}

/* ---- TABLE ---- */
QTableWidget {
    background-color: #0d1117;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    gridline-color: #21262d;
    selection-background-color: #1f6feb40;
    selection-color: #e6edf3;
    alternate-background-color: #161b22;
    font-size: 12px;
}
QTableWidget::item {
    padding: 6px 8px;
}
QTableWidget::item:selected {
    background-color: #1f6feb40;
    color: #e6edf3;
}
QHeaderView::section {
    background-color: #161b22;
    color: #8b949e;
    border: none;
    border-bottom: 2px solid #30363d;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
QHeaderView::section:hover {
    background-color: #21262d;
    color: #58a6ff;
}

/* ---- TEXTEDIT (LOG) ---- */
QTextEdit {
    background-color: #010409;
    color: #3fb950;
    border: 1px solid #21262d;
    border-radius: 6px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
    padding: 8px;
    selection-background-color: #1f6feb;
}

/* ---- SCROLLBARS ---- */
QScrollBar:vertical {
    background: #0d1117;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #30363d;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #58a6ff;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: #0d1117;
    height: 8px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: #30363d;
    border-radius: 4px;
    min-width: 20px;
}
QScrollBar::handle:horizontal:hover {
    background: #58a6ff;
}

/* ---- SPLITTER ---- */
QSplitter::handle {
    background-color: #30363d;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QSplitter::handle:vertical {
    height: 2px;
}

/* ---- TABS ---- */
QTabWidget::pane {
    border: 1px solid #30363d;
    border-radius: 6px;
    background-color: #161b22;
}
QTabBar::tab {
    background-color: #0d1117;
    color: #8b949e;
    border: 1px solid #30363d;
    border-bottom: none;
    padding: 7px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected {
    background-color: #161b22;
    color: #58a6ff;
    border-bottom: 2px solid #58a6ff;
}
QTabBar::tab:hover {
    color: #e6edf3;
}

/* ---- CHECKBOX ---- */
QCheckBox {
    color: #e6edf3;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #30363d;
    border-radius: 4px;
    background-color: #0d1117;
}
QCheckBox::indicator:checked {
    background-color: #1f6feb;
    border-color: #58a6ff;
}

/* ---- TOOLTIP ---- */
QToolTip {
    background-color: #161b22;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}
"""
