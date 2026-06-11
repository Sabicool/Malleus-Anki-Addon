"""
Malleus UI Styles
Centralised stylesheet and design tokens for all Malleus dialogs.

Design philosophy:
  - No forced background colours — inherits Anki's light/dark theme
  - Accent blue #4a82cc drawn from the Malleus logo tapir highlight
  - Styled interactive elements (buttons, inputs, indicators) work in both modes
  - Semi-transparent rgba borders adapt to whatever background is underneath
"""

# ── Design tokens ─────────────────────────────────────────────────────────────
COLORS = {
    # Accent — drawn from the logo's tapir highlight
    "accent":           "#4a82cc",
    "accent_dim":       "#3a6aaa",
    "accent_glow":      "#6a9fd8",
    # Borders — semi-transparent so they adapt to light and dark themes
    "border":           "rgba(74, 130, 204, 0.28)",
    "border_medium":    "rgba(74, 130, 204, 0.50)",
    "border_focus":     "#4a82cc",
    # Scrollbar track/handle — visible in both modes
    "scroll_track":     "rgba(74, 130, 204, 0.10)",
    "scroll_handle":    "rgba(74, 130, 204, 0.35)",
    "scroll_handle_hov":"rgba(74, 130, 204, 0.60)",
    # Danger
    "danger":           "#c05050",
    "danger_glow":      "#d46060",
}

C = COLORS  # Shorthand

