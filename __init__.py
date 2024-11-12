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
from typing import List, Dict, Optional
import json
import time
from pathlib import Path
import threading

# Load environment variables
addon_dir = os.path.dirname(os.path.realpath(__file__))
env_path = os.path.join(addon_dir, '.env')
load_dotenv(env_path)

NOTION_TOKEN = os.getenv('NOTION_TOKEN')
SUBJECT_DATABASE_ID = os.getenv('DATABASE_ID')  
PHARMACOLOGY_DATABASE_ID = os.getenv('PHARMACOLOGY_DATABASE_ID') 

class NotionSyncProgress(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Syncing Notion Database")
        self.setWindowModality(Qt.WindowModal)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()
        self.status_label = QLabel("Downloading database contents...")
        self.progress_label = QLabel("This may take a few minutes")

        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_label)

        self.setLayout(layout)
        self.resize(300, 100)

class NotionCache:
    """Handles caching of Notion database content"""
    CACHE_VERSION = 1
    CACHE_EXPIRY = 24 * 60 * 60  # 24 hours in seconds

    def __init__(self, addon_dir: str):
        self.cache_dir = Path(addon_dir) / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_lock = threading.Lock()
        self.sync_progress = None
        self.sync_timer = None
        self._sync_thread = None
        self.headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }

    def confirm_sync(self) -> bool:
        """Ask user for confirmation before syncing"""
        # Use QMessageBox directly for confirmation
        msg = QMessageBox(mw)
        msg.setWindowTitle("Sync Confirmation")
        msg.setText("Would you like to sync the Notion database?")
        msg.setInformativeText("This may take a few minutes.")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.Yes)
        return msg.exec_() == QMessageBox.Yes

    def get_cache_path(self, database_id: str) -> Path:
        """Get the path for a specific database's cache file"""
        return self.cache_dir / f"{database_id}.json"

    def save_to_cache(self, database_id: str, pages: List[Dict]):
        """Save pages to cache file"""
        cache_path = self.get_cache_path(database_id)
        cache_data = {
            'version': self.CACHE_VERSION,
            'timestamp': time.time(),
            'pages': pages
        }

        with self.cache_lock:
            with cache_path.open('w', encoding='utf-8') as f:
                json.dump(cache_data, f)

    def load_from_cache(self, database_id: str) -> Optional[List[Dict]]:
        """Load cached data if it exists and is not expired"""
        cache_path = self.get_cache_path(database_id)
        if not cache_path.exists():
            return None

        try:
            with cache_path.open('r', encoding='utf-8') as f:
                cache_data = json.load(f)

            # Check cache version and expiry
            if (cache_data.get('version') != self.CACHE_VERSION or
                time.time() - cache_data.get('timestamp', 0) > self.CACHE_EXPIRY):
                return None

            return cache_data.get('pages', [])
        except Exception as e:
            mw.taskman.run_on_main(lambda: showInfo(f"Error loading cache: {e}"))
            return None

    def show_sync_progress(self):
        """Show sync progress dialog"""
        # Create and show progress dialog in the main thread
        def create_dialog():
            self.sync_progress = NotionSyncProgress(mw)
            self.sync_progress.show()

            # Create timer to check sync status
            self.sync_timer = QTimer()
            self.sync_timer.timeout.connect(self.check_sync_status)
            self.sync_timer.start(500)  # Check every 500ms

        mw.taskman.run_on_main(create_dialog)

    def check_sync_status(self):
        """Check if sync thread is still running"""
        if not self._sync_thread or not self._sync_thread.is_alive():
            if self.sync_progress:
                self.sync_progress.close()
            if self.sync_timer:
                self.sync_timer.stop()
            self.sync_progress = None
            self.sync_timer = None
            mw.taskman.run_on_main(lambda: tooltip("Sync completed"))

    def update_cache_async(self, database_id: str, force: bool = False):
        """Update cache in background with progress indicator"""
        # Check if sync is already running
        if self._sync_thread and self._sync_thread.is_alive():
            mw.taskman.run_on_main(lambda: tooltip("Sync already in progress"))
            return

        # Check if cache exists and is not expired
        cache_path = self.get_cache_path(database_id)
        if not force and cache_path.exists():
            try:
                with cache_path.open('r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                if time.time() - cache_data.get('timestamp', 0) <= self.CACHE_EXPIRY:
                    return  # Cache is still valid
            except Exception:
                pass  # If there's any error reading cache, proceed with sync

        # Get user confirmation in the main thread
        if not force:
            confirmed = [False]  # Use list to modify in inner function
            def ask_confirmation():
                confirmed[0] = self.confirm_sync()
            mw.taskman.run_on_main(ask_confirmation)
            if not confirmed[0]:
                return

        def sync_task():
            try:
                pages = self.fetch_all_pages(database_id)
                if pages:
                    self.save_to_cache(database_id, pages)
            except Exception as e:
                mw.taskman.run_on_main(lambda: showInfo(f"Error during sync: {e}"))

        self._sync_thread = threading.Thread(target=sync_task)
        self._sync_thread.start()
        self.show_sync_progress()

    def fetch_all_pages(self, database_id: str) -> List[Dict]:
        """Fetch all pages from a Notion database"""
        pages = []
        has_more = True
        start_cursor = None

        while has_more:
            payload = {
                "filter": {
                    "property": "For Search",
                    "formula": {
                        "checkbox": {
                            "equals": True
                        }
                    }
                },
                "page_size": 100
            }

            if start_cursor:
                payload["start_cursor"] = start_cursor

            try:
                response = requests.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()

                pages.extend(data['results'])
                has_more = data.get('has_more', False)
                start_cursor = data.get('next_cursor')

            except Exception as e:
                showInfo(f"Error fetching from Notion: {e}")
                break

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

class ConfigManager:
    """
    Handles configuration management for the Anki addon
    """
    DEFAULT_CONFIG = {
        "deck_name": "Malleus Clinical Medicine (AU/NZ)"
    }

    def __init__(self):
        # Get the addon directory path
        addon_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(addon_dir, "config.json")
        self.config = self.load_config()

    def load_config(self):
        """Load configuration from file or create default if not exists"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Merge with defaults to ensure all required fields exist
                    return {**self.DEFAULT_CONFIG, **config}
            else:
                self.save_config(self.DEFAULT_CONFIG)
                return self.DEFAULT_CONFIG
        except Exception as e:
            showInfo(f"Error loading config: {e}")
            return self.DEFAULT_CONFIG

    def save_config(self, config):
        """Save configuration to file"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            showInfo(f"Error saving config: {e}")

    def get_deck_name(self):
        """Get configured deck name"""
        return self.config.get("deck_name", self.DEFAULT_CONFIG["deck_name"])
    
class NotionPageSelector(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.notion_cache = NotionCache(addon_dir)
        # Initialize cache on startup without forcing
        if SUBJECT_DATABASE_ID:
            self.notion_cache.update_cache_async(SUBJECT_DATABASE_ID, force=False)
        if PHARMACOLOGY_DATABASE_ID:
            self.notion_cache.update_cache_async(PHARMACOLOGY_DATABASE_ID, force=False)
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
                "Screening/Prevention",
                "Main Tag"
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
        self.database_selector.addItems(["Subjects", "Pharmacology"])
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

        button_layout.addWidget(select_all_button)
        button_layout.addWidget(find_cards_button)
        button_layout.addWidget(create_cards_button)

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
        else:
            return PHARMACOLOGY_DATABASE_ID

    def query_notion_pages(self, filter_text: str, database_id: str) -> List[Dict]:
        """Query pages from cache and filter them"""
        try:
            # Try to get from cache first
            all_pages = self.notion_cache.load_from_cache(database_id)

            # If cache is empty or failed, try direct fetch
            if not all_pages:
                tooltip("Cache empty, fetching from notion...")
                all_pages = self.notion_cache.fetch_all_pages(database_id)
                if all_pages:
                    self.notion_cache.save_to_cache(database_id, all_pages)

            # Debug output
            # print(f"Total pages loaded: {len(all_pages)}")

            # Filter the pages
            filtered_pages = self.notion_cache.filter_pages(all_pages, filter_text)
            # print(f"Filtered pages found: {len(filtered_pages)}")

            return filtered_pages

        except Exception as e:
            # showInfo(f"Error in query_notion_pages: {str(e)}")
            showInfo(f"Error accessing data: {str(e)}")
            return []

    def perform_search(self):
        search_term = self.search_input.text()
        if not search_term:
            showInfo("Please enter a search term")
            return

        database_id = self.get_selected_database_id()

        # Force sync if cache is empty
        if not self.notion_cache.load_from_cache(database_id):
            self.notion_cache.update_cache_async(database_id, force=True)
            tooltip("Cache is being updated. Please try your search again in a moment.")
            return

        # Clear existing checkboxes
        for i in reversed(range(self.checkbox_layout.count())):
            self.checkbox_layout.itemAt(i).widget().setParent(None)

        self.pages_data = self.query_notion_pages(search_term, database_id)
        # print(f"Found {len(self.pages_data)} matching pages")

        # Create checkboxes for results
        for page in self.pages_data:
            try:
                title = page['properties']['Name']['title'][0]['text']['content'] if page['properties']['Name']['title'] else "Untitled"
                search_suffix = page['properties']['Search Suffix']['formula']['string'] if page['properties'].get('Search Suffix', {}).get('formula', {}).get('string') else ""
                # print(f"Adding result: {title} {search_suffix}")

                display_text = f"{title} {search_suffix}"
                checkbox = QCheckBox(display_text)
                self.checkbox_layout.addWidget(checkbox)
            except Exception as e:
                showInfo(f"Error processing page: {e}")

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
        
        # Format tags for Anki search
        search_query = " or ".join(f"tag:{tag}{subtag}" for tag in individual_tags)

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
        """Extract property content from page data"""
        prop = page['properties'].get(property_name)
        if prop and prop['type'] == 'rich_text' and prop['rich_text']:
            return prop['rich_text'][0]['text']['content']
        return ""

    def create_cards(self):
        config_manager=ConfigManager()
        config = mw.addonManager.getConfig(__name__)
        selected_pages = []
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            if checkbox.isChecked():
                selected_pages.append(self.pages_data[i])

        property_name = self.property_selector.currentText()

        # Get tags from pages
        tags = []
        for page in selected_pages:
            tag_prop = page['properties'].get(property_name)
            if tag_prop and tag_prop['type'] == 'formula':
                formula_value = tag_prop['formula']
                if formula_value['type'] == 'string':
                    tags.extend(formula_value['string'].split())

        if not selected_pages:
            tags = ["#Malleus_CM::#TO_BE_TAGGED"]
            # return

        # Create note data
        note = {
            'deckName': config['deck_name'],  # Make this configurable
            'modelName': 'MalleusCM - Cloze (Malleus Clinical Medicine / Stapedius)',  # Make this configurable
            # Can consider adding something like this, or have the source field populate
            # 'fields': {
            #     'Front': property_name,
            #     'Back': '\n\n'.join(self.get_property_content(page, property_name)
            #                       for page in selected_pages)
            # },
            'tags': tags
        }

        # Open add cards dialog
        self.guiAddCards(note)
        self.accept()

    def guiAddCards(self, note):
        collection = mw.col

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

            dialogs.open('AddCards', mw)
            addCards.setAndFocusNote(addCards.editor.note)

        currentWindow = dialogs._dialogs['AddCards'][1]

        if currentWindow is not None:
            current
        else:
            openNewWindow()

def show_page_selector(browser=None):
    dialog = NotionPageSelector(browser if browser else mw)
    dialog.exec_()

malleus_add_card_action = QAction("Malleus Find/Add Cards", mw)
malleus_add_card_action.triggered.connect(show_page_selector)
mw.form.menuTools.addAction(malleus_add_card_action)

# def setup_editor_buttons(buttons, editor):
#     b = editor.addButton(
#         "",
#         "Add Malleus Tags",
#         show_page_selector,
#         tip="Find Malleus tags for an your card"
#         #os.path.join(addon_path, "icons", "hyperlink.png"),
#         #"hyperlinkbutton",
#         #toggle_hyperlink,
#         #tip="Insert Hyperlink ({})".format(
#         #    keystr(gc("shortcut_insert_link", ""))),
#         #keys=gc('shortcut_insert_link')
#     )
#     buttons.append(b)
# 
#     return buttons
# 
# addHook("setupEditorButtons", setup_editor_buttons)  # noqa

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

# Add hook for browser setup
from aqt.gui_hooks import browser_menus_did_init
browser_menus_did_init.append(setup_browser_menu)

malleus_add_card_action = QAction("Update Malleus Database Cache", mw)
malleus_add_card_action.triggered.connect(update_notion_cache)
mw.form.menuTools.addAction(malleus_add_card_action)

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
    except Exception as e:
        showInfo(f"Error initializing cache: {e}")

# Add to addon initialization
mw.addonManager.setConfigAction(__name__, init_notion_cache)
