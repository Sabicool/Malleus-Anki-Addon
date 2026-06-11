"""
Update Malleus Subject Tags
Feature to update subject tags by re-searching the database cache
"""
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
                    QPushButton, QLabel, QScrollArea, QWidget,
                    QFrame, QCheckBox, QGroupBox, QTimer, Qt, QKeyEvent)
from aqt.utils import showInfo
from ..utils import malleus_tooltip
from aqt import mw
from typing import List, Dict, Tuple, Optional
import re
from ..config import DATABASE_PROPERTIES, get_database_id
from ..cache_updater import perform_cache_update
from ..extra_sync import (
    build_additional_resources_content,
    get_matching_se_entries, get_existing_se_ids_from_field,
    build_field_from_selected_entries, SE_EXTRA_TAG_PREFIX,
    SYNCED_EXTRA_DATABASE_ID, EXTRA_FIELD
)
from .synced_extra_dialog import SyncedExtraSelectionDialog
from ..tag_utils import parse_tag, normalize_subtag_for_matching
from ..suggest_tags import suggest_subject_tags
from .page_selector import _SubtagChip
try:
    from ..ui.styles import apply_malleus_style, make_header, COLORS
except Exception:
    def apply_malleus_style(w): pass
    def make_header(title="Malleus Clinical Medicine", subtitle=None, logo_path=None):
        from aqt.qt import QWidget, QHBoxLayout, QLabel
        h = QWidget(); h.setFixedHeight(48 if not subtitle else 62)
        lay = QHBoxLayout(h); lay.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel(title); lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        lay.addWidget(lbl); lay.addStretch(); return h
    COLORS = {}
import unicodedata