# ── Master stylesheet ─────────────────────────────────────────────────────────
MALLEUS_STYLE = f"""

/* ── Font ────────────────────────────────────────────── */
QDialog, QWidget {{
    font-family: 'Segoe UI', 'SF Pro Text', -apple-system,
                 BlinkMacSystemFont, Ubuntu, sans-serif;
    font-size: 13px;
}}

/* ── Group Boxes ─────────────────────────────────────── */
QGroupBox {{
    border: 1.5px solid {C['border']};
    border-radius: 10px;
    margin-top: 10px;
    padding: 14px 10px 10px 10px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    color: {C['accent']};
}}

/* ── Line Edits ──────────────────────────────────────── */
QLineEdit {{
    background-color: palette(base);
    border: 1.5px solid {C['border']};
    border-radius: 7px;
    color: palette(text);
    padding: 7px 12px;
    font-size: 13px;
    selection-background-color: {C['accent']};
    selection-color: white;
    min-height: 20px;
}}
QLineEdit:focus {{
    border: 1.5px solid {C['border_focus']};
}}
QLineEdit:hover:!focus {{
    border: 1.5px solid {C['border_medium']};
}}

/* ── Combo Boxes ─────────────────────────────────────── */
QComboBox {{
    background-color: palette(base);
    border: 1.5px solid {C['border']};
    border-radius: 7px;
    color: palette(text);
    padding: 7px 28px 7px 12px;
    font-size: 13px;
    min-width: 110px;
    min-height: 20px;
}}
QComboBox:focus, QComboBox:on {{
    border: 1.5px solid {C['border_focus']};
}}
QComboBox:hover:!focus {{
    border: 1.5px solid {C['border_medium']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: right center;
}}
QComboBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C['accent']};
}}
QComboBox QAbstractItemView {{
    background-color: palette(base);
    border: 1.5px solid {C['border_medium']};
    border-radius: 6px;
    color: palette(text);
    padding: 4px;
    outline: none;
    selection-background-color: {C['accent']};
    selection-color: white;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 12px;
    border-radius: 4px;
    min-height: 24px;
}}

/* ── Push Buttons — primary (blue gradient) ──────────── */
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {C['accent_glow']}, stop:1 {C['accent_dim']});
    border: none;
    border-radius: 7px;
    color: white;
    padding: 7px 16px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.2px;
    min-height: 20px;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #7ab0e8, stop:1 {C['accent']});
}}
QPushButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {C['accent_dim']}, stop:1 #2a5090);
    padding-top: 8px;
    padding-bottom: 6px;
}}
QPushButton:disabled {{
    background: rgba(74, 130, 204, 0.25);
    color: rgba(74, 130, 204, 0.50);
}}

/* ── Destructive button — setObjectName("danger") ────── */
QPushButton#danger {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {C['danger_glow']}, stop:1 {C['danger']});
    color: white;
}}
QPushButton#danger:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #e07070, stop:1 #c04040);
}}
QPushButton#danger:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {C['danger']}, stop:1 #8a2020);
}}

/* ── Outlined / ghost button — setObjectName("secondary") ── */
QPushButton#secondary {{
    background: transparent;
    border: 1.5px solid {C['border_medium']};
    border-radius: 7px;
    color: {C['accent']};
    font-weight: 600;
}}
QPushButton#secondary:hover {{
    background: rgba(74, 130, 204, 0.12);
    border: 1.5px solid {C['border_focus']};
    color: {C['accent_glow']};
}}
QPushButton#secondary:pressed {{
    background: rgba(74, 130, 204, 0.20);
    border: 1.5px solid {C['accent_dim']};
    color: {C['accent_dim']};
}}

/* ── Donate / coffee button — setObjectName("donate") ── */
/* Warm amber outline, unobtrusive but clearly distinct   */
QPushButton#donate {{
    background: transparent;
    border: 1.5px solid rgba(210, 155, 50, 0.55);
    border-radius: 7px;
    color: #c8902a;
    font-weight: 600;
    font-size: 12px;
}}
QPushButton#donate:hover {{
    background: rgba(210, 155, 50, 0.12);
    border: 1.5px solid rgba(210, 155, 50, 0.80);
    color: #e0a83a;
}}
QPushButton#donate:pressed {{
    background: rgba(210, 155, 50, 0.22);
}}

/* ── Check Boxes ─────────────────────────────────────── */
QCheckBox {{
    font-size: 13px;
    spacing: 10px;
    padding: 5px 4px;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1.5px solid {C['border_medium']};
    background-color: palette(base);
}}
QCheckBox::indicator:hover {{
    border: 1.5px solid {C['border_focus']};
}}
QCheckBox::indicator:checked {{
    background-color: {C['accent']};
    border: 1.5px solid {C['accent']};
}}
QCheckBox::indicator:checked:hover {{
    background-color: {C['accent_glow']};
    border: 1.5px solid {C['accent_glow']};
}}

/* ── Radio Buttons — filled squircle style ───────────── */
QRadioButton {{
    font-size: 13px;
    spacing: 10px;
    padding: 5px 4px;
}}
QRadioButton::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1.5px solid {C['border_medium']};
    background-color: palette(base);
}}
QRadioButton::indicator:hover {{
    border: 1.5px solid {C['accent']};
    background-color: rgba(74, 130, 204, 0.08);
}}
QRadioButton::indicator:checked {{
    background-color: {C['accent']};
    border: 1.5px solid {C['accent']};
    border-radius: 5px;
}}
QRadioButton::indicator:checked:hover {{
    background-color: {C['accent_glow']};
    border: 1.5px solid {C['accent_glow']};
}}

/* ── Scroll Areas ────────────────────────────────────── */
QScrollArea {{
    border: 1px solid rgba(128, 128, 128, 0.22);
    border-radius: 9px;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

/* ── Yield segment container ─────────────────────────── */
QWidget#yield_segment {{
    border: 1px solid rgba(128, 128, 128, 0.30);
    border-radius: 8px;
    background-color: rgba(128, 128, 128, 0.12);
}}

/* ── Card-style panels (yield, specialty) ────────────── */
QFrame#card_panel {{
    border: 1px solid rgba(128, 128, 128, 0.22);
    border-radius: 9px;
    background-color: palette(base);
}}

QScrollBar:vertical {{
    background: {C['scroll_track']};
    width: 7px;
    border-radius: 3px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {C['scroll_handle']};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C['scroll_handle_hov']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

QScrollBar:horizontal {{
    background: {C['scroll_track']};
    height: 7px;
    border-radius: 3px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {C['scroll_handle']};
    border-radius: 3px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C['scroll_handle_hov']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

/* ── Labels ──────────────────────────────────────────── */
QLabel {{
    background-color: transparent;
}}
QLabel#tag_missing {{
    color: #d46060;
    font-size: 12px;
}}

/* ── Menus ───────────────────────────────────────────── */
QMenu {{
    background-color: palette(base);
    border: 1.5px solid {C['border_medium']};
    border-radius: 7px;
    padding: 4px;
    color: palette(text);
}}
QMenu::item {{
    padding: 6px 14px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: rgba(74, 130, 204, 0.18);
    color: palette(text);
}}
QMenu::separator {{
    height: 1px;
    background: {C['border']};
    margin: 3px 8px;
}}

/* ── Separators ──────────────────────────────────────── */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {{
    color: {C['border_medium']};
    background-color: {C['border_medium']};
    border: none;
    max-height: 1px;
}}

/* ── Tool Tips ───────────────────────────────────────── */
/* palette(toolTipBase) / palette(toolTipText) are the dedicated system   */
/* tooltip colours — they resolve correctly in Anki's light AND dark mode */
/* and are never transparent, unlike palette(window) on some platforms.   */
QToolTip {{
    background-color: palette(toolTipBase);
    color: palette(toolTipText);
    border: 1px solid {C['border_medium']};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    opacity: 255;
}}

/* ── Progress Bar ────────────────────────────────────── */
QProgressBar {{
    background-color: palette(base);
    border: 1px solid {C['border']};
    border-radius: 5px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {C['accent_dim']}, stop:1 {C['accent_glow']});
    border-radius: 5px;
}}

/* ── SpinBox ─────────────────────────────────────────── */
QSpinBox {{
    background-color: palette(base);
    border: 1.5px solid {C['border']};
    border-radius: 7px;
    padding: 5px 8px;
    color: palette(text);
}}
QSpinBox:focus {{
    border: 1.5px solid {C['border_focus']};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    border: none;
    background: transparent;
    width: 16px;
}}

/* ── Dialog Button Box ───────────────────────────────── */
QDialogButtonBox QPushButton {{
    min-width: 80px;
}}
"""


