"""
Page Selector Dialog
Main UI for searching and selecting Notion pages
"""
from aqt import mw, dialogs
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox,
                    QLineEdit, QPushButton, QGroupBox, QScrollArea,
                    QWidget, QCheckBox, QButtonGroup, QRadioButton,
                    QLabel, QFrame, QTimer, Qt, QUrl, QWidget as QWidgetBase,
                    QKeyEvent, QColor, QPalette)
from aqt.browser import Browser
from aqt.addcards import AddCards
from aqt.editcurrent import EditCurrent
from aqt.utils import showInfo
from ..utils import malleus_tooltip
from PyQt6.QtGui import QDesktopServices
import anki.notes
from ..config import (DATABASE_PROPERTIES, get_database_id, get_database_name,
                       SUBJECT_DATABASE_ID, PHARMACOLOGY_DATABASE_ID)
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
        # Derive addon_dir from the cache directory so we can load the logo
        import os
        self._addon_dir = str(notion_cache.cache_dir.parent)
        # Initialize cache on startup without forcing
        # if SUBJECT_DATABASE_ID:
        #     self.notion_cache.update_cache_async(SUBJECT_DATABASE_ID, force=False)
        # if PHARMACOLOGY_DATABASE_ID:
        #     self.notion_cache.update_cache_async(PHARMACOLOGY_DATABASE_ID, force=False)
        # if ETG_DATABASE_ID:
        #     self.notion_cache.update_cache_async(ETG_DATABASE_ID, force=False)
        # if ROTATION_DATABASE_ID:
        #     self.notion_cache.update_cache_async(ROTATION_DATABASE_ID, force=False)

        self.database_properties = DATABASE_PROPERTIES
        self.pages_data = []  # Store full page data
        self.setup_ui()
        apply_malleus_style(self)

    def has_notes_to_process(self):
        """Check if there are notes available to process"""
        parent = self.parent()

        if isinstance(parent, Browser):
            # Check if any cards are selected
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
        # Wider when a note is open: the result rows show card count + confidence
        # dots side-by-side and need the extra space to avoid truncation.
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

        # Search section
        search_layout = QHBoxLayout()
        search_layout.setSpacing(8)

        # Database selector
        self.database_selector = QComboBox()
        self.database_selector.addItems(["Subjects", "Pharmacology", "eTG", "Rotation", "Textbooks", "Guidelines"])
        self.database_selector.currentTextChanged.connect(self.update_property_selector)
        self.database_selector.currentTextChanged.connect(self.clear_search_results)
        search_layout.addWidget(self.database_selector)

        # Initialize search timer
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search pages...")
        self.search_input.textChanged.connect(self.on_search_text_changed)
        self.search_input.setMinimumHeight(34)
        search_layout.addWidget(self.search_input)

        # Property selector
        self.property_selector = QComboBox()
        search_layout.addWidget(self.property_selector)

        self.update_property_selector(self.database_selector.currentText())

        # Search button
        if not self.config['autosearch']:
            search_button = QPushButton("Search")
            search_button.clicked.connect(self.perform_search)
            search_layout.addWidget(search_button)

        content_layout.addLayout(search_layout)

        # Results section
        self.results_group = QGroupBox("Search Results")
        results_layout = QVBoxLayout()

        # Scrollable area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(220)
        scroll_widget = QWidget()
        self.checkbox_layout = QVBoxLayout()
        scroll_widget.setLayout(self.checkbox_layout)
        scroll.setWidget(scroll_widget)

        results_layout.addWidget(scroll)
        self.results_group.setLayout(results_layout)
        content_layout.addWidget(self.results_group, stretch=1)

        # Yield selection section
        yield_group = QGroupBox("Yield Level")
        yield_layout = QVBoxLayout()
        yield_layout.setSpacing(2)
        yield_layout.setContentsMargins(6, 4, 6, 6)

        # Create a horizontal layout with title and info icon
        yield_title_layout = QHBoxLayout()
        yield_title_label = QLabel("Yield Level")
        yield_title_label.setStyleSheet("font-weight: 700; font-size: 13px; background: transparent;")

        # Create info icon with combined tooltip
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

        info_label = QLabel("ℹ️")
        info_label.setToolTip(combined_tooltip)
        info_label.setStyleSheet("QLabel { color: #4a82cc; font-size: 14px; margin-left: 5px; background: transparent; }")
        info_label.setFixedSize(20, 20)
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setCursor(Qt.CursorShape.WhatsThisCursor)

        yield_title_layout.addWidget(yield_title_label)
        yield_title_layout.addWidget(info_label)
        yield_title_layout.addStretch()

        # Hide the default title and add custom layout
        yield_group.setTitle("")
        yield_layout.addLayout(yield_title_layout)

        # Add separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        yield_layout.addWidget(separator)

        # Create radio buttons for yield selection
        self.yield_button_group = QButtonGroup(self)
        self.yield_button_group.setExclusive(True)

        self.yield_radio_buttons = {}
        yield_options = [
            "High Yield",
            "Medium Yield",
            "Low Yield",
            "Beyond medical student level"
        ]

        for yield_option in yield_options:
            radio_button = QRadioButton(yield_option)
            self.yield_radio_buttons[yield_option] = radio_button
            self.yield_button_group.addButton(radio_button)
            yield_layout.addWidget(radio_button)

            # Connect to handle click for deselection
            radio_button.clicked.connect(lambda checked, opt=yield_option: self.handle_yield_click(opt))

        # Initialize tracking variable
        self._last_checked_yield = None

        print(f"DEBUG RESTORE: Class last_yield_selection = '{NotionPageSelector.last_yield_selection}'")

        # Restore last selection if it exists
        if NotionPageSelector.last_yield_selection:
            if NotionPageSelector.last_yield_selection in self.yield_radio_buttons:
                self.yield_radio_buttons[NotionPageSelector.last_yield_selection].setChecked(True)
                self._last_checked_yield = NotionPageSelector.last_yield_selection
                print(f"DEBUG RESTORE: Set _last_checked_yield to '{self._last_checked_yield}'")
            else:
                print(f"DEBUG RESTORE: '{NotionPageSelector.last_yield_selection}' not found in buttons")
        else:
            print(f"DEBUG RESTORE: No last selection to restore")

        yield_group.setLayout(yield_layout)

        # Paediatrics section (right side)
        # Mirror yield_group structure exactly:
        # use a non-empty placeholder title then suppress it with setTitle("")
        # so Qt's native title margin is removed identically to yield_group.
        paeds_group = QGroupBox("Specialty Tags")
        paeds_layout = QVBoxLayout()
        paeds_layout.setSpacing(2)
        paeds_layout.setContentsMargins(6, 4, 6, 6)

        paeds_title_layout = QHBoxLayout()
        paeds_title_layout.setContentsMargins(0, 0, 0, 0)
        paeds_title_layout.setSpacing(0)
        paeds_title = QLabel("Specialty Tags")
        paeds_title.setStyleSheet("font-weight: 700; font-size: 13px; background: transparent;")
        paeds_title_layout.addWidget(paeds_title)
        paeds_title_layout.addStretch()

        # Suppress the native QGroupBox title, drawing our own instead (same as yield_group)
        paeds_group.setTitle("")
        paeds_layout.addLayout(paeds_title_layout)

        paeds_separator = QFrame()
        paeds_separator.setFrameShape(QFrame.Shape.HLine)
        paeds_separator.setFrameShadow(QFrame.Shadow.Sunken)
        paeds_layout.addWidget(paeds_separator)

        paeds_layout.addSpacing(6)
        
        paeds_question = QLabel("Is this a card on paediatrics?")
        paeds_question.setWordWrap(True)
        paeds_layout.addWidget(paeds_question)

        self.paeds_checkbox = QCheckBox("Yes")
        paeds_layout.addWidget(self.paeds_checkbox)
        paeds_layout.addStretch()  # pin content to top; absorb remaining height

        paeds_group.setLayout(paeds_layout)

        # Place yield and paediatrics side by side
        # Wrap in a QWidget with fixed vertical size policy so this row never grows
        from aqt.qt import QSizePolicy
        yield_paeds_widget = QWidget()
        yield_paeds_layout = QHBoxLayout(yield_paeds_widget)
        yield_paeds_layout.setContentsMargins(0, 0, 0, 0)
        yield_paeds_layout.addWidget(yield_group, stretch=2)
        yield_paeds_layout.addWidget(paeds_group, stretch=1)
        yield_paeds_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed     # Fixed vertical — won't grow on resize
        )
        content_layout.addWidget(yield_paeds_widget, stretch=0)

        # ── Buttons — two rows ────────────────────────────────────────────────
        # Buttons: two rows when a note is open (extra action buttons present),
        # single row otherwise (just Select All / Find / Create / utilities).
        has_notes = self.has_notes_to_process()
        buttons_vbox = QVBoxLayout()
        buttons_vbox.setSpacing(6)

        # Shared utility buttons (always present)
        update_database_button = QPushButton("↻  Update Database")
        update_database_button.setObjectName("secondary")
        update_database_button.clicked.connect(
            lambda: (perform_cache_update(self.notion_cache, mw), invalidate_index())
        )
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
        # button_layout.addWidget(donate_button)

        if has_notes:
            # ── Row 1: tag management (all outlined — reversible/config actions)
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

            # ── Row 2: execute / utilities (primary blue for Find/Create) ─────
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
            # ── Single row: no note open — all stretch to fill ────────────────
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

    # ── Card count + confidence helpers ──────────────────────────────────────

    def _load_note_tag_strings(self) -> None:
        """
        Fetch every note's raw tag string from the collection DB in one query
        and cache the result on self._note_tag_strings.

        This is called once before a batch of _get_card_count_for_page() calls
        so that counting N result rows costs one DB round-trip instead of N.
        The cache is keyed on the collection path; if it changes (user switches
        profile) the cache is transparently rebuilt.
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
                return  # already cached for this collection
            # db.list returns a flat list of values for a single-column query
            self._note_tag_strings = col.db.list("select tags from notes")
            self._note_tag_strings_col = col_path
        except Exception as e:
            print(f"[MalleusCardCount] failed to load note tag strings: {e}")
            self._note_tag_strings = []
            self._note_tag_strings_col = None

    def _get_card_count_for_page(self, page: dict) -> int:
        """
        Return the number of notes that have a tag matching this Notion page.

        Uses the cached tag strings loaded by _load_note_tag_strings() so
        counting N result rows costs one DB query total.

        Checks 'Main Tag' first (gives the page-root prefix that is shared by
        all subtag variants on notes), then falls back to 'Tag'.  General pages
        show 0 — their Main Tag path omits *General and so won't match, but
        that edge case is acceptable.
        """
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
        """
        Convert a raw suggestion score to a 5-dot confidence string.

        The max_score is TOPIC_SEARCH_BONUS (4.0) — a perfect cloze-first match
        gives ~4.0 before title bonus, so that anchors the scale.
        Scores above max are capped at 5 dots.
        """
        normalised = min(score / max_score, 1.0)
        filled = max(1, round(normalised * 5))
        return '●' * filled + '○' * (5 - filled)

    def _make_result_row(self, display_text: str, page: dict,
                         score: float = None,
                         show_count: bool = True) -> tuple:
        """
        Build a single result row widget.

        Returns (row_widget, checkbox) where row_widget is a QWidget
        containing a QHBoxLayout with:
          - QCheckBox (the display text)
          - card count pill  (only when show_count=True)
          - confidence dots  (only when score is provided, i.e. suggestions)

        The checkbox is also returned separately so callers can wire it
        into checkbox_layout tracking.
        """
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 4, 0)
        row_layout.setSpacing(6)

        cb = QCheckBox(display_text)
        row_layout.addWidget(cb, stretch=1)

        # Card count pill — omitted when the result set is large
        if show_count:
            card_count = self._get_card_count_for_page(page)
            count_label = QLabel(f"{card_count} {'note' if card_count == 1 else 'notes'}")
            count_label.setToolTip("Number of notes in your collection tagged with this page")
            if card_count == 0:
                count_label.setStyleSheet(
                    "color: rgba(128,128,128,0.6); font-size: 11px; padding: 1px 6px;"
                )
            else:
                count_label.setStyleSheet(
                    "color: #58a6ff; font-size: 11px; font-weight: 600; padding: 1px 6px;"
                )
            row_layout.addWidget(count_label, stretch=0)

        # Confidence dots (suggestions only)
        if score is not None:
            dots = self._score_to_dots(score)
            dots_label = QLabel(dots)
            dots_label.setToolTip(f"Suggestion confidence (raw score: {score:.2f})")
            dots_label.setStyleSheet(
                "font-size: 11px; letter-spacing: 2px; color: #f0a500; padding: 1px 4px;"
            )
            row_layout.addWidget(dots_label, stretch=0)

        return row, cb

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
            # Find current focused checkbox index
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
            # Trigger primary action
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
        """Return all QCheckBox widgets currently in the results area."""
        checkboxes = []
        for i in range(self.checkbox_layout.count()):
            item = self.checkbox_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is None:
                continue
            # Direct checkbox (old-style from perform_search)
            if isinstance(widget, QCheckBox):
                checkboxes.append(widget)
            # Row widget containing a checkbox (new _make_result_row style)
            elif hasattr(widget, 'layout') and widget.layout():
                for j in range(widget.layout().count()):
                    child = widget.layout().itemAt(j).widget()
                    if isinstance(child, QCheckBox):
                        checkboxes.append(child)
        return checkboxes

    def suggest_tags_from_card(self):
        """
        Run the local tag suggester against the current note's Text field,
        then show the results so the user can select which ones to apply.

        How results are displayed
        -------------------------
        1. The database selector is switched to "Subjects".
        2. The search box is cleared and the results area is populated
           directly with the suggested pages — same checkbox UI as a normal
           search, so the existing Select All / Create Cards / Add Tags
           buttons all work without any changes.
        3. A tooltip on each checkbox shows the confidence score so the
           user can judge quality at a glance.
        """
        # 1. Get the card text to analyse
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

        # Collect supplementary fields individually so each can be weighted
        # differently inside the suggester.
        def _field(name):
            try:
                v = note[name]
                return v if v and v.strip() else ''
            except Exception:
                return ''

        extra_text          = _field('Extra')
        addl_resources_text = _field('Additional Resources')
        source_text         = _field('Source')

        # 2. Run the suggester
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

        # 3. Switch UI to Subjects database
        subjects_index = self.database_selector.findText("Subjects")
        if subjects_index >= 0:
            self.database_selector.setCurrentIndex(subjects_index)

        # 4. Populate the results area with suggestion checkboxes
        #    (mirrors what perform_search does, reusing the same layout)
        self.pages_data = [s['page'] for s in suggestions]

        # Clear existing checkboxes
        for i in reversed(range(self.checkbox_layout.count())):
            widget = self.checkbox_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        # Load note tag cache once for all suggestion rows
        self._load_note_tag_strings()

        for suggestion in suggestions:
            page    = suggestion['page']
            title   = suggestion['title']
            score   = suggestion['score']

            try:
                suffix = (
                    page['properties']
                    .get('Search Suffix', {})
                    .get('formula', {})
                    .get('string', '')
                )
                prefix = (
                    page['properties']
                    .get('Search Prefix', {})
                    .get('formula', {})
                    .get('string', '')
                )
                display_text = f"{prefix} {title} {suffix}".strip()
            except Exception:
                display_text = title

            row, _cb = self._make_result_row(display_text, page, score=score)
            self.checkbox_layout.addWidget(row)

        # Pre-select the suggested subtag in the property selector.
        # All suggestions share the same subtag (the card tests one concept).
        subtag = suggestions[0].get('suggested_subtag')
        if subtag:
            idx = self.property_selector.findText(subtag)
            if idx >= 0:
                self.property_selector.setCurrentIndex(idx)

        # Update the group box title so the user knows these are suggestions
        subtag_label = f" · subtag: {subtag}" if subtag else ""
        self.results_group.setTitle(
            f"Suggested Tags ({len(suggestions)} found{subtag_label})"
        )
        malleus_tooltip(f"Found {len(suggestions)} suggested tag(s)")

    def handle_yield_click(self, yield_option):
        """Handle yield radio button clicks - allow deselection of selected button"""
        radio_button = self.yield_radio_buttons[yield_option]

        print(f"DEBUG: Clicked on '{yield_option}'")
        print(f"  Button is checked: {radio_button.isChecked()}")
        print(f"  Last tracked type: {type(self._last_checked_yield)}")
        print(f"  Last tracked value: {repr(self._last_checked_yield)}")

        # Check if this button was already checked before the click
        if radio_button.isChecked() and self._last_checked_yield == yield_option:
            print(f"  Action: UNSELECTING")
            # This button is currently selected, so unselect it
            # Temporarily allow deselection
            self.yield_button_group.setExclusive(False)
            radio_button.setChecked(False)
            self.yield_button_group.setExclusive(True)

            self._last_checked_yield = None
            NotionPageSelector.last_yield_selection = ""
        else:
            print(f"  Action: SELECTING")
            # This button is being newly selected
            self._last_checked_yield = yield_option
            NotionPageSelector.last_yield_selection = yield_option

        print(f"  After - Last tracked: {repr(self._last_checked_yield)}")

    def get_selected_yield_tags(self):
        """Get the selected yield tags from the radio buttons"""
        # Map the display text to the actual tag
        yield_tag_mapping = {
            "High Yield": "#Malleus_CM::#Yield::High",
            "Medium Yield": "#Malleus_CM::#Yield::Medium",
            "Low Yield": "#Malleus_CM::#Yield::Low",
            "Beyond medical student level": "#Malleus_CM::#Yield::Beyond_medical_student_level"
        }

        # Find which radio button is checked
        for yield_option, radio_button in self.yield_radio_buttons.items():
            if radio_button.isChecked():
                tag = yield_tag_mapping.get(yield_option)
                return [tag] if tag else []

        # No selection
        return []

    def get_existing_yield_tags(self, tags):
        """Extract existing yield tags from a list of tags"""
        yield_pattern = "#Malleus_CM::#Yield::"
        existing_yields = [tag for tag in tags if tag.startswith(yield_pattern)]
        return existing_yields

    def get_yield_search_query(self):
        """Get the yield search query for browser"""
        # Map the display text to the search query format
        yield_search_mapping = {
            "High Yield": "tag:#Malleus_CM::#Yield::High",
            "Medium Yield": "tag:#Malleus_CM::#Yield::Medium",
            "Low Yield": "tag:#Malleus_CM::#Yield::Low",
            "Beyond medical student level": "tag:#Malleus_CM::#Yield::Beyond_medical_student_level"
        }

        # Find which radio button is checked
        for yield_option, radio_button in self.yield_radio_buttons.items():
            if radio_button.isChecked():
                return yield_search_mapping.get(yield_option, "")

        # No selection
        return ""

    def get_paediatrics_tag(self):
        """Get the paediatrics rotation tag if the paediatrics checkbox is checked"""
        if hasattr(self, 'paeds_checkbox') and self.paeds_checkbox.isChecked():
            return ["#Malleus_CM::#Resources_by_Rotation::Paediatrics"]
        return []

    def update_property_selector(self, database_name):
        """Update property selector items based on selected database"""
        self.property_selector.clear()
        properties = self.database_properties.get(database_name, [])
        self.property_selector.addItems(properties)

    def get_selected_database_id(self):
        """Get database ID from selected database name"""
        return get_database_id(self.database_selector.currentText())

    def clear_search_results(self):
        """Clear the search results when database is changed"""
        # Clear existing checkboxes
        for i in reversed(range(self.checkbox_layout.count())):
            widget = self.checkbox_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        # Reset pages data
        self.pages_data = []

        # Optional: also clear the search input
        # self.search_input.clear()

    def query_notion_pages(self, filter_text: str, database_id: str) -> list[dict]:
        """Query pages from cache and filter them"""
        try:
            cached_pages, last_sync_timestamp = self.notion_cache.load_from_cache(database_id)
            if cached_pages:
                filtered_pages = self.notion_cache.filter_pages(cached_pages, filter_text)
                return filtered_pages or []
            return []
        except Exception as e:
            showInfo(f"Error accessing data: {str(e)}")
            return []

    def perform_search(self):
        search_term = self.search_input.text()
        if not search_term or len(search_term) < 2:
            self.clear_search_results()
            return

        database_id = self.get_selected_database_id()
        print(f"Using database ID: {database_id}")

        # Since cache is checked on startup, directly perform search
        self.pages_data = self.query_notion_pages(search_term, database_id)

        # Clear existing checkboxes
        for i in reversed(range(self.checkbox_layout.count())):
            self.checkbox_layout.itemAt(i).widget().setParent(None)

        if not self.pages_data and not self.config['autosearch']:
            malleus_tooltip("No results found. Try a different search term")
            return

        # Only show card counts when the result set is small enough to be useful.
        # The threshold is configurable (config key: card_count_threshold, default 10).
        threshold = self.config.get('card_count_threshold', 10)
        show_count = len(self.pages_data) <= threshold

        # Load note tag cache once (one DB query) — only needed when showing counts
        if show_count:
            self._load_note_tag_strings()

        # Create result rows (checkbox + optional card count pill)
        for page in self.pages_data:
            try:
                if self.database_selector.currentText() == "Textbooks":
                    title = page['properties']['Search Term']['formula']['string'] if page['properties'].get('Search Term', {}).get('formula', {}).get('string') else "Untitled"
                else:
                    title = page['properties']['Name']['title'][0]['text']['content'] if page['properties']['Name']['title'] else "Untitled"

                search_suffix = page['properties']['Search Suffix']['formula']['string'] if page['properties'].get('Search Suffix', {}).get('formula', {}).get('string') else ""

                if self.database_selector.currentText() in ("Subjects", "Pharmacology"):
                    search_prefix = page['properties']['Search Prefix']['formula']['string'] if page['properties'].get('Search Suffix', {}).get('formula', {}).get('string') else ""
                    display_text = f"{search_prefix} {title} {search_suffix}"
                else:
                    display_text = f"{title} {search_suffix}"

                row, _cb = self._make_result_row(display_text, page, show_count=show_count)
                self.checkbox_layout.addWidget(row)
            except Exception as e:
                showInfo(f"Error processing page: {e}")

    def on_search_text_changed(self, text):
        """Handle search text changes and perform search when typing"""
        # Only search if we have at least 2 characters to avoid too many results
        if self.config['autosearch']:
            if len(text) >= 2:
                # Wait 300ms before performing search
                self.search_timer.start(self.config['search_delay'])
            else:
                self.clear_search_results()

    def select_all_pages(self):
        for cb in self._get_result_checkboxes():
            cb.setChecked(True)

    def search_cards(self):
        selected_pages = []
        for i, cb in enumerate(self._get_result_checkboxes()):
            if cb.isChecked():
                selected_pages.append(self.pages_data[i])

        if not selected_pages:
            showInfo("Please select at least one page")
            return

        property_name = self.property_selector.currentText()

        # Get tags from pages
        tags = []
        for page in selected_pages:
            tag_prop = page['properties'].get('Tag')
            if tag_prop and tag_prop['type'] == 'formula':
                formula_value = tag_prop['formula']
                if formula_value['type'] == 'string':
                    tags.extend(formula_value['string'].split())

        database_id = self.get_selected_database_id()

        # Split each tag by spaces and flatten the list
        individual_tags = []
        for tag in tags:
            individual_tags.extend(tag.split())

        if property_name == '' or property_name == 'Tag' or property_name == 'Main Tag':
            subtag = ""
        else:
            subtag = f"::*{property_name}".replace(' ', '_')

        def escape_underscores(tag):
            return tag.replace('_', '\\_')

        # Format tags for Anki search
        search_query = " or ".join(f"\"tag:{escape_underscores(tag)}{subtag}\"" for tag in individual_tags)

        # Add yield search query if any yields are selected
        yield_query = self.get_yield_search_query()
        if yield_query:
            search_query = f"({search_query}) and ({yield_query})"

        if isinstance(self.parent(), Browser):
            # If called from browser, update the current browser
            browser = self.parent()
            browser.form.searchEdit.lineEdit().setText(search_query)
            if hasattr(browser, 'onSearch'):
                browser.onSearch()
            else:
                browser.onSearchActivated()
        else:
            # Otherwise open a new browser window
            open_browser_with_search(search_query)

        self.accept()

    def get_property_content(self, page, property_name):
        """Extract property content from page data with enhanced formatting"""
        prop = page['properties'].get(property_name)

        # Handle formula type properties (like Source)
        if prop and prop['type'] == 'formula':
            formula_value = prop['formula']

            # Handle different formula result types
            if formula_value['type'] == 'string':
                source_text = formula_value.get('string', '')

                # Make sure source_text is a string before processing
                if not isinstance(source_text, str):
                    source_text = str(source_text) if source_text is not None else ""

                # Parse and format URLs in the source text
                def format_urls(text):
                    import re

                    # Regex to find URLs
                    url_pattern = re.compile(r'(https?://\S+)')

                    # Replace URLs with HTML hyperlinks
                    def replace_url(match):
                        url = match.group(1)
                        # Try to get a clean display text
                        display_text = url.split('//')[1].split('/')[0]  # Get domain
                        return f'<a href="{url}" target="_blank">{display_text}</a>'

                    return url_pattern.sub(replace_url, text)

                # Format the source text with clickable links
                formatted_source = format_urls(source_text)

                return formatted_source

            # Add handling for other formula types if needed
            return ""

        # Fallback for other property types
        if prop and prop['type'] == 'rich_text' and prop['rich_text']:
            return prop['rich_text'][0]['text']['content']

        return ""

    def create_cards(self):
        # Check yield selection for card creation
        selected_yields = self.get_selected_yield_tags()
        if len(selected_yields) > 1:
            showInfo("Please select only one yield level when creating cards")
            return

        if len(selected_yields) == 0:
            showInfo("Please select one yield level when creating cards")
            return

        selected_pages = []
        for i, cb in enumerate(self._get_result_checkboxes()):
            if cb.isChecked():
                selected_pages.append(self.pages_data[i])

        property_name = self.property_selector.currentText()

        if not selected_pages:
            showInfo("Please select at least one page")
            return

        all_general = all(
            'ℹ️' in page.get('properties', {}).get('Search Prefix', {}).get('formula', {}).get('string', '')
            for page in selected_pages
            )

        if property_name == "":
            if self.database_selector.currentText() in ("Subjects", "Pharmacology"):
                if not all_general:  # Only show warning if NOT all general
                    showInfo("Please select a subtag (Change the dropdown to the right of the searchbox)")
                    return
                else:
                    property_name = "Main Tag"  # Use main tag if all are general
            else:
                property_name = "Tag"

        # Special handling for Subjects database when Tag is selected
        #if property_name == "":
        #    if self.database_selector.currentText() in ("Subjects", "Pharmacology", "eTG"):
        #        showInfo("Please select a subtag (Change the dropdown to the right of the searchbox)")
        #        return
        #    else:
        #        property_name = "Tag"

        # if self.database_selector.currentText() in ("Subjects", "Pharmacology", "eTG") and property_name == "":
        #     # Use Main Tag instead of Tag
        #     # property_name = "Main Tag"

        #     showInfo("Please select a subtag (Change the 'Tag' dropdown to the right of the searchbox)")
        #     return

        # Special handling for eTG database when subtag is empty
        if self.database_selector.currentText() == "eTG" and property_name != "Tag" and property_name != "Main Tag":
            # Check if the selected subtag property is empty
            subtag_pages = []
            for page in selected_pages:
                subtag_prop = page['properties'].get(property_name)

                # If subtag is empty, use 'Tag' property instead
                if (not subtag_prop or
                    (subtag_prop['type'] == 'formula' and
                     (not subtag_prop['formula'].get('string') or subtag_prop['formula'].get('string').strip() == ''))):

                    # Fallback to 'Tag' property
                    tag_prop = page['properties'].get('Tag')
                    if tag_prop and tag_prop['type'] == 'formula' and tag_prop['formula'].get('string'):
                        subtag_pages.append(page)
                else:
                    subtag_pages.append(page)

            selected_pages = subtag_pages

        tags = []
        for page in selected_pages:
            # Try to use the selected subtag property
            tag_prop = page['properties'].get(property_name)

            # If subtag is empty, fall back to 'Tag'
            if (not tag_prop or
                (tag_prop['type'] == 'formula' and
                 (not tag_prop['formula'].get('string') or tag_prop['formula'].get('string').strip() == ''))):
                if self.database_selector.currentText() == "Subjects":
                    tag_prop = page['properties'].get('Main Tag')
                else:
                    tag_prop = page['properties'].get('Tag')

            if tag_prop and tag_prop['type'] == 'formula':
                formula_value = tag_prop['formula']
                if formula_value['type'] == 'string':
                    tags.extend(formula_value['string'].split())

        if not selected_pages:
            tags = ["#Malleus_CM::#TO_BE_TAGGED"]

        # Add yield and paediatrics tags
        all_tags = tags + selected_yields + self.get_paediatrics_tag()

        # Prepare note data
        note = {
            'deckName': self.config['deck_name'],
            'modelName': 'MalleusCM - Cloze (Malleus Clinical Medicine [AU/NZ] / Stapedius)',
            'fields': {},
            'tags': all_tags
        }

        # Add source field for eTG database
        if self.database_selector.currentText() == "eTG" or self.database_selector.currentText() == "Textbooks" or self.database_selector.currentText() == "Guidelines":
            sources = []
            for page in selected_pages:
                source = self.get_property_content(page, 'Source')
                if source:
                    sources.append(source)

            # Combine sources, remove duplicates
            unique_sources = list(dict.fromkeys(sources))

            # Join sources with line breaks and add to fields
            if unique_sources:
                note['fields']['Source'] = '<br>'.join(unique_sources)

        # Open add cards dialog first, then async-populate Extra (Synced)
        self.guiAddCards(note)

        # After dialog opens, populate Extra (Synced) in background (Subjects only)
        if self.database_selector.currentText() == "Subjects":
            self._async_update_extra_synced(all_tags)
        # self.accept()

    def _apply_extra_synced_dialog(self, anki_note, notion_cache, parent_widget=None, note_context=None):
        """
        Show the SE selection dialog for Extra (Synced) and update the note in place.
        - Finds all matching SE entries for the note's current tags.
        - Pre-checks entries already present in the field (by <!-- se:N --> marker).
        - Builds new field content + updates SE Anki tags from user selection.
        - If no matches exist, clears the field and removes SE tags silently.
        Does NOT flush the note.
        """
        from aqt.qt import QDialog
        if parent_widget is None:
            parent_widget = self

        # 1. Check field exists
        try:
            current_field = anki_note[EXTRA_FIELD]
        except Exception:
            return  # field not in note type

        # 2. Find all matching entries
        entries = get_matching_se_entries(list(anki_note.tags), notion_cache, SYNCED_EXTRA_DATABASE_ID)

        # 3. No matches — clear field and remove SE tags
        if not entries:
            existing_se_ids = get_existing_se_ids_from_field(current_field)
            if existing_se_ids or current_field.strip():
                anki_note[EXTRA_FIELD] = ""
                anki_note.tags = [t for t in anki_note.tags
                                  if not t.startswith(SE_EXTRA_TAG_PREFIX)]
            return

        # 4. Determine which entries are already in the field
        existing_se_ids = get_existing_se_ids_from_field(current_field)

        # 4b. Skip the dialog if every matched entry is already in the field —
        #     there is nothing new to show.  But if even one entry is new
        #     (its se_id is absent from existing_se_ids) we show the dialog so
        #     the user can decide whether to include it.
        all_already_present = all(
            e.get('se_id') and e['se_id'] in existing_se_ids
            for e in entries
        )
        if all_already_present:
            return

        # 5. Show dialog
        dialog = SyncedExtraSelectionDialog(
            parent_widget, entries, existing_se_ids, note_context=note_context
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return  # user cancelled — leave field unchanged

        selected = dialog.get_selected_entries()

        # 6. Build new field content
        anki_note[EXTRA_FIELD] = build_field_from_selected_entries(selected)

        # 7. Sync SE Anki tags: strip all, re-add for selected entries only
        base_tags = [t for t in anki_note.tags if not t.startswith(SE_EXTRA_TAG_PREFIX)]
        for entry in selected:
            if entry.get("tag"):
                base_tags.append(entry["tag"])
        anki_note.tags = list(set(base_tags))

    def _schedule_extra_synced(self, anki_note, notion_cache, subjects_db=True):
        """
        Update both synced fields:
        - Additional Resources (Synced): always auto-populated silently.
        - Extra (Synced): shows the selection dialog when subjects_db=True,
          but only if there is at least one SE entry not already in the field.
          _apply_extra_synced_dialog handles that check internally, so this
          method simply delegates whenever the Subjects database is active.
        Does NOT flush the note.
        """
        # Additional Resources always auto-populates without a dialog
        set_additional_resources_on_note(anki_note, notion_cache)

        # Extra (Synced) — delegate; internal guard skips if nothing is new
        if subjects_db:
            note_context = None
            try:
                note_context = anki_note['Text'] or None
            except Exception:
                pass
            self._apply_extra_synced_dialog(
                anki_note, notion_cache,
                parent_widget=self,
                note_context=note_context
            )

    def _async_update_extra_synced(self, tags):
        """
        Called after Create Cards opens the AddCards dialog.
        Shows the SE selection dialog for Extra (Synced) and auto-fills
        Additional Resources (Synced). Runs synchronously on the main thread.
        """
        from aqt import dialogs
        from aqt.qt import QDialog
        try:
            ac = dialogs._dialogs.get('AddCards', [None, None])[1]
            if not (ac and hasattr(ac, 'editor') and ac.editor.note):
                return
            note = ac.editor.note
            changed = False

            # Additional Resources — auto
            additional = build_additional_resources_content(tags, self.notion_cache)
            if additional and 'Additional Resources (Synced)' in note:
                note['Additional Resources (Synced)'] = additional
                changed = True

            # Extra (Synced) — dialog only when the field is currently empty
            entries = get_matching_se_entries(tags, self.notion_cache, SYNCED_EXTRA_DATABASE_ID)
            if entries:
                try:
                    current_field = note[EXTRA_FIELD]
                except Exception:
                    current_field = ''
                # Skip only when every entry is already in the field
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

    def guiAddCards(self, note):
        collection = mw.col

        print(self.parent()) #debugging
        # If we're in the add cards dialog, update the existing note
        if isinstance(self.parent(), AddCards):
            addCards = self.parent()
            current_note = addCards.editor.note

            # Update tags
            if 'tags' in note:
                current_tags = current_note.tags
                current_tags.extend(note['tags'])
                current_note.tags = list(set(current_tags))  # Remove duplicates

            # Apply any pre-built fields (e.g. Extra (Synced), Source) from note dict
            if 'fields' in note:
                for name, value in note['fields'].items():
                    try:
                        if current_note[name] is not None:
                            current_note[name] = value
                    except Exception:
                        pass

            # Refresh the editor to show the new tags
            try:
                # Try new version method first
                addCards.editor.loadNote()
            except TypeError:
                try:
                    # Try old version method
                    addCards.editor.loadNote(full=True)
                except:
                    # Fallback to basic loadNote if both fail
                    addCards.editor.loadNote(current_note)

            # self.accept()
            return

        # Otherwise, proceed with creating a new note as before
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

        # Fill note fields
        if 'fields' in note:
            for name, value in note['fields'].items():
                if name in ankiNote:
                    ankiNote[name] = value

        # Set tags
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

    def get_tags_from_selected_pages(self):
        """Extract tags from selected pages"""
        selected_pages = []
        for i, checkbox in enumerate(self._get_result_checkboxes()):
            if checkbox.isChecked():
                selected_pages.append(self.pages_data[i])

        property_name = self.property_selector.currentText()

        # Normalise empty property_name per database
        if self.database_selector.currentText() == "Subjects" and property_name == "":
            property_name = "Main Tag"
        elif self.database_selector.currentText() == "Pharmacology" and property_name == "":
            property_name = "Tag"
        elif self.database_selector.currentText() == "eTG" and property_name == "":
            property_name = "Tag"

        tags = []

        if self.database_selector.currentText() == "eTG":
            # Build Subject / Pharmacology lookup dicts once (keyed by page id, both
            # hyphenated and bare forms so we always find a match regardless of format)
            subjects_lookup = {}
            pharmacology_lookup = {}

            subjects_subtags = {s for s in DATABASE_PROPERTIES.get("Subjects", []) if s}
            pharmacology_subtags = {s for s in DATABASE_PROPERTIES.get("Pharmacology", []) if s}

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

            for page in selected_pages:
                tags.extend(self._get_etg_tags_for_page(
                    page, property_name, subjects_subtags, pharmacology_subtags,
                    subjects_lookup, pharmacology_lookup
                ))
        else:
            for page in selected_pages:
                if property_name == "Tag" or property_name == "Main Tag":
                    tag_prop = page['properties'].get(property_name)
                else:
                    tag_prop = page['properties'].get(property_name)
                    if (not tag_prop or
                            (tag_prop['type'] == 'formula' and
                             (not tag_prop['formula'].get('string') or
                              tag_prop['formula'].get('string').strip() == ''))):
                        tag_prop = page['properties'].get('Tag')

                if tag_prop and tag_prop['type'] == 'formula':
                    formula_value = tag_prop['formula']
                    if formula_value['type'] == 'string':
                        tags.extend(formula_value['string'].split())

        if not selected_pages:
            tags = ["#Malleus_CM::#TO_BE_TAGGED"]

        return tags

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

        # 1. Always add the eTG page's own Tag formula
        etg_tag_prop = page['properties'].get('Tag')
        if etg_tag_prop and etg_tag_prop.get('type') == 'formula':
            tag_str = etg_tag_prop['formula'].get('string', '').strip()
            if tag_str:
                tags.extend(tag_str.split())

        # 2. If no meaningful subtag selected, we're done
        if not property_name or property_name in ('Tag', 'Main Tag'):
            return tags

        # 3. Subjects subtag — look up each linked Subject page
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

        # 4. Pharmacology subtag — look up each linked Pharmacology page
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

    def show_tag_selection_dialog(self, tags_with_subtags):
        """Show dialog for user to select which tags to replace"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Tags to Replace")
        dialog.setMinimumWidth(600)

        layout = QVBoxLayout()

        # Info label
        info_label = QLabel("Multiple subtags detected. Please select which tags you want to replace:")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Scrollable area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        checkbox_layout = QVBoxLayout()

        # Store checkboxes and their associated data
        checkboxes = []

        for tag, subtag in tags_with_subtags:
            # Remove #Malleus_CM:: prefix for display
            display_tag = tag.replace("#Malleus_CM::", "")

            checkbox = QCheckBox(display_tag)
            checkbox.tag_data = (tag, subtag)  # Store full tag and subtag
            checkboxes.append(checkbox)
            checkbox_layout.addWidget(checkbox)

        scroll_widget.setLayout(checkbox_layout)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # Buttons
        button_layout = QHBoxLayout()

        # select_all_button = QPushButton("Select All")
        # select_all_button.clicked.connect(lambda: [cb.setChecked(True) for cb in checkboxes])
        # button_layout.addWidget(select_all_button)

        button_layout.addStretch()

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)
        dialog.setLayout(layout)

        # Show dialog and get result
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Get selected tags
            selected = [(cb.tag_data[0], cb.tag_data[1]) for cb in checkboxes if cb.isChecked()]
            return selected

        return None

    # Add this helper method to your NotionPageSelector class

    def get_notes_to_process(self):
        """Get all notes that should be processed based on current context"""
        parent = self.parent()
        notes = []

        if isinstance(parent, Browser):
            # Check if multiple cards are selected
            selected_card_ids = parent.selectedCards()

            if len(selected_card_ids) > 1:
                # Multiple cards selected - get all notes
                for card_id in selected_card_ids:
                    card = mw.col.get_card(card_id)
                    note = card.note()
                    if note and note not in notes:  # Avoid duplicates
                        notes.append(note)
            elif len(selected_card_ids) == 1:
                # Single card selected - use editor note
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


    def remove_tags(self):
        """Remove all tags associated with the currently selected database"""
        notes = self.get_notes_to_process()

        if not notes:
            showInfo("No notes found in current context")
            return

        # Get selected database name
        database_name = self.database_selector.currentText()

        # Map database selector names to their tag equivalents
        database_tag_mapping = {
            "Subjects": "Subjects",
            "Pharmacology": "Pharmacology",
            "eTG": "eTG",
            "Rotation": "Resources_by_Rotation",
            "Textbooks": "Textbooks",
            "Guidelines": "Guidelines"
        }

        # Get the actual tag name
        tag_database_name = database_tag_mapping.get(database_name, database_name)
        database_pattern = f"#{tag_database_name}::"

        # Track statistics
        total_notes = len(notes)
        notes_modified = 0
        total_tags_removed = 0
        all_removed_tags = set()

        # Check if we're in AddCards context
        parent = self.parent()
        is_add_cards = isinstance(parent, AddCards)

        # Process each note
        for note in notes:
            current_tags = list(note.tags)
            tags_to_remove = [tag for tag in current_tags if database_pattern in tag]

            if tags_to_remove:
                # Remove the tags
                remaining_tags = [tag for tag in current_tags if tag not in tags_to_remove]
                note.tags = remaining_tags

                # Update Extra (Synced) to reflect removed tags
                self._schedule_extra_synced(
                    note, self.notion_cache,
                    subjects_db=(database_name == "Subjects")
                )

                # Only flush if not in AddCards dialog
                if not is_add_cards:
                    note.flush()

                notes_modified += 1
                total_tags_removed += len(tags_to_remove)
                all_removed_tags.update(tags_to_remove)

        # Refresh the UI
        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        # Show summary
        if notes_modified == 0:
            showInfo(f"No tags found for database: {database_name}")
        else:
            # Create a summary message
            summary = f"Successfully processed {total_notes} note(s)\n"
            summary += f"Modified: {notes_modified} note(s)\n"
            summary += f"Total tags removed: {total_tags_removed}\n\n"

            # Show unique tags that were removed (limit to 20 for readability)
            unique_tags = sorted(all_removed_tags)
            if len(unique_tags) <= 20:
                summary += "Tags removed:\n" + "\n".join(unique_tags)
            else:
                summary += "Tags removed (showing first 20):\n" + "\n".join(unique_tags[:20])
                summary += f"\n... and {len(unique_tags) - 20} more"

            showInfo(summary)
            
    def add_tags(self):
        """Add new tags to existing ones"""
        notes = self.get_notes_to_process()

        if not notes:
            showInfo("No notes found in current context")
            return

        selected_pages = []
        for i, checkbox in enumerate(self._get_result_checkboxes()):
            if checkbox.isChecked():
                selected_pages.append(self.pages_data[i])

        selected_yields = self.get_selected_yield_tags()

        # Check if user has selected either pages or yields
        if not selected_pages and not selected_yields:
            showInfo("Please select at least one page or yield level")
            return

        # If only yield is selected (no pages), just update yield
        if not selected_pages and selected_yields:
            return self._update_yield_only(notes, selected_yields)

        property_name = self.property_selector.currentText()

        # Check if all selected pages are general
        all_general = all(
            'ℹ️' in page.get('properties', {}).get('Search Prefix', {}).get('formula', {}).get('string', '')
            for page in selected_pages
        )

        if property_name == "":
            if self.database_selector.currentText() in ("Subjects", "Pharmacology"):
                if not all_general:
                    showInfo("Please select a subtag (Change the dropdown to the right of the searchbox)")
                    return
                else:
                    property_name = "Main Tag"
            else:
                property_name = "Tag"

        # For single note, use dedicated function
        if len(notes) == 1:
            result = self._add_tags_single_note(notes[0], selected_pages, property_name)

            if result:
                parent = self.parent()
                if isinstance(parent, Browser):
                    parent.model.reset()
                elif isinstance(parent, EditCurrent):
                    parent.editor.loadNote()
                elif isinstance(parent, AddCards):
                    parent.editor.loadNote()
            return

        # Track statistics for multiple notes
        total_notes = len(notes)
        notes_modified = 0
        notes_with_yield_issues = 0
        notes_needing_yield = 0

        # Check if we're in AddCards context
        parent = self.parent()
        is_add_cards = isinstance(parent, AddCards)

        # Process each note
        for note in notes:
            # Handle yield tags
            existing_yields = self.get_existing_yield_tags(note.tags)
            selected_yields = self.get_selected_yield_tags()

            # Validate yield selection
            if len(selected_yields) > 1:
                notes_with_yield_issues += 1
                continue

            # Determine final yield tags to use
            final_yield_tags = []
            if not existing_yields and not selected_yields:
                notes_needing_yield += 1
                continue
            elif existing_yields and not selected_yields:
                final_yield_tags = existing_yields
            elif selected_yields:
                final_yield_tags = selected_yields

            # Get current tags
            current_tags = set(note.tags)

            # Remove any existing yield tags
            current_tags = {
                tag for tag in current_tags
                if not tag.startswith("#Malleus_CM::#Yield::")
            }

            # Get new tags
            # Temporarily set property selector
            original_property = self.property_selector.currentText()
            if property_name and property_name not in ("Tag", "Main Tag"):
                index = self.property_selector.findText(property_name)
                if index >= 0:
                    self.property_selector.setCurrentIndex(index)

            new_tags = set(self.get_tags_from_selected_pages())

            # Restore original property selector
            original_index = self.property_selector.findText(original_property)
            if original_index >= 0:
                self.property_selector.setCurrentIndex(original_index)

            # Combine new tags with final yield tags and paediatrics tag
            all_new_tags = new_tags | set(final_yield_tags) | set(self.get_paediatrics_tag())

            # Combine everything
            combined_tags = list(current_tags | all_new_tags)

            # Update the note
            note.tags = combined_tags

            # Populate Extra (Synced) field from SE array
            self._schedule_extra_synced(
                note, self.notion_cache,
                subjects_db=(self.database_selector.currentText() == "Subjects")
            )

            # Only flush if not in AddCards dialog
            if not is_add_cards:
                note.flush()

            notes_modified += 1

        # Refresh the UI
        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        # Show summary only for multiple notes
        summary = f"Successfully processed {total_notes} note(s)\n"
        summary += f"Modified: {notes_modified} note(s)\n"

        if notes_with_yield_issues > 0:
            summary += f"Skipped (multiple yields selected): {notes_with_yield_issues} note(s)\n"
        if notes_needing_yield > 0:
            summary += f"Skipped (no yield selected): {notes_needing_yield} note(s)\n"

        showInfo(summary)


    def _update_yield_only(self, notes, selected_yields):
        """Update only the yield tags without adding any other tags"""
        # Validate yield selection
        if len(selected_yields) > 1:
            showInfo("Please select only one yield level")
            return

        if len(selected_yields) == 0:
            showInfo("Please select a yield level")
            return

        # Check if we're in AddCards context
        parent = self.parent()
        is_add_cards = isinstance(parent, AddCards)
        is_single_note = (len(notes) == 1)

        # Track statistics
        total_notes = len(notes)
        notes_modified = 0

        # Process each note
        for note in notes:
            # Get current tags
            current_tags = list(note.tags)

            # Remove any existing yield tags
            remaining_tags = [tag for tag in current_tags if not tag.startswith("#Malleus_CM::#Yield::")]

            # Add the selected yield tag
            final_tags = remaining_tags + selected_yields

            # Update the note
            note.tags = final_tags

            # Update Additional Resources only (yield change doesn't affect SE matching)
            self._schedule_extra_synced(note, self.notion_cache, subjects_db=False)

            # Only flush if not in AddCards dialog
            if not is_add_cards:
                note.flush()

            notes_modified += 1

        # Refresh the UI
        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        # Show summary only for multiple notes
        if not is_single_note:
            summary = f"Successfully updated yield for {total_notes} note(s)\n"
            summary += f"New yield: {selected_yields[0].replace('#Malleus_CM::#Yield::', '')}"
            showInfo(summary)


    def _add_tags_single_note(self, note, selected_pages, property_name):
        """Handle add tags for a single note with proper validation"""
        # Handle yield tags
        existing_yields = self.get_existing_yield_tags(note.tags)
        selected_yields = self.get_selected_yield_tags()

        print(f"DEBUG Add Tags Single Note:")
        print(f"  Note tags: {note.tags}")
        print(f"  Existing yields: {existing_yields}")
        print(f"  Selected yields: {selected_yields}")

        # Validate yield selection
        if len(selected_yields) > 1:
            showInfo("Please select only one yield level")
            return False

        # Determine final yield tags to use
        final_yield_tags = []
        if not existing_yields and not selected_yields:
            showInfo("Please select a yield level for this card")
            return False
        elif existing_yields and not selected_yields:
            # Keep existing yield
            final_yield_tags = existing_yields
            print(f"  Using existing yield tags: {final_yield_tags}")
        elif selected_yields:
            # Use selected yield (replace existing if any)
            final_yield_tags = selected_yields
            print(f"  Using selected yield tags: {final_yield_tags}")

        # Get current tags
        current_tags = set(note.tags)

        # Remove any existing yield tags
        current_tags = {
            tag for tag in current_tags
            if not tag.startswith("#Malleus_CM::#Yield::")
        }

        print(f"  Tags after removing yields: {current_tags}")

        # Get new tags
        # Temporarily set property selector
        original_property = self.property_selector.currentText()
        if property_name and property_name not in ("Tag", "Main Tag"):
            index = self.property_selector.findText(property_name)
            if index >= 0:
                self.property_selector.setCurrentIndex(index)

        new_tags = set(self.get_tags_from_selected_pages())

        # Restore original property selector
        original_index = self.property_selector.findText(original_property)
        if original_index >= 0:
            self.property_selector.setCurrentIndex(original_index)

        print(f"  New tags to add: {new_tags}")

        # Combine new tags with final yield tags and paediatrics tag
        all_new_tags = new_tags | set(final_yield_tags) | set(self.get_paediatrics_tag())

        # Combine everything
        combined_tags = list(current_tags | all_new_tags)

        print(f"  Final combined tags: {combined_tags}")

        # Update the note
        note.tags = combined_tags

        # Populate Extra (Synced) field from SE array
        self._schedule_extra_synced(
            note, self.notion_cache,
            subjects_db=(self.database_selector.currentText() == "Subjects")
        )

        # Only flush if not in AddCards dialog
        parent = self.parent()
        if not isinstance(parent, AddCards):
            note.flush()

        return True

    def replace_tags(self):
        """
        Improved replace tags with better tag identification and user selection
        """
        from .tag_selection_dialog import TagSelectionDialog
        from ..tag_utils import simplify_tags_by_page
        from aqt.qt import QDialog

        notes = self.get_notes_to_process()

        if not notes:
            showInfo("No notes found in current context")
            return

        selected_pages = []
        for i, checkbox in enumerate(self._get_result_checkboxes()):
            if checkbox.isChecked():
                selected_pages.append(self.pages_data[i])

        selected_yields = self.get_selected_yield_tags()

        # Check if user has selected either pages or yields
        if not selected_pages and not selected_yields:
            showInfo("Please select at least one page or yield level")
            return

        # If only yield is selected (no pages), just update yield
        if not selected_pages and selected_yields:
            return self._update_yield_only(notes, selected_yields)

        # VALIDATION: Check if multiple pages are selected
        if len(selected_pages) > 1:
            showInfo("Please select only ONE page at a time when replacing tags.\n\n"
                    "Multiple pages selected will make tag replacement ambiguous.")
            return

        # Get selected database name
        database_name = self.database_selector.currentText()

        # Define possible subtags
        possible_subtags = self.database_properties.get(database_name, [])
        possible_subtags = [s for s in possible_subtags if s]

        # Get user-selected subtag from property selector
        user_selected_subtag = self.property_selector.currentText()

        # Check if all selected pages are general
        all_general = all(
            'ℹ️' in page.get('properties', {}).get('Search Prefix', {}).get('formula', {}).get('string', '')
            for page in selected_pages
        )

        # Track statistics
        total_notes = len(notes)
        notes_modified = 0
        notes_with_yield_issues = 0
        notes_skipped = 0

        # Check if we're in AddCards context
        parent = self.parent()
        is_add_cards = isinstance(parent, AddCards)

        # Process each note
        for note_index, note in enumerate(notes):
            # For batch operations, show progress
            if len(notes) > 1:
                print(f"Processing note {note_index + 1} of {total_notes}")

            # Handle yield tags
            existing_yields = self.get_existing_yield_tags(note.tags)
            selected_yields = self.get_selected_yield_tags()

            # Validate yield selection
            if len(selected_yields) > 1:
                notes_with_yield_issues += 1
                continue

            # Determine final yield tags
            final_yield_tags = []
            if not existing_yields and not selected_yields:
                # Prompt user to select a yield for this card
                note_context = None
                if 'Text' in note:
                    note_context = note['Text']
                
                prompted_yield = self._prompt_for_yield_selection(note_context)
                
                if prompted_yield is None:
                    # User cancelled - skip this note
                    notes_skipped += 1
                    continue
                
                final_yield_tags = [prompted_yield]
            elif existing_yields and not selected_yields:
                final_yield_tags = existing_yields
            elif selected_yields:
                final_yield_tags = selected_yields

            # Get current tags
            current_tags = list(note.tags)

            # Find tags matching the selected database
            database_pattern = f"#Malleus_CM::#{database_name}::"
            matching_tags = [tag for tag in current_tags if tag.startswith(database_pattern)]

            if not matching_tags:
                # No tags to replace
                continue

            # Simplify tags by page and subtag
            simplified_tags = simplify_tags_by_page(matching_tags, database_name)

            if not simplified_tags:
                continue

            # Determine which tags to replace
            tags_to_replace = []

            if len(simplified_tags) == 1:
                # Only one unique page/subtag combination - replace it directly
                tags_to_replace = simplified_tags[0]['original_tags']
            else:
                # Multiple tags - show selection dialog
                # Get context from note's Text field if available
                note_context = None
                if 'Text' in note:
                    note_context = note['Text']

                # Show dialog for this specific note
                dialog = TagSelectionDialog(self, simplified_tags, note_context)

                if dialog.exec() == QDialog.DialogCode.Accepted:
                    selected_tag_info = dialog.get_selected_tags()
                    # Collect all original tags from selected items
                    for tag_info in selected_tag_info:
                        tags_to_replace.extend(tag_info['original_tags'])
                else:
                    # User cancelled - skip this note
                    notes_skipped += 1
                    continue

            if not tags_to_replace:
                continue

            # Now perform the replacement
            result = self._perform_tag_replacement(
                note, 
                tags_to_replace, 
                selected_pages,
                database_name,
                possible_subtags,
                user_selected_subtag,
                all_general,
                final_yield_tags,
                is_add_cards
            )

            if result:
                notes_modified += 1

        # Refresh the UI
        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        # Show summary
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
        Perform the actual tag replacement on a note

        FIXED: Now properly handles subtags with number prefixes (e.g., "10_Management")

        Args:
            note: The note to modify
            tags_to_replace: List of tag strings to replace
            selected_pages: List of selected page data from Notion
            database_name: Name of the database
            possible_subtags: List of possible subtags for this database
            user_selected_subtag: User's selected subtag from dropdown
            all_general: Whether all pages are general pages
            final_yield_tags: The yield tags to use
            is_add_cards: Whether we're in AddCards context

        Returns:
            True if successful, False otherwise
        """
        from ..tag_utils import (get_subtag_from_tag, get_all_subtags_from_tags, 
                                 normalize_subtag_for_matching, get_subtags_with_normalization)

        # Get current tags
        current_tags = list(note.tags)

        # Remove the tags we're replacing
        remaining_tags = [tag for tag in current_tags if tag not in tags_to_replace]

        # Determine what subtag to use
        final_subtag = None

        if user_selected_subtag and user_selected_subtag not in ("", "Tag", "Main Tag"):
            # User explicitly selected a subtag
            final_subtag = user_selected_subtag
        else:
            # Infer subtag from the tags being replaced
            # Get the actual subtags from the tags (with number prefixes)
            raw_subtags = get_all_subtags_from_tags(tags_to_replace)

            if len(raw_subtags) == 1:
                # All tags have the same subtag - normalize it to match property selector
                raw_subtag = list(raw_subtags)[0]
                final_subtag = normalize_subtag_for_matching(raw_subtag, possible_subtags)
                print(f"DEBUG: Normalized subtag '{raw_subtag}' → '{final_subtag}'")
            elif len(raw_subtags) == 0:
                # No subtags in original tags
                if user_selected_subtag == "":
                    if database_name in ("Subjects", "Pharmacology"):
                        if not all_general:
                            showInfo("Please select a subtag (Change the dropdown to the right of the searchbox)")
                            return False
                        else:
                            final_subtag = "Main Tag"
                    else:
                        final_subtag = "Tag"
                else:
                    final_subtag = user_selected_subtag
            else:
                # Multiple different subtags
                # Try to normalize them and see if they're actually the same
                normalized_subtags = get_subtags_with_normalization(tags_to_replace, possible_subtags)

                if len(normalized_subtags) == 1:
                    # After normalization, they're all the same
                    final_subtag = list(normalized_subtags)[0]
                    print(f"DEBUG: Multiple raw subtags normalized to single: '{final_subtag}'")
                else:
                    # They're genuinely different - need user selection
                    if not user_selected_subtag or user_selected_subtag == "":
                        showInfo("The tags you're replacing have different subtags. Please select a subtag from the dropdown.")
                        return False
                    final_subtag = user_selected_subtag

        print(f"DEBUG: Final subtag to use: '{final_subtag}'")

        # Set property selector temporarily to get new tags
        original_property = self.property_selector.currentText()

        if final_subtag == "Main Tag" or (database_name in ("Subjects", "Pharmacology") and all_general):
            self.property_selector.setCurrentIndex(0)
            print("DEBUG: Set property selector to index 0 (Main Tag)")
        elif final_subtag and final_subtag not in ("Tag", "Main Tag"):
            index = self.property_selector.findText(final_subtag)
            if index >= 0:
                self.property_selector.setCurrentIndex(index)
                print(f"DEBUG: Set property selector to '{final_subtag}' at index {index}")
            else:
                print(f"WARNING: Could not find '{final_subtag}' in property selector")
                # Try without spaces
                for i in range(self.property_selector.count()):
                    item_text = self.property_selector.itemText(i)
                    if item_text.replace(' ', '').lower() == final_subtag.replace(' ', '').lower():
                        self.property_selector.setCurrentIndex(i)
                        print(f"DEBUG: Found match at index {i}: '{item_text}'")
                        break
        else:
            self.property_selector.setCurrentIndex(0)
            print("DEBUG: Set property selector to index 0 (default)")

        # Get new tags
        new_tags = self.get_tags_from_selected_pages()
        print(f"DEBUG: New tags: {new_tags}")

        # Restore property selector
        original_index = self.property_selector.findText(original_property)
        if original_index >= 0:
            self.property_selector.setCurrentIndex(original_index)

        # Remove existing yield tags
        remaining_tags = [tag for tag in remaining_tags if not tag.startswith("#Malleus_CM::#Yield::")]

        # Combine tags
        all_new_tags = new_tags + final_yield_tags + self.get_paediatrics_tag()
        final_tags = list(set(remaining_tags + all_new_tags))

        # Final validation
        yield_tags_in_final = [tag for tag in final_tags if tag.startswith("#Malleus_CM::#Yield::")]
        if len(yield_tags_in_final) > 1:
            showInfo(f"Error: Multiple yield tags detected in final result:\n" + "\n".join(yield_tags_in_final))
            return False
        elif len(yield_tags_in_final) == 0:
            showInfo("No yield tag. Please select a yield level.")
            return False

        print(f"DEBUG: Final tags for note: {final_tags}")

        # Update note
        note.tags = final_tags

        # Populate Extra (Synced) field from SE array
        self._schedule_extra_synced(
            note, self.notion_cache,
            subjects_db=(self.database_selector.currentText() == "Subjects")
        )

        # Only flush if not in AddCards dialog
        if not is_add_cards:
            note.flush()

        return True

    def _normalize_for_comparison(self, text):
        """Normalize text for comparison - handle spaces, slashes, underscores"""
        return text.replace(' ', '_').replace('/', '_').replace('&', '_').lower()

    def _prompt_for_yield_selection(self, note_context=None):
        """
        Show a dialog to prompt user for yield selection
        
        Args:
            note_context: Optional context from note's Text field to help identify the card
            
        Returns:
            Selected yield tag as string, or None if cancelled
        """
        from aqt.qt import QDialog, QVBoxLayout, QLabel, QRadioButton, QButtonGroup, QDialogButtonBox, QFrame
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Yield Level")
        dialog.setMinimumWidth(400)
        apply_malleus_style(dialog)

        layout = QVBoxLayout()
        
        # Info label
        info_label = QLabel("This card has no yield level. Please select one:")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(info_label)
        
        # Show note context if available
        if note_context:
            context_frame = QFrame()
            context_frame.setFrameShape(QFrame.Shape.StyledPanel)
            context_frame.setStyleSheet("background-color: palette(alternateBase); padding: 10px; border-radius: 7px; border: 1px solid rgba(74,130,204,0.30);")
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
        
        # Yield selection radio buttons
        button_group = QButtonGroup(dialog)
        radio_buttons = {}
        
        yield_options = {
            "High Yield": "#Malleus_CM::#Yield::High",
            "Medium Yield": "#Malleus_CM::#Yield::Medium",
            "Low Yield": "#Malleus_CM::#Yield::Low",
            "Beyond medical student level": "#Malleus_CM::#Yield::Beyond_medical_student_level"
        }
        
        for display_text, tag_value in yield_options.items():
            radio = QRadioButton(display_text)
            radio_buttons[display_text] = (radio, tag_value)
            button_group.addButton(radio)
            layout.addWidget(radio)
        
        # Set High Yield as default
        radio_buttons["High Yield"][0].setChecked(True)
        
        # OK and Cancel buttons
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.setLayout(layout)
        
        # Show dialog and return result
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for display_text, (radio, tag_value) in radio_buttons.items():
                if radio.isChecked():
                    return tag_value
        
        return None
