"""
Page Selector Dialog
Main UI for searching and selecting Notion pages
"""
from aqt import mw, dialogs
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox,
                    QLineEdit, QPushButton, QGroupBox, QScrollArea,
                    QWidget, QCheckBox, QButtonGroup, QRadioButton,
                    QLabel, QFrame, QTimer, Qt, QUrl, QWidget as QWidgetBase)
from aqt.browser import Browser
from aqt.addcards import AddCards
from aqt.editcurrent import EditCurrent
from aqt.utils import showInfo, tooltip
from PyQt6.QtGui import QDesktopServices
import anki.notes
from ..config import DATABASE_PROPERTIES, get_database_id, get_database_name
from ..utils import open_browser_with_search
from ..cache_updater import perform_cache_update

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
        self.setMinimumWidth(600)

        layout = QVBoxLayout()

        # Search section
        search_layout = QHBoxLayout()

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
        self.search_input.setPlaceholderText("Enter search term...")
        self.search_input.textChanged.connect(self.on_search_text_changed)
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

        layout.addLayout(search_layout)

        # Results section
        self.results_group = QGroupBox("Search Results")
        results_layout = QVBoxLayout()

        # Scrollable area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.checkbox_layout = QVBoxLayout()
        scroll_widget.setLayout(self.checkbox_layout)
        scroll.setWidget(scroll_widget)

        results_layout.addWidget(scroll)
        self.results_group.setLayout(results_layout)
        layout.addWidget(self.results_group)

        # Yield selection section
        yield_group = QGroupBox("Yield Level")
        yield_layout = QVBoxLayout()

        # Create a horizontal layout with title and info icon
        yield_title_layout = QHBoxLayout()
        yield_title_label = QLabel("Yield Level")
        yield_title_label.setStyleSheet("font-weight: bold; font-size: 13px;")

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
        info_label.setStyleSheet("QLabel { color: #666; font-size: 14px; margin-left: 5px; }")
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
        layout.addWidget(yield_group)

        # Buttons
        button_layout = QHBoxLayout()
        select_all_button = QPushButton("Select All")
        select_all_button.clicked.connect(self.select_all_pages)
        button_layout.addWidget(select_all_button)

        find_cards_button = QPushButton("Find Cards")
        find_cards_button.clicked.connect(self.search_cards)
        button_layout.addWidget(find_cards_button)
        
        if isinstance(self.parent(), AddCards):
            add_tags_button = QPushButton("Add Tags")
            add_tags_button.clicked.connect(self.add_tags)
            button_layout.addWidget(add_tags_button)
        else:
            create_cards_button = QPushButton("Create Cards")
            create_cards_button.clicked.connect(self.create_cards)
            button_layout.addWidget(create_cards_button)
            # Show Add Tags button if we have notes to process
            if self.has_notes_to_process():
                add_tags_button = QPushButton("Add Tags")
                add_tags_button.clicked.connect(self.add_tags)
                button_layout.addWidget(add_tags_button)

        # Show these buttons when editing an existing note OR when in browser with selected cards
        if self.has_notes_to_process():
            replace_tags_button = QPushButton("Replace Tags")
            replace_tags_button.clicked.connect(self.replace_tags)
            button_layout.addWidget(replace_tags_button)

            remove_tags_button = QPushButton("Remove Tags")
            remove_tags_button.clicked.connect(self.remove_tags)
            button_layout.addWidget(remove_tags_button)

        update_database_button = QPushButton("Update database")
        update_database_button.clicked.connect(
            lambda: perform_cache_update(self.notion_cache, mw)
        )
        button_layout.addWidget(update_database_button)

        guidelines_button = QPushButton("Submission Guidelines")
        guidelines_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://malleuscm.notion.site/submission-guidelines")
            )
        )
        button_layout.addWidget(guidelines_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

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
            tooltip("No results found. Try a different search term")
            return

        # Create checkboxes for results

        for page in self.pages_data:
            try:
                if self.database_selector.currentText() == "Textbooks":
                    title = page['properties']['Search Term']['formula']['string'] if page['properties'].get('Search Term', {}).get('formula', {}).get('string') else "Untitled"
                else:
                    title = page['properties']['Name']['title'][0]['text']['content'] if page['properties']['Name']['title'] else "Untitled"

                search_suffix = page['properties']['Search Suffix']['formula']['string'] if page['properties'].get('Search Suffix', {}).get('formula', {}).get('string') else ""

                if self.database_selector.currentText() == "Subjects" or self.database_selector.currentText() == "Pharmacology":
                    search_prefix = page['properties']['Search Prefix']['formula']['string'] if page['properties'].get('Search Suffix', {}).get('formula', {}).get('string') else ""
                    display_text = f"{search_prefix} {title} {search_suffix}"
                else:
                    display_text = f"{title} {search_suffix}"

                checkbox = QCheckBox(display_text)
                self.checkbox_layout.addWidget(checkbox)
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
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            checkbox.setChecked(True)

    def search_cards(self):
        selected_pages = []
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            if checkbox.isChecked():
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
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            if checkbox.isChecked():
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

        # Add yield tags
        all_tags = tags + selected_yields

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

        # Open add cards dialog
        self.guiAddCards(note)
        # self.accept()

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
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            if checkbox.isChecked():
                selected_pages.append(self.pages_data[i])

        property_name = self.property_selector.currentText()

        # Special handling for Subjects database when empty is selected
        if self.database_selector.currentText() == "Subjects" and property_name == "":
            property_name = "Main Tag"

        if self.database_selector.currentText() == "Pharmacology" and property_name == "":
            property_name = "Tag"

        tags = []
        for page in selected_pages:
            if property_name == "Tag" or property_name == "Main Tag":
                tag_prop = page['properties'].get(property_name)
            else:
                tag_prop = page['properties'].get(property_name)
                if (not tag_prop or
                    (tag_prop['type'] == 'formula' and
                     (not tag_prop['formula'].get('string') or tag_prop['formula'].get('string').strip() == ''))):
                    tag_prop = page['properties'].get('Tag')

            if tag_prop and tag_prop['type'] == 'formula':
                formula_value = tag_prop['formula']
                if formula_value['type'] == 'string':
                    tags.extend(formula_value['string'].split())

        if not selected_pages:
            tags = ["#Malleus_CM::#TO_BE_TAGGED"]

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
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
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

            # Combine new tags with final yield tags
            all_new_tags = new_tags | set(final_yield_tags)

            # Combine everything
            combined_tags = list(current_tags | all_new_tags)

            # Update the note
            note.tags = combined_tags

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

        # Combine new tags with final yield tags
        all_new_tags = new_tags | set(final_yield_tags)

        # Combine everything
        combined_tags = list(current_tags | all_new_tags)

        print(f"  Final combined tags: {combined_tags}")

        # Update the note
        note.tags = combined_tags

        # Only flush if not in AddCards dialog
        parent = self.parent()
        if not isinstance(parent, AddCards):
            note.flush()

        return True


    def replace_tags(self):
        """Replace existing tags with new ones from selected database"""
        notes = self.get_notes_to_process()

        if not notes:
            showInfo("No notes found in current context")
            return

        selected_pages = []
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
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
        notes_needing_yield = 0
        notes_with_multiple_subtags = 0

        # Check if we're in AddCards context
        parent = self.parent()
        is_add_cards = isinstance(parent, AddCards)

        # For single note, allow interactive dialog
        if len(notes) == 1:
            # Original single-note logic with dialog
            note = notes[0]
            result = self._replace_tags_single_note(
                note, selected_pages, database_name, possible_subtags,
                user_selected_subtag, all_general
            )

            if result:
                if isinstance(parent, Browser):
                    parent.model.reset()
                elif isinstance(parent, EditCurrent):
                    parent.editor.loadNote()
                elif isinstance(parent, AddCards):
                    parent.editor.loadNote()
            return

        # For multiple notes, process automatically
        for note in notes:
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
                notes_needing_yield += 1
                continue
            elif existing_yields and not selected_yields:
                final_yield_tags = existing_yields
            elif selected_yields:
                final_yield_tags = selected_yields

            # Get current tags
            current_tags = list(note.tags)

            # Find tags that match the selected database
            database_pattern = f"#Malleus_CM::#{database_name}::"
            tags_with_subtags = []
            detected_subtags = set()

            for tag in current_tags:
                if tag.startswith(database_pattern):
                    detected_subtag = None
                    tag_parts = tag.split("::")

                    if len(tag_parts) > 2:
                        last_segment = tag_parts[-1]

                        for subtag in possible_subtags:
                            normalized_subtag = self._normalize_for_comparison(subtag)
                            normalized_segment = self._normalize_for_comparison(last_segment)

                            if normalized_segment == normalized_subtag or normalized_segment.endswith(f"_{normalized_subtag}"):
                                detected_subtag = subtag
                                break

                            import re
                            segment_without_prefix = re.sub(r'^\d+_', '', last_segment)
                            normalized_without_prefix = self._normalize_for_comparison(segment_without_prefix)

                            if normalized_without_prefix == normalized_subtag:
                                detected_subtag = subtag
                                break

                    tags_with_subtags.append((tag, detected_subtag))
                    if detected_subtag:
                        detected_subtags.add(detected_subtag)

            # Determine which tags to remove and what subtag to use
            tags_to_remove = []
            final_subtag = None

            if user_selected_subtag and user_selected_subtag not in ("", "Tag", "Main Tag"):
                final_subtag = user_selected_subtag
                tags_to_remove = [tag for tag, subtag in tags_with_subtags]
            elif len(detected_subtags) > 1:
                # Multiple subtags - skip this note in batch mode
                notes_with_multiple_subtags += 1
                continue
            elif len(detected_subtags) == 1:
                final_subtag = list(detected_subtags)[0]
                tags_to_remove = [tag for tag, subtag in tags_with_subtags]
            else:
                tags_to_remove = [tag for tag, subtag in tags_with_subtags]

                if user_selected_subtag == "":
                    if database_name in ("Subjects", "Pharmacology"):
                        if not all_general:
                            continue
                        else:
                            final_subtag = "Main Tag"
                    else:
                        final_subtag = "Tag"
                else:
                    final_subtag = user_selected_subtag

            # Remove selected tags
            remaining_tags = [tag for tag in current_tags if tag not in tags_to_remove]

            # Set property selector temporarily
            original_property = self.property_selector.currentText()

            if final_subtag == "Main Tag" or (database_name in ("Subjects", "Pharmacology") and all_general):
                self.property_selector.setCurrentIndex(0)
            elif final_subtag and final_subtag not in ("Tag", "Main Tag"):
                index = self.property_selector.findText(final_subtag)
                if index >= 0:
                    self.property_selector.setCurrentIndex(index)
            else:
                self.property_selector.setCurrentIndex(0)

            # Get new tags
            new_tags = self.get_tags_from_selected_pages()

            # Restore property selector
            original_index = self.property_selector.findText(original_property)
            if original_index >= 0:
                self.property_selector.setCurrentIndex(original_index)

            # Remove existing yield tags
            remaining_tags = [tag for tag in remaining_tags if not tag.startswith("#Malleus_CM::#Yield::")]

            # Combine tags
            all_new_tags = new_tags + final_yield_tags
            final_tags = list(set(remaining_tags + all_new_tags))

            # Update note
            note.tags = final_tags

            # Only flush if not in AddCards dialog
            if not is_add_cards:
                note.flush()

            notes_modified += 1

        # Refresh UI
        parent = self.parent()
        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()
        elif isinstance(parent, AddCards):
            parent.editor.loadNote()

        # Show summary
        summary = f"Successfully processed {total_notes} note(s)\n"
        summary += f"Modified: {notes_modified} note(s)\n"

        if notes_with_yield_issues > 0:
            summary += f"Skipped (multiple yields selected): {notes_with_yield_issues} note(s)\n"
        if notes_needing_yield > 0:
            summary += f"Skipped (no yield selected): {notes_needing_yield} note(s)\n"
        if notes_with_multiple_subtags > 0:
            summary += f"Skipped (multiple subtags detected): {notes_with_multiple_subtags} note(s)\n"

        showInfo(summary)


    def _normalize_for_comparison(self, text):
        """Normalize text for comparison - handle spaces, slashes, underscores"""
        return text.replace(' ', '_').replace('/', '_').replace('&', '_').lower()


    def _replace_tags_single_note(self, note, selected_pages, database_name, 
                                   possible_subtags, user_selected_subtag, all_general):
        """Handle replace tags for a single note (with dialog support)"""
        # Handle yield tags
        existing_yields = self.get_existing_yield_tags(note.tags)
        selected_yields = self.get_selected_yield_tags()

        # Validate yield selection
        if len(selected_yields) > 1:
            showInfo("Please select only one yield level")
            return False

        # Determine final yield tags
        final_yield_tags = []
        if not existing_yields and not selected_yields:
            showInfo("Please select a yield level for this card")
            return False
        elif existing_yields and not selected_yields:
            final_yield_tags = existing_yields
        elif selected_yields:
            final_yield_tags = selected_yields

        # Get current tags
        current_tags = list(note.tags)

        # Find tags matching database
        database_pattern = f"#Malleus_CM::#{database_name}::"
        tags_with_subtags = []
        detected_subtags = set()

        for tag in current_tags:
            if tag.startswith(database_pattern):
                detected_subtag = None
                tag_parts = tag.split("::")

                if len(tag_parts) > 2:
                    last_segment = tag_parts[-1]

                    for subtag in possible_subtags:
                        normalized_subtag = self._normalize_for_comparison(subtag)
                        normalized_segment = self._normalize_for_comparison(last_segment)

                        if normalized_segment == normalized_subtag or normalized_segment.endswith(f"_{normalized_subtag}"):
                            detected_subtag = subtag
                            break

                        import re
                        segment_without_prefix = re.sub(r'^\d+_', '', last_segment)
                        normalized_without_prefix = self._normalize_for_comparison(segment_without_prefix)

                        if normalized_without_prefix == normalized_subtag:
                            detected_subtag = subtag
                            break

                tags_with_subtags.append((tag, detected_subtag))
                if detected_subtag:
                    detected_subtags.add(detected_subtag)

        # Determine which tags to remove and what subtag to use
        tags_to_remove = []
        final_subtag = None

        if user_selected_subtag and user_selected_subtag not in ("", "Tag", "Main Tag"):
            final_subtag = user_selected_subtag
            tags_to_remove = [tag for tag, subtag in tags_with_subtags]
        elif len(detected_subtags) > 1:
            # Show selection dialog
            selected_tags_data = self.show_tag_selection_dialog(tags_with_subtags)

            if selected_tags_data is None:
                return False

            if not selected_tags_data:
                showInfo("Please select at least one tag to replace")
                return False

            selected_subtags = set(subtag for tag, subtag in selected_tags_data if subtag)

            if len(selected_subtags) > 1:
                showInfo(f"Selected tags have different subtags: {', '.join(sorted(selected_subtags))}\n\nPlease select tags with the same subtag.")
                return False
            elif len(selected_subtags) == 1:
                final_subtag = list(selected_subtags)[0]
                tags_to_remove = [tag for tag, subtag in selected_tags_data]
            else:
                if database_name in ("Subjects", "Pharmacology"):
                    if not all_general:
                        showInfo("Selected tags have no subtags. Please select a subtag from the dropdown.")
                        return False
                    else:
                        final_subtag = "Main Tag"
                        tags_to_remove = [tag for tag, subtag in selected_tags_data]
                else:
                    final_subtag = "Tag"
                    tags_to_remove = [tag for tag, subtag in selected_tags_data]
        elif len(detected_subtags) == 1:
            final_subtag = list(detected_subtags)[0]
            tags_to_remove = [tag for tag, subtag in tags_with_subtags]
        else:
            tags_to_remove = [tag for tag, subtag in tags_with_subtags]

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

        # Remove selected tags
        remaining_tags = [tag for tag in current_tags if tag not in tags_to_remove]

        # Set property selector temporarily
        original_property = self.property_selector.currentText()

        if final_subtag == "Main Tag" or (database_name in ("Subjects", "Pharmacology") and all_general):
            self.property_selector.setCurrentIndex(0)
        elif final_subtag and final_subtag not in ("Tag", "Main Tag"):
            index = self.property_selector.findText(final_subtag)
            if index >= 0:
                self.property_selector.setCurrentIndex(index)
        else:
            self.property_selector.setCurrentIndex(0)

        # Get new tags
        new_tags = self.get_tags_from_selected_pages()

        # Restore property selector
        original_index = self.property_selector.findText(original_property)
        if original_index >= 0:
            self.property_selector.setCurrentIndex(original_index)

        # Remove existing yield tags
        remaining_tags = [tag for tag in remaining_tags if not tag.startswith("#Malleus_CM::#Yield::")]

        # Combine tags
        all_new_tags = new_tags + final_yield_tags
        final_tags = list(set(remaining_tags + all_new_tags))

        # Final validation
        yield_tags_in_final = [tag for tag in final_tags if tag.startswith("#Malleus_CM::#Yield::")]
        if len(yield_tags_in_final) > 1:
            showInfo(f"Error: Multiple yield tags detected in final result:\n" + "\n".join(yield_tags_in_final))
            return False
        elif len(yield_tags_in_final) == 0:
            showInfo("No yield tag. Please select a yield level.")
            return False

        # Update note
        note.tags = final_tags

        # Only flush if not in AddCards dialog
        parent = self.parent()
        if not isinstance(parent, AddCards):
            note.flush()

        return True
                