class MissingPageDialog(QDialog):
    """Dialog shown when a subject tag's page cannot be found in the cache.

    Mirrors the Page Selector's result UI: each result is a checkable row with
    its own inline subtag chip (pre-selected from the missing tag's subtag, or
    the suggestion's subtag).  Multiple pages can be selected — every checked
    page contributes its tags as the replacement."""

    def __init__(self, parent, missing_tag: str, note_context: str, notion_cache, config):
        super().__init__(parent)
        self.missing_tag = missing_tag
        self.note_context = note_context
        self.notion_cache = notion_cache
        self.config = config
        self.selections = []   # [(page, subtag_property_name), ...] on 'replace'
        self.action = None     # 'ignore' or 'replace'

        self.parsed_tag = parse_tag(missing_tag)
        self._result_rows = []  # [{page, checkbox, chip}, ...]

        # Subtag options + the default chip pre-selection derived from the
        # missing tag's own subtag (e.g. '05_Pathophysiology' → 'Pathophysiology').
        self._subtag_options = DATABASE_PROPERTIES.get("Subjects", [""])
        self._default_subtag = ''
        if self.parsed_tag and self.parsed_tag.get('subtag'):
            possible = [s for s in self._subtag_options if s]
            normalized = normalize_subtag_for_matching(self.parsed_tag['subtag'], possible)
            if normalized:
                self._default_subtag = normalized

        self.setup_ui()
        apply_malleus_style(self)

    def setup_ui(self):
        self.setWindowTitle("Tag Not Found")
        self.setMinimumWidth(700)
        self.setMinimumHeight(540)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = make_header("Tag Not Found",
                             "This subject tag could not be matched in the database")
        layout.addWidget(header)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(16, 14, 16, 12)
        content_layout.setSpacing(10)

        # ── Missing tag ──────────────────────────────────────────────
        tag_label = QLabel("Missing tag:")
        tag_label.setStyleSheet(f"font-weight: 700; font-size: 11px; color: {COLORS.get('accent','#4a82cc')}; background: transparent; letter-spacing: 0.3px;")
        content_layout.addWidget(tag_label)

        tag_frame = QFrame()
        tag_frame.setStyleSheet("background-color: palette(base); border: 1.5px solid rgba(192,80,80,0.45); border-radius: 6px; padding: 6px;")
        tag_frame_layout = QVBoxLayout(tag_frame)
        tag_frame_layout.setContentsMargins(10, 6, 10, 6)
        tag_value = QLabel(self.missing_tag)
        tag_value.setStyleSheet("color: #c05050; font-size: 12px; font-family: monospace; background: transparent;")
        tag_value.setWordWrap(True)
        tag_frame_layout.addWidget(tag_value)
        content_layout.addWidget(tag_frame)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        content_layout.addWidget(sep1)

        # ── Card context ─────────────────────────────────────────────
        if self.note_context:
            ctx_frame = QFrame()
            ctx_frame.setFrameShape(QFrame.Shape.StyledPanel)
            ctx_frame.setStyleSheet(
                f"background-color: palette(alternateBase); padding: 8px; border-radius: 7px; border: 1px solid rgba(74,130,204,0.35);"
            )
            ctx_layout = QVBoxLayout()

            ctx_title = QLabel("Card context:")
            ctx_title.setStyleSheet(f"font-weight: 700; font-size: 11px; color: {COLORS.get('accent','#4a82cc')}; background: transparent; letter-spacing: 0.3px;")
            ctx_layout.addWidget(ctx_title)

            display_ctx = self.note_context[:300] + ("..." if len(self.note_context) > 300 else "")
            ctx_text = QLabel(display_ctx)
            ctx_text.setWordWrap(True)
            ctx_text.setStyleSheet("font-size: 11px; background: transparent;")
            ctx_layout.addWidget(ctx_text)

            ctx_frame.setLayout(ctx_layout)
            content_layout.addWidget(ctx_frame)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        content_layout.addWidget(sep2)

        # ── Search section ───────────────────────────────────────────
        search_group = QGroupBox("Suggest alternative page:")
        search_layout = QVBoxLayout()
        search_layout.setSpacing(8)

        search_controls = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search...")
        # Pre-fill with page name from missing tag
        if self.parsed_tag and self.parsed_tag.get('page_name'):
            raw = self.parsed_tag['page_name'].replace('_', ' ')
            normalised = unicodedata.normalize('NFKD', raw)
            normalised = ''.join(c for c in normalised if not unicodedata.combining(c))
            self.search_input.setText(normalised)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        search_controls.addWidget(self.search_input)

        suggest_btn = QPushButton("✦ Suggest")
        suggest_btn.setObjectName("secondary")
        suggest_btn.setToolTip(
            "Suggest pages based on the card content\n"
            "(uses the same engine as the main Page Selector)"
        )
        suggest_btn.clicked.connect(self._suggest_tags)
        search_controls.addWidget(suggest_btn)

        search_layout.addLayout(search_controls)

        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)

        results_label = QLabel("Results:  (check one or more — each row has its own subtag chip)")
        results_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        search_layout.addWidget(results_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(180)
        scroll_widget = QWidget()
        self.results_layout = QVBoxLayout()
        self.results_layout.setSpacing(0)
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll_widget.setLayout(self.results_layout)
        scroll.setWidget(scroll_widget)
        search_layout.addWidget(scroll)

        search_group.setLayout(search_layout)
        content_layout.addWidget(search_group)

        # ── Action buttons ───────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        # Update database button (left-aligned, secondary style)
        update_db_btn = QPushButton("↻  Update Database")
        update_db_btn.setObjectName("secondary")
        update_db_btn.setToolTip(
            "Download the latest Malleus database cache\n"
            "Shift+click: full rebuild directly from Notion (slower)"
        )

        def _on_update_db():
            from aqt.qt import QApplication, Qt
            full = bool(
                QApplication.queryKeyboardModifiers()
                & Qt.KeyboardModifier.ShiftModifier
            )
            perform_cache_update(self.notion_cache, mw, full=full)

        update_db_btn.clicked.connect(_on_update_db)
        btn_layout.addWidget(update_db_btn)

        btn_layout.addStretch()

        ignore_btn = QPushButton("Ignore and Remove Tag")
        ignore_btn.setObjectName("danger")
        ignore_btn.clicked.connect(self.ignore_tag)
        btn_layout.addWidget(ignore_btn)

        replace_btn = QPushButton("Use selected page(s) instead")
        replace_btn.setDefault(True)
        replace_btn.clicked.connect(self.replace_tag)
        btn_layout.addWidget(replace_btn)

        content_layout.addLayout(btn_layout)
        layout.addWidget(content_widget)
        self.setLayout(layout)

        # Trigger initial search after UI renders
        prefill = self.search_input.text()
        prefill_clean = re.sub(r"['\u2019\u2018\u02bc]", '', prefill)
        self.search_input.setText(prefill_clean)
        if prefill_clean:
            self.search_timer.start(100)

    # ── Search helpers ───────────────────────────────────────────────

    def on_search_text_changed(self, text):
        if len(text) >= 2:
            self.search_timer.start(self.config.get('search_delay', 300))
        else:
            self.clear_results()

    def clear_results(self):
        """Remove all result rows from the layout."""
        for i in reversed(range(self.results_layout.count())):
            widget = self.results_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self._result_rows = []

    def _add_result_row(self, page: dict, preset_subtag: str = None):
        """Add one checkable result row with its own inline subtag chip
        (mirrors the Page Selector).  General ℹ️ pages get no chip — they
        always use the 'Main Tag' property."""
        try:
            title = (
                page['properties']['Name']['title'][0]['text']['content']
                if page['properties']['Name']['title'] else "Untitled"
            )
            suffix = page['properties'].get('Search Suffix', {}).get('formula', {}).get('string', '')
            prefix = page['properties'].get('Search Prefix', {}).get('formula', {}).get('string', '')
            # Escape & as && so QCheckBox renders a literal ampersand rather
            # than treating it as a Qt mnemonic prefix.
            display = f"{prefix} {title} {suffix}".strip().replace('&', '&&')
        except Exception:
            display = "Untitled"

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 1, 2, 1)
        row_layout.setSpacing(6)

        cb = QCheckBox(display)
        row_layout.addWidget(cb, stretch=1)

        chip = None
        if not is_general_page(page):
            chip = _SubtagChip(self._subtag_options)
            sel = preset_subtag or self._default_subtag
            if sel:
                idx = chip.findText(sel)
                if idx >= 0:
                    chip.setCurrentIndex(idx)
            chip.setVisible(False)   # revealed when the row is checked
            cb.stateChanged.connect(lambda state, c=chip: c.setVisible(state == 2))
            row_layout.addWidget(chip, stretch=0)

        self.results_layout.addWidget(row)
        self._result_rows.append({'page': page, 'checkbox': cb, 'chip': chip})

    def _get_result_checkboxes(self) -> list:
        return [r['checkbox'] for r in self._result_rows]

    def keyPressEvent(self, event: QKeyEvent):
        """
        Keyboard shortcuts:
          Up / Down    — move focus between result rows
          Space        — toggle the focused row (Qt default)
          Enter/Return — confirm selection (when focus is outside the search box)
          Escape       — close the dialog
        """
        key = event.key()
        checkboxes = self._get_result_checkboxes()

        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            if not checkboxes:
                super().keyPressEvent(event)
                return
            focused = None
            for i, cb in enumerate(checkboxes):
                if cb.hasFocus():
                    focused = i
                    break
            if key == Qt.Key.Key_Down:
                next_idx = (focused + 1) if focused is not None else 0
                next_idx = min(next_idx, len(checkboxes) - 1)
            else:
                next_idx = (focused - 1) if focused is not None else len(checkboxes) - 1
                next_idx = max(next_idx, 0)
            checkboxes[next_idx].setFocus()
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Don't intercept Enter while the user is typing in the search box
            if not self.search_input.hasFocus():
                self.replace_tag()
                event.accept()
                return

        super().keyPressEvent(event)

    def _suggest_tags(self):
        """Populate results with pages suggested from the card's text content."""
        self.clear_results()
        malleus_tooltip("Analysing card text…")

        suggestions = suggest_subject_tags(self.note_context or "", self.notion_cache)

        if not suggestions:
            no_res = QLabel("No suggestions found — try searching manually")
            no_res.setStyleSheet(
                "font-style: italic; padding: 8px; color: palette(placeholderText);"
            )
            self.results_layout.addWidget(no_res)
            return

        # Pre-set each row's chip to the suggested subtag (falls back to the
        # missing tag's subtag, then to no selection).
        suggested_subtag = suggestions[0].get('suggested_subtag')
        for suggestion in suggestions:
            self._add_result_row(suggestion['page'], preset_subtag=suggested_subtag)

        malleus_tooltip(f"Found {len(suggestions)} suggestion(s)")

    def perform_search(self):
        search_term = self.search_input.text()
        if not search_term or len(search_term) < 2:
            self.clear_results()
            return

        self.clear_results()

        # Strip apostrophe variants before searching — filter_pages splits on
        # punctuation so "Barrett's" → "Barrett s" → fails to match anything.
        # Removing the apostrophe entirely gives "Barretts" which matches fine.
        search_term_normalised = re.sub(r"['\u2019\u2018\u02bc]", '', search_term)

        database_id = get_database_id("Subjects")
        try:
            cached_pages, _ = self.notion_cache.load_from_cache(database_id)
            pages = (
                self.notion_cache.filter_pages(cached_pages, search_term_normalised)
                if cached_pages else []
            )
        except Exception as e:
            showInfo(f"Error searching: {e}")
            pages = []

        if not pages:
            no_res = QLabel("No results found")
            no_res.setStyleSheet("font-style: italic; padding: 8px; color: palette(placeholderText);")
            self.results_layout.addWidget(no_res)
            return

        for page in pages:
            try:
                self._add_result_row(page)
            except Exception as e:
                print(f"Error processing page result: {e}")

    # ── Button handlers ──────────────────────────────────────────────

    def ignore_tag(self):
        self.action = 'ignore'
        self.accept()

    def replace_tag(self):
        checked = [r for r in self._result_rows if r['checkbox'].isChecked()]
        if not checked:
            showInfo("Please select at least one page, or click 'Ignore and Remove Tag'.")
            return

        selections = []
        for row in checked:
            page = row['page']
            chip = row['chip']
            if chip is None:
                # General ℹ️ page — no subtag needed, uses 'Main Tag'
                selections.append((page, "Main_Tag"))
                continue
            subtag = chip.currentText().strip()
            if not subtag:
                try:
                    title = page['properties']['Name']['title'][0]['text']['content']
                except Exception:
                    title = "this page"
                showInfo(
                    f"Please select a subtag for:\n{title}\n\n"
                    "(Use the chip next to the checked result to choose a "
                    "category, e.g. Management, Clinical Features)"
                )
                return
            selections.append((page, subtag))

        self.selections = selections
        self.action = 'replace'
        self.accept()

    def get_result(self) -> Tuple[str, List[Tuple[Dict, str]]]:
        """Returns (action, selections) where selections is a list of
        (page_data, subtag_property_name) — one entry per checked row."""
        return (self.action, self.selections)


