"""
Page Selector Dialog
Main UI for searching and selecting Notion pages
"""
from aqt import mw, dialogs
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox,
                    QLineEdit, QPushButton, QGroupBox, QScrollArea,
                    QWidget, QCheckBox, QButtonGroup, QRadioButton,
                    QLabel, QFrame, QTimer, Qt, QUrl, QWidget as QWidgetBase,
                    QKeyEvent, QColor, QPalette, QPixmap, QIcon, QSize, QMenu,
                    QSizePolicy, QApplication)
from aqt.browser import Browser
from aqt.addcards import AddCards
from aqt.editcurrent import EditCurrent
from aqt.utils import showInfo
from ..utils import malleus_tooltip
from PyQt6.QtGui import QDesktopServices
import re as _re
import anki.notes


def _fix_amp_display(text: str) -> str:
    """Prepare a display string for use in Qt widgets (QCheckBox / QLabel).

    Two problems are fixed here:

    1. Legacy cache format: older Notion formula outputs stored '&' as ' _'
       (e.g. 'Bradyarrhythmias _Conduction Disorders').  Convert those back
       to ' & ' so they display correctly.

    2. Qt mnemonic escaping: Qt widgets treat a bare '&' as a keyboard
       accelerator prefix — '&F' hides the '&' and underlines 'F'.  Replacing
       every '&' with '&&' tells Qt to display a literal ampersand instead.
    """
    # Step 1 — fix legacy underscore-for-ampersand substitution
    text = _re.sub(r' _([A-Za-z])', r' & \1', text)
    # Step 2 — escape for Qt so the ampersand renders visibly
    text = text.replace('&', '&&')
    return text


# ── Database display constants ────────────────────────────────────────────────

# Emoji indicators for databases that don't use the page's own Search Prefix.
# Subjects and Pharmacology use the page's Search Prefix property (🩺 / 💊 / ℹ️).
# eTG uses a logo image (loaded lazily in _make_result_row).
_DB_EMOJI = {
    "Rotation":   "🏥",
    "Textbooks":  "📖",
    "Guidelines": "🖊️",
}

# ── Related-subject tree gutter ───────────────────────────────────────────────

class _TreeGutter(QWidget):
    """A thin left gutter that paints the tree line for a related-subject row:
    a vertical stroke plus a horizontal branch into the row.  The last child's
    vertical stroke stops at the branch (a true └); others run full height so
    consecutive rows join into one continuous line."""

    _COLOR = (74, 130, 204, 150)
    _X = 5          # vertical-stroke x (aligns under the parent's prefix badge)

    def __init__(self, is_last: bool, parent=None):
        super().__init__(parent)
        self._is_last = is_last
        self.setFixedWidth(20)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        from aqt.qt import QPainter, QColor, QPen
        p = QPainter(self)
        pen = QPen(QColor(*self._COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        h, w, cy = self.height(), self.width(), self.height() // 2
        p.drawLine(self._X, 0, self._X, cy if self._is_last else h)   # vertical
        p.drawLine(self._X, cy, w, cy)                                 # branch
        p.end()


# ── Inline subtag chip ────────────────────────────────────────────────────────

class _SubtagChip(QPushButton):
    """Compact inline tag-chip for subtag selection.

    Appears to the right of the checkbox text when the row is checked.
    Clicking opens a QMenu listing all subtag options; the selected option
    is shown on the chip as "Selection  ▾".

    Exposes the same minimal API that the rest of the code uses on subtag
    controls: currentText(), findText(), setCurrentIndex().
    """

    _MAX_W = 170

    def __init__(self, options: list, apply_all_callback=None, parent=None):
        super().__init__(parent)
        self._options            = options
        self._selection          = options[0] if options else ''
        self._apply_all_callback = apply_all_callback   # callable(selection) or None
        self._refresh_label()
        self.setMaximumWidth(self._MAX_W)
        self.setStyleSheet(
            "QPushButton {"
            "  border: 1px solid #4a82cc; border-radius: 9px;"
            "  padding: 2px 10px 2px 10px; font-size: 11px;"
            "  background: rgba(74,130,204,0.15); color: #4a82cc;"
            "  text-align: left;"
            "}"
            "QPushButton:hover  { background: rgba(74,130,204,0.28); }"
            "QPushButton:pressed { background: rgba(74,130,204,0.40); }"
        )
        self.setToolTip("Click to set subtag · Shift+click to apply to all checked rows")
        self.clicked.connect(self._open_menu)

    # ── QComboBox-compatible API ───────────────────────────────────────────

    def currentText(self) -> str:
        return self._selection

    def findText(self, text: str) -> int:
        for i, opt in enumerate(self._options):
            if opt == text:
                return i
        return -1

    def setCurrentIndex(self, idx: int):
        if 0 <= idx < len(self._options):
            self._selection = self._options[idx]
            self._refresh_label()

    # ── Internal ──────────────────────────────────────────────────────────

    def _refresh_label(self):
        label = self._selection or 'Select…'
        if len(label) > 22:
            label = label[:20] + '…'
        self.setText(f"{label}  ▾")

    def _open_menu(self):
        menu = QMenu(self)

        # QMenu is a top-level popup window and does not inherit the dialog's
        # stylesheet, so we must apply colours explicitly.
        try:
            pal = QApplication.instance().palette()
            dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
            base   = "#1e2236" if dark else "#ffffff"
            text   = "#dce0ef" if dark else "#1a1d28"
            border = "rgba(74,130,204,0.50)"
            sel    = "rgba(74,130,204,0.18)"
            sep    = "rgba(74,130,204,0.28)"
            menu.setStyleSheet(f"""
                QMenu {{
                    background-color: {base};
                    border: 1.5px solid {border};
                    border-radius: 7px;
                    padding: 4px;
                    color: {text};
                }}
                QMenu::item {{
                    padding: 6px 14px;
                    border-radius: 4px;
                    color: {text};
                }}
                QMenu::item:selected {{
                    background-color: {sel};
                    color: {text};
                }}
                QMenu::separator {{
                    height: 1px;
                    background: {sep};
                    margin: 3px 8px;
                }}
            """)
        except Exception:
            pass

        # ── "Apply to all" shortcut at the top ────────────────────────────
        apply_action = None
        if self._apply_all_callback:
            cur = self._selection or 'current subtag'
            apply_action = menu.addAction(f"↕  Apply '{cur}' to all checked rows")
            menu.addSeparator()

        # ── Per-row subtag options (skip blank placeholder) ───────────────
        for opt in self._options:
            if not opt:
                continue
            action = menu.addAction(opt)
            action.setCheckable(True)
            action.setChecked(opt == self._selection)

        # Use triggered signal (fires while the user's finger is still on the
        # mouse/keyboard) so queryKeyboardModifiers() reliably catches Shift.
        def _on_action(action):
            if action is apply_action:
                self._apply_all_callback(self._selection)
                return

            self._selection = action.text()
            self._refresh_label()

            shift_held = bool(
                QApplication.queryKeyboardModifiers()
                & Qt.KeyboardModifier.ShiftModifier
            )
            if shift_held and self._apply_all_callback:
                self._apply_all_callback(self._selection)

        menu.triggered.connect(_on_action)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))


# Maps the UI database name to the fragment used in Anki tag strings.
# Most are identical; Rotation is the exception.
DB_TAG_MAPPING = {
    "Subjects":     "Subjects",
    "Pharmacology": "Pharmacology",
    "eTG":          "eTG",
    "Rotation":     "Resources_by_Rotation",
    "Textbooks":    "Textbooks",
    "Guidelines":   "Guidelines",
}

# Maximum search results shown across all databases
_MAX_SEARCH_RESULTS = 15

# Score multipliers applied per-database before the global top-N sort.
# Values > 1.0 push that database's results higher; < 1.0 pushes them lower.
_DB_SCORE_BIAS = {
    "Subjects":     1.30,
    "Pharmacology": 1.10,
    "eTG":          1.00,
    "Rotation":     0.85,
    "Textbooks":    0.80,
    "Guidelines":   0.85,
}


def _is_general_page(page: dict) -> bool:
    """Return True when the page is a 'general' overview page (ℹ️ in Search Prefix)."""
    prefix = (page.get('properties', {})
              .get('Search Prefix', {})
              .get('formula', {})
              .get('string', ''))
    return 'ℹ️' in prefix


def _relation_ids(page: dict, property_name: str) -> list:
    """Return the list of related page IDs for a relation property (may be empty)."""
    return [r.get('id', '') for r in
            page.get('properties', {}).get(property_name, {}).get('relation', [])]


def _page_needs_subtag(page: dict) -> bool:
    """
    True when a result row must have a subtag chosen before tags can be applied:
    non-general Subjects/Pharmacology pages.  (Guidelines rows never need a
    subtag — their linked Subjects appear as separate rows that carry their own
    subtag requirement.)
    """
    db = page.get('_database_name', '')
    return db in ("Subjects", "Pharmacology") and not _is_general_page(page)


from ..config import (DATABASE_PROPERTIES, get_database_id, get_database_name,
                       SUBJECT_DATABASE_ID, PHARMACOLOGY_DATABASE_ID,
                       ROTATION_DATABASE_ID, SUBJECT_DATABASE_ID_ORIGINAL)
from ..utils import open_browser_with_search
from ..cache_updater import perform_cache_update
from ..extra_sync import (
    build_additional_resources_content, set_additional_resources_on_note,
    get_matching_se_entries, get_existing_se_ids_from_field,
    build_field_from_selected_entries, SE_EXTRA_TAG_PREFIX,
    SYNCED_EXTRA_DATABASE_ID, EXTRA_FIELD
)
from .synced_extra_dialog import SyncedExtraSelectionDialog
from ..suggest_tags import suggest_subject_tags, invalidate_index
from .tag_selection_dialog import TagSelectionDialog
try:
    from .styles import apply_malleus_style, make_header, COLORS
except Exception:
    def apply_malleus_style(w): pass
    def make_header(title="Malleus Clinical Medicine", subtitle=None, logo_path=None):
        from aqt.qt import QWidget, QHBoxLayout, QLabel
        h = QWidget(); h.setFixedHeight(48 if not subtitle else 62)
        lay = QHBoxLayout(h); lay.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel(title); lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        lay.addWidget(lbl); lay.addStretch(); return h
    COLORS = {}
from ..tag_utils import (simplify_tags_by_page, get_subtag_from_tag,
                         get_all_subtags_from_tags, normalize_subtag_for_matching,
                         get_subtags_with_normalization)