def apply_malleus_style(widget):
    """
    Apply the Malleus stylesheet to a widget and all its children.

    QToolTip background is resolved to a concrete hex colour at call time
    because ``palette(toolTipBase)`` is unreliable on macOS / some Qt builds
    and renders as fully transparent.  We sample the current app palette and
    pick appropriate light / dark colours ourselves.
    """
    try:
        from aqt.qt import QApplication, QPalette
        pal  = QApplication.instance().palette()
        dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
        tt_bg   = "#2b2b2b" if dark else "#f5f5f5"
        tt_text = "#e6e6e6" if dark else "#1a1a1a"
        tt_border = "rgba(120,120,120,0.55)" if dark else "rgba(120,120,120,0.40)"
    except Exception:
        tt_bg, tt_text, tt_border = "#fffde7", "#1a1a1a", "rgba(160,140,80,0.55)"

    tooltip_override = f"""
QToolTip {{
    background-color: {tt_bg};
    color: {tt_text};
    border: 1px solid {tt_border};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    opacity: 255;
}}
"""
    widget.setStyleSheet(MALLEUS_STYLE + tooltip_override)


# ── Sponsor logo widget ───────────────────────────────────────────────────────
def make_sponsor_widget(svg_path: str,
                        url: str = "https://emedici.com",
                        caption: str = "SPONSORED BY",
                        logo_height: int = 17) -> "QWidget":
    """
    Small theme-aware sponsor block: a tiny uppercase caption above the sponsor
    logo, clickable through to the sponsor's website.

    The SVG's ``currentColor`` fills are substituted with the current theme's
    text colour at render time, so a wordmark that uses currentColor (like the
    eMedici logo) stays legible in both Anki's light and dark modes; fixed
    brand colours in the SVG are left untouched.

    Falls back to a plain text label if Qt's SVG module or the file is
    unavailable.  Returns None only if even the fallback cannot be built.
    """
    from aqt.qt import (QWidget, QVBoxLayout, QLabel, Qt, QApplication,
                        QPalette, QPixmap, QUrl)
    from PyQt6.QtGui import QDesktopServices

    # Theme text colour for the wordmark; caption uses the muted placeholder tone.
    try:
        pal = QApplication.instance().palette()
        text_hex = pal.color(QPalette.ColorRole.WindowText).name()
    except Exception:
        text_hex = "#808080"

    logo_label = None
    try:
        from PyQt6.QtSvg import QSvgRenderer
        from aqt.qt import QPainter
        with open(svg_path, encoding="utf-8") as f:
            svg = f.read()
        renderer = QSvgRenderer(svg.replace("currentColor", text_hex).encode("utf-8"))
        if renderer.isValid():
            vb = renderer.defaultSize()                     # e.g. 300 × 63
            w = round(logo_height * vb.width() / max(vb.height(), 1))
            # Render at the screen's device pixel ratio so it's crisp on retina.
            try:
                dpr = QApplication.instance().devicePixelRatio()
            except Exception:
                dpr = 1.0
            pm = QPixmap(round(w * dpr), round(logo_height * dpr))
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            renderer.render(painter)
            painter.end()
            pm.setDevicePixelRatio(dpr)
            logo_label = QLabel()
            logo_label.setPixmap(pm)
            logo_label.setFixedSize(w, logo_height)
    except Exception as e:
        print(f"[Malleus] sponsor SVG unavailable, using text fallback: {e}")

    # NOTE: every stylesheet below is scoped to an object name.  Selector-less
    # rules also restyle the widget's own QToolTip (Qt applies them to the
    # tooltip raised for the widget), which made the tooltip background
    # transparent.  Scoped rules leave the tooltip to the dialog-level
    # QToolTip style from apply_malleus_style().
    if logo_label is None:                                  # text fallback
        logo_label = QLabel("eMedici")
        logo_label.setObjectName("sponsor_logo_text")
        logo_label.setStyleSheet(
            f"#sponsor_logo_text {{ color: {text_hex}; font-size: 13px;"
            " font-weight: 700; background: transparent; border: none; }}"
        )

    caption_label = QLabel(caption)
    caption_label.setObjectName("sponsor_caption")
    caption_label.setStyleSheet(
        "#sponsor_caption { color: palette(placeholderText); font-size: 8px;"
        " font-weight: 600; letter-spacing: 1.2px;"
        " background: transparent; border: none; }"
    )

    sponsor = QWidget()
    sponsor.setObjectName("sponsor_block")
    sponsor.setStyleSheet("#sponsor_block { background: transparent; border: none; }")
    lay = QVBoxLayout(sponsor)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    lay.addWidget(caption_label, alignment=Qt.AlignmentFlag.AlignHCenter)
    lay.addWidget(logo_label, alignment=Qt.AlignmentFlag.AlignHCenter)

    sponsor.setToolTip(f"eMedici — proud sponsor of the Malleus project.\nClick to visit {url}")
    sponsor.setCursor(Qt.CursorShape.PointingHandCursor)

    def _open(event, _url=url):
        QDesktopServices.openUrl(QUrl(_url))
    sponsor.mousePressEvent = _open

    return sponsor