# ── Standalone helpers ───────────────────────────────────────────────────────

def parse_subject_tag(tag: str) -> Optional[Tuple[str, str]]:
    """
    Parse a #Subjects tag into (page_name_with_underscores, raw_subtag).

    raw_subtag is the literal last segment of the tag, e.g. '05_Pathophysiology',
    or 'Main_Tag' when the page is general (no subtag / asterisk subtag).
    """
    if not tag.startswith("#Malleus_CM::#Subjects::"):
        return None

    parsed = parse_tag(tag)
    if not parsed:
        return None

    page_name = parsed.get('page_name')  # e.g. 'Inclusion_body_myositis'
    subtag = parsed.get('subtag')        # e.g. '05_Pathophysiology' or None

    if not page_name:
        return None

    if not subtag or subtag.startswith('*'):
        subtag = "Main_Tag"

    return (page_name, subtag)

def _normalise(text: str) -> str:
    """Normalise text for exact-match comparison."""
    # Decompose accented characters (è → e + combining accent) then strip accents
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    # Normalise all apostrophe variants to straight quote
    text = text.replace('\u2019', "'").replace('\u2018', "'").replace('\u02bc', "'")
    # Underscores to spaces FIRST so that "_&_" becomes " & " before ampersand
    # substitution — otherwise "Foo_ and _Bar" → double spaces after underscore
    # replacement, which mismatches "Foo and Bar" (single space) from the title.
    text = text.replace('_', ' ')
    # Normalise ampersand (tag uses & , title may spell out 'and' or vice versa)
    text = re.sub(r'\s*&\s*', ' and ', text)
    # Collapse any runs of whitespace produced by the above steps
    text = re.sub(r'\s+', ' ', text)
    return text.lower().strip()

