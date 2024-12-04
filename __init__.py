from aqt import mw
from aqt.qt import *
from aqt.utils import showInfo, tooltip
import os
import requests
from dotenv import load_dotenv
from aqt import dialogs
from aqt.browser import Browser
from aqt.addcards import AddCards
from anki.hooks import addHook
import anki.notes
from typing import Tuple, List, Dict, Optional
import json
import time
from pathlib import Path
import threading
from datetime import datetime
import asyncio
from functools import partial

# Load environment variables
addon_dir = os.path.dirname(os.path.realpath(__file__))
env_path = os.path.join(addon_dir, '.env')
load_dotenv(env_path)

NOTION_TOKEN = os.getenv('NOTION_TOKEN')
SUBJECT_DATABASE_ID = os.getenv('DATABASE_ID')
PHARMACOLOGY_DATABASE_ID = os.getenv('PHARMACOLOGY_DATABASE_ID')
ETG_DATABASE_ID = os.getenv('ETG_DATABASE_ID')

config = mw.addonManager.getConfig(__name__)

class NotionCache:
    """Handles caching of Notion database content"""
    CACHE_VERSION = 1
    # CACHE_EXPIRY = config['cache_expiry'] * 24 * 60 * 60  # 24 hours in seconds

    def __init__(self, addon_dir: str):
        self.cache_dir = Path(addon_dir) / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_lock = threading.Lock()
        #self.sync_progress = None
        #self.sync_timer = None
        self._sync_thread = None
        self.headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        #self.config_manager = ConfigManager()
        self.CACHE_EXPIRY = config['cache_expiry'] * 24 * 60 * 60

    def confirm_sync(self, database_name: str) -> bool:
        """Ask user for confirmation before syncing a specific database"""
        # Use QMessageBox directly for confirmation
        msg = QMessageBox(mw)
        msg.setWindowTitle("Sync Confirmation")
        msg.setText(f"Would you like to sync the {database_name} database?")
        msg.setInformativeText("This may take a few minutes.")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.Yes)
        return msg.exec_() == QMessageBox.Yes

    def get_cache_path(self, database_id: str) -> Path:
        """Get the path for a specific database's cache file"""
        return self.cache_dir / f"{database_id}.json"

    def load_from_cache(self, database_id: str) -> Tuple[List[Dict], float]:
        """Load cached data if it exists and is not expired"""
        cache_path = self.get_cache_path(database_id)
        if not cache_path.exists():
            return [], time.time()  # Return current time instead of 0

        try:
            with cache_path.open('r', encoding='utf-8') as f:
                cache_data = json.load(f)

            # Check cache version and expiry
            current_time = time.time()
            cache_timestamp = float(cache_data.get('timestamp', current_time))

            if (cache_data.get('version') != self.CACHE_VERSION or
                current_time - cache_timestamp > self.CACHE_EXPIRY):
                return [], current_time  # Return current time instead of 0

            return cache_data.get('pages', []), cache_timestamp

        except Exception as e:
            mw.taskman.run_on_main(lambda: showInfo(f"Error loading cache: {e}"))
            return [], time.time()

    def save_to_cache(self, database_id: str, pages: List[Dict]):
        """Save pages to cache file and update timestamp"""
        cache_path = self.get_cache_path(database_id)
        current_time = time.time()

        try:
            # Try to load existing cache data
            with cache_path.open('r', encoding='utf-8') as f:
                cache_data = json.load(f)
                existing_pages = cache_data.get('pages', [])
        except (FileNotFoundError, json.JSONDecodeError):
            # If no existing cache, create new cache data
            cache_data = {
                'version': self.CACHE_VERSION,
                'pages': []
            }
            existing_pages = []

        # Always update the timestamp, regardless of whether there are new pages
        cache_data['timestamp'] = current_time

        # Only merge pages if there are new ones
        if pages:
            # Create dictionaries for easy lookup
            existing_dict = {page['id']: page for page in existing_pages}
            new_dict = {page['id']: page for page in pages}

            # Merge existing and new pages
            merged_dict = {**existing_dict, **new_dict}
            cache_data['pages'] = list(merged_dict.values())

        # Save the updated cache data
        with self.cache_lock:
            with cache_path.open('w', encoding='utf-8') as f:
                json.dump(cache_data, f)

    def is_cache_expired(self, database_id: str) -> bool:
        """Check if cache is expired"""
        cache_path = self.get_cache_path(database_id)
        if not cache_path.exists():
            return True

        try:
            with cache_path.open('r', encoding='utf-8') as f:
                cache_data = json.load(f)

            cache_timestamp = float(cache_data.get('timestamp', 0))
            return (time.time() - cache_timestamp) > self.CACHE_EXPIRY
        except Exception:
            return True

    def update_cache_async(self, database_id: str, force: bool = False, callback: callable = None):
        """Update cache asynchronously with optional callback"""
        if not force and not self.is_cache_expired(database_id):
            if callback:
                callback()
            return

        # Get user confirmation in the main thread if not forced
        if not force:
            confirmed = [False]
            def ask_confirmation():
                # Determine database name based on database_id
                database_name = "Unknown Database"
                if database_id == SUBJECT_DATABASE_ID:
                    database_name = "Subjects"
                elif database_id == PHARMACOLOGY_DATABASE_ID:
                    database_name = "Pharmacology"
                elif database_id == ETG_DATABASE_ID:
                    database_name = "eTG"

                confirmed[0] = self.confirm_sync(database_name)
            mw.taskman.run_on_main(ask_confirmation)
            if not confirmed[0]:
                if callback:
                    callback()
                return

        def sync_thread():
            try:
                mw.taskman.run_on_main(lambda: tooltip("Updating database...", period=1000))
                cached_pages, last_sync_timestamp = self.load_from_cache(database_id)
                pages = self.fetch_updated_pages(database_id, last_sync_timestamp)
                self.save_to_cache(database_id, pages)
                mw.taskman.run_on_main(lambda: tooltip("Update complete"))

                if callback:
                    mw.taskman.run_on_main(callback)
            except Exception as e:
                mw.taskman.run_on_main(lambda: showInfo(f"Error during sync: {e}"))
                if callback:
                    mw.taskman.run_on_main(callback)

        self._sync_thread = threading.Thread(target=sync_thread)
        self._sync_thread.start()

    def fetch_updated_pages(self, database_id: str, last_sync_timestamp: float) -> List[Dict]:
        """Fetch all pages from a Notion database that have been updated since the last sync"""
        pages = []
        has_more = True
        start_cursor = None

        # Ensure we have a valid timestamp
        if last_sync_timestamp <= 0:
            last_sync_timestamp = time.time() - self.CACHE_EXPIRY

        last_sync_date = datetime.fromtimestamp(last_sync_timestamp).strftime('%Y-%m-%d')

        while has_more:
            payload = {
                "filter": {
                    "and": [
                        {
                            "property": "For Search",
                            "formula": {
                                "checkbox": {
                                    "equals": True
                                }
                            }
                        },
                        {
                            "timestamp": "last_edited_time",
                            "last_edited_time": {
                                "on_or_after": last_sync_date
                            }
                        }
                    ]
                },
                "page_size": 100
            }

            if start_cursor:
                payload["start_cursor"] = start_cursor

            try:
                print(f"Fetching pages updated since: {datetime.fromtimestamp(last_sync_timestamp).isoformat()}")
                print(f"Payload: {payload}")
                response = requests.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    headers=self.headers,
                    json=payload
                )
                print(f"Response status code: {response.status_code}")
                print(f"Response data: {response.json()}")
                response.raise_for_status()
                data = response.json()

                pages.extend(data['results'])
                has_more = data.get('has_more', False)
                start_cursor = data.get('next_cursor')

            except Exception as e:
                showInfo(f"Error fetching from Notion: {e}")
                break

        print(f"Found {len(pages)} updated pages")
        return pages

    def filter_pages(self, pages: List[Dict], search_term: str) -> List[Dict]:
        """Filter pages based on search term"""
        search_term = search_term.lower()
        filtered_pages = []

        for page in pages:
            # Check if page has properties
            if not page.get('properties'):
                continue

            # Get the search term from the page
            search_term_prop = page['properties'].get('Search Term', {})
            if not search_term_prop or search_term_prop.get('type') != 'formula':
                continue

            page_search_term = search_term_prop.get('formula', {}).get('string', '').lower()

            # Check if the search term matches
            if search_term in page_search_term: #or page_search_term in search_term:
                filtered_pages.append(page)

        return filtered_pages

