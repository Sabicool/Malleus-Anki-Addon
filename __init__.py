from aqt import mw
from aqt.qt import *
from aqt.utils import showInfo, tooltip
import os
import requests
from aqt import dialogs
from aqt.browser import Browser
from aqt.addcards import AddCards
from aqt.editcurrent import EditCurrent
from aqt.editor import Editor
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
from aqt.qt import QKeySequence, QShortcut
import weakref
from aqt.qt import Qt

# Load environment variables
addon_dir = os.path.dirname(os.path.realpath(__file__))

# Hard coded environment variables because was causing issues
NOTION_TOKEN = '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'
SUBJECT_DATABASE_ID = '2674b67cbdf84a11a057a29cc24c524f'
PHARMACOLOGY_DATABASE_ID = '9ff96451736d43909d49e3b9d60971f8'
ETG_DATABASE_ID = '22282971487f4f559dce199476709b03'
ROTATION_DATABASE_ID = '69b3e7fdce1548438b26849466d7c18e'
TEXTBOOKS_DATABASE_ID = '13d5964e68a480bfb07cf7e2f1786075'

config = mw.addonManager.getConfig(__name__)

class NotionCache:
    """Handles caching of Notion database content"""
    CACHE_VERSION = 1
    # CACHE_EXPIRY = config['cache_expiry'] * 24 * 60 * 60  # 24 hours in seconds

    def __init__(self, addon_dir: str):
        self.cache_dir = Path(addon_dir) / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_lock = threading.Lock()
        self._sync_thread = None
        self.headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        #self.config_manager = ConfigManager()
        self.CACHE_EXPIRY = config['cache_expiry'] * 24 * 60 * 60 + 1 * 60 * 60 # Added 1 hour to allow time for github bot
        self.github_repo = "Sabicool/Malleus-Anki-Addon"  # Replace with your GitHub repo
        self.github_branch = "main"  # Or whatever branch you use

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
                #return [], current_time  # Return current time instead of 0
                tooltip("Newer database version available. Restart anki or click update database to update database")

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
        database_name = self.get_database_name(database_id)

        if not force and not self.is_cache_expired(database_id):
            if callback:
                callback()
            return

        if force:
            # If force=True, directly update the cache without downloading from GitHub
            self._update_cache_thread(database_id, database_name, callback)
        else:
            # If cache is expired but not forced, download from GitHub
            def download_thread():
                try:
                    success = self.download_all_caches_from_github()
                    if not success:
                        # If GitHub download fails, log error but don't proceed with update
                        print(f"Error downloading cache from GitHub")
                    if callback:
                        mw.taskman.run_on_main(callback)
                except Exception as e:
                    print(f"Error during GitHub cache download: {e}")
                    if callback:
                        mw.taskman.run_on_main(callback)

            self._sync_thread = threading.Thread(target=download_thread)
            self._sync_thread.start()

    def get_database_name(self, database_id: str) -> str:
        """Helper method to get database name based on ID"""
        if database_id == SUBJECT_DATABASE_ID:
            return "Subjects"
        elif database_id == PHARMACOLOGY_DATABASE_ID:
            return "Pharmacology"
        elif database_id == ETG_DATABASE_ID:
            return "eTG"
        elif database_id == ROTATION_DATABASE_ID:
            return "Rotation"
        elif database_id == TEXTBOOKS_DATABASE_ID:
            return "Textbooks"
        return "Unknown Database"

    def _update_cache_thread(self, database_id: str, database_name: str, callback: callable = None):
        """Internal method to update cache in a thread"""
        def sync_thread():
            try:
                #mw.taskman.run_on_main(lambda: tooltip(f"{database_name} database updated"))
                cached_pages, last_sync_timestamp = self.load_from_cache(database_id)
                pages = self.fetch_updated_pages(database_id, last_sync_timestamp)
                self.save_to_cache(database_id, pages)

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
        """Filter pages based on search term using fuzzy matching with precise word boundaries"""
        from difflib import SequenceMatcher
        import re
        from functools import lru_cache

        # Medical term variations stored as a constant to avoid rebuilding
        MEDICAL_VARIATIONS = {
            'paed': {'paediatric', 'paediatrics', 'pediatric', 'pediatrics'},
            'paediatric': {'paed', 'paediatrics', 'pediatric', 'pediatrics'},
            'paediatrics': {'paed', 'paediatric', 'pediatric', 'pediatrics'},
            'emergency': {'emergencies'},
            'emergencies': {'emergency'},
            'cardio': {'cardiac', 'cardiovascular'},
            'cardiac': {'cardio', 'cardiovascular'},
            'cardiology': {'cardio', 'cardiac', 'cardiovascular'},
            'gastro': {'gastrointestinal', 'gastroenterology'},
            'neuro': {'neurological', 'neurology'},
            'pulm': {'pulmonary', 'respiratory'},
            'resp': {'respiratory', 'pulmonary'},
            'gyn': {'gynecology', 'gynaecology'},
            'obs': {'obstetrics', 'obstetrical', 'gynecology', 'gynaecology'},
            'obgyn': {'obstetrics', 'obstetrical'},
            'psych': {'psychiatry'},
            'surg': {'surgical', 'surgery'}
        }

        @lru_cache(maxsize=1000)
        def get_word_variations(word: str) -> frozenset:
            """Get common variations of medical terms with caching"""
            variations = {word}
            word_lower = word.lower()

            # Add variations from mappings
            for key, values in MEDICAL_VARIATIONS.items():
                if word_lower == key:
                    variations.update(values)
                elif word_lower in values:
                    variations.add(key)
                    variations.update(values)

            # Handle plural forms
            if word.endswith('y'):
                variations.add(word[:-1] + 'ies')
            elif word.endswith('ies'):
                variations.add(word[:-3] + 'y')
            elif word.endswith('s') and not word.endswith('ss'):
                variations.add(word[:-1])
            else:
                variations.add(word + 's')

            return frozenset(variations)  # Immutable for caching

        @lru_cache(maxsize=1000)
        def normalize_word(word: str) -> frozenset:
            """Normalize a single word and get its variations"""
            if len(word) <= 2:
                return frozenset({word})
            return get_word_variations(word)

        def normalize_text(text: str) -> set:
            """Normalize text and return set of variations"""
            # Clean and split text
            words = re.sub(r'[^\w\s]', ' ', text.lower()).split()

            # Get variations for each word and union them
            normalized = set()
            for word in words:
                normalized.update(normalize_word(word))
            return normalized

        @lru_cache(maxsize=1000)
        def is_partial_match(search_word: str, target_word: str) -> bool:
            """Check if search_word is a partial match of target_word"""
            if search_word == target_word:
                return True

            search_variations = normalize_word(search_word)
            target_variations = normalize_word(target_word)

            # Check for direct matches first
            if not search_variations.isdisjoint(target_variations):
                return True

            # Only do expensive operations for longer terms
            if len(search_word) > 5 and len(target_word) > 5:
                if any(s in target_word for s in search_variations):
                    return True

                # Use sequence matcher as last resort
                similarity = SequenceMatcher(None, search_word, target_word).ratio()
                if similarity > 0.85:
                    return True

            return False

        def calculate_word_match_score(search_words: set, target_words: set) -> float:
            """Calculate how well the search words match the target words"""
            if not search_words:
                return 0.0

            matches = sum(1 for sword in search_words
                         if any(is_partial_match(sword, tword) for tword in target_words))

            return matches / len(search_words) if matches == len(search_words) else 0.0

        # Pre-process search term once
        search_words = normalize_text(search_term)

        # Pre-process and cache all page terms
        page_terms_cache = {}
        for page in pages:
            if not page.get('properties'):
                continue

            search_term_prop = page['properties'].get('Search Term', {})
            if not search_term_prop or search_term_prop.get('type') != 'formula':
                continue

            page_search_term = search_term_prop.get('formula', {}).get('string', '')
            if page_search_term:
                page_terms_cache[id(page)] = normalize_text(page_search_term)

        # Filter pages
        filtered_pages = []
        similarity_threshold = 0.5

        for page in pages:
            page_terms = page_terms_cache.get(id(page))
            if not page_terms:
                continue

            # Calculate word match score
            word_match_score = calculate_word_match_score(search_words, page_terms)

            if word_match_score > 0:
                # Only calculate sequence similarity if words match
                sequence_similarity = SequenceMatcher(
                    None,
                    ' '.join(search_words),
                    ' '.join(page_terms)
                ).ratio()

                similarity = (word_match_score * 0.9) + (sequence_similarity * 0.1)

                if similarity >= similarity_threshold:
                    page['_similarity'] = similarity
                    filtered_pages.append(page)

        filtered_pages.sort(key=lambda x: x.get('_similarity', 0), reverse=True)
        return filtered_pages

    def download_cache_from_github(self, database_id: str) -> bool:
        """Download cache file from GitHub"""
        cache_filename = f"{database_id}.json"
        url = f"https://raw.githubusercontent.com/{self.github_repo}/{self.github_branch}/cache/{cache_filename}"

        try:
            response = requests.get(url)
            response.raise_for_status()

            # Parse the JSON to validate it
            cache_data = response.json()

            # Save to cache with lock to prevent conflicts
            with self.cache_lock:
                cache_path = self.get_cache_path(database_id)
                with cache_path.open('w', encoding='utf-8') as f:
                    json.dump(cache_data, f)

            return True
        except Exception as e:
            print(f"Error downloading cache from GitHub: {e}")
            return False

    def download_all_caches_from_github(self) -> bool:
        """Download all cache files from GitHub"""
        success = True
        for database_id in [
            '2674b67cbdf84a11a057a29cc24c524f',  # SUBJECT_DATABASE_ID
            '9ff96451736d43909d49e3b9d60971f8',  # PHARMACOLOGY_DATABASE_ID
            '22282971487f4f559dce199476709b03',  # ETG_DATABASE_ID
            '69b3e7fdce1548438b26849466d7c18e',  # ROTATION_DATABASE_ID
            '13d5964e68a480bfb07cf7e2f1786075'
        ]:
            if not self.download_cache_from_github(database_id):
                success = False

        return success

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

        # Initialize current_note first
        self.current_note = None
        if isinstance(parent, Browser) and hasattr(parent.editor, 'note'):
            self.current_note = parent.editor.note
        elif isinstance(parent, EditCurrent) and hasattr(parent.editor, 'note'):
            self.current_note = parent.editor.note
        elif isinstance(parent, AddCards) and hasattr(parent.editor, 'note'):
            self.current_note = parent.editor.note

        self.notion_cache = NotionCache(addon_dir)
        # Initialize cache on startup without forcing
        # if SUBJECT_DATABASE_ID:
        #     self.notion_cache.update_cache_async(SUBJECT_DATABASE_ID, force=False)
        # if PHARMACOLOGY_DATABASE_ID:
        #     self.notion_cache.update_cache_async(PHARMACOLOGY_DATABASE_ID, force=False)
        # if ETG_DATABASE_ID:
        #     self.notion_cache.update_cache_async(ETG_DATABASE_ID, force=False)
        # if ROTATION_DATABASE_ID:
        #     self.notion_cache.update_cache_async(ROTATION_DATABASE_ID, force=False)

        self.database_properties = {
            "Subjects": [
                "",
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
                "",
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
                "",
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
            ],
            "Rotation": [
                ""
            ],
            "Textbooks": [
                ""
            ]
        }
        self.pages_data = []  # Store full page data
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Malleus Page Selector")
        self.setMinimumWidth(600)

        layout = QVBoxLayout()

        # Search section
        search_layout = QHBoxLayout()

        # Database selector
        self.database_selector = QComboBox()
        self.database_selector.addItems(["Subjects", "Pharmacology", "eTG", "Rotation", "Textbooks"])
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
        if not config['autosearch']:
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
        button_layout.addWidget(select_all_button)

        find_cards_button = QPushButton("Find Cards")
        find_cards_button.clicked.connect(self.search_cards)
        button_layout.addWidget(find_cards_button)

        if isinstance(self.parent(), AddCards):
            create_cards_button = QPushButton("Add Tags")
        else:
            create_cards_button = QPushButton("Create Cards")

        create_cards_button.clicked.connect(self.create_cards)
        button_layout.addWidget(create_cards_button)

        # Only show these buttons when editing an existing note

        if self.current_note is not None or isinstance(self.parent(), AddCards):
            replace_tags_button = QPushButton("Replace Tags")
            replace_tags_button.clicked.connect(self.replace_tags)
            button_layout.addWidget(replace_tags_button)

        if self.current_note is not None:
            add_tags_button = QPushButton("Add Tags")
            add_tags_button.clicked.connect(self.add_tags)
            button_layout.addWidget(add_tags_button)

        update_database_button = QPushButton("Update database")
        update_database_button.clicked.connect(download_github_cache)
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
        elif self.database_selector.currentText() == "eTG":
            return ETG_DATABASE_ID
        elif self.database_selector.currentText() == "Rotation":
            return ROTATION_DATABASE_ID
        else:
            return TEXTBOOKS_DATABASE_ID

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

        if not self.pages_data and not config['autosearch']:
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
        if config['autosearch']:
            if len(text) >= 2:
                # Wait 300ms before performing search
                self.search_timer.start(config['search_delay'])
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

        # Rest of the method remains the same...

        # Prepare note data
        note = {
            'deckName': config['deck_name'],
            'modelName': 'MalleusCM - Cloze (Malleus Clinical Medicine [AU/NZ] / Stapedius)',
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

        if self.database_selector.currentText() == "Textbooks":
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

        # Special handling for Subjects database when Tag is selected
        if self.database_selector.currentText() == "Subjects" and property_name == "Tag":
            property_name = "Main Tag"

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

    def add_tags(self):
        """Add new tags to existing ones"""
        # Get the latest note reference
        note = None
        parent = self.parent()

        if isinstance(parent, Browser):
            note = parent.editor.note
        elif isinstance(parent, EditCurrent):
            note = parent.editor.note
        else:
            note = self.current_note

        if not note:
            showInfo("No note found in current context")
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

        # Get current tags
        current_tags = set(note.tags)

        # Get new tags
        new_tags = set(self.get_tags_from_selected_pages())

        # Combine tags
        combined_tags = list(current_tags | new_tags)

        # Update the note's tags
        note.tags = combined_tags

        # Save the note
        note.flush()

        # Refresh the editor
        if isinstance(parent, Browser):
            parent.model.reset()
        elif isinstance(parent, EditCurrent):
            parent.editor.loadNote()

    def replace_tags(self):
        """Replace existing tags with new ones"""
        # Get the appropriate note reference based on context
        note = None
        parent = self.parent()

        if isinstance(parent, Browser):
            note = parent.editor.note
        elif isinstance(parent, EditCurrent):
            note = parent.editor.note
        elif isinstance(parent, AddCards):
            note = parent.editor.note
        else:
            note = self.current_note

        if not note:
            showInfo("No note found in current context")
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

        # Get new tags from selected pages
        new_tags = self.get_tags_from_selected_pages()

        # Update the note's tags
        note.tags = new_tags

        # Save and refresh based on context
        if isinstance(parent, AddCards):
            # For AddCards dialog
            parent.editor.loadNote()
            parent.editor.setNote(note)
            parent.editor.loadNote()
            mw.requireReset()
        else:
            # For Browser/EditCurrent contexts
            note.flush()
            if isinstance(parent, Browser):
                parent.model.reset()
            elif isinstance(parent, EditCurrent):
                parent.editor.loadNote()

            #self.accept()

def show_page_selector(parent=None):
    """Show the page selector dialog with the appropriate parent window"""
    # Ensure we have a proper QWidget parent
    if parent is None or not isinstance(parent, QWidget):
        parent = mw

    # Use a dictionary to track dialogs per parent
    if not hasattr(parent, '_malleus_dialogs'):
        parent._malleus_dialogs = []

    # Clean up any deleted dialogs
    parent._malleus_dialogs = [d for d in parent._malleus_dialogs if not sip.isdeleted(d)]

    # Create new dialog with proper parent
    dialog = NotionPageSelector(parent)

    # Set up browser note selection change handler
    if isinstance(parent, Browser):
        # Store original currentRowChanged handler
        original_handler = parent.onRowChanged

        # Create a wrapper function that updates the dialog
        def row_changed_wrapper(current, previous):
            # Call the original handler first
            original_handler(current, previous)

            # Update the dialog's current_note reference
            if dialog and not sip.isdeleted(dialog):
                if hasattr(parent, 'editor') and hasattr(parent.editor, 'note'):
                    dialog.current_note = parent.editor.note

        # Replace the browser's row change handler
        parent.onRowChanged = row_changed_wrapper

        # Restore original handler when dialog closes
        def on_dialog_finished():
            if hasattr(parent, 'onRowChanged') and parent.onRowChanged == row_changed_wrapper:
                parent.onRowChanged = original_handler

        dialog.finished.connect(on_dialog_finished)

    parent._malleus_dialogs.append(dialog)
    dialog.show()
    return dialog

malleus_add_card_action = QAction("Malleus Find/Add Cards", mw)
malleus_add_card_action.triggered.connect(show_page_selector)
mw.form.menuTools.addAction(malleus_add_card_action)

def download_github_cache(browser=None):
    """Download cache from GitHub repository and update cache from Notion"""
    notion_cache = NotionCache(addon_dir)
    current_notion_update = 0

    # Create progress dialog on main thread
    def create_progress():
        nonlocal progress
        progress = QProgressDialog("Initializing...", None, 0, 10, mw)
        progress.setWindowTitle("Cache Update")
        progress.show()

    progress = None
    mw.taskman.run_on_main(create_progress)

    def update_progress(step, message):
        """Update progress dialog with step number and message"""
        def update():
            if progress is None:
                return
            progress.setValue(step)
            progress.setLabelText(message)
        mw.taskman.run_on_main(update)

    def process_next_notion_update():
        """Process the next Notion database update"""
        nonlocal current_notion_update

        databases = [
            (SUBJECT_DATABASE_ID, "Subjects database"),
            (PHARMACOLOGY_DATABASE_ID, "Pharmacology database"),
            (ETG_DATABASE_ID, "eTG database"),
            (ROTATION_DATABASE_ID, "Rotation database"),
            (TEXTBOOKS_DATABASE_ID, "Textbooks database")
        ]

        if current_notion_update < len(databases):
            db_id, name = databases[current_notion_update]
            if db_id:
                update_progress(current_notion_update + 5, f"Updating new {name} pages from Notion...")
                notion_cache.update_cache_async(db_id, force=True, callback=on_notion_update_complete)
            else:
                on_notion_update_complete()
        else:
            def complete():
                if progress is None:
                    return
                progress.setValue(10)
                progress.close()
                tooltip("Cache successfully downloaded and updated")
            mw.taskman.run_on_main(complete)

    def on_notion_update_complete():
        """Handle completion of a Notion update"""
        nonlocal current_notion_update
        current_notion_update += 1
        process_next_notion_update()

    def update_all_databases():
        """Start the chain of Notion database updates"""
        process_next_notion_update()

    def on_error():
        def error():
            if progress is None:
                return
            progress.close()
            tooltip("Error downloading cache from GitHub. Check the console for details.")
        mw.taskman.run_on_main(error)

    def download_thread():
        # GitHub downloads (steps 0-3)
        for idx, (name, database_id) in enumerate([
            ("Subjects", SUBJECT_DATABASE_ID),
            ("Pharmacology", PHARMACOLOGY_DATABASE_ID),
            ("eTG", ETG_DATABASE_ID),
            ("Rotation", ROTATION_DATABASE_ID),
            ("Textbooks", TEXTBOOKS_DATABASE_ID)
        ]):
            update_progress(idx, f"Downloading {name} database from GitHub...")
            success = notion_cache.download_cache_from_github(database_id)
            if not success:
                on_error()
                return
            time.sleep(0.5)  # Small delay to make progress visible

        # Start Notion updates (steps 4-7)
        update_all_databases()

    thread = threading.Thread(target=download_thread)
    thread.start()

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
    update_cache_action.triggered.connect(lambda _, b=browser: download_github_cache(b))

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

# Add hook for browser setup
from aqt.gui_hooks import browser_menus_did_init
browser_menus_did_init.append(setup_browser_menu)

# Initialize cache on addon load
def init_notion_cache():
    """Initialize the cache check asynchronously on startup"""
    global config
    if 'shortcut' not in config:
        config['shortcut'] = 'Ctrl+Alt+M'
        mw.addonManager.writeConfig(__name__, config)
    def check_caches():
        try:
            print("Starting background cache check...")
            cache = NotionCache(addon_dir)
            databases = [
                (SUBJECT_DATABASE_ID, "Subjects"),
                (PHARMACOLOGY_DATABASE_ID, "Pharmacology"),
                (ETG_DATABASE_ID, "eTG"),
                (ROTATION_DATABASE_ID, "Rotation"),
                (TEXTBOOKS_DATABASE_ID, "Textbooks")
            ]

            for db_id, name in databases:
                if not db_id:
                    print(f"Skipping {name} - no database ID")
                    continue

                print(f"Checking {name} cache status...")
                if cache.is_cache_expired(db_id):
                    print(f"{name} cache is expired, attempting GitHub download...")
                    if cache.download_cache_from_github(db_id):
                        print(f"Successfully updated {name} cache from GitHub")
                        continue
                    print(f"GitHub download failed for {name}, falling back to Notion update...")
                    cache.update_cache_async(db_id, force=True)
                else:
                    print(f"{name} cache is up to date")

            print("Background cache check completed")

        except Exception as e:
            print(f"Error in background cache check: {e}")

    thread = threading.Thread(target=check_caches, daemon=True)
    thread.start()

# mw.addonManager.setConfigAction(__name__, init_notion_cache)
# Function to register the shortcut in a window
def register_shortcut(window):
    shortcut_key = config.get('shortcut', 'Ctrl+Alt+M')
    shortcut = QShortcut(QKeySequence(shortcut_key), window)

    # Handle Qt version differences for shortcut context
    if hasattr(Qt, 'ShortcutContext'):
        # Qt6 style
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
    else:
        # Qt5 style
        shortcut.setContext(Qt.WindowShortcut)

    weak_window = weakref.ref(window)

    def trigger():
        target_window = weak_window()
        if target_window and not sip.isdeleted(target_window):
            show_page_selector(target_window)

    shortcut.activated.connect(trigger)

    if not hasattr(window, '_malleus_shortcuts'):
        window._malleus_shortcuts = []
    window._malleus_shortcuts.append(shortcut)

# Register shortcuts for all major window types
def register_shortcuts():
    """Register shortcut for main window - others via hooks"""
    register_shortcut(mw)

# Hooks to register shortcut when windows are created
def on_browser_setup(browser):
    register_shortcut(browser)

def on_addcards_setup(add_cards_dialog):
    register_shortcut(add_cards_dialog)

def on_editor_did_load_note(editor):
    # Check if we're in an EditCurrent window
    from aqt.editcurrent import EditCurrent
    if isinstance(editor.parentWindow, EditCurrent):
        register_shortcut(editor.parentWindow)

# Register all hooks
from aqt.gui_hooks import browser_will_show, add_cards_did_init, editor_did_load_note

browser_will_show.append(on_browser_setup)
add_cards_did_init.append(on_addcards_setup)
editor_did_load_note.append(on_editor_did_load_note)

# Initial registration for existing windows
register_shortcuts()

# Initialize cache when addon is loaded
init_notion_cache()