# ── Reusable header widget ────────────────────────────────────────────────────
def make_header(title: str = "Malleus Clinical Medicine",
                subtitle: str = None,
                logo_path: str = None,
                sponsor_svg_path: str = None) -> "QWidget":
    """
    Compact branded header bar for Malleus dialogs.

    A 2 px accent line along the bottom provides the brand anchor without
    forcing a background colour, so it works in both Anki's light and dark
    themes.

    Args:
        title:            Primary text.
        subtitle:         Smaller secondary line (optional).
        logo_path:        Absolute path to the Malleus logo image.
                          Typically: os.path.join(addon_dir, 'logo.png')
        sponsor_svg_path: Absolute path to a sponsor SVG (optional).  Shown as a
                          small clickable "SPONSORED BY" block right of centre.
    """
    from aqt.qt import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                        Qt, QPixmap, QSize)

    # ── Header container — no fixed height so subtitle can wrap ────────────
    # Transparency rules are scoped under the header's object name: a bare
    # "QWidget" selector would also match QToolTip (a QWidget subclass) and
    # render every tooltip raised inside the header with no background.
    header = QWidget()
    header.setObjectName("malleus_header")
    header.setStyleSheet(
        "#malleus_header, #malleus_header QWidget { background: transparent; }"
    )

    outer_layout = QVBoxLayout(header)
    outer_layout.setContentsMargins(0, 0, 0, 0)
    outer_layout.setSpacing(0)

    inner = QWidget()
    h_layout = QHBoxLayout(inner)
    h_layout.setContentsMargins(16, 8, 12, 8)
    h_layout.setSpacing(10)

    # ── Text ────────────────────────────────────────────────────────────────
    text_layout = QVBoxLayout()
    text_layout.setSpacing(3)

    title_label = QLabel(title)
    title_label.setStyleSheet(f"""
        QLabel {{
            color: {COLORS['accent']};
            font-size: 14px;
            font-weight: 700;
            background: transparent;
            border: none;
            letter-spacing: 0.3px;
        }}
    """)
    text_layout.addWidget(title_label)

    if subtitle:
        sub_label = QLabel(subtitle)
        sub_label.setWordWrap(True)
        sub_label.setStyleSheet("""
            QLabel {
                font-size: 11px;
                background: transparent;
                border: none;
                letter-spacing: 0.2px;
            }
        """)
        text_layout.addWidget(sub_label)

    h_layout.addLayout(text_layout)
    h_layout.addStretch()

    # ── Sponsor block (right of centre, before the Malleus logo) ───────────
    if sponsor_svg_path:
        try:
            sponsor = make_sponsor_widget(sponsor_svg_path)
            if sponsor is not None:
                h_layout.addWidget(sponsor)
                h_layout.addSpacing(18)
        except Exception as e:
            print(f"[Malleus] could not build sponsor widget: {e}")

    # ── Logo (top-right) ────────────────────────────────────────────────────
    logo_shown = False
    if logo_path:
        try:
            logo_size = 42
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                pixmap = pixmap.scaled(
                    QSize(logo_size, logo_size),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                logo_label = QLabel()
                logo_label.setObjectName("malleus_header_logo")
                logo_label.setPixmap(pixmap)
                logo_label.setFixedSize(logo_size, logo_size)
                logo_label.setStyleSheet(
                    "#malleus_header_logo { background: transparent; border: none; }"
                )
                logo_label.setAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                logo_label.setToolTip("Visit malleus.org.au")
                logo_label.setCursor(Qt.CursorShape.PointingHandCursor)
                # Make the logo clickable — open the Malleus website
                def _open_malleus(event, _url="https://malleus.org.au"):
                    from aqt.qt import QUrl
                    from PyQt6.QtGui import QDesktopServices
                    QDesktopServices.openUrl(QUrl(_url))
                logo_label.mousePressEvent = _open_malleus
                h_layout.addWidget(logo_label)
                logo_shown = True
        except Exception:
            pass

    # ── Fallback decoration ──────────────────────────────────────────────────
    if not logo_shown:
        dots = QLabel("· · ·")
        dots.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['accent']};
                font-size: 18px;
                letter-spacing: 4px;
                background: transparent;
                border: none;
            }}
        """)
        dots.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h_layout.addWidget(dots)

    # Assemble: inner content + bottom accent border
    outer_layout.addWidget(inner)

    from aqt.qt import QFrame
    separator = QFrame()
    separator.setFrameShape(QFrame.Shape.HLine)
    separator.setFixedHeight(2)
    separator.setStyleSheet("background-color: rgba(74, 130, 204, 0.55); border: none;")
    outer_layout.addWidget(separator)

    return header