def open_browser_with_search(search_query):
    """Open the browser with a search query"""
    browser = dialogs.open('Browser', mw)
    browser.activateWindow()

    if search_query:
        browser.form.searchEdit.lineEdit().setText(search_query)
        if hasattr(browser, 'onSearch'):
            browser.onSearch()
        else:
            browser.onSearchActivated()
    return

class NotionPageSelector(QDialog):
    def __init__(self, parent=None):
        if parent is not None and not isinstance(parent, QWidget):
            parent = mw
        super().__init__(parent)
        self.notion_cache = NotionCache(addon_dir)
        # Initialize cache on startup without forcing
        if SUBJECT_DATABASE_ID:
            self.notion_cache.update_cache_async(SUBJECT_DATABASE_ID, force=False)
        if PHARMACOLOGY_DATABASE_ID:
            self.notion_cache.update_cache_async(PHARMACOLOGY_DATABASE_ID, force=False)
        if ETG_DATABASE_ID:
            self.notion_cache.update_cache_async(ETG_DATABASE_ID, force=False)
        self.database_properties = {
            "Subjects": [
                "Tag",
                "Epidemiology",
                "Aetiology",
                "Risk Factors",
                "Physiology/Anatomy",
                "Pathophysiology",
                "Clinical Features",
                "Pathology",
                "Diagnosis/Investigations",
                "Scoring Criteria",
                "Management",
                "Complications/Prognosis",
                "Screening/Prevention"
            ],
            "Pharmacology": [
                "Tag",
                "Generic Names",
                "Indications",
                "Contraindications/Precautions",
                "Route/Frequency",
                "Adverse Effects",
                "Toxicity & Reversal",
                "Advantages/Disadvantages",
                "Monitoring"
            ],
            "eTG": [
                "Tag",
                "Epidemiology",
                "Aetiology",
                "Risk Factors",
                "Physiology/Anatomy",
                "Pathophysiology",
                "Clinical Features",
                "Pathology",
                "Diagnosis/Investigations",
                "Scoring Criteria",
                "Management",
                "Complications/Prognosis",
                "Screening/Prevention",
                "Generic Names",
                "Indications",
                "Contraindications/Precautions",
                "Route/Frequency",
                "Adverse Effects",
                "Toxicity & Reversal",
                "Advantages/Disadvantages",
                "Monitoring"
            ]
        }
        self.setup_ui()
        self.pages_data = []  # Store full page data

    def setup_ui(self):
        self.setWindowTitle("Malleus Page Selector")
        self.setMinimumWidth(600)

        layout = QVBoxLayout()

        # Search section
        search_layout = QHBoxLayout()

        # Database selector
        self.database_selector = QComboBox()
        self.database_selector.addItems(["Subjects", "Pharmacology", "eTG"])
        self.database_selector.currentTextChanged.connect(self.update_property_selector)
        search_layout.addWidget(self.database_selector)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter search term...")
        search_layout.addWidget(self.search_input)

        # Property selector
        self.property_selector = QComboBox()
        search_layout.addWidget(self.property_selector)

        # TODO Somehow make this dynamic depending on the database
        self.update_property_selector(self.database_selector.currentText())

        # Search button
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

        # Buttons
        button_layout = QHBoxLayout()
        select_all_button = QPushButton("Select All")
        select_all_button.clicked.connect(self.select_all_pages)

        find_cards_button = QPushButton("Find Cards")
        find_cards_button.clicked.connect(self.search_cards)

        create_cards_button = QPushButton("Create Cards")
        create_cards_button.clicked.connect(self.create_cards)

        update_database_button = QPushButton("Update database")
        update_database_button.clicked.connect(update_notion_cache)

        button_layout.addWidget(select_all_button)
        button_layout.addWidget(find_cards_button)
        button_layout.addWidget(create_cards_button)
        button_layout.addWidget(update_database_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def update_property_selector(self, database_name):
        """Update property selector items based on selected database"""
        self.property_selector.clear()
        properties = self.database_properties.get(database_name, [])
        self.property_selector.addItems(properties)

    def get_selected_database_id(self):
        if self.database_selector.currentText() == "Subjects":
            return SUBJECT_DATABASE_ID
        elif self.database_selector.currentText() == "Pharmacology":
            return PHARMACOLOGY_DATABASE_ID
        else:  # eTG
            return ETG_DATABASE_ID

    def query_notion_pages(self, filter_text: str, database_id: str) -> List[Dict]:
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
        if not search_term:
            showInfo("Please enter a search term")
            return

        database_id = self.get_selected_database_id()
        print(f"Using database ID: {database_id}")

        # Force sync if cache is empty
        def search_callback():
            # Clear existing checkboxes
            for i in reversed(range(self.checkbox_layout.count())):
                self.checkbox_layout.itemAt(i).widget().setParent(None)

            self.pages_data = self.query_notion_pages(search_term, database_id)

            # Create checkboxes for results
            for page in self.pages_data:
                try:
                    title = page['properties']['Name']['title'][0]['text']['content'] if page['properties']['Name']['title'] else "Untitled"
                    search_suffix = page['properties']['Search Suffix']['formula']['string'] if page['properties'].get('Search Suffix', {}).get('formula', {}).get('string') else ""

                    display_text = f"{title} {search_suffix}"
                    checkbox = QCheckBox(display_text)
                    self.checkbox_layout.addWidget(checkbox)
                except Exception as e:
                    showInfo(f"Error processing page: {e}")

        # Check if cache needs updating
        if self.notion_cache.is_cache_expired(database_id):
            self.notion_cache.update_cache_async(database_id, force=True, callback=search_callback)
        else:
            search_callback()

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

        if property_name == 'Tag' or property_name == 'Main Tag':
            subtag = ""
        else:
            subtag = f"::*{property_name}".replace(' ', '_')

        def escape_underscores(tag):
            return tag.replace('_', '\\_')

        # Format tags for Anki search
        search_query = " or ".join(f"\"tag:{escape_underscores(tag)}{subtag}\"" for tag in individual_tags)

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
        selected_pages = []
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            if checkbox.isChecked():
                selected_pages.append(self.pages_data[i])

        property_name = self.property_selector.currentText()

        # Special handling for Subjects database when Tag is selected
        if self.database_selector.currentText() == "Subjects" and property_name == "Tag":
            # Use Main Tag instead of Tag
            property_name = "Main Tag"

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
            # Determine which property to use for tags
            if property_name == "Tag" or property_name == "Main Tag":
                tag_prop = page['properties'].get(property_name)
            else:
                # Try to use the selected subtag property
                tag_prop = page['properties'].get(property_name)

                # If subtag is empty, fall back to 'Tag'
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

        # Rest of the method remains the same...

        if not selected_pages:
            tags = ["#Malleus_CM::#TO_BE_TAGGED"]

        # Rest of the method remains the same...

        # Prepare note data
        note = {
            'deckName': config['deck_name'],
            'modelName': 'MalleusCM - Cloze (Malleus Clinical Medicine / Stapedius)',
            'fields': {},
            'tags': tags
        }

        # Add source field for eTG database
        if self.database_selector.currentText() == "eTG":
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
        self.accept()

    def guiAddCards(self, note):
        collection = mw.col

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

            self.accept()
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

def show_page_selector(browser=None):
    """Show the page selector dialog with the appropriate parent window"""
    # Ensure we have a proper QWidget parent
    if browser is None or not isinstance(browser, QWidget):
        parent = mw
    else:
        parent = browser
    dialog = NotionPageSelector(parent)
    dialog.exec_()

malleus_add_card_action = QAction("Malleus Find/Add Cards", mw)
malleus_add_card_action.triggered.connect(show_page_selector)
mw.form.menuTools.addAction(malleus_add_card_action)

def setup_editor_buttons(buttons, editor):
    """Add Malleus button to the editor toolbar"""
    button = editor.addButton(
        icon=None,  # You can add an icon file path here if you have one
        cmd="malleus",
        func=lambda e: show_page_selector(editor.parentWindow),
        tip="Find/Add Malleus Tags",
        label="Add Malleus Tags"
    )
    buttons.append(button)
    return buttons

def show_page_selector_from_editor(editor):
    """Show the page selector dialog from the editor context"""
    # Get the AddCards window that contains this editor
    addCards = editor.parentWindow
    if isinstance(addCards, QWidget):  # Ensure parent is a QWidget
        dialog = NotionPageSelector(addCards)
        dialog.exec_()

# Add the hook for editor buttons
addHook("setupEditorButtons", setup_editor_buttons)

def setup_browser_menu(browser):
    # Get or create Malleus menu
    def getMenu(parent, menu_name):
        menubar = parent.form.menubar
        for action in menubar.actions():
            if menu_name == action.text():
                return action.menu()
        return menubar.addMenu(menu_name)

    notion_menu = getMenu(browser, "&Malleus")

    # Add action for showing page selector
    page_selector_action = QAction(browser)
    page_selector_action.setText("Find/Create Malleus Cards")
    notion_menu.addAction(page_selector_action)
    page_selector_action.triggered.connect(lambda _, b=browser: show_page_selector(b))

    # Add action for updating Notion cache
    update_cache_action = QAction(browser)
    update_cache_action.setText("Update Malleus Database Cache")
    notion_menu.addAction(update_cache_action)
    update_cache_action.triggered.connect(lambda _, b=browser: update_notion_cache(b))

    # Add to browser toolbar
    try:
        from aqt.qt import QToolBar
        toolbar = browser.findChild(QToolBar)
        if toolbar:
            page_selector_button = QAction(browser)
            page_selector_button.setText("Malleus")
            page_selector_button.setToolTip("Find/Create Malleus Cards")
            page_selector_button.triggered.connect(lambda _, b=browser: show_page_selector(b))
            toolbar.addAction(page_selector_button)
    except:
        pass

def update_notion_cache(browser=None):
    """Update the Notion database cache"""
    notion_cache = NotionCache(addon_dir)
    if SUBJECT_DATABASE_ID:
        notion_cache.update_cache_async(SUBJECT_DATABASE_ID, force=True)
    if PHARMACOLOGY_DATABASE_ID:
        notion_cache.update_cache_async(PHARMACOLOGY_DATABASE_ID, force=True)
    if ETG_DATABASE_ID:
        notion_cache.update_cache_async(ETG_DATABASE_ID, force=True)

# Add hook for browser setup
from aqt.gui_hooks import browser_menus_did_init
browser_menus_did_init.append(setup_browser_menu)

# Initialize cache on addon load
def init_notion_cache():
    """Initialize the cache on startup"""
    try:
        cache = NotionCache(addon_dir)
        # Initialize without forcing sync
        if SUBJECT_DATABASE_ID:
            cache.update_cache_async(SUBJECT_DATABASE_ID, force=False)
        if PHARMACOLOGY_DATABASE_ID:
            cache.update_cache_async(PHARMACOLOGY_DATABASE_ID, force=False)
        if ETG_DATABASE_ID:
            cache.update_cache_async(ETG_DATABASE_ID, force=False)
    except Exception as e:
        showInfo(f"Error initializing cache: {e}")

# Add to addon initialization
# mw.addonManager.setConfigAction(__name__, init_notion_cache)
