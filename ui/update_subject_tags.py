"""
Update Malleus Subject Tags
Feature to update subject tags by re-searching the database cache
"""
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
                    QPushButton, QLabel, QScrollArea, QWidget,
                    QFrame, QComboBox, QRadioButton,
                    QButtonGroup, QGroupBox, QTimer)
from aqt.utils import showInfo, tooltip
from aqt import mw
from typing import List, Dict, Tuple, Optional
import re
from ..config import DATABASE_PROPERTIES, get_database_id
from ..tag_utils import parse_tag, normalize_subtag_for_matching


class MissingPageDialog(QDialog):
    """Dialog shown when a subject tag's page cannot be found in the cache."""

    def __init__(self, parent, missing_tag: str, note_context: str, notion_cache, config):
        super().__init__(parent)
        self.missing_tag = missing_tag
        self.note_context = note_context
        self.notion_cache = notion_cache
        self.config = config
        self.selected_page = None
        self.selected_subtag = None
        self.action = None  # 'ignore' or 'replace'

        self.parsed_tag = parse_tag(missing_tag)
        self.pages_data = []

        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Tag Not Found")
        self.setMinimumWidth(700)
        self.setMinimumHeight(520)

        layout = QVBoxLayout()

        # ── Missing tag ──────────────────────────────────────────────
        tag_label = QLabel("Tag not found:")
        tag_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(tag_label)

        tag_value = QLabel(self.missing_tag)
        tag_value.setStyleSheet("color: #d32f2f; margin-bottom: 6px;")
        tag_value.setWordWrap(True)
        layout.addWidget(tag_value)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep1)

        # ── Card context ─────────────────────────────────────────────
        if self.note_context:
            ctx_frame = QFrame()
            ctx_frame.setFrameShape(QFrame.Shape.StyledPanel)
            ctx_frame.setStyleSheet(
                "background-color: #f0f0f0; padding: 8px; border-radius: 4px; margin: 6px 0;"
            )
            ctx_layout = QVBoxLayout()

            ctx_title = QLabel("Card context:")
            ctx_title.setStyleSheet("font-weight: bold; font-size: 11px;")
            ctx_layout.addWidget(ctx_title)

            display_ctx = self.note_context[:300] + ("..." if len(self.note_context) > 300 else "")
            ctx_text = QLabel(display_ctx)
            ctx_text.setWordWrap(True)
            ctx_text.setStyleSheet("font-size: 10px; color: #333;")
            ctx_layout.addWidget(ctx_text)

            ctx_frame.setLayout(ctx_layout)
            layout.addWidget(ctx_frame)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        # ── Search section ───────────────────────────────────────────
        search_group = QGroupBox("Suggest alternative page:")
        search_layout = QVBoxLayout()

        search_controls = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search...")
        # Pre-fill with page name from missing tag
        if self.parsed_tag and self.parsed_tag.get('page_name'):
            self.search_input.setText(
                self.parsed_tag['page_name'].replace('_', ' ')
            )
        self.search_input.textChanged.connect(self.on_search_text_changed)
        search_controls.addWidget(self.search_input)

        # Subtag selector – pre-select to match the missing tag's subtag
        self.subtag_selector = QComboBox()
        properties = DATABASE_PROPERTIES.get("Subjects", [])
        self.subtag_selector.addItems(properties)
        if self.parsed_tag and self.parsed_tag.get('subtag'):
            raw = self.parsed_tag['subtag']
            possible = [s for s in properties if s]
            normalized = normalize_subtag_for_matching(raw, possible)
            if normalized:
                idx = self.subtag_selector.findText(normalized)
                if idx >= 0:
                    self.subtag_selector.setCurrentIndex(idx)
        search_controls.addWidget(self.subtag_selector)

        search_layout.addLayout(search_controls)

        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)

        results_label = QLabel("Results:")
        results_label.setStyleSheet("font-weight: bold; margin-top: 6px;")
        search_layout.addWidget(results_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(180)
        scroll_widget = QWidget()
        self.results_layout = QVBoxLayout()
        scroll_widget.setLayout(self.results_layout)
        scroll.setWidget(scroll_widget)
        search_layout.addWidget(scroll)

        search_group.setLayout(search_layout)
        layout.addWidget(search_group)

        # Button group for radio buttons (created here so clear_results can reference it)
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)

        # ── Action buttons ───────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        ignore_btn = QPushButton("Ignore and Remove Tag")
        ignore_btn.clicked.connect(self.ignore_tag)
        btn_layout.addWidget(ignore_btn)

        replace_btn = QPushButton("Use this tag instead")
        replace_btn.setDefault(True)
        replace_btn.clicked.connect(self.replace_tag)
        btn_layout.addWidget(replace_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

        # Trigger initial search after UI renders
        if self.search_input.text():
            self.search_timer.start(100)

    # ── Search helpers ───────────────────────────────────────────────

    def on_search_text_changed(self, text):
        if len(text) >= 2:
            self.search_timer.start(self.config.get('search_delay', 300))
        else:
            self.clear_results()

    def clear_results(self):
        """Remove all result radio buttons from both the layout and the button group."""
        for btn in self.button_group.buttons():
            self.button_group.removeButton(btn)
        for i in reversed(range(self.results_layout.count())):
            widget = self.results_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self.pages_data = []

    def perform_search(self):
        search_term = self.search_input.text()
        if not search_term or len(search_term) < 2:
            self.clear_results()
            return

        self.clear_results()

        database_id = get_database_id("Subjects")
        try:
            cached_pages, _ = self.notion_cache.load_from_cache(database_id)
            self.pages_data = (
                self.notion_cache.filter_pages(cached_pages, search_term)
                if cached_pages else []
            )
        except Exception as e:
            showInfo(f"Error searching: {e}")
            self.pages_data = []

        if not self.pages_data:
            no_res = QLabel("No results found")
            no_res.setStyleSheet("color: #666; font-style: italic; padding: 8px;")
            self.results_layout.addWidget(no_res)
            return

        for page in self.pages_data:
            try:
                title = (
                    page['properties']['Name']['title'][0]['text']['content']
                    if page['properties']['Name']['title'] else "Untitled"
                )
                suffix = page['properties'].get('Search Suffix', {}).get('formula', {}).get('string', '')
                prefix = page['properties'].get('Search Prefix', {}).get('formula', {}).get('string', '')
                display = f"{prefix} {title} {suffix}".strip()

                radio = QRadioButton(display)
                radio.page_data = page
                self.button_group.addButton(radio)
                self.results_layout.addWidget(radio)
            except Exception as e:
                print(f"Error processing page result: {e}")

    # ── Button handlers ──────────────────────────────────────────────

    def ignore_tag(self):
        self.action = 'ignore'
        self.accept()

    def replace_tag(self):
        selected = self.button_group.checkedButton()
        if not selected:
            showInfo("Please select a page, or click 'Ignore and Remove Tag'.")
            return
        self.selected_page = selected.page_data
        self.selected_subtag = self.subtag_selector.currentText()
        self.action = 'replace'
        self.accept()

    def get_result(self) -> Tuple[str, Optional[Dict], Optional[str]]:
        """Returns (action, page_data, subtag_property_name)."""
        return (self.action, self.selected_page, self.selected_subtag)


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
        target = page_name.replace('_', ' ').lower().strip()

        for page in cached_pages:
            try:
                title_list = page['properties']['Name']['title']
                if not title_list:
                    continue
                title = title_list[0]['text']['content'].lower().strip()
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

    layout = QVBoxLayout()

    info_label = QLabel("This card has no yield level. Please select one:")
    info_label.setWordWrap(True)
    info_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
    layout.addWidget(info_label)

    if note_context:
        ctx_frame = QFrame()
        ctx_frame.setFrameShape(QFrame.Shape.StyledPanel)
        ctx_frame.setStyleSheet("background-color: #f0f0f0; padding: 8px; border-radius: 4px;")
        ctx_layout = QVBoxLayout()

        ctx_title = QLabel("Card context:")
        ctx_title.setStyleSheet("font-weight: bold; font-size: 11px;")
        ctx_layout.addWidget(ctx_title)

        ctx_text = QLabel(note_context[:200] + ("..." if len(note_context) > 200 else ""))
        ctx_text.setWordWrap(True)
        ctx_text.setStyleSheet("font-size: 10px; color: #333;")
        ctx_layout.addWidget(ctx_text)

        ctx_frame.setLayout(ctx_layout)
        layout.addWidget(ctx_frame)

    button_group = QButtonGroup(dialog)
    radio_buttons = {}

    yield_options = {
        "High Yield":                    "#Malleus_CM::#Yield::High",
        "Medium Yield":                  "#Malleus_CM::#Yield::Medium",
        "Low Yield":                     "#Malleus_CM::#Yield::Low",
        "Beyond medical student level":  "#Malleus_CM::#Yield::Beyond_medical_student_level"
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
         - Missing → show MissingPageDialog; user picks a replacement or ignores.
      5. Write final tags back to the note.
    """
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

    for note_index, note in enumerate(notes):
        print(f"Processing note {note_index + 1} of {total_notes}")

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

            if page:
                tags = get_tags_for_page(page, raw_subtag)
                if tags:
                    new_tags.extend(tags)
                    total_tags_updated += 1
                else:
                    # Page found but property empty – keep the original tag
                    print(f"No tag data for property '{raw_subtag}' on page '{page_name}', keeping original")
                    remaining_tags.append(original_tag)
            else:
                # Page not found – ask the user
                dialog = MissingPageDialog(
                    browser, original_tag, note_context, notion_cache, config
                )
                if dialog.exec():
                    action, selected_page, selected_subtag = dialog.get_result()
                    if action == 'replace' and selected_page:
                        tags = get_tags_for_page(selected_page, selected_subtag)
                        new_tags.extend(tags)
                        total_tags_updated += 1
                    else:  # 'ignore'
                        total_tags_removed += 1
                        # Tag is simply not re-added
                else:
                    # Dialog cancelled – restore original tag
                    remaining_tags.append(original_tag)

        final_tags = list(set(remaining_tags + new_tags))

        # Check for yield tag — prompt if missing
        has_yield = any(t.startswith("#Malleus_CM::#Yield::") for t in final_tags)
        if not has_yield:
            yield_tag = prompt_for_yield_selection(browser, note_context)
            if yield_tag:
                final_tags.append(yield_tag)

        if set(final_tags) != set(current_tags):
            note.tags = final_tags
            note.flush()
            notes_modified += 1

    browser.model.reset()

    summary = (
        f"Update Malleus Subject Tags Complete\n\n"
        f"Total notes processed: {total_notes}\n"
        f"Notes modified: {notes_modified}\n"
        f"Notes with no subject tags: {notes_with_no_subject_tags}\n"
        f"Tags successfully updated: {total_tags_updated}\n"
        f"Tags removed: {total_tags_removed}\n"
    )
    tooltip(f"Updated {notes_modified} notes")
    showInfo(summary)