def is_general_page(page: Dict) -> bool:
    """Check if a page is a general page (has ℹ️ in Search Prefix)."""
    search_prefix = (
        page.get('properties', {})
            .get('Search Prefix', {})
            .get('formula', {})
            .get('string', '')
    )
    return 'ℹ️' in search_prefix

def search_page_in_cache(notion_cache, page_name: str) -> Optional[Dict]:
    """
    Find a page by *exact* title match (case-insensitive, underscore-tolerant)
    in the Subjects database cache.

    page_name comes straight from the tag, e.g. 'Inclusion_body_myositis'.
    """
    database_id = get_database_id("Subjects")
    try:
        cached_pages, _ = notion_cache.load_from_cache(database_id, warn_if_expired=False)
        if not cached_pages:
            return None

        # Normalise: underscores → spaces, lowercase
        target = _normalise(page_name)

        for page in cached_pages:
            try:
                title_list = page['properties']['Name']['title']
                if not title_list:
                    continue
                title = _normalise(title_list[0]['text']['content'])
                if title == target:
                    return page
            except Exception:
                continue

        return None

    except Exception as e:
        print(f"Error searching cache: {e}")
        return None


def get_tags_for_page(page: Dict, raw_subtag: str) -> List[str]:
    """
    Return the Anki tag string(s) stored in the appropriate Notion property.

    raw_subtag can be:
      - 'Main_Tag' or '' → use the 'Main Tag' property
      - '05_Pathophysiology' → strip number prefix → 'Pathophysiology' property
      - 'Pathophysiology'   → use directly (already normalised, e.g. from dialog)
    """
    possible_subtags = [s for s in DATABASE_PROPERTIES.get("Subjects", []) if s]

    if not raw_subtag or raw_subtag == "Main_Tag":
        property_name = "Main Tag"
    else:
        # normalize_subtag_for_matching strips leading digits and matches against
        # the human-readable property names in DATABASE_PROPERTIES
        property_name = normalize_subtag_for_matching(raw_subtag, possible_subtags)
        if not property_name:
            property_name = "Main Tag"

    # Try the resolved property first, fall back to 'Main Tag'
    tag_prop = page['properties'].get(property_name)

    if (not tag_prop or
            (tag_prop.get('type') == 'formula' and
             not tag_prop.get('formula', {}).get('string', '').strip())):
        tag_prop = page['properties'].get('Main Tag')

    if tag_prop and tag_prop.get('type') == 'formula':
        tag_string = tag_prop.get('formula', {}).get('string', '')
        return tag_string.split() if tag_string else []

    return []