class NotionPageSelector(QDialog):
    last_yield_selection = ""  # Class variable to remember last selection

    def __init__(self, parent, notion_cache, config):
        if parent is not None and not isinstance(parent, QWidget):
            parent = mw
        super().__init__(parent)

        # Initialize current_note first
        self.current_note = None
        if isinstance(parent, Browser) and hasattr(parent.editor, 'note'):
            self.current_note = parent.editor.note
        elif isinstance(parent, EditCurrent) and hasattr(parent.editor, 'note'):
            self.current_note = parent.editor.note
        elif isinstance(parent, AddCards) and hasattr(parent.editor, 'note'):
            self.current_note = parent.editor.note
        self.notion_cache = notion_cache
        self.config = config
        import os
        self._addon_dir = str(notion_cache.cache_dir.parent)

        self.database_properties = DATABASE_PROPERTIES
        # _result_rows: list of dicts {page, checkbox, subtag_combo, row_widget}
        # Replaces the old self.pages_data + fragile index-based checkbox lookup.
        self._result_rows = []
        self._showing_recent = False
        self._db_chips = {}  # db_name → QPushButton (filter chips)
        self.setup_ui()
        apply_malleus_style(self)

    def has_notes_to_process(self):
        """Check if there are notes available to process"""
        parent = self.parent()

        if isinstance(parent, Browser):
            selected_card_ids = parent.selectedCards()
            return len(selected_card_ids) > 0
        elif isinstance(parent, EditCurrent):
            return hasattr(parent.editor, 'note') and parent.editor.note is not None
        elif isinstance(parent, AddCards):
            return hasattr(parent.editor, 'note') and parent.editor.note is not None
        else:
            return self.current_note is not None

    def setup_ui(self):
        self.setWindowTitle("Malleus Page Selector")
        self.setMinimumWidth(820 if self.has_notes_to_process() else 640)
        self.setMinimumHeight(580)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Branded header ──────────────────────────────────────────────────
        import os as _os
        _logo = _os.path.join(self._addon_dir, "logo.png")
        if not _os.path.exists(_logo):
            _logo = _os.path.join(self._addon_dir, "logo.jpg")
        header = make_header(
            title="Malleus Clinical Medicine",
            subtitle="Find, create and tag Anki cards",
            logo_path=_logo if _os.path.exists(_logo) else None,
        )
        layout.addWidget(header)

        # ── Inner content (padded) ──────────────────────────────────────────
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(16, 14, 16, 12)
        content_layout.setSpacing(10)

        # ── Search input (full row) ─────────────────────────────────────────
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search…")
        self.search_input.textChanged.connect(self.on_search_text_changed)
        self.search_input.setMinimumHeight(34)
        content_layout.addWidget(self.search_input)

        if not self.config['autosearch']:
            search_button = QPushButton("Search")
            search_button.clicked.connect(self.perform_search)
            content_layout.addWidget(search_button)

        # ── Database filter chips ───────────────────────────────────────────
        chips_row = QHBoxLayout()
        chips_row.setSpacing(4)
        chips_row.setContentsMargins(0, 0, 0, 0)

        chips_label = QLabel("Databases:")
        chips_label.setStyleSheet(
            "font-size: 11px; color: palette(placeholderText); background: transparent;"
        )
        chips_row.addWidget(chips_label)

        _chip_style = (
            "QPushButton {"
            "  border: 1px solid rgba(74,130,204,0.35); border-radius: 13px;"
            "  padding: 3px 11px 3px 8px; font-size: 11px; background: transparent;"
            "  color: palette(placeholderText); font-weight: 500;"
            "}"
            "QPushButton:checked {"
            "  background: #4a82cc; border-color: #4a82cc; color: white; font-weight: 600;"
            "}"
            "QPushButton:!checked:hover {"
            "  background: rgba(74,130,204,0.10); color: #4a82cc;"
            "  border-color: rgba(74,130,204,0.55);"
            "}"
        )

        # Chip labels — emoji prefix for all databases; eTG gets an image icon
        _chip_labels = {
            "Subjects":     "🩺 Subjects",
            "Pharmacology": "💊 Pharmacology",
            "eTG":          "eTG",          # icon set separately below
            "Rotation":     "🏥 Rotation",
            "Textbooks":    "📖 Textbooks",
            "Guidelines":   "🖊️ Guidelines",
        }

        # Build the eTG icon once (16×16) for use on the chip
        _etg_chip_icon = None
        try:
            import os as _os
            _etg_pm = QPixmap(_os.path.join(self._addon_dir, 'images', 'eTG.jpg'))
            if not _etg_pm.isNull():
                _etg_chip_icon = QIcon(
                    _etg_pm.scaled(16, 16,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                )
        except Exception:
            pass

        for db_name in ["Subjects", "Pharmacology", "eTG", "Rotation", "Textbooks", "Guidelines"]:
            btn = QPushButton(_chip_labels.get(db_name, db_name))
            if db_name == "eTG" and _etg_chip_icon:
                btn.setIcon(_etg_chip_icon)
                btn.setIconSize(QSize(16, 16))
                btn.setText("  eTG")  # leading spaces add gap between icon and label
            btn.setCheckable(True)
            btn.setChecked(True)   # all active by default
            btn.setStyleSheet(_chip_style)
            btn.clicked.connect(self._on_chip_toggled)
            chips_row.addWidget(btn)
            self._db_chips[db_name] = btn

        chips_row.addStretch()

        # ── All / None quick-select buttons ────────────────────────────────
        _toggle_btn_style = (
            "QPushButton {"
            "  border: 1px solid palette(mid); border-radius: 3px;"
            "  padding: 1px 7px; font-size: 10px; background: transparent;"
            "  color: palette(windowText);"
            "}"
            "QPushButton:hover { background: rgba(74,130,204,0.12); }"
        )
        all_btn  = QPushButton("All")
        none_btn = QPushButton("None")
        for qb in (all_btn, none_btn):
            qb.setFixedHeight(22)
            qb.setStyleSheet(_toggle_btn_style)
        all_btn.setToolTip("Enable all databases")
        none_btn.setToolTip("Disable all databases")

        def _select_all_chips():
            for b in self._db_chips.values():
                b.setChecked(True)
            self._on_chip_toggled()

        def _select_no_chips():
            for b in self._db_chips.values():
                b.setChecked(False)
            self._on_chip_toggled()

        all_btn.clicked.connect(_select_all_chips)
        none_btn.clicked.connect(_select_no_chips)
        chips_row.addWidget(all_btn)
        chips_row.addWidget(none_btn)

        content_layout.addLayout(chips_row)

        # ── Results section ─────────────────────────────────────────────────
        results_header_layout = QHBoxLayout()
        results_header_layout.setContentsMargins(2, 0, 2, 0)
        self._results_section_label = QLabel("Search Results")
        self._results_section_label.setStyleSheet(
            "QLabel { font-size: 10px; font-weight: 700; letter-spacing: 0.8px;"
            " color: palette(placeholderText); background: transparent;"
            " text-transform: uppercase; }"
        )
        self._results_count_label = QLabel("")
        self._results_count_label.setStyleSheet(
            "QLabel { font-size: 11px; color: palette(placeholderText); background: transparent; }"
        )
        results_header_layout.addWidget(self._results_section_label)
        results_header_layout.addStretch()
        results_header_layout.addWidget(self._results_count_label)
        content_layout.addLayout(results_header_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(220)
        scroll_widget = QWidget()
        self.checkbox_layout = QVBoxLayout()
        self.checkbox_layout.setSpacing(0)
        self.checkbox_layout.setContentsMargins(0, 0, 0, 0)
        self.checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll_widget.setLayout(self.checkbox_layout)
        scroll.setWidget(scroll_widget)
        content_layout.addWidget(scroll, stretch=1)

        # Shim so setTitle() calls update the section label text
        class _SectionLabelShim:
            def __init__(self_, lbl): self_._lbl = lbl
            def setTitle(self_, text): self_._lbl.setText(text)
        self.results_group = _SectionLabelShim(self._results_section_label)

        # ── Yield selection — segmented control ─────────────────────────────
        _YIELD_DEFS = [
            ("High Yield",                   "High",      "#3a9e6a"),
            ("Medium Yield",                 "Medium",    "#c8902a"),
            ("Low Yield",                    "Low",       "#c06030"),
            ("Beyond Medical Student Level", "Beyond\nMedical School", "#7a5ab8"),
        ]

        combined_tooltip = """<p style="margin: 0; padding: 4px;">
        <b style="font-size: 14px;">High Yield</b> <span>(~50% cards)</span><br><br>
        <span>• If you study just these cards, you will likely pass final year medical school exams, but likely not do much better if studied in isolation</span><br>
        <span>• These cards touch on high yield topics that are essential for basic understanding of clinical medicine at the level of a final year medical student/intern and should be prioritised for study</span><br><br>
        <b>Examples:</b><br>
        <span style="margin-left: 12px;">◦ First line management of acute heart failure (LMNOP)</span><br>
        <span style="margin-left: 12px;">◦ 1st line empirical antibiotics used for low-severity community acquired pneumonia</span><br>
        <span style="margin-left: 12px;">◦ Basics of statistics (PPV, NPP, sensitivity, specificity, etc.)</span><br>
        <span style="margin-left: 12px;">◦ Identifying ST elevation criteria on an ECG</span><br>
        <span style="margin-left: 12px;">◦ Common causes of HAGMAs/NAGMAs</span><br><br>

        <b style="font-size: 14px;">Medium Yield</b> <span>(~30% cards)</span><br><br>
        <span>• These cards cover topics that are <i>useful</i>, but not essential for basic understanding of clinical medicine</span><br>
        <span>• They may provide helpful context to 'high yield' cards and background knowledge</span><br><br>
        <b>Examples:</b><br>
        <span style="margin-left: 12px;">◦ ST-elevation cut-offs (ie. mm) on ECG according to national guidelines</span><br>
        <span style="margin-left: 12px;">◦ Antibiotics used in management of cystitis in women &lt;50 years old</span><br><br>

        <b style="font-size: 14px;">Low Yield</b> <span>(~10% cards)</span><br><br>
        <span>• These cards are low yield and cover knowledge that goes well beyond what is expected for a basic understanding of clinical medicine</span><br>
        <span>• Includes niche topics and facts that might be useful for getting a HD in topics for final year medical school exams, but has little utility beyond that</span><br><br>
        <b>Examples:</b><br>
        <span style="margin-left: 12px;">◦ Epidemiology of VSDs in the population</span><br>
        <span style="margin-left: 12px;">◦ Subtypes of gram-negative bacterium</span><br>
        <span style="margin-left: 12px;">◦ Exact components of niche risk stratification tools (ie. HASBLED)</span><br>
        <span style="margin-left: 12px;">◦ Niche examination findings found in Talley &amp; O'Connor (ie. JVP waveform interpretation)</span><br><br>

        <b style="font-size: 14px;">Beyond Medical Student Level</b> <span>(~10% cards)</span><br><br>
        <span>• These cards are a level 'below' low yield, and are tagged to easily filter out content that may be useful for some clinicians (such as Medical Registrars) however has no role in the curriculum of medical school finals</span><br>
        <span>• These tags were envisioned to be used for cards made on topics from textbooks directly; it's easy to make cards this way however only select cards will actually be high yield for medical school</span><br><br>
        <b>Examples:</b><br>
        <span style="margin-left: 12px;">◦ Diagnostic criteria for sepsis according to college guidelines</span><br>
        <span style="margin-left: 12px;">◦ Niche pharmacology including half-lives and pharmacokinetics of drugs</span>
        </p>"""

        # Card panel
        yield_panel = QFrame()
        yield_panel.setObjectName("card_panel")
        yield_panel.setFrameShape(QFrame.Shape.NoFrame)
        yield_panel_layout = QVBoxLayout(yield_panel)
        yield_panel_layout.setContentsMargins(10, 10, 10, 10)
        yield_panel_layout.setSpacing(8)

        # Title row: label + badge + info icon
        yield_header_row = QHBoxLayout()
        yield_header_row.setSpacing(6)
        yield_title_label = QLabel("Yield Level")
        yield_title_label.setStyleSheet("font-size: 12px; font-weight: 700; background: transparent;")
        self._yield_badge = QLabel("None selected")
        self._yield_badge.setStyleSheet(
            "font-size: 10px; color: palette(placeholderText);"
            " background: palette(midlight); border: 1px solid rgba(128,128,128,0.25);"
            " border-radius: 5px; padding: 1px 6px;"
        )
        info_label = QLabel("ℹ️")
        info_label.setToolTip(combined_tooltip)
        info_label.setStyleSheet("QLabel { color: #4a82cc; font-size: 14px; margin-left: 5px; background: transparent; }")
        info_label.setFixedSize(20, 20)
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setCursor(Qt.CursorShape.WhatsThisCursor)
        yield_header_row.addWidget(yield_title_label)
        yield_header_row.addWidget(self._yield_badge)
        yield_header_row.addWidget(info_label)
        yield_header_row.addStretch()
        yield_panel_layout.addLayout(yield_header_row)

        # Segmented control
        yield_segment = QWidget()
        yield_segment.setObjectName("yield_segment")
        yield_segment_layout = QHBoxLayout(yield_segment)
        yield_segment_layout.setContentsMargins(0, 0, 0, 0)
        yield_segment_layout.setSpacing(0)
        yield_segment.setFixedHeight(44)

        self.yield_radio_buttons = {}
        self._yield_btn_colors = {}
        self._last_checked_yield = None

        for i, (full_name, short_name, color) in enumerate(_YIELD_DEFS):
            btn = QPushButton(short_name)
            btn.setFixedHeight(44)
            is_last = (i == len(_YIELD_DEFS) - 1)
            sep_r = "" if is_last else "border-right: 1px solid rgba(128,128,128,0.25);"
            inactive = (
                "QPushButton {"
                "  background: transparent; border: none; border-radius: 0px;"
                f"  {sep_r}"
                "  color: palette(placeholderText);"
                "  font-size: 11px; font-weight: 600; letter-spacing: 0.2px;"
                "}"
                "QPushButton:hover { background: rgba(74,130,204,0.08); }"
            )
            r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
            sep_r_active = "" if is_last else f"border-right: 1px solid rgba({r},{g},{b},0.6);"
            active = (
                "QPushButton {"
                f"  background: {color}; border: none; border-radius: 0px;"
                f"  {sep_r_active}"
                "  color: white;"
                "  font-size: 11px; font-weight: 600; letter-spacing: 0.2px;"
                "}"
            )
            btn.setStyleSheet(inactive)
            btn._inactive_style = inactive
            btn._active_style = active
            btn._yield_color = color
            btn._yield_rgb = (r, g, b)
            self.yield_radio_buttons[full_name] = btn
            self._yield_btn_colors[full_name] = color
            btn.clicked.connect(lambda _, opt=full_name: self.handle_yield_click(opt))
            yield_segment_layout.addWidget(btn)

        yield_panel_layout.addWidget(yield_segment)

        # Restore previous yield selection if any
        if NotionPageSelector.last_yield_selection in self.yield_radio_buttons:
            self._last_checked_yield = NotionPageSelector.last_yield_selection
            _btn = self.yield_radio_buttons[NotionPageSelector.last_yield_selection]
            _btn.setStyleSheet(_btn._active_style)
            _r, _g, _b = _btn._yield_rgb
            _short = {"High Yield": "High Yield", "Medium Yield": "Medium Yield",
                      "Low Yield": "Low Yield", "Beyond Medical Student Level": "Beyond MS"}
            self._yield_badge.setText(_short.get(NotionPageSelector.last_yield_selection, ""))
            self._yield_badge.setStyleSheet(
                f"font-size: 10px; color: {_btn._yield_color}; font-weight: 600;"
                f" background: rgba({_r},{_g},{_b},0.12);"
                f" border: 1px solid rgba({_r},{_g},{_b},0.30);"
                " border-radius: 5px; padding: 1px 6px;"
            )

        # ── Paediatrics / Specialty Tags ────────────────────────────────────
        paeds_panel = QFrame()
        paeds_panel.setObjectName("card_panel")
        paeds_panel.setFrameShape(QFrame.Shape.NoFrame)
        paeds_layout = QVBoxLayout(paeds_panel)
        paeds_layout.setContentsMargins(10, 10, 10, 10)
        paeds_layout.setSpacing(6)

        paeds_title = QLabel("Specialty Tags")
        paeds_title.setStyleSheet("font-size: 12px; font-weight: 700; background: transparent;")
        paeds_layout.addWidget(paeds_title)

        paeds_question = QLabel("Is this a paediatrics card?")
        paeds_question.setStyleSheet("font-size: 11px; color: palette(placeholderText); background: transparent;")
        paeds_question.setWordWrap(True)
        paeds_layout.addWidget(paeds_question)

        self.paeds_checkbox = QCheckBox("Paediatrics")
        paeds_layout.addWidget(self.paeds_checkbox)
        paeds_layout.addStretch()

        # Yield + Paediatrics side by side
        yield_paeds_widget = QWidget()
        yield_paeds_layout = QHBoxLayout(yield_paeds_widget)
        yield_paeds_layout.setContentsMargins(0, 0, 0, 0)
        yield_paeds_layout.setSpacing(10)
        yield_paeds_layout.addWidget(yield_panel, stretch=2)
        yield_paeds_layout.addWidget(paeds_panel, stretch=1)
        yield_paeds_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )
        content_layout.addWidget(yield_paeds_widget, stretch=0)

        # ── Buttons ─────────────────────────────────────────────────────────
        has_notes = self.has_notes_to_process()
        buttons_vbox = QVBoxLayout()
        buttons_vbox.setSpacing(6)

        update_database_button = QPushButton("↻  Update Database")
        update_database_button.setObjectName("secondary")

        def _on_update_database():
            full = bool(
                QApplication.queryKeyboardModifiers()
                & Qt.KeyboardModifier.ShiftModifier
            )

            def _after_update():
                # Runs once the async update chain finishes — refreshing the
                # suggestion index / age label any earlier would read stale data.
                invalidate_index()
                try:
                    self._update_cache_age_label()
                except RuntimeError:
                    pass   # dialog was closed while the update ran

            perform_cache_update(self.notion_cache, mw, full=full,
                                 on_complete=_after_update)

        update_database_button.clicked.connect(_on_update_database)
        self._update_database_button = update_database_button
        self._update_cache_age_label()

        guidelines_button = QPushButton("Guidelines ↗")
        guidelines_button.setObjectName("secondary")
        guidelines_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://malleuscm.notion.site/submission-guidelines")
            )
        )
        donate_button = QPushButton("🫶 Support")
        donate_button.setObjectName("donate")
        donate_button.setToolTip("Support Malleus on Paypal")
        donate_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://www.paypal.com/donate/?hosted_button_id=9VM7MHMMK5JJJ")
            )
        )

        if has_notes:
            row1 = QHBoxLayout()
            row1.setSpacing(6)

            select_all_button = QPushButton("Select All")
            select_all_button.setObjectName("secondary")
            select_all_button.clicked.connect(self.select_all_pages)
            row1.addWidget(select_all_button, stretch=1)

            suggest_button = QPushButton("✦ Suggest Tags")
            suggest_button.setObjectName("secondary")
            suggest_button.setToolTip(
                "Automatically suggest subject tags based on the card's text content"
            )
            suggest_button.clicked.connect(self.suggest_tags_from_card)
            row1.addWidget(suggest_button, stretch=1)

            add_tags_button = QPushButton("Add Tags")
            add_tags_button.setObjectName("secondary")
            add_tags_button.clicked.connect(self.add_tags)
            row1.addWidget(add_tags_button, stretch=1)

            if not isinstance(self.parent(), AddCards):
                replace_tags_button = QPushButton("Replace Tags")
                replace_tags_button.setObjectName("secondary")
                replace_tags_button.clicked.connect(self.replace_tags)
                row1.addWidget(replace_tags_button, stretch=1)

                remove_tags_button = QPushButton("Remove Tags")
                remove_tags_button.setObjectName("danger")
                remove_tags_button.clicked.connect(self.remove_tags)
                row1.addWidget(remove_tags_button, stretch=1)

            buttons_vbox.addLayout(row1)

            row2 = QHBoxLayout()
            row2.setSpacing(6)

            find_cards_button = QPushButton("Find Cards")
            find_cards_button.clicked.connect(self.search_cards)
            row2.addWidget(find_cards_button, stretch=1)

            if not isinstance(self.parent(), AddCards):
                create_cards_button = QPushButton("Create Cards")
                create_cards_button.clicked.connect(self.create_cards)
                row2.addWidget(create_cards_button, stretch=1)

            row2.addWidget(update_database_button, stretch=1)
            row2.addWidget(guidelines_button, stretch=1)
            row2.addWidget(donate_button, stretch=1)
            buttons_vbox.addLayout(row2)

        else:
            row1 = QHBoxLayout()
            row1.setSpacing(6)

            select_all_button = QPushButton("Select All")
            select_all_button.setObjectName("secondary")
            select_all_button.clicked.connect(self.select_all_pages)
            row1.addWidget(select_all_button, stretch=1)

            find_cards_button = QPushButton("Find Cards")
            find_cards_button.clicked.connect(self.search_cards)
            row1.addWidget(find_cards_button, stretch=1)

            create_cards_button = QPushButton("Create Cards")
            create_cards_button.clicked.connect(self.create_cards)
            row1.addWidget(create_cards_button, stretch=1)

            row1.addWidget(update_database_button, stretch=1)
            row1.addWidget(guidelines_button, stretch=1)
            row1.addWidget(donate_button, stretch=1)
            buttons_vbox.addLayout(row1)

        button_widget = QWidget()
        button_widget.setLayout(buttons_vbox)
        button_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )
        content_layout.addWidget(button_widget, stretch=0)

        layout.addWidget(content_widget)
        self.setLayout(layout)

        # Show recent tags on first open
        self._show_recent_tags()

    # ── Database chip helpers ─────────────────────────────────────────────────

    def _update_selected_count(self):
        """Refresh the 'X selected' label in the results section header."""
        try:
            n = sum(1 for r in self._result_rows if r['checkbox'].isChecked())
            if n == 0:
                self._results_count_label.setText("")
            else:
                self._results_count_label.setText(f"{n} selected")
        except Exception:
            pass

    def _get_active_db_names(self) -> list:
        """Return list of database names whose filter chip is currently active."""
        return [db for db, btn in self._db_chips.items() if btn.isChecked()]

    def _get_active_db_ids(self) -> list:
        """Return list of (db_id, db_name) for all active filter chips."""
        result = []
        for db_name, btn in self._db_chips.items():
            if btn.isChecked():
                db_id = get_database_id(db_name)
                if db_id:
                    result.append((db_id, db_name))
        return result

    def _on_chip_toggled(self):
        """Re-run search (or show recent tags) when a database chip is toggled."""
        if len(self.search_input.text()) >= 2:
            self.search_timer.stop()
            self.perform_search()
        else:
            self.clear_search_results()

    # ── Card count + confidence helpers ──────────────────────────────────────

    def _load_note_tag_strings(self) -> None:
        """
        Fetch every note's raw tag string from the collection DB in one query
        and cache the result on self._note_tag_strings.
        """
        try:
            col = mw.col
            if col is None:
                self._note_tag_strings = []
                self._note_tag_strings_col = None
                return
            col_path = str(col.path)
            if (getattr(self, '_note_tag_strings_col', None) == col_path
                    and hasattr(self, '_note_tag_strings')):
                return
            self._note_tag_strings = col.db.list("select tags from notes")
            self._note_tag_strings_col = col_path
        except Exception as e:
            print(f"[MalleusCardCount] failed to load note tag strings: {e}")
            self._note_tag_strings = []
            self._note_tag_strings_col = None

    def _get_card_count_for_page(self, page: dict) -> int:
        """Return the number of notes tagged with this Notion page."""
        try:
            tag = ''
            properties = page.get('properties', {})
            for prop_name in ('Main Tag', 'Tag'):
                prop = properties.get(prop_name)
                if not prop or not isinstance(prop, dict):
                    continue
                if prop.get('type') == 'formula':
                    val = prop.get('formula', {}).get('string', '').strip()
                    if val:
                        tag = val.split()[0]
                        break
            if not tag:
                return 0
            tag_strings = getattr(self, '_note_tag_strings', [])
            return sum(1 for ts in tag_strings if tag in ts)
        except Exception as e:
            print(f"[MalleusCardCount] error: {e}")
            return 0

    @staticmethod
    def _score_to_dots(score: float, max_score: float = 4.0) -> str:
        """Convert a raw suggestion score to a 5-dot confidence string."""
        normalised = min(score / max_score, 1.0)
        filled = max(1, round(normalised * 5))
        return '●' * filled + '○' * (5 - filled)

    def _make_result_row(self, display_text: str, page: dict,
                         score: float = None,
                         show_count: bool = True,
                         subtitle: str = None) -> tuple:
        """
        Build a single result row widget.

        Layout: [3px accent bar] [db indicator] [checkbox indicator]
                [text block: title / subtitle] [subtag chip] [card count pill] [dots]

        Returns (outer_widget, checkbox, subtag_combo_or_None).
        The subtag_combo is None for databases that have no subtag options
        (Rotation, Textbooks, Guidelines) and for ℹ️ general pages.
        """
        db_name = page.get('_database_name', '')

        # ── Outer wrapper — holds accent bar + inner row ───────────────────
        outer = QWidget()
        # Fixed vertical policy prevents the scroll area from expanding rows
        # to fill unused viewport height.
        outer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        accent_bar = QFrame()
        accent_bar.setFixedWidth(3)
        accent_bar.setFrameShape(QFrame.Shape.NoFrame)
        accent_bar.setStyleSheet("background: transparent;")
        outer_layout.addWidget(accent_bar)

        row = QWidget()
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        outer_layout.addWidget(row, stretch=1)

        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 4, 4, 4)
        row_layout.setSpacing(6)

        # ── Database indicator ─────────────────────────────────────────────
        # • Subjects / Pharmacology → the page's own Search Prefix emoji (🩺 💊 ℹ️)
        # • eTG                     → the eTG logo image (lazy-loaded pixmap)
        # • Rotation / Textbooks / Guidelines → a fixed emoji
        if db_name:
            badge = QLabel()
            badge.setFixedWidth(28)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet("background: transparent;")
            badge.setToolTip(db_name)

            if db_name in ("Subjects", "Pharmacology"):
                # Extract the page's own prefix emoji
                prefix = (page.get('properties', {})
                          .get('Search Prefix', {})
                          .get('formula', {}).get('string', ''))
                badge.setText(prefix or "")
                badge.setStyleSheet(
                    "background: transparent; font-size: 15px;"
                )
            elif db_name == "eTG":
                # Lazy-load the eTG logo pixmap once per dialog instance
                if not hasattr(self, '_etg_pixmap'):
                    import os as _os
                    img_path = _os.path.join(self._addon_dir, 'images', 'eTG.jpg')
                    pm = QPixmap(img_path)
                    self._etg_pixmap = (
                        pm.scaled(22, 22,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                        if not pm.isNull() else None
                    )
                if self._etg_pixmap:
                    badge.setPixmap(self._etg_pixmap)
                else:
                    badge.setText("eTG")
            else:
                emoji = _DB_EMOJI.get(db_name, "")
                badge.setText(emoji)
                badge.setStyleSheet(
                    "background: transparent; font-size: 15px;"
                )

            row_layout.addWidget(badge, stretch=0)

        # ── Checkbox indicator (no text — text lives in the block below) ───
        cb = QCheckBox()
        cb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        row_layout.addWidget(cb, stretch=0)

        # ── Text block: title on top, subtitle below ───────────────────────
        text_block = QWidget()
        text_block.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_block.setMinimumWidth(60)
        text_block_layout = QVBoxLayout(text_block)
        text_block_layout.setContentsMargins(0, 0, 0, 0)
        text_block_layout.setSpacing(1)
        text_block.setStyleSheet("background: transparent;")

        # QLabel is a plain-text widget — it does NOT interpret && as a literal
        # ampersand (that escaping is only for accelerator-capable widgets like
        # QCheckBox / QPushButton).  Strip the double-ampersand here so the
        # label renders a clean single &.
        title_lbl = QLabel(display_text.replace('&&', '&'))
        title_lbl.setStyleSheet(
            "font-size: 13px; font-weight: 500; background: transparent;"
        )
        title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_lbl.setMinimumWidth(40)
        text_block_layout.addWidget(title_lbl)

        if subtitle:
            sub_lbl = QLabel(subtitle.replace('&&', '&'))
            sub_lbl.setStyleSheet(
                "font-size: 11px; color: palette(placeholderText); background: transparent;"
            )
            sub_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text_block_layout.addWidget(sub_lbl)

        row_layout.addWidget(text_block, stretch=1)

        # Make clicking anywhere on the row toggle the checkbox
        def _row_press(event, _cb=cb): _cb.toggle()
        row.mousePressEvent = _row_press

        # ── Subtag chip (inline, shown when checkbox is checked) ───────────
        # Applies to Subjects/Pharmacology 🩺/💊 pages and all eTG pages.
        # ℹ️ general Subjects/Pharmacology pages skip it (no subtag needed).
        subtag_combo = None
        has_subtag_options = db_name in ("Subjects", "Pharmacology", "eTG")
        is_general = _is_general_page(page)
        skip_combo = (db_name in ("Subjects", "Pharmacology") and is_general)
        # (Guidelines pages get NO subtag combo — their linked Subjects pages are
        # surfaced as separate checkable rows, each with its own subtag combo.)

        if has_subtag_options and not skip_combo:
            props = DATABASE_PROPERTIES.get(db_name, [""])

            def _apply_all(selection, rows_ref=self._result_rows):
                """Propagate *selection* to every currently checked chip."""
                for rd in rows_ref:
                    chip = rd.get('subtag_combo')
                    chk  = rd.get('checkbox')
                    if chip is not None and chk is not None and chk.isChecked():
                        idx = chip.findText(selection)
                        if idx >= 0:
                            chip.setCurrentIndex(idx)

            subtag_combo = _SubtagChip(props, apply_all_callback=_apply_all)
            subtag_combo.setVisible(False)

            def _toggle_subtag(state, sc=subtag_combo):
                sc.setVisible(state == 2)

            cb.stateChanged.connect(_toggle_subtag)
            row_layout.addWidget(subtag_combo, stretch=0)

        # ── Card count pill ────────────────────────────────────────────────
        if show_count:
            card_count = self._get_card_count_for_page(page)
            count_label = QLabel(f"{card_count} {'note' if card_count == 1 else 'notes'}")
            count_label.setToolTip("Number of notes in your collection tagged with this page")
            if card_count == 0:
                count_label.setStyleSheet(
                    "color: rgba(128,128,128,0.55); font-size: 11px;"
                    " background: transparent; padding: 1px 7px;"
                )
            else:
                count_label.setStyleSheet(
                    "color: #4a82cc; font-size: 11px; font-weight: 600;"
                    " background: rgba(74,130,204,0.12); border-radius: 8px; padding: 1px 7px;"
                )
            row_layout.addWidget(count_label, stretch=0)

        # ── Confidence dots (suggestions only) ────────────────────────────
        if score is not None:
            dots = self._score_to_dots(score)
            dots_label = QLabel(dots)
            dots_label.setToolTip(f"Suggestion confidence (raw score: {score:.2f})")
            dots_label.setStyleSheet(
                "font-size: 11px; letter-spacing: 2px; color: #f0a500; padding: 1px 4px;"
            )
            row_layout.addWidget(dots_label, stretch=0)

        # ── Accent bar + row background — update on checkbox state change ──
        def _on_state_changed(state, _acc=accent_bar, _row=row, _tb=text_block):
            if state == 2:
                _acc.setStyleSheet("background: #4a82cc;")
                _row.setStyleSheet("background: rgba(74,130,204,0.07);")
                _tb.setStyleSheet("background: transparent;")
            else:
                _acc.setStyleSheet("background: transparent;")
                _row.setStyleSheet("")
                _tb.setStyleSheet("background: transparent;")
            self._update_selected_count()

        cb.stateChanged.connect(_on_state_changed)

        return outer, cb, subtag_combo

    # ── Keyboard navigation ───────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        """
        Keyboard shortcuts for the dialog:

          Up / Down    — move focus between result checkboxes
          Space        — toggle the focused checkbox  (Qt default, kept)
          Enter/Return — trigger primary action (Add Tags if note open,
                         otherwise Create Cards)
          Escape       — close the dialog
          Ctrl+A       — select all checkboxes
          Ctrl+D       — deselect all checkboxes
        """
        key = event.key()
        Qt_Key = Qt.Key

        checkboxes = self._get_result_checkboxes()

        if key in (Qt_Key.Key_Down, Qt_Key.Key_Up):
            if not checkboxes:
                return
            focused = None
            for i, cb in enumerate(checkboxes):
                if cb.hasFocus():
                    focused = i
                    break

            if key == Qt_Key.Key_Down:
                next_idx = (focused + 1) if focused is not None else 0
                next_idx = min(next_idx, len(checkboxes) - 1)
            else:
                next_idx = (focused - 1) if focused is not None else len(checkboxes) - 1
                next_idx = max(next_idx, 0)

            checkboxes[next_idx].setFocus()
            event.accept()
            return

        if key in (Qt_Key.Key_Return, Qt_Key.Key_Enter):
            if self.has_notes_to_process():
                self.add_tags()
            else:
                self.create_cards()
            event.accept()
            return

        if key == Qt_Key.Key_A and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.select_all_pages()
            event.accept()
            return

        if key == Qt_Key.Key_D and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            for cb in checkboxes:
                cb.setChecked(False)
            event.accept()
            return

        super().keyPressEvent(event)

    def _get_result_checkboxes(self) -> list:
        """Return all QCheckBox widgets from the current result rows."""
        return [row['checkbox'] for row in self._result_rows]

    def _get_selected_rows(self) -> list:
        """Return _result_rows entries whose checkbox is checked."""
        return [row for row in self._result_rows if row['checkbox'].isChecked()]

    def _get_row_property_name(self, row_data: dict) -> str:
        """
        Derive the Notion property name to use for tag extraction from a result row.

        Rules:
        • ℹ️ general pages (Subjects/Pharmacology): always "Main Tag"
        • Row has a subtag_combo: return its current text (may be "" = no selection)
        • Rotation/Textbooks/Guidelines (no combo): return "Tag"
        • Subjects with no combo (shouldn't happen for non-general): "Main Tag"
        """
        page = row_data['page']
        db_name = page.get('_database_name', '')

        # General pages always use Main Tag
        if db_name in ("Subjects", "Pharmacology") and _is_general_page(page):
            return "Main Tag"

        subtag_combo = row_data.get('subtag_combo')
        if subtag_combo is not None:
            return subtag_combo.currentText()  # may be "" (no subtag chosen)

        # Databases without subtag combos
        if db_name == "Subjects":
            return "Main Tag"
        return "Tag"

    # ── Suggest tags ──────────────────────────────────────────────────────────

    def suggest_tags_from_card(self):
        """
        Run the local tag suggester against the current note's Text field,
        then show the results so the user can select which ones to apply.

        The database filter chips are not changed — results are stamped with
        _database_name = "Subjects" and displayed using the same checkbox UI
        as a regular search.  The suggested subtag (if any) is pre-set on each
        result's subtag combo so it's ready when the user checks the row.
        """
        notes = self.get_notes_to_process()
        if not notes:
            showInfo("No note found — open a card in the editor first.")
            return

        note = notes[0]
        try:
            card_text = note['Text']
        except Exception:
            card_text = ''

        if not card_text or not card_text.strip():
            showInfo("The card's Text field is empty — nothing to analyse.")
            return

        def _field(name):
            try:
                v = note[name]
                return v if v and v.strip() else ''
            except Exception:
                return ''

        extra_text          = _field('Extra')
        addl_resources_text = _field('Additional Resources')
        source_text         = _field('Source')

        malleus_tooltip("Analysing card text…")
        suggestions = suggest_subject_tags(
            card_text, self.notion_cache,
            extra=extra_text,
            additional_resources=addl_resources_text,
            source=source_text,
        )

        if not suggestions:
            showInfo(
                "No matching subject pages found for this card's content.\n\n"
                "Try searching manually using the search box."
            )
            return

        # Populate results area
        self._clear_checkbox_layout()
        self._result_rows = []
        self._showing_recent = False

        show_count = self.config.get('show_card_counts', False)
        if show_count:
            self._load_note_tag_strings()

        for suggestion in suggestions:
            page          = suggestion['page']
            title         = suggestion['title']
            score         = suggestion['score']
            matched_terms = suggestion.get('matched_terms', [])

            # Stamp database so _make_result_row can show the correct badge/combo
            page['_database_name'] = 'Subjects'

            try:
                suffix = (page['properties']
                          .get('Search Suffix', {})
                          .get('formula', {}).get('string', ''))
                _sub_raw = suffix.lstrip('*').strip()
                _subtitle = (
                    _fix_amp_display(_sub_raw.replace(' (', ' · ').rstrip(')'))
                    if _sub_raw else None
                )
            except Exception:
                _subtitle = None

            row, cb, subtag_combo = self._make_result_row(
                _fix_amp_display(title), page, score=score, show_count=show_count,
                subtitle=_subtitle
            )
            self.checkbox_layout.addWidget(row)
            self._result_rows.append({
                'page': page,
                'checkbox': cb,
                'subtag_combo': subtag_combo,
                'row_widget': row,
            })

            # ── Matched-terms hint ─────────────────────────────────────────
            if matched_terms:
                hint_text = "matched: " + "  ·  ".join(matched_terms)
                hint_lbl = QLabel(hint_text)
                hint_lbl.setStyleSheet(
                    "color: rgba(128,128,128,0.75); font-size: 10px;"
                    " font-style: italic; background: transparent;"
                    " padding-left: 38px; padding-bottom: 2px;"
                )
                hint_lbl.setToolTip(
                    "Terms from this card that matched the suggested page"
                )
                self.checkbox_layout.addWidget(hint_lbl)

        # Pre-set the suggested subtag on every combo (visible once user checks the row)
        subtag = suggestions[0].get('suggested_subtag')
        if subtag:
            for row_data in self._result_rows:
                sc = row_data.get('subtag_combo')
                if sc:
                    idx = sc.findText(subtag)
                    if idx >= 0:
                        sc.setCurrentIndex(idx)

        subtag_label = f" · subtag: {subtag}" if subtag else ""
        self.results_group.setTitle(
            f"Suggested Tags ({len(suggestions)} found{subtag_label})"
        )
        malleus_tooltip(f"Found {len(suggestions)} suggested tag(s)")

    # ── Yield handlers ────────────────────────────────────────────────────────

    def handle_yield_click(self, yield_option):
        """Handle yield segment button clicks — allow deselection of selected button."""
        btn = self.yield_radio_buttons[yield_option]
        _short = {
            "High Yield": "High Yield",
            "Medium Yield": "Medium Yield",
            "Low Yield": "Low Yield",
            "Beyond Medical Student Level": "Beyond Medical School",
        }

        if self._last_checked_yield == yield_option:
            # Deselect current
            btn.setStyleSheet(btn._inactive_style)
            self._last_checked_yield = None
            NotionPageSelector.last_yield_selection = ""
            self._yield_badge.setText("None selected")
            self._yield_badge.setStyleSheet(
                "font-size: 10px; color: palette(placeholderText);"
                " background: palette(midlight); border: 1px solid rgba(128,128,128,0.25);"
                " border-radius: 5px; padding: 1px 6px;"
            )
        else:
            # Deselect previous
            if self._last_checked_yield and self._last_checked_yield in self.yield_radio_buttons:
                old_btn = self.yield_radio_buttons[self._last_checked_yield]
                old_btn.setStyleSheet(old_btn._inactive_style)
            # Select new
            btn.setStyleSheet(btn._active_style)
            self._last_checked_yield = yield_option
            NotionPageSelector.last_yield_selection = yield_option
            r, g, b = btn._yield_rgb
            self._yield_badge.setText(_short.get(yield_option, yield_option))
            self._yield_badge.setStyleSheet(
                f"font-size: 10px; color: {btn._yield_color}; font-weight: 600;"
                f" background: rgba({r},{g},{b},0.12);"
                f" border: 1px solid rgba({r},{g},{b},0.30);"
                " border-radius: 5px; padding: 1px 6px;"
            )

    def get_selected_yield_tags(self):
        """Get the selected yield tags from the segmented control."""
        yield_tag_mapping = {
            "High Yield": "#Malleus_CM::#Yield::High",
            "Medium Yield": "#Malleus_CM::#Yield::Medium",
            "Low Yield": "#Malleus_CM::#Yield::Low",
            "Beyond Medical Student Level": "#Malleus_CM::#Yield::Beyond_medical_student_level"
        }
        if self._last_checked_yield:
            tag = yield_tag_mapping.get(self._last_checked_yield)
            return [tag] if tag else []
        return []

    def get_existing_yield_tags(self, tags):
        """Extract existing yield tags from a list of tags."""
        yield_pattern = "#Malleus_CM::#Yield::"
        return [tag for tag in tags if tag.startswith(yield_pattern)]

    def get_yield_search_query(self):
        """Get the yield search query for the Anki browser."""
        yield_search_mapping = {
            "High Yield": "tag:#Malleus_CM::#Yield::High",
            "Medium Yield": "tag:#Malleus_CM::#Yield::Medium",
            "Low Yield": "tag:#Malleus_CM::#Yield::Low",
            "Beyond Medical Student Level": "tag:#Malleus_CM::#Yield::Beyond_medical_student_level"
        }
        if self._last_checked_yield:
            return yield_search_mapping.get(self._last_checked_yield, "")
        return ""

    def get_paediatrics_tag(self):
        """Get the paediatrics rotation tag if the paediatrics checkbox is checked."""
        if hasattr(self, 'paeds_checkbox') and self.paeds_checkbox.isChecked():
            return ["#Malleus_CM::#Resources_by_Rotation::Paediatrics"]
        return []

    # ── Recent tags ───────────────────────────────────────────────────────────

    def _recent_tags_path(self):
        import os
        return os.path.join(self._addon_dir, "recent_tags.json")

    def _load_recent_tags(self):
        """Load the list of recently used page selections from disk."""
        import json, os
        path = self._recent_tags_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def _save_recent_tag(self, page, database_name=None):
        """Prepend a page to the recent tags list and persist it (max 8 entries)."""
        import json
        if database_name is None:
            database_name = page.get('_database_name', '')
        try:
            title = ""
            if database_name == "Textbooks":
                title = (page.get('properties', {}).get('Search Term', {})
                         .get('formula', {}).get('string', '') or "Untitled")
            else:
                title_list = page.get('properties', {}).get('Name', {}).get('title', [])
                title = title_list[0]['text']['content'] if title_list else "Untitled"

            suffix = (page.get('properties', {}).get('Search Suffix', {})
                      .get('formula', {}).get('string', ''))
            # Prefix emoji lives in the badge widget, not stored in display_text
            display_text = _fix_amp_display(f"{title} {suffix}".strip())

            entry = {
                'page_id': page.get('id', ''),
                'database_name': database_name,
                'display_text': display_text,
                'page_data': page,
            }

            recent = self._load_recent_tags()
            recent = [r for r in recent if r.get('page_id') != entry['page_id']]
            recent.insert(0, entry)
            recent = recent[:8]

            with open(self._recent_tags_path(), 'w', encoding='utf-8') as f:
                json.dump(recent, f)
        except Exception:
            pass

    def _show_recent_tags(self):
        """Populate the checkbox layout with recently used tags."""
        recent = self._load_recent_tags()
        if not recent:
            return

        from aqt.qt import QSizePolicy as _QSP
        sep_widget = QWidget()
        sep_widget.setSizePolicy(_QSP.Policy.Expanding, _QSP.Policy.Fixed)
        sep_layout = QHBoxLayout(sep_widget)
        sep_layout.setContentsMargins(2, 6, 2, 2)
        sep_layout.setSpacing(6)

        for _ in range(2):
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet("color: palette(mid); background: transparent;")
            sep_layout.addWidget(line, stretch=1)
            if _ == 0:
                lbl = QLabel("RECENT")
                lbl.setStyleSheet(
                    "font-size: 9px; color: palette(placeholderText); "
                    "background: transparent; letter-spacing: 1px;"
                )
                sep_layout.addWidget(lbl)

        self.checkbox_layout.addWidget(sep_widget)

        for entry in recent:
            page = entry.get('page_data')
            if not page:
                continue
            db_name = entry.get('database_name', '')
            # Stamp _database_name so the badge and subtag combo render correctly
            page['_database_name'] = db_name

            # Re-derive clean title from page properties (same logic as search path)
            if db_name == "Textbooks":
                title = (page.get('properties', {}).get('Search Term', {})
                         .get('formula', {}).get('string', '') or 'Untitled')
            else:
                title_list = page.get('properties', {}).get('Name', {}).get('title', [])
                title = title_list[0]['text']['content'] if title_list else 'Untitled'

            # Strip Search Prefix emoji for Subjects / Pharmacology (badge shows it)
            if db_name in ("Subjects", "Pharmacology"):
                stored_prefix = (page.get('properties', {})
                                 .get('Search Prefix', {})
                                 .get('formula', {}).get('string', ''))
                if stored_prefix and title.startswith(stored_prefix):
                    title = title[len(stored_prefix):].lstrip()

            # Build subtitle from Search Suffix — same transform as search results
            suffix = (page.get('properties', {}).get('Search Suffix', {})
                      .get('formula', {}).get('string', ''))
            _sub_raw = suffix.lstrip('*').strip()
            _subtitle = (
                _fix_amp_display(_sub_raw.replace(' (', ' · ').rstrip(')'))
                if _sub_raw else None
            )

            row, cb, subtag_combo = self._make_result_row(
                _fix_amp_display(title), page, show_count=False, subtitle=_subtitle
            )
            self.checkbox_layout.addWidget(row)
            self._result_rows.append({
                'page': page,
                'checkbox': cb,
                'subtag_combo': subtag_combo,
                'row_widget': row,
            })

        self._showing_recent = True

    def _update_cache_age_label(self, _unused=None):
        """Update the 'Update Database' button tooltip showing the oldest cache age."""
        if not hasattr(self, '_update_database_button'):
            return
        try:
            import time as _time
            from ..config import DATABASES
            oldest_days = 0
            oldest_name = ""
            missing_name = None
            for db_id, db_name in DATABASES:
                _, ts = self.notion_cache.load_from_cache(db_id, warn_if_expired=False)
                if ts <= 0:   # no cache file yet (e.g. right after an add-on update)
                    missing_name = db_name
                    break
                age_days = int((_time.time() - ts) / 86400)
                if age_days > oldest_days:
                    oldest_days = age_days
                    oldest_name = db_name
            if missing_name:
                age_text = f"{missing_name}: not downloaded yet"
                warning = " — click to download"
            elif oldest_days == 0:
                age_text = "all databases updated today"
                warning = ""
            elif oldest_days == 1:
                age_text = f"{oldest_name}: 1 day old"
                warning = ""
            else:
                age_text = f"{oldest_name}: {oldest_days} days old"
                warning = " — consider updating" if oldest_days > 7 else ""
            self._update_database_button.setToolTip(
                f"Cache: {age_text}{warning}\n"
                "Download the latest Malleus database cache\n"
                "Shift+click: full rebuild directly from Notion (slower)"
            )
        except Exception:
            self._update_database_button.setToolTip(
                "Download the latest Malleus database cache\n"
                "Shift+click: full rebuild directly from Notion (slower)"
            )

    # ── Search ────────────────────────────────────────────────────────────────

    def _clear_checkbox_layout(self):
        """Remove all widgets from checkbox_layout."""
        for i in reversed(range(self.checkbox_layout.count())):
            widget = self.checkbox_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        if hasattr(self, '_results_count_label'):
            self._results_count_label.setText("")

    def clear_search_results(self):
        """Clear search results and show recent tags."""
        self._clear_checkbox_layout()
        self._result_rows = []
        self._showing_recent = False
        self._show_recent_tags()

    def _search_single_database(self, db_id: str, db_name: str, search_term: str) -> list:
        """
        Load one database from cache, filter by search_term, stamp each result
        with _database_name, and return the filtered page list.
        """
        try:
            cached_pages, _ = self.notion_cache.load_from_cache(db_id)
            if not cached_pages:
                return []
            results = self.notion_cache.filter_pages(cached_pages, search_term)
            for page in results:
                page['_database_name'] = db_name
            return results
        except Exception as e:
            print(f"[Search] Error searching {db_name}: {e}")
            return []

    def perform_search(self):
        search_term = self.search_input.text()
        if not search_term or len(search_term) < 2:
            self.clear_search_results()
            return

        enabled_dbs = self._get_active_db_ids()
        if not enabled_dbs:
            self.clear_search_results()
            return

        # Search all enabled databases sequentially (all local JSON, very fast)
        all_results = []
        for db_id, db_name in enabled_dbs:
            all_results.extend(self._search_single_database(db_id, db_name, search_term))

        # Apply per-database score bias then sort globally, take top N
        for page in all_results:
            bias = _DB_SCORE_BIAS.get(page.get('_database_name', ''), 1.0)
            page['_composite_score'] = page.get('_composite_score', 0) * bias
        all_results.sort(key=lambda p: -p.get('_composite_score', 0))
        all_results = all_results[:_MAX_SEARCH_RESULTS]

        # Rebuild the result area
        self._clear_checkbox_layout()
        self._result_rows = []
        self._showing_recent = False

        if not all_results:
            if not self.config['autosearch']:
                malleus_tooltip("No results found. Try a different search term")
            return

        # Card counts are opt-in (config: show_card_counts) — computing them
        # scans every note's tags and slows results down on large collections.
        threshold = self.config.get('card_count_threshold', 10)
        show_count = (self.config.get('show_card_counts', False)
                      and len(all_results) <= threshold)
        if show_count:
            self._load_note_tag_strings()

        for page in all_results:
            db_name = page.get('_database_name', '')
            try:
                if db_name == "Textbooks":
                    title = (page['properties'].get('Search Term', {})
                             .get('formula', {}).get('string', '') or "Untitled")
                else:
                    title_list = page['properties']['Name']['title']
                    title = title_list[0]['text']['content'] if title_list else "Untitled"

                search_suffix = (page['properties'].get('Search Suffix', {})
                                 .get('formula', {}).get('string', ''))

                # Prefix emoji (🩺 💊 ℹ️) lives in the badge, not the checkbox text
                _title_text = _fix_amp_display(title)
                _sub_raw = search_suffix.lstrip('*').strip()
                _subtitle = (
                    _fix_amp_display(_sub_raw.replace(' (', ' · ').rstrip(')'))
                    if _sub_raw else None
                )

                row, cb, subtag_combo = self._make_result_row(
                    _title_text, page, show_count=show_count, subtitle=_subtitle
                )
                self.checkbox_layout.addWidget(row)
                self._result_rows.append({
                    'page': page,
                    'checkbox': cb,
                    'subtag_combo': subtag_combo,
                    'row_widget': row,
                })
                # Pages linked to Subjects pages: offer each linked subject as its
                # own checkable row, revealed when this row is checked.
                if db_name == "Pharmacology" and _relation_ids(page, 'Related Subject'):
                    self._append_related_subject_rows(page, cb, show_count, 'Related Subject')
                elif db_name == "Guidelines" and _relation_ids(page, 'Related Subjects'):
                    self._append_related_subject_rows(page, cb, show_count, 'Related Subjects')
            except Exception as e:
                showInfo(f"Error processing page: {e}")

    def on_search_text_changed(self, text):
        """Handle search text changes with debounce."""
        if self.config['autosearch']:
            if len(text) >= 2:
                self.search_timer.start(self.config['search_delay'])
            else:
                self.clear_search_results()

    def select_all_pages(self):
        for cb in self._get_result_checkboxes():
            cb.setChecked(True)

    # ── Tag extraction helpers ────────────────────────────────────────────────

    def _load_id_lookup(self, database_id: str) -> dict:
        """Load a database's cache and index it by page id (dash + dash-less)."""
        pages, _ = self.notion_cache.load_from_cache(database_id)
        lookup = {}
        for p in pages:
            lookup[p['id']] = p
            lookup[p['id'].replace('-', '')] = p
        return lookup

    # ── Related-subject rows (Pharmacology → Subjects) ────────────────────────

    @staticmethod
    def _page_title(page: dict) -> str:
        return "".join(t.get('plain_text', '')
                       for t in page.get('properties', {}).get('Name', {}).get('title', []))

    def _active_subjects_index(self):
        """(by_id, by_name) index of the ACTIVE Subjects cache (the one the add-on
        uses — generated for the testing copy, formula-based for the original)."""
        if getattr(self, '_rs_active_idx', None) is None:
            by_id, by_name = {}, {}
            pages, _ = self.notion_cache.load_from_cache(SUBJECT_DATABASE_ID)
            for p in pages:
                by_id[p['id']] = p
                by_id[p['id'].replace('-', '')] = p
                by_name.setdefault(self._page_title(p), p)
            self._rs_active_idx = (by_id, by_name)
        return self._rs_active_idx

    def _original_subjects_byid(self):
        """id→page index of the ORIGINAL Subjects cache, used only to bridge the
        testing pharma copy's `Related Subject` ids (which point at original
        subject ids) to a name we can find in the active cache.  Empty when the
        active cache already IS the original (release)."""
        if getattr(self, '_rs_orig_idx', None) is None:
            idx = {}
            if SUBJECT_DATABASE_ID_ORIGINAL != SUBJECT_DATABASE_ID:
                try:
                    pages, _ = self.notion_cache.load_from_cache(SUBJECT_DATABASE_ID_ORIGINAL)
                    for p in pages:
                        idx[p['id']] = p
                        idx[p['id'].replace('-', '')] = p
                except Exception as e:
                    print(f"[RelatedSubject] could not load original subjects cache: {e}")
            self._rs_orig_idx = idx
        return self._rs_orig_idx

    def _resolve_related_subjects(self, page: dict, relation_prop: str) -> list:
        """Resolve a page's subject-relation ids (Pharmacology `Related Subject`
        or Guidelines `Related Subjects`) to Subjects page objects, preferring the active
        (generated) cache — including a name bridge through the original cache so
        the testing copy resolves to the clean generated page (with proper
        #Question_Banks eMedici tags)."""
        ids = _relation_ids(page, relation_prop)
        if not ids:
            return []
        active_by_id, active_by_name = self._active_subjects_index()
        orig_by_id = self._original_subjects_byid()
        out, seen = [], set()
        for rid in ids:
            sp = active_by_id.get(rid) or active_by_id.get(rid.replace('-', ''))
            if sp is None:
                op = orig_by_id.get(rid) or orig_by_id.get(rid.replace('-', ''))
                if op is not None:
                    sp = active_by_name.get(self._page_title(op)) or op
            if sp is not None and id(sp) not in seen:
                seen.add(id(sp))
                out.append(sp)
        return out

    def _append_related_subject_rows(self, parent_page: dict, parent_cb,
                                     show_count: bool, relation_prop: str):
        """For a Pharmacology (`Related Subject`) or Guidelines (`Related Subjects`)
        result row, render each linked Subjects page as its own indented,
        independently-checkable row (reusing the Subjects row UI), connected by a
        left tree line and revealed only while the parent row is checked."""
        subjects = self._resolve_related_subjects(parent_page, relation_prop)
        if not subjects:
            return

        # Group holds the child rows stacked tightly; each row carries its own
        # tree-gutter so the line is continuous and ends at a └ on the last child.
        group = QWidget()
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        gv = QVBoxLayout(group)
        gv.setContentsMargins(16, 0, 0, 0)       # places the rail under the parent badge
        gv.setSpacing(0)

        child_cbs = []
        n = len(subjects)
        for i, sp in enumerate(subjects):
            sp = dict(sp)                       # shallow copy — don't mutate cache
            sp['_database_name'] = 'Subjects'
            sp['_related_subject'] = True

            title_list = sp.get('properties', {}).get('Name', {}).get('title', [])
            title = "".join(t.get('plain_text', '') for t in title_list) or "Untitled"
            suffix = (sp['properties'].get('Search Suffix', {})
                      .get('formula', {}).get('string', '') or '')
            _sub_raw = suffix.lstrip('*').strip()
            subtitle = (_fix_amp_display(_sub_raw.replace(' (', ' · ').rstrip(')'))
                        if _sub_raw else None)

            row, cb, subtag_combo = self._make_result_row(
                _fix_amp_display(title), sp, show_count=show_count, subtitle=subtitle
            )

            row_h = QWidget()
            row_h.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            rh = QHBoxLayout(row_h)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(0)
            rh.addWidget(_TreeGutter(is_last=(i == n - 1)), 0)
            rh.addWidget(row, 1)
            gv.addWidget(row_h)

            self._result_rows.append({
                'page': sp,
                'checkbox': cb,
                'subtag_combo': subtag_combo,
                'row_widget': row_h,
            })
            child_cbs.append(cb)

        group.setVisible(False)                  # revealed only while parent is checked
        self.checkbox_layout.addWidget(group)

        def _toggle_related(state, _g=group, _cbs=child_cbs):
            visible = (state == 2)
            _g.setVisible(visible)
            if not visible:
                for rcb in _cbs:        # collapsing also clears the child selections
                    rcb.setChecked(False)

        parent_cb.stateChanged.connect(_toggle_related)

    def _get_guidelines_tags_for_page(self, page, rotation_lookup) -> list:
        """
        Tags for a single Guidelines page:

          1. its own #Guidelines:: hierarchy tag(s) — precomputed into the cache
             `Tag` from the `Parent item` graph at build time.
          2. one rotation tag per linked `Rotation` page (that page's `Tag`).

        Linked Subjects pages are NOT handled here — they are offered as separate
        checkable rows (see _append_related_subject_rows) so the user can pick
        each subject's subtag and opt in/out individually.
        """
        tags = []

        # 1. own hierarchy tag(s)
        tag_prop = page['properties'].get('Tag')
        if tag_prop and tag_prop.get('type') == 'formula':
            s = tag_prop['formula'].get('string', '').strip()
            if s:
                tags.extend(s.split())

        # 2. rotation cross-references
        for rid in _relation_ids(page, 'Rotation'):
            rp = rotation_lookup.get(rid) or rotation_lookup.get(rid.replace('-', ''))
            if not rp:
                continue
            rt = rp['properties'].get('Tag')
            if rt and rt.get('type') == 'formula':
                s = rt['formula'].get('string', '').strip()
                if s:
                    tags.extend(s.split())

        # de-dupe, preserve order
        out, seen = [], set()
        for t in tags:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _get_tags_for_page(self, page: dict, db_name: str, property_name: str) -> list:
        """
        Extract Anki tag strings for one page using the given property name.

        For eTG pages the cross-database Subject/Pharmacology lookup is
        performed here.  Guidelines pages additionally pull rotation/subject
        cross-reference tags from their relations.  For all other databases the
        named formula property is read, with a fallback to Main Tag (Subjects)
        or Tag (others).
        """
        if db_name == "Guidelines":
            rotation_lookup = (self._load_id_lookup(ROTATION_DATABASE_ID)
                               if _relation_ids(page, 'Rotation') else {})
            return self._get_guidelines_tags_for_page(page, rotation_lookup)

        if db_name == "eTG":
            subjects_subtags    = {s for s in DATABASE_PROPERTIES.get("Subjects", []) if s}
            pharmacology_subtags = {s for s in DATABASE_PROPERTIES.get("Pharmacology", []) if s}
            subjects_lookup     = {}
            pharmacology_lookup = {}

            if property_name in subjects_subtags:
                subject_pages, _ = self.notion_cache.load_from_cache(SUBJECT_DATABASE_ID)
                for p in subject_pages:
                    subjects_lookup[p['id']] = p
                    subjects_lookup[p['id'].replace('-', '')] = p
            elif property_name in pharmacology_subtags:
                pharm_pages, _ = self.notion_cache.load_from_cache(PHARMACOLOGY_DATABASE_ID)
                for p in pharm_pages:
                    pharmacology_lookup[p['id']] = p
                    pharmacology_lookup[p['id'].replace('-', '')] = p

            return self._get_etg_tags_for_page(
                page, property_name,
                subjects_subtags, pharmacology_subtags,
                subjects_lookup, pharmacology_lookup,
            )

        # Non-eTG path
        tag_prop = page['properties'].get(property_name)

        # Fall back when the subtag property is empty/missing
        if (not tag_prop or
                (tag_prop.get('type') == 'formula' and
                 not tag_prop.get('formula', {}).get('string', '').strip())):
            fallback = 'Main Tag' if db_name == 'Subjects' else 'Tag'
            tag_prop = page['properties'].get(fallback)

        if tag_prop and tag_prop.get('type') == 'formula':
            val = tag_prop['formula'].get('string', '').strip()
            if val:
                return val.split()

        return []

    def get_tags_from_selected_pages(self) -> list:
        """
        Extract Anki tag strings from all currently checked result rows.

        Each row's per-result subtag combo (if visible) determines which
        property to read for that page.  eTG cross-database lookups are
        batched to avoid redundant cache loads.
        """
        selected_rows = self._get_selected_rows()
        if not selected_rows:
            return ["#Malleus_CM::#TO_BE_TAGGED"]

        # Pre-build eTG cross-DB lookups once if any eTG rows are selected
        etg_rows = [r for r in selected_rows if r['page'].get('_database_name') == 'eTG']
        subjects_lookup     = {}
        pharmacology_lookup = {}
        etg_subjects_subtags    = {s for s in DATABASE_PROPERTIES.get("Subjects", []) if s}
        etg_pharm_subtags       = {s for s in DATABASE_PROPERTIES.get("Pharmacology", []) if s}

        if etg_rows:
            used_props = {self._get_row_property_name(r) for r in etg_rows}
            if used_props & etg_subjects_subtags:
                subjects_lookup = self._load_id_lookup(SUBJECT_DATABASE_ID)
            if used_props & etg_pharm_subtags:
                pharmacology_lookup = self._load_id_lookup(PHARMACOLOGY_DATABASE_ID)

        # Pre-build the Rotation lookup once if any Guidelines rows are selected
        # (linked Subjects are handled as their own selectable rows, not here).
        guideline_rows = [r for r in selected_rows if r['page'].get('_database_name') == 'Guidelines']
        rotation_lookup = {}
        if guideline_rows and any(_relation_ids(r['page'], 'Rotation') for r in guideline_rows):
            rotation_lookup = self._load_id_lookup(ROTATION_DATABASE_ID)

        tags = []
        for row_data in selected_rows:
            page    = row_data['page']
            db_name = page.get('_database_name', '')
            prop    = self._get_row_property_name(row_data)

            if db_name == 'eTG':
                tags.extend(self._get_etg_tags_for_page(
                    page, prop,
                    etg_subjects_subtags, etg_pharm_subtags,
                    subjects_lookup, pharmacology_lookup,
                ))
            elif db_name == 'Guidelines':
                tags.extend(self._get_guidelines_tags_for_page(page, rotation_lookup))
            else:
                tags.extend(self._get_tags_for_page(page, db_name, prop))

        return tags if tags else ["#Malleus_CM::#TO_BE_TAGGED"]

    # ── Search cards ──────────────────────────────────────────────────────────

    def search_cards(self):
        selected_rows = self._get_selected_rows()
        if not selected_rows:
            showInfo("Please select at least one page")
            return

        # Collect all tag strings from selected pages
        tags = []
        for row_data in selected_rows:
            page    = row_data['page']
            db_name = page.get('_database_name', '')
            # For search purposes use the base 'Tag' property (all subtag variants)
            tag_prop = page['properties'].get('Tag')
            if tag_prop and tag_prop.get('type') == 'formula':
                val = tag_prop['formula'].get('string', '')
                tags.extend(val.split())

        if not tags:
            showInfo("Could not determine tags for selected pages.")
            return

        # Determine subtag filter from each row's combo
        # Build per-row search queries
        individual_tags = list(dict.fromkeys(tags))  # deduplicate, preserve order

        # Get subtag from any selected row (use first non-empty)
        property_name = ""
        for row_data in selected_rows:
            p = self._get_row_property_name(row_data)
            if p and p not in ("Tag", "Main Tag", ""):
                property_name = p
                break

        if property_name and property_name not in ("Tag", "Main Tag"):
            subtag = f"::*{property_name}".replace(' ', '_')
        else:
            subtag = ""

        def escape_underscores(tag):
            return tag.replace('_', '\\_')

        search_query = " or ".join(
            f'"tag:{escape_underscores(tag)}{subtag}"' for tag in individual_tags
        )

        yield_query = self.get_yield_search_query()
        if yield_query:
            search_query = f"({search_query}) and ({yield_query})"

        if isinstance(self.parent(), Browser):
            browser = self.parent()
            browser.form.searchEdit.lineEdit().setText(search_query)
            if hasattr(browser, 'onSearch'):
                browser.onSearch()
            else:
                browser.onSearchActivated()
        else:
            open_browser_with_search(search_query)

        for row_data in selected_rows:
            self._save_recent_tag(row_data['page'])

        self.accept()

    # ── Property content helper ───────────────────────────────────────────────

    def get_property_content(self, page, property_name):
        """Extract property content from page data with enhanced formatting."""
        prop = page['properties'].get(property_name)

        if prop and prop['type'] == 'formula':
            formula_value = prop['formula']
            if formula_value['type'] == 'string':
                source_text = formula_value.get('string', '')
                if not isinstance(source_text, str):
                    source_text = str(source_text) if source_text is not None else ""

                def format_urls(text):
                    import re
                    url_pattern = _re.compile(r'(https?://\S+)')
                    def replace_url(match):
                        url = match.group(1)
                        display_text = url.split('//')[1].split('/')[0]
                        return f'<a href="{url}" target="_blank">{display_text}</a>'
                    return url_pattern.sub(replace_url, text)

                return format_urls(source_text)
            return ""

        if prop and prop['type'] == 'rich_text' and prop['rich_text']:
            return prop['rich_text'][0]['text']['content']
        return ""

    # ── Create cards ──────────────────────────────────────────────────────────

    def create_cards(self):
        selected_yields = self.get_selected_yield_tags()
        if len(selected_yields) > 1:
            showInfo("Please select only one yield level when creating cards")
            return
        if len(selected_yields) == 0:
            showInfo("Please select one yield level when creating cards")
            return

        selected_rows = self._get_selected_rows()
        if not selected_rows:
            showInfo("Please select at least one page")
            return

        # Validate that pages needing a subtag (🩺 Subjects/Pharmacology, or
        # Guidelines linked to Subjects) have one chosen
        for row_data in selected_rows:
            page    = row_data['page']
            if _page_needs_subtag(page):
                sc = row_data.get('subtag_combo')
                if sc is None or not sc.currentText():
                    try:
                        title_list = page['properties']['Name']['title']
                        title = title_list[0]['text']['content'] if title_list else "this page"
                    except Exception:
                        title = "this page"
                    showInfo(
                        f"Please select a subtag for:\n{title}\n\n"
                        "(Check the result to reveal the subtag dropdown)"
                    )
                    return

        tags = self.get_tags_from_selected_pages()
        selected_db_names = {r['page'].get('_database_name', '') for r in selected_rows}

        all_tags = tags + selected_yields + self.get_paediatrics_tag()

        note = {
            'deckName':  self.config['deck_name'],
            'modelName': 'MalleusCM - Cloze (Malleus Clinical Medicine [AU/NZ] / Stapedius)',
            'fields':    {},
            'tags':      all_tags,
        }

        # Populate Source field for eTG / Textbooks / Guidelines pages
        source_dbs = selected_db_names & {"eTG", "Textbooks", "Guidelines"}
        if source_dbs:
            sources = []
            for row_data in selected_rows:
                if row_data['page'].get('_database_name') in source_dbs:
                    source = self.get_property_content(row_data['page'], 'Source')
                    if source:
                        sources.append(source)
            unique_sources = list(dict.fromkeys(sources))
            if unique_sources:
                note['fields']['Source'] = '<br>'.join(unique_sources)

        self.guiAddCards(note)

        # Async-populate Extra (Synced) for Subjects and eTG cards
        if selected_db_names & {"Subjects", "eTG"}:
            self._async_update_extra_synced(all_tags)

    # ── Extra (Synced) helpers ────────────────────────────────────────────────

    def _apply_extra_synced_dialog(self, anki_note, notion_cache, parent_widget=None, note_context=None):
        """
        Show the SE selection dialog for Extra (Synced) and update the note in place.
        Does NOT flush the note.
        """
        from aqt.qt import QDialog
        if parent_widget is None:
            parent_widget = self

        try:
            current_field = anki_note[EXTRA_FIELD]
        except Exception:
            return

        entries = get_matching_se_entries(list(anki_note.tags), notion_cache, SYNCED_EXTRA_DATABASE_ID)

        if not entries:
            existing_se_ids = get_existing_se_ids_from_field(current_field)
            if existing_se_ids or current_field.strip():
                anki_note[EXTRA_FIELD] = ""
                anki_note.tags = [t for t in anki_note.tags
                                  if not t.startswith(SE_EXTRA_TAG_PREFIX)]
            return

        existing_se_ids = get_existing_se_ids_from_field(current_field)

        all_already_present = all(
            e.get('se_id') and e['se_id'] in existing_se_ids
            for e in entries
        )
        if all_already_present:
            return

        dialog = SyncedExtraSelectionDialog(
            parent_widget, entries, existing_se_ids, note_context=note_context
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dialog.get_selected_entries()
        anki_note[EXTRA_FIELD] = build_field_from_selected_entries(selected)
        base_tags = [t for t in anki_note.tags if not t.startswith(SE_EXTRA_TAG_PREFIX)]
        for entry in selected:
            if entry.get("tag"):
                base_tags.append(entry["tag"])
        anki_note.tags = list(set(base_tags))

    def _schedule_extra_synced(self, anki_note, notion_cache, subjects_db=True):
        """
        Update both synced fields on a note.
        Does NOT flush the note.
        """
        set_additional_resources_on_note(anki_note, notion_cache)
        if subjects_db:
            note_context = None
            try:
                note_context = anki_note['Text'] or None
            except Exception:
                pass
            self._apply_extra_synced_dialog(
                anki_note, notion_cache,
                parent_widget=self,
                note_context=note_context,
            )

    def _async_update_extra_synced(self, tags):
        """
        Called after Create Cards opens the AddCards dialog.
        Shows the SE selection dialog for Extra (Synced) and auto-fills
        Additional Resources (Synced).
        """
        from aqt import dialogs
        from aqt.qt import QDialog
        try:
            ac = dialogs._dialogs.get('AddCards', [None, None])[1]
            if not (ac and hasattr(ac, 'editor') and ac.editor.note):
                return
            note = ac.editor.note
            changed = False

            additional = build_additional_resources_content(tags, self.notion_cache)
            if additional and 'Additional Resources (Synced)' in note:
                note['Additional Resources (Synced)'] = additional
                changed = True

            entries = get_matching_se_entries(tags, self.notion_cache, SYNCED_EXTRA_DATABASE_ID)
            if entries:
                try:
                    current_field = note[EXTRA_FIELD]
                except Exception:
                    current_field = ''
                existing_se_ids = get_existing_se_ids_from_field(current_field)
                all_present = all(
                    e.get('se_id') and e['se_id'] in existing_se_ids
                    for e in entries
                )
                if all_present:
                    entries = []
            if entries:
                dlg = SyncedExtraSelectionDialog(ac, entries, existing_se_ids)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    selected = dlg.get_selected_entries()
                    if 'Extra (Synced)' in note:
                        note[EXTRA_FIELD] = build_field_from_selected_entries(selected)
                    base_tags = [t for t in note.tags if not t.startswith(SE_EXTRA_TAG_PREFIX)]
                    for entry in selected:
                        if entry.get('tag'):
                            base_tags.append(entry['tag'])
                    note.tags = list(set(base_tags))
                    changed = True

            if changed:
                ac.editor.loadNote()
        except Exception as e:
            print(f"[ExtraSync] Error in _async_update_extra_synced: {e}")

    # ── guiAddCards ───────────────────────────────────────────────────────────

    def guiAddCards(self, note):
        collection = mw.col
        print(self.parent())

        if isinstance(self.parent(), AddCards):
            addCards = self.parent()
            current_note = addCards.editor.note

            if 'tags' in note:
                current_tags = current_note.tags
                current_tags.extend(note['tags'])
                current_note.tags = list(set(current_tags))

            if 'fields' in note:
                for name, value in note['fields'].items():
                    try:
                        if current_note[name] is not None:
                            current_note[name] = value
                    except Exception:
                        pass

            try:
                addCards.editor.loadNote()
            except TypeError:
                try:
                    addCards.editor.loadNote(full=True)
                except Exception:
                    addCards.editor.loadNote(current_note)
            return

        deck = collection.decks.by_name(note['deckName'])
        if deck is None:
            raise Exception('deck was not found: {}'.format(note['deckName']))

        collection.decks.select(deck['id'])
        savedMid = deck.pop('mid', None)

        model = collection.models.by_name(note['modelName'])
        if model is None:
            raise Exception('model was not found: {}'.format(note['modelName']))

        collection.models.set_current(model)
        collection.models.update(model)

        ankiNote = anki.notes.Note(collection, model)

        if 'fields' in note:
            for name, value in note['fields'].items():
                if name in ankiNote:
                    ankiNote[name] = value

        if 'tags' in note:
            ankiNote.tags = note['tags']

        def openNewWindow():
            nonlocal ankiNote
            addCards = dialogs.open('AddCards', mw)
            if savedMid:
                deck['mid'] = savedMid
            addCards.editor.set_note(ankiNote)
            addCards.activateWindow()

        currentWindow = dialogs._dialogs['AddCards'][1]
        if currentWindow is not None:
            currentWindow.setAndFocusNote(ankiNote)
        else:
            openNewWindow()

        self.accept()

    # ── eTG cross-database lookup ─────────────────────────────────────────────

    def _get_etg_tags_for_page(self, page, property_name,
                                subjects_subtags, pharmacology_subtags,
                                subjects_lookup, pharmacology_lookup):
        """
        Return the tags to apply for a single eTG page.

        Always includes the eTG page's own 'Tag' formula value.  When the user
        has selected a subtag that belongs to the Subjects or Pharmacology
        database, also looks up every page linked via the corresponding relation
        property and appends the formula value of that subtag from the linked page.
        """
        tags = []

        etg_tag_prop = page['properties'].get('Tag')
        if etg_tag_prop and etg_tag_prop.get('type') == 'formula':
            tag_str = etg_tag_prop['formula'].get('string', '').strip()
            if tag_str:
                tags.extend(tag_str.split())

        if not property_name or property_name in ('Tag', 'Main Tag'):
            return tags

        if property_name in subjects_subtags:
            subject_rel = page['properties'].get('Subject', {})
            for rel in subject_rel.get('relation', []):
                rel_id = rel.get('id', '')
                subject_page = subjects_lookup.get(rel_id) or subjects_lookup.get(rel_id.replace('-', ''))
                if not subject_page:
                    continue
                subtag_prop = subject_page['properties'].get(property_name)
                if subtag_prop and subtag_prop.get('type') == 'formula':
                    subtag_str = subtag_prop['formula'].get('string', '').strip()
                    if subtag_str:
                        tags.extend(subtag_str.split())

        elif property_name in pharmacology_subtags:
            pharm_rel = page['properties'].get('Pharmacology', {})
            for rel in pharm_rel.get('relation', []):
                rel_id = rel.get('id', '')
                pharm_page = pharmacology_lookup.get(rel_id) or pharmacology_lookup.get(rel_id.replace('-', ''))
                if not pharm_page:
                    continue
                subtag_prop = pharm_page['properties'].get(property_name)
                if subtag_prop and subtag_prop.get('type') == 'formula':
                    subtag_str = subtag_prop['formula'].get('string', '').strip()
                    if subtag_str:
                        tags.extend(subtag_str.split())

        return tags

    # ── Tag selection dialog (used by replace_tags) ───────────────────────────

    def show_tag_selection_dialog(self, tags_with_subtags):
        """Show dialog for user to select which tags to replace."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Tags to Replace")
        dialog.setMinimumWidth(600)

        layout = QVBoxLayout()
        info_label = QLabel("Multiple subtags detected. Please select which tags you want to replace:")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        checkbox_layout = QVBoxLayout()

        checkboxes = []
        for tag, subtag in tags_with_subtags:
            display_tag = tag.replace("#Malleus_CM::", "")
            checkbox = QCheckBox(display_tag)
            checkbox.tag_data = (tag, subtag)
            checkboxes.append(checkbox)
            checkbox_layout.addWidget(checkbox)

        scroll_widget.setLayout(checkbox_layout)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)
        dialog.setLayout(layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            return [(cb.tag_data[0], cb.tag_data[1]) for cb in checkboxes if cb.isChecked()]
        return None

    # ── Note context helpers ──────────────────────────────────────────────────

    def get_notes_to_process(self):
        """Get all notes that should be processed based on current context."""
        parent = self.parent()
        notes = []

        if isinstance(parent, Browser):
            selected_card_ids = parent.selectedCards()
            if len(selected_card_ids) > 1:
                for card_id in selected_card_ids:
                    card = mw.col.get_card(card_id)
                    note = card.note()
                    if note and note not in notes:
                        notes.append(note)
            elif len(selected_card_ids) == 1:
                if hasattr(parent, 'editor') and hasattr(parent.editor, 'note'):
                    note = parent.editor.note
                    if note:
                        notes.append(note)
        elif isinstance(parent, EditCurrent):
            if hasattr(parent.editor, 'note'):
                note = parent.editor.note
                if note:
                    notes.append(note)
        elif isinstance(parent, AddCards):
            if hasattr(parent.editor, 'note'):
                note = parent.editor.note
                if note:
                    notes.append(note)
        else:
            if self.current_note:
                notes.append(self.current_note)

        return notes

    # ── Remove tags ───────────────────────────────────────────────────────────

    def remove_tags(self):
        """
        Remove Malleus tags from selected notes with the following priority:

        1. Pages + subtag selected  → remove only those specific subtag tags
        2. Pages selected, no subtag → remove all tags for those pages
        3. No pages selected, some chips active → remove tags for active databases
        4. No pages selected, no chips active  → remove all #Malleus_CM:: tags
        """
        notes = self.get_notes_to_process()
        if not notes:
            showInfo("No notes found in current context")
            return

        selected_rows = self._get_selected_rows()
        parent = self.parent()
        is_add_cards = isinstance(parent, AddCards)
        total_notes = len(notes)
        notes_modified = 0
        total_tags_removed = 0
        all_removed_tags = set()

        # ── Determine removal predicate based on selection state ─────────────
        if selected_rows:
            # Cases 1 & 2: pages are selected
            # Build a list of (match_fn, is_subjects_db) per row
            page_matchers = []
            any_subjects = False

            for row_data in selected_rows:
                page    = row_data['page']
                db_name = page.get('_database_name', '')
                prop    = self._get_row_property_name(row_data)

                if db_name in ("Subjects", "eTG"):
                    any_subjects = True

                if prop and prop not in ("", "Tag", "Main Tag"):
                    # Case 1: specific subtag → match the exact tag value
                    subtag_tags = self._get_tags_for_page(page, db_name, prop)
                    tag_set = set(subtag_tags)
                    page_matchers.append(lambda t, ts=tag_set: t in ts)
                else:
                    # Case 2: whole page → match any tag starting with main tag prefix
                    main_tag_prop = page['properties'].get('Main Tag') or page['properties'].get('Tag')
                    main_tag = ''
                    if main_tag_prop and main_tag_prop.get('type') == 'formula':
                        main_tag = main_tag_prop['formula'].get('string', '').strip().split()[0] if main_tag_prop['formula'].get('string', '').strip() else ''
                    if main_tag:
                        page_matchers.append(lambda t, mt=main_tag: t.startswith(mt))
                    else:
                        # Fallback: match by database prefix
                        tag_db = DB_TAG_MAPPING.get(db_name, db_name)
                        pattern = f"#{tag_db}::"
                        page_matchers.append(lambda t, pat=pattern: pat in t)

            def should_remove(tag):
                return any(m(tag) for m in page_matchers)

            subjects_db_flag = any_subjects

        else:
            # Cases 3 & 4: no pages selected
            active_db_names = self._get_active_db_names()

            if active_db_names:
                # Case 3: remove tags for active databases
                patterns = [f"#{DB_TAG_MAPPING.get(db, db)}::" for db in active_db_names]
                def should_remove(tag):
                    return any(pat in tag for pat in patterns)
                subjects_db_flag = bool(set(active_db_names) & {"Subjects", "eTG"})
            else:
                # Case 4: nuclear — remove everything starting with #Malleus_CM::
                def should_remove(tag):
                    return tag.startswith("#Malleus_CM::")
                subjects_db_flag = True

        # ── Apply removal to each note ────────────────────────────────────────
        for note in notes:
            current_tags = list(note.tags)
            tags_to_remove = [t for t in current_tags if should_remove(t)]

            if tags_to_remove:
                note.tags = [t for t in current_tags if t not in tags_to_remove]

                self._schedule_extra_synced(
                    note, self.notion_cache,
                    subjects_db=subjects_db_flag,
                )

                if not is_add_cards:
                    note.flush()

                notes_modified += 1
                total_tags_removed += len(tags_to_remove)
                all_removed_tags.update(tags_to_remove)

        # ── Refresh UI ────────────────────────────────────────────────────────
        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        # ── Summary ───────────────────────────────────────────────────────────
        if notes_modified == 0:
            showInfo("No matching tags found on the selected notes.")
        else:
            summary = f"Successfully processed {total_notes} note(s)\n"
            summary += f"Modified: {notes_modified} note(s)\n"
            summary += f"Total tags removed: {total_tags_removed}\n\n"
            unique_tags = sorted(all_removed_tags)
            if len(unique_tags) <= 20:
                summary += "Tags removed:\n" + "\n".join(unique_tags)
            else:
                summary += "Tags removed (showing first 20):\n" + "\n".join(unique_tags[:20])
                summary += f"\n... and {len(unique_tags) - 20} more"
            showInfo(summary)

    # ── Add tags ──────────────────────────────────────────────────────────────

    def add_tags(self):
        """Add new tags to existing ones."""
        notes = self.get_notes_to_process()
        if not notes:
            showInfo("No notes found in current context")
            return

        selected_rows  = self._get_selected_rows()
        selected_yields = self.get_selected_yield_tags()

        if not selected_rows and not selected_yields:
            showInfo("Please select at least one page or yield level")
            return

        if not selected_rows and selected_yields:
            return self._update_yield_only(notes, selected_yields)

        # Validate subtags
        for row_data in selected_rows:
            page    = row_data['page']
            if _page_needs_subtag(page):
                sc = row_data.get('subtag_combo')
                if sc is None or not sc.currentText():
                    try:
                        title = page['properties']['Name']['title'][0]['text']['content']
                    except Exception:
                        title = "this page"
                    showInfo(
                        f"Please select a subtag for:\n{title}\n\n"
                        "(Check the result to reveal the subtag dropdown)"
                    )
                    return

        if len(notes) == 1:
            result = self._add_tags_single_note(notes[0], selected_rows)
            if result:
                for row_data in selected_rows:
                    self._save_recent_tag(row_data['page'])
                parent = self.parent()
                if isinstance(parent, Browser):
                    parent.model.reset()
                elif isinstance(parent, EditCurrent):
                    parent.editor.loadNote()
                elif isinstance(parent, AddCards):
                    parent.editor.loadNote()
            return

        # Multiple notes
        total_notes             = len(notes)
        notes_modified          = 0
        notes_with_yield_issues = 0
        notes_needing_yield     = 0
        parent   = self.parent()
        is_add_cards = isinstance(parent, AddCards)

        for note in notes:
            existing_yields = self.get_existing_yield_tags(note.tags)
            sel_yields      = self.get_selected_yield_tags()

            if len(sel_yields) > 1:
                notes_with_yield_issues += 1
                continue

            final_yield_tags = []
            if not existing_yields and not sel_yields:
                notes_needing_yield += 1
                continue
            elif existing_yields and not sel_yields:
                final_yield_tags = existing_yields
            elif sel_yields:
                final_yield_tags = sel_yields

            current_tags = {t for t in note.tags if not t.startswith("#Malleus_CM::#Yield::")}
            new_tags     = set(self.get_tags_from_selected_pages())
            all_new_tags = new_tags | set(final_yield_tags) | set(self.get_paediatrics_tag())
            note.tags    = list(current_tags | all_new_tags)

            selected_db_names = {r['page'].get('_database_name', '') for r in selected_rows}
            self._schedule_extra_synced(
                note, self.notion_cache,
                subjects_db=bool(selected_db_names & {"Subjects", "eTG"}),
            )

            if not is_add_cards:
                note.flush()
            notes_modified += 1

        if notes_modified > 0:
            for row_data in selected_rows:
                self._save_recent_tag(row_data['page'])

        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        summary = f"Successfully processed {total_notes} note(s)\n"
        summary += f"Modified: {notes_modified} note(s)\n"
        if notes_with_yield_issues > 0:
            summary += f"Skipped (multiple yields selected): {notes_with_yield_issues} note(s)\n"
        if notes_needing_yield > 0:
            summary += f"Skipped (no yield selected): {notes_needing_yield} note(s)\n"
        showInfo(summary)

    def _update_yield_only(self, notes, selected_yields):
        """Update only the yield tags without adding any other tags."""
        if len(selected_yields) > 1:
            showInfo("Please select only one yield level")
            return
        if len(selected_yields) == 0:
            showInfo("Please select a yield level")
            return

        parent       = self.parent()
        is_add_cards = isinstance(parent, AddCards)
        is_single    = (len(notes) == 1)
        total_notes  = len(notes)
        notes_modified = 0

        for note in notes:
            remaining = [t for t in note.tags if not t.startswith("#Malleus_CM::#Yield::")]
            note.tags = remaining + selected_yields
            self._schedule_extra_synced(note, self.notion_cache, subjects_db=False)
            if not is_add_cards:
                note.flush()
            notes_modified += 1

        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        if not is_single:
            summary = f"Successfully updated yield for {total_notes} note(s)\n"
            summary += f"New yield: {selected_yields[0].replace('#Malleus_CM::#Yield::', '')}"
            showInfo(summary)

    def _add_tags_single_note(self, note, selected_rows):
        """Handle add_tags for a single note with proper validation."""
        existing_yields = self.get_existing_yield_tags(note.tags)
        selected_yields = self.get_selected_yield_tags()

        if len(selected_yields) > 1:
            showInfo("Please select only one yield level")
            return False

        final_yield_tags = []
        if not existing_yields and not selected_yields:
            showInfo("Please select a yield level for this card")
            return False
        elif existing_yields and not selected_yields:
            final_yield_tags = existing_yields
        elif selected_yields:
            final_yield_tags = selected_yields

        current_tags = {t for t in note.tags if not t.startswith("#Malleus_CM::#Yield::")}
        new_tags     = set(self.get_tags_from_selected_pages())
        all_new_tags = new_tags | set(final_yield_tags) | set(self.get_paediatrics_tag())
        note.tags    = list(current_tags | all_new_tags)

        selected_db_names = {r['page'].get('_database_name', '') for r in selected_rows}
        self._schedule_extra_synced(
            note, self.notion_cache,
            subjects_db=bool(selected_db_names & {"Subjects", "eTG"}),
        )

        if not isinstance(self.parent(), AddCards):
            note.flush()

        return True

    # ── Replace tags ──────────────────────────────────────────────────────────

    def replace_tags(self):
        """Replace existing database tags with newly selected ones."""
        from .tag_selection_dialog import TagSelectionDialog
        from ..tag_utils import simplify_tags_by_page
        from aqt.qt import QDialog

        notes = self.get_notes_to_process()
        if not notes:
            showInfo("No notes found in current context")
            return

        selected_rows  = self._get_selected_rows()
        selected_yields = self.get_selected_yield_tags()

        if not selected_rows and not selected_yields:
            showInfo("Please select at least one page or yield level")
            return

        if not selected_rows and selected_yields:
            return self._update_yield_only(notes, selected_yields)

        if len(selected_rows) > 1:
            showInfo(
                "Please select only ONE page at a time when replacing tags.\n\n"
                "Multiple pages selected will make tag replacement ambiguous."
            )
            return

        # Derive database context from the single selected row
        selected_row     = selected_rows[0]
        page             = selected_row['page']
        database_name    = page.get('_database_name', '')
        possible_subtags = [s for s in self.database_properties.get(database_name, []) if s]
        user_selected_subtag = self._get_row_property_name(selected_row)
        all_general      = _is_general_page(page)

        selected_pages = [page]

        total_notes             = len(notes)
        notes_modified          = 0
        notes_with_yield_issues = 0
        notes_skipped           = 0
        parent   = self.parent()
        is_add_cards = isinstance(parent, AddCards)

        for note in notes:
            existing_yields = self.get_existing_yield_tags(note.tags)
            sel_yields      = self.get_selected_yield_tags()

            if len(sel_yields) > 1:
                notes_with_yield_issues += 1
                continue

            final_yield_tags = []
            if not existing_yields and not sel_yields:
                note_context = None
                if 'Text' in note:
                    note_context = note['Text']
                prompted_yield = self._prompt_for_yield_selection(note_context)
                if prompted_yield is None:
                    notes_skipped += 1
                    continue
                final_yield_tags = [prompted_yield]
            elif existing_yields and not sel_yields:
                final_yield_tags = existing_yields
            elif sel_yields:
                final_yield_tags = sel_yields

            current_tags = list(note.tags)
            tag_frag = DB_TAG_MAPPING.get(database_name, database_name)
            database_pattern = f"#Malleus_CM::#{tag_frag}::"
            matching_tags = [t for t in current_tags if t.startswith(database_pattern)]

            if not matching_tags:
                continue

            simplified_tags = simplify_tags_by_page(matching_tags, database_name)
            if not simplified_tags:
                continue

            tags_to_replace = []
            if len(simplified_tags) == 1:
                tags_to_replace = simplified_tags[0]['original_tags']
            else:
                note_context = note['Text'] if 'Text' in note else None
                dialog = TagSelectionDialog(self, simplified_tags, note_context)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    for tag_info in dialog.get_selected_tags():
                        tags_to_replace.extend(tag_info['original_tags'])
                else:
                    notes_skipped += 1
                    continue

            if not tags_to_replace:
                continue

            result = self._perform_tag_replacement(
                note, tags_to_replace, selected_pages,
                database_name, possible_subtags, user_selected_subtag,
                all_general, final_yield_tags, is_add_cards,
            )
            if result:
                notes_modified += 1

        if notes_modified > 0:
            self._save_recent_tag(page)

        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        if len(notes) > 1:
            summary = f"Successfully processed {total_notes} note(s)\n"
            summary += f"Modified: {notes_modified} note(s)\n"
            if notes_with_yield_issues > 0:
                summary += f"Skipped (multiple yields selected): {notes_with_yield_issues} note(s)\n"
            if notes_skipped > 0:
                summary += f"Skipped (user cancelled): {notes_skipped} note(s)\n"
            showInfo(summary)

    def _perform_tag_replacement(self, note, tags_to_replace, selected_pages, database_name,
                                 possible_subtags, user_selected_subtag, all_general,
                                 final_yield_tags, is_add_cards):
        """
        Perform the actual tag replacement on a note.

        Determines the final subtag to use (from user selection or inferred from
        existing tags), extracts new tags via _get_tags_for_page directly (no
        temporary property selector manipulation), then rebuilds the note's tags.

        Returns True on success, False if the user needs to take further action.
        """
        from ..tag_utils import (get_subtag_from_tag, get_all_subtags_from_tags,
                                 normalize_subtag_for_matching, get_subtags_with_normalization)

        current_tags  = list(note.tags)
        remaining_tags = [t for t in current_tags if t not in tags_to_replace]

        # ── Determine final subtag ────────────────────────────────────────────
        final_subtag = None

        if user_selected_subtag and user_selected_subtag not in ("", "Tag", "Main Tag"):
            final_subtag = user_selected_subtag
        else:
            raw_subtags = get_all_subtags_from_tags(tags_to_replace)

            if len(raw_subtags) == 1:
                raw_subtag   = list(raw_subtags)[0]
                final_subtag = normalize_subtag_for_matching(raw_subtag, possible_subtags)
            elif len(raw_subtags) == 0:
                if user_selected_subtag == "":
                    if database_name in ("Subjects", "Pharmacology"):
                        if not all_general:
                            showInfo(
                                "Please select a subtag (check the result to reveal "
                                "the subtag dropdown)"
                            )
                            return False
                        else:
                            final_subtag = "Main Tag"
                    else:
                        final_subtag = "Tag"
                else:
                    final_subtag = user_selected_subtag
            else:
                normalized_subtags = get_subtags_with_normalization(tags_to_replace, possible_subtags)
                if len(normalized_subtags) == 1:
                    final_subtag = list(normalized_subtags)[0]
                else:
                    if not user_selected_subtag or user_selected_subtag == "":
                        showInfo(
                            "The tags you're replacing have different subtags. "
                            "Please select a subtag from the result row's dropdown."
                        )
                        return False
                    final_subtag = user_selected_subtag

        # ── Resolve property name ─────────────────────────────────────────────
        if final_subtag in (None, "Main Tag"):
            prop_to_use = "Main Tag" if database_name == "Subjects" else "Tag"
        elif final_subtag == "Tag":
            prop_to_use = "Tag"
        else:
            prop_to_use = final_subtag

        # ── Extract new tags directly ─────────────────────────────────────────
        new_tags = []
        for page in selected_pages:
            new_tags.extend(self._get_tags_for_page(page, database_name, prop_to_use))

        # ── Rebuild final tag list ────────────────────────────────────────────
        remaining_tags = [t for t in remaining_tags if not t.startswith("#Malleus_CM::#Yield::")]
        all_new_tags   = new_tags + final_yield_tags + self.get_paediatrics_tag()
        final_tags     = list(set(remaining_tags + all_new_tags))

        yield_in_final = [t for t in final_tags if t.startswith("#Malleus_CM::#Yield::")]
        if len(yield_in_final) > 1:
            showInfo(f"Error: Multiple yield tags detected:\n" + "\n".join(yield_in_final))
            return False
        elif len(yield_in_final) == 0:
            showInfo("No yield tag. Please select a yield level.")
            return False

        note.tags = final_tags

        selected_db_names = {p.get('_database_name', '') for p in selected_pages}
        self._schedule_extra_synced(
            note, self.notion_cache,
            subjects_db=bool(selected_db_names & {"Subjects", "eTG"}),
        )

        if not is_add_cards:
            note.flush()

        return True

    # ── Misc helpers ──────────────────────────────────────────────────────────

    def _normalize_for_comparison(self, text):
        """Normalize text for comparison — handle spaces, slashes, underscores."""
        return text.replace(' ', '_').replace('/', '_').replace('&', '_').lower()

    def _prompt_for_yield_selection(self, note_context=None):
        """
        Show a dialog to prompt user for yield selection.

        Returns the selected yield tag string, or None if cancelled.
        """
        from aqt.qt import QDialog, QVBoxLayout, QLabel, QRadioButton, QButtonGroup, QDialogButtonBox, QFrame

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Yield Level")
        dialog.setMinimumWidth(400)
        apply_malleus_style(dialog)

        layout = QVBoxLayout()

        info_label = QLabel("This card has no yield level. Please select one:")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(info_label)

        if note_context:
            context_frame = QFrame()
            context_frame.setFrameShape(QFrame.Shape.StyledPanel)
            context_frame.setStyleSheet(
                "background-color: palette(alternateBase); padding: 10px; "
                "border-radius: 7px; border: 1px solid rgba(74,130,204,0.30);"
            )
            context_layout = QVBoxLayout()

            context_title = QLabel("Card Context:")
            context_title.setStyleSheet("font-weight: bold; font-size: 11px;")
            context_layout.addWidget(context_title)

            context_text = QLabel(note_context[:200] + ("..." if len(note_context) > 200 else ""))
            context_text.setWordWrap(True)
            context_text.setStyleSheet("font-size: 10px;")
            context_layout.addWidget(context_text)

            context_frame.setLayout(context_layout)
            layout.addWidget(context_frame)

        button_group  = QButtonGroup(dialog)
        radio_buttons = {}

        yield_options = {
            "High Yield":                   "#Malleus_CM::#Yield::High",
            "Medium Yield":                 "#Malleus_CM::#Yield::Medium",
            "Low Yield":                    "#Malleus_CM::#Yield::Low",
            "Beyond Medical Student Level": "#Malleus_CM::#Yield::Beyond_medical_student_level",
        }

        for display_text, tag_value in yield_options.items():
            radio = QRadioButton(display_text)
            radio_buttons[display_text] = (radio, tag_value)
            button_group.addButton(radio)
            layout.addWidget(radio)

        radio_buttons["High Yield"][0].setChecked(True)

        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.setLayout(layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            for display_text, (radio, tag_value) in radio_buttons.items():
                if radio.isChecked():
                    return tag_value
        return None