def prompt_for_yield_selection(parent, note_context: str = None) -> Optional[str]:
    """
    Prompt the user to select a yield level for a note that has none.
    Returns the yield tag string, or None if cancelled.
    """
    from aqt.qt import QDialog, QVBoxLayout, QLabel, QRadioButton, QButtonGroup, QDialogButtonBox, QFrame

    dialog = QDialog(parent)
    dialog.setWindowTitle("Select Yield Level")
    dialog.setMinimumWidth(400)
    apply_malleus_style(dialog)

    layout = QVBoxLayout()

    info_label = QLabel("This card has no yield level. Please select one:")
    info_label.setWordWrap(True)
    info_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
    layout.addWidget(info_label)

    if note_context:
        ctx_frame = QFrame()
        ctx_frame.setFrameShape(QFrame.Shape.StyledPanel)
        ctx_frame.setStyleSheet("background-color: palette(alternateBase); padding: 8px; border-radius: 6px; border: 1px solid rgba(74,130,204,0.30);")
        ctx_layout = QVBoxLayout()

        ctx_title = QLabel("Card context:")
        ctx_title.setStyleSheet("font-weight: bold; font-size: 11px;")
        ctx_layout.addWidget(ctx_title)

        ctx_text = QLabel(note_context[:200] + ("..." if len(note_context) > 200 else ""))
        ctx_text.setWordWrap(True)
        ctx_text.setStyleSheet("font-size: 10px;")
        ctx_layout.addWidget(ctx_text)

        ctx_frame.setLayout(ctx_layout)
        layout.addWidget(ctx_frame)

    button_group = QButtonGroup(dialog)
    radio_buttons = {}

    yield_options = {
        "High Yield":                    "#Malleus_CM::#Yield::High",
        "Medium Yield":                  "#Malleus_CM::#Yield::Medium",
        "Low Yield":                     "#Malleus_CM::#Yield::Low",
        "Beyond Medical Student Level":  "#Malleus_CM::#Yield::Beyond_medical_student_level"
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

# ── Helpers ──────────────────────────────────────────────────────────────────

def _show_update_summary(browser, summary_text, notes, tag_snapshot):
    """
    Show a summary dialog after a bulk subject tag update.
    Includes an Undo button that restores all affected notes to their pre-update tags.
    """
    from aqt.qt import (QDialog, QVBoxLayout, QLabel, QDialogButtonBox,
                        QPushButton, QHBoxLayout)

    dialog = QDialog(browser)
    dialog.setWindowTitle("Update Complete")
    dialog.setMinimumWidth(420)
    apply_malleus_style(dialog)

    layout = QVBoxLayout()
    layout.setContentsMargins(16, 16, 16, 12)
    layout.setSpacing(10)

    label = QLabel(summary_text)
    label.setWordWrap(True)
    layout.addWidget(label)

    btn_layout = QHBoxLayout()
    btn_layout.setSpacing(8)

    undo_btn = QPushButton("Undo")
    undo_btn.setObjectName("danger")
    undo_btn.setToolTip("Restore all affected notes to their original tags")

    ok_btn = QPushButton("OK")
    ok_btn.setDefault(True)

    btn_layout.addWidget(undo_btn)
    btn_layout.addStretch()
    btn_layout.addWidget(ok_btn)
    layout.addLayout(btn_layout)
    dialog.setLayout(layout)

    def do_undo():
        for note in notes:
            original_tags = tag_snapshot.get(note.id)
            if original_tags is not None:
                note.tags = original_tags
                note.flush()
        browser.model.reset()
        dialog.accept()
        malleus_tooltip("Undo complete — tags restored")

    undo_btn.clicked.connect(do_undo)
    ok_btn.clicked.connect(dialog.accept)

    dialog.exec()


# ── Main entry point ─────────────────────────────────────────────────────────

def update_subject_tags_for_browser(browser, notion_cache, config):
    """
    Update Malleus #Subjects tags for the selected cards in the browser.

    For each note:
      1. Find all #Subjects tags.
      2. Parse each into (page_name, raw_subtag).
      3. Remove the old subject tags.
      4. For each (page_name, raw_subtag), do an exact-match cache lookup.
         - Found  → replace with the fresh tag from get_tags_for_page().
         - Missing → show MissingPageDialog; user picks replacement(s) or ignores.
      5. Write final tags back to the note.

    Shift+invoke = silent mode: the yield prompt and the Extra (Synced)
    selection dialog are skipped (notes missing a yield are left without one;
    Extra (Synced) content is left untouched).  The Tag-Not-Found dialog still
    appears — it needs user input to resolve.  The run can be cancelled from
    the progress dialog; already-processed notes keep their changes and can be
    reverted with Undo from the summary.
    """
    from aqt.qt import QApplication, Qt
    silent = bool(
        QApplication.queryKeyboardModifiers()
        & Qt.KeyboardModifier.ShiftModifier
    )

    selected_card_ids = browser.selectedCards()
    if not selected_card_ids:
        showInfo("No cards selected")
        return

    # Collect unique notes
    notes = []
    seen_note_ids = set()
    for card_id in selected_card_ids:
        card = mw.col.get_card(card_id)
        note = card.note()
        if note.id not in seen_note_ids:
            notes.append(note)
            seen_note_ids.add(note.id)

    if not notes:
        showInfo("No notes found")
        return

    total_notes = len(notes)
    notes_modified = 0
    notes_with_no_subject_tags = 0
    total_tags_updated = 0
    total_tags_removed = 0

    # Snapshot tags before any changes for undo support
    tag_snapshot = {note.id: list(note.tags) for note in notes}

    replacement_cache = {}  # page_name → (action, selections)

    from aqt.qt import QProgressDialog
    _progress = QProgressDialog(
        f"Updating subject tags (0/{total_notes})...",
        "Cancel",      # cancellable — large batch updates can be aborted
        0,
        total_notes,
        browser,
    )
    _progress.setWindowTitle("Malleus: Update Subject Tags")
    _progress.setWindowModality(Qt.WindowModality.WindowModal)
    _progress.setMinimumDuration(0)   # show immediately, no delay
    _progress.setValue(0)             # triggers the initial paint
    QApplication.processEvents()      # flush the render queue

    def _progress_update(note_index):
        _progress.setValue(note_index + 1)
        _progress.setLabelText(
            f"Updating subject tags ({note_index + 1}/{total_notes})..."
        )
        QApplication.processEvents()

    def _progress_finish():
        _progress.close()

    cancelled = False
    notes_processed = 0

    for note_index, note in enumerate(notes):
        if _progress.wasCanceled():
            cancelled = True
            break
        notes_processed = note_index + 1
        _progress_update(note_index)
        current_tags = list(note.tags)
        subject_tags = [t for t in current_tags if t.startswith("#Malleus_CM::#Subjects::")]

        if not subject_tags:
            notes_with_no_subject_tags += 1
            continue

        # Parse each subject tag → (original_tag, page_name, raw_subtag)
        parsed_tags = []
        for tag in subject_tags:
            result = parse_subject_tag(tag)
            if result:
                parsed_tags.append((tag, result[0], result[1]))
            else:
                print(f"Could not parse subject tag, keeping as-is: {tag}")

        if not parsed_tags:
            notes_with_no_subject_tags += 1
            continue

        # Strip old subject tags; unparseable ones are kept automatically
        parseable_originals = {pt[0] for pt in parsed_tags}
        remaining_tags = [t for t in current_tags if t not in parseable_originals]

        note_context = note['Text'] if 'Text' in note else ""
        new_tags = []

        for original_tag, page_name, raw_subtag in parsed_tags:
            page = search_page_in_cache(notion_cache, page_name)
            is_improper = False  # track whether this tag is structurally bad

            if page:
                if raw_subtag == "Main_Tag" and not is_general_page(page):
                    page = None
                    is_improper = True  # bad tag — do NOT restore if user cancels

            if page:
                tags = get_tags_for_page(page, raw_subtag)
                if tags:
                    new_tags.extend(tags)
                    total_tags_updated += 1
                else:
                    print(f"No tag data for property '{raw_subtag}' on '{page_name}', keeping original")
                    remaining_tags.append(original_tag)
            else:
                # Improper tags (non-general page with no numbered subtag) must always
                # go through the dialog — never silently reuse a cached subtag that was
                # chosen for a different occurrence of the same page name.
                use_cache = (page_name in replacement_cache) and not is_improper

                if use_cache:
                    action, selections = replacement_cache[page_name]
                    if action == 'replace' and selections:
                        # Use raw_subtag from the current tag (e.g. '06_Clinical_Features')
                        # so each legitimate occurrence gets the right property.
                        for sel_page, _sel_subtag in selections:
                            new_tags.extend(get_tags_for_page(sel_page, raw_subtag))
                        total_tags_updated += 1
                    elif action == 'cancel':
                        # Never restore an improper tag (is_improper is False here,
                        # but keep the guard for clarity)
                        if not is_improper:
                            remaining_tags.append(original_tag)
                    else:  # ignore
                        total_tags_removed += 1
                else:
                    _progress.hide()
                    dialog = MissingPageDialog(
                        browser, original_tag, note_context, notion_cache, config
                    )
                    if dialog.exec():
                        action, selections = dialog.get_result()
                        if action == 'replace' and selections:
                            for sel_page, sel_subtag in selections:
                                new_tags.extend(get_tags_for_page(sel_page, sel_subtag))
                            total_tags_updated += 1
                        else:  # ignore
                            total_tags_removed += 1
                    else:
                        # Dialog cancelled via X
                        action, selections = ('cancel', [])
                        if not is_improper:
                            remaining_tags.append(original_tag)
                        # if improper, drop it — it was a bad tag and the user closed the dialog

                    # Only cache the result for non-improper tags so the same
                    # user choice can be reused for other legitimate missing pages.
                    if not is_improper:
                        replacement_cache[page_name] = (action, selections)
                    _progress.show()

        final_tags = list(set(remaining_tags + new_tags))

        # Check for yield tag — prompt if missing (skipped in silent mode:
        # the note is left without a yield tag rather than interrupting).
        has_yield = any(t.startswith("#Malleus_CM::#Yield::") for t in final_tags)
        if not has_yield and not silent:
            _progress.hide()
            yield_tag = prompt_for_yield_selection(browser, note_context)
            if yield_tag:
                final_tags.append(yield_tag)
            _progress.show()

        # ── Additional Resources (Synced) — auto-populate ───────────────────
        _additional = build_additional_resources_content(list(final_tags), notion_cache)
        if _additional and 'Additional Resources (Synced)' in note:
            note['Additional Resources (Synced)'] = _additional

        # ── Extra (Synced) — selection dialog ────────────────────────────────
        # Silent mode leaves Extra (Synced) content and SE tags entirely
        # untouched (no dialog, and no silent clearing either).
        _se_changed = False
        entries = ([] if silent else
                   get_matching_se_entries(list(final_tags), notion_cache, SYNCED_EXTRA_DATABASE_ID))
        if entries:
            try:
                current_extra = note[EXTRA_FIELD]
            except Exception:
                current_extra = ''
            existing_se_ids = get_existing_se_ids_from_field(current_extra)

            _progress.hide()
            se_dlg = SyncedExtraSelectionDialog(
                browser, entries, existing_se_ids, note_context=note_context
            )
            from aqt.qt import QDialog
            _se_result = se_dlg.exec()
            _progress.show()
            if _se_result == QDialog.DialogCode.Accepted:
                selected = se_dlg.get_selected_entries()
                try:
                    note[EXTRA_FIELD] = build_field_from_selected_entries(selected)
                except Exception:
                    pass
                # Sync SE Anki tags
                final_tags = [t for t in final_tags if not t.startswith(SE_EXTRA_TAG_PREFIX)]
                for entry in selected:
                    if entry.get('tag'):
                        final_tags.append(entry['tag'])
                final_tags = list(set(final_tags))
                _se_changed = True
            # If cancelled, leave existing Extra (Synced) content untouched
        elif not silent:
            # No matches — clear field and strip SE tags
            try:
                if note[EXTRA_FIELD].strip():
                    note[EXTRA_FIELD] = ''
                    _se_changed = True
            except Exception:
                pass
            final_tags = [t for t in final_tags if not t.startswith(SE_EXTRA_TAG_PREFIX)]

        if set(final_tags) != set(current_tags) or _additional or _se_changed:
            note.tags = final_tags
            note.flush()
            notes_modified += 1

    _progress_finish()
    browser.model.reset()

    if cancelled:
        headline = (
            f"Update Malleus Subject Tags Cancelled\n"
            f"(stopped after {notes_processed} of {total_notes} notes — "
            f"changes already made are kept; use Undo to revert them)\n\n"
        )
    else:
        headline = "Update Malleus Subject Tags Complete\n\n"

    summary = (
        headline +
        f"Total notes processed: {notes_processed if cancelled else total_notes}\n"
        f"Notes modified: {notes_modified}\n"
        f"Notes with no subject tags: {notes_with_no_subject_tags}\n"
        f"Tags successfully updated: {total_tags_updated}\n"
        f"Tags removed: {total_tags_removed}\n"
    )
    malleus_tooltip(f"Updated {notes_modified} notes")
    _show_update_summary(browser, summary, notes, tag_snapshot)
