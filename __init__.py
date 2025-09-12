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
from anki.hooks import addHook, wrap
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
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices

# Load environment variables
addon_dir = os.path.dirname(os.path.realpath(__file__))

# Hard coded environment variables because was causing issues
NOTION_TOKEN = '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'
SUBJECT_DATABASE_ID = '2674b67cbdf84a11a057a29cc24c524f'
PHARMACOLOGY_DATABASE_ID = '9ff96451736d43909d49e3b9d60971f8'
ETG_DATABASE_ID = '22282971487f4f559dce199476709b03'
ROTATION_DATABASE_ID = '69b3e7fdce1548438b26849466d7c18e'
TEXTBOOKS_DATABASE_ID = '13d5964e68a480bfb07cf7e2f1786075'
GUIDELINES_DATABASE_ID = '13d5964e68a48056b40de8148dd91a06'

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
        elif database_id == GUIDELINES_DATABASE_ID:
            return "Guidelines"
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
        """Filter pages based on search term using fuzzy matching with multi-tier sorting"""
        from difflib import SequenceMatcher
        import re
        from functools import lru_cache

        # Early return if search term is too short
        if len(search_term.replace(' ', '')) < 3:
            return []

        # Medical term variations stored as a constant to avoid rebuilding
        MEDICAL_VARIATIONS = {
            'paed': {'paediatric', 'paediatrics'},
            'paeds': {'paediatric', 'paediatrics'},
            'emergency': {'emergencies'},
            'emergencies': {'emergency'},
            'cardio': {'cardiac', 'cardiovascular'},
            'cardiac': {'cardiovascular'},
            'cardiology': {'cardio', 'cardiac', 'cardiovascular'},
            'gastro': {'gastrointestinal', 'gastroenterology'},
            'neuro': {'neurological', 'neurology'},
            'rheum': {'rheumatology', 'rheumatological'},
            'haem': {'haematology', 'haematological'},
            'onc': {'oncology', 'oncological'},
            'endo': {'endocrinology', 'endocrinological'},
            'pulm': {'pulmonary', 'respiratory'},
            'resp': {'respiratory', 'pulmonary'},
            'gyn': {'gynecology', 'gynaecology'},
            'gynae': {'gynecology', 'gynaecology'},
            'obs': {'obstetrics', 'obstetrical'},
            'obgyn': {'obstetrics', 'obstetrical'},
            'psych': {'psychiatry'},
            'surg': {'surgical', 'surgery'},
            'pall': {'palliative'},
            'uro': {'urological', 'urology'}
        }

        @lru_cache(maxsize=1000)
        def normalize_text(text: str) -> set:
            """Normalize text and return set of variations"""
            # Clean and split text
            words = re.sub(r'[^\w\s]', ' ', text.lower()).split()

            # Get variations for each word
            normalized = set()
            for word in words:
                # Always include original word, lowercase variants, and medical variations
                normalized.add(word)
                normalized.add(word.lower())

                # Add plurals and medical variations
                if word.endswith('y'):
                    normalized.add(word[:-1] + 'ies')
                elif word.endswith('s') and not word.endswith('ss'):
                    normalized.add(word[:-1])

                # Add medical term variations if applicable
                for key, variations in MEDICAL_VARIATIONS.items():
                    if word.lower() == key or word.lower() in variations:
                        normalized.update(variations)
                        normalized.add(key)

            return normalized

        def page_matches_all_terms(page_words: list, search_terms: set) -> bool:
            """Check if page matches ALL search terms with start-of-word matching"""
            for search_term in search_terms:
                # Flag to track if this term matches any word
                term_matched = False

                for page_word in page_words:
                    # Normalize both search term and page word
                    page_variations = normalize_text(page_word)

                    # Check if search term starts any normalized variation
                    if any(
                        var.startswith(search_term)
                        for var in page_variations
                    ):
                        term_matched = True
                        break

                # If no match found for this term, return False
                if not term_matched:
                    return False

            return True

        # Pre-process search term
        search_terms = search_term.lower().split()
        normalized_search_term = search_term.lower()

        # Filter pages
        filtered_pages = []
        for page in pages:
            # Skip pages without properties
            if not page.get('properties'):
                continue

            # Extract page search terms
            page_search_terms_prop = page['properties'].get('Search Term', {})
            if not page_search_terms_prop or page_search_terms_prop.get('type') != 'formula':
                continue

            page_search_term = page_search_terms_prop.get('formula', {}).get('string', '').lower()
            if not page_search_term:
                continue

            # Normalize page search terms
            page_terms = normalize_text(page_search_term)

            # Check if page matches ALL search terms
            if page_matches_all_terms(page_terms, search_terms):
                # Extract title for additional context
                title_prop = page['properties'].get('Name', {})
                title = title_prop['title'][0]['text']['content'] if title_prop.get('title') else ""
                title_lower = title.lower()

                # Calculate multiple similarity metrics
                # 1. Exact match score
                exact_match_score = 1.0 if normalized_search_term in page_search_term else 0.0

                # 2. Title match score
                title_match_score = 1.0 if normalized_search_term in title_lower else (
                    0.9 if any(term in title_lower for term in search_terms) else 0.0
                )

                # 3. Term frequency score
                term_freq_score = sum(
                    page_search_term.count(term) for term in search_terms
                ) / len(search_terms)

                # 4. Sequence similarity
                sequence_similarity = SequenceMatcher(
                    None,
                    normalized_search_term,
                    page_search_term
                ).ratio()

                # Composite ranking score
                # Prioritize exact matches, then title matches, then frequency
                composite_score = (
                    exact_match_score * 0.4 +
                    title_match_score * 0.3 +
                    term_freq_score * 0.2 +
                    sequence_similarity * 0.1
                )

                # Store scores for sorting
                page['_composite_score'] = composite_score
                page['_title'] = title_lower
                page['_exact_match'] = exact_match_score

                filtered_pages.append(page)

        # Sorting with more nuanced ranking
        filtered_pages.sort(
            key=lambda x: (
                -x.get('_exact_match', 0),      # Exact matches first
                -x.get('_composite_score', 0),  # Then by composite score
                x.get('_title', '')             # Then alphabetically
            )
        )

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
            '13d5964e68a480bfb07cf7e2f1786075',  # TEXTBOOKS_DATABASE_ID
            '13d5964e68a48056b40de8148dd91a06'
        ]:
            if not self.download_cache_from_github(database_id):
                success = False

        return success

def get_anki_version():
    try:
        # 2.1.50+ because of bdd5b27715bb11e4169becee661af2cb3d91a443, https://github.com/ankitects/anki/pull/1451
        from anki.utils import point_version
    except:
        try:
            # introduced with 66714260a3c91c9d955affdc86f10910d330b9dd in 2020-01-19, should be in 2.1.20+
            from anki.utils import pointVersion
        except:
            # <= 2.1.19
            from anki import version as anki_version
            out = int(anki_version.split(".")[-1])
        else:
            out = pointVersion()
    else:
        out = point_version()
    return out

anki_point_version = get_anki_version()

def insert_at_cursor(editor, html):
    if anki_point_version <= 49:
        # For older Anki versions
        js = "document.execCommand('insertHTML', false, %s);" % json.dumps(html)
    else:
        # For newer Anki versions, we need to work directly with the selection
        js = """
(function() {
    // Get the active element
    var activeElement = document.activeElement;

    // Ensure we have focus on a field
    if (!activeElement || !activeElement.classList.contains('field')) {
        // If no field is active, find and focus the first field
        var fields = document.querySelectorAll('.field');
        if (fields.length > 0) {
            activeElement = fields[0];
            activeElement.focus();
        }
    }

    // Insert the content at cursor position
    if (activeElement) {
        // Ensure field is focused before inserting
        activeElement.focus();

        // Small delay to ensure focus is complete and selection is restored
        setTimeout(function() {
            document.execCommand('insertHTML', false, %s);
        }, 50);
    }
})();
""" % json.dumps(html)

    # Ensure editor is focused before running JavaScript
    editor.web.setFocus()
    editor.web.eval(js)

class RandomizationDialog(QDialog):
    def __init__(self, parent, editor):
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Add Randomization Elements")
        self.setMinimumWidth(500)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        # Element type selection
        type_group = QGroupBox("Element Type")
        type_layout = QVBoxLayout()

        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "Random Number", "Random List", "Scored List",
            "Scored Number", "Show Score", "Answer by Score"
        ])
        self.type_combo.currentIndexChanged.connect(self.update_form)
        type_layout.addWidget(self.type_combo)

        type_group.setLayout(type_layout)
        layout.addWidget(type_group)

        # Stacked widget setup
        self.stack = QStackedWidget()

        # Random Number form
        random_number_widget = QWidget()
        random_number_layout = QFormLayout()

        self.min_value = QLineEdit()
        self.min_value.setPlaceholderText("e.g., 1")
        random_number_layout.addRow("Minimum Value:", self.min_value)

        self.max_value = QLineEdit()
        self.max_value.setPlaceholderText("e.g., 100")
        random_number_layout.addRow("Maximum Value:", self.max_value)

        self.decimals = QSpinBox()
        self.decimals.setMinimum(0)
        self.decimals.setMaximum(10)
        random_number_layout.addRow("Decimal Places:", self.decimals)

        random_number_widget.setLayout(random_number_layout)
        self.stack.addWidget(random_number_widget)

        # Random List form with dynamic fields
        random_list_widget = QWidget()
        random_list_layout = QVBoxLayout()

        random_list_layout.addWidget(QLabel("Enter options:"))

        self.random_list_items_layout = QVBoxLayout()

        # Add first item field
        self.random_list_items = []
        self.add_random_list_item()

        random_list_layout.addLayout(self.random_list_items_layout)

        # Add and Remove buttons
        buttons_layout = QHBoxLayout()

        add_button = QPushButton("Add Option")
        add_button.clicked.connect(self.add_random_list_item)
        buttons_layout.addWidget(add_button)

        remove_button = QPushButton("Remove Option")
        remove_button.clicked.connect(self.remove_random_list_item)
        buttons_layout.addWidget(remove_button)

        random_list_layout.addLayout(buttons_layout)

        random_list_widget.setLayout(random_list_layout)
        self.stack.addWidget(random_list_widget)

        # Scored List form with dynamic fields
        scored_list_widget = QWidget()
        scored_list_layout = QVBoxLayout()

        scored_list_layout.addWidget(QLabel("Enter options with scores:"))

        self.scored_list_items_layout = QVBoxLayout()

        # Add first scored item
        self.scored_list_items = []
        self.add_scored_list_item()

        scored_list_layout.addLayout(self.scored_list_items_layout)

        # Add and Remove buttons for scored list
        sl_buttons_layout = QHBoxLayout()

        sl_add_button = QPushButton("Add Option")
        sl_add_button.clicked.connect(self.add_scored_list_item)
        sl_buttons_layout.addWidget(sl_add_button)

        sl_remove_button = QPushButton("Remove Option")
        sl_remove_button.clicked.connect(self.remove_scored_list_item)
        sl_buttons_layout.addWidget(sl_remove_button)

        scored_list_layout.addLayout(sl_buttons_layout)

        scored_list_widget.setLayout(scored_list_layout)
        self.stack.addWidget(scored_list_widget)

        # Modified Scored Number form
        scored_number_widget = QWidget()
        scored_number_layout = QVBoxLayout()

        # Visual range display
        self.range_display = QWidget()
        self.range_display.setMinimumHeight(50)
        self.range_display.setStyleSheet("background: #f0f0f0;")
        scored_number_layout.addWidget(self.range_display)

        # Threshold controls
        self.thresholds_container = QWidget()
        self.thresholds_layout = QVBoxLayout(self.thresholds_container)

        self.threshold_items = []
        self.add_threshold_item(initial=True)

        # Range controls
        range_controls = QHBoxLayout()
        add_range_btn = QPushButton("Add Range")
        add_range_btn.clicked.connect(lambda: self.add_threshold_item())
        range_controls.addWidget(add_range_btn)

        remove_range_btn = QPushButton("Remove Range")
        remove_range_btn.clicked.connect(self.remove_threshold_item)
        range_controls.addWidget(remove_range_btn)

        scored_number_layout.addWidget(self.thresholds_container)
        scored_number_layout.addLayout(range_controls)

        # Decimal places
        decimals_container = QHBoxLayout()
        decimals_container.addWidget(QLabel("Decimal Places:"))
        self.sn_decimals = QSpinBox()
        self.sn_decimals.setRange(0, 10)
        decimals_container.addWidget(self.sn_decimals)
        scored_number_layout.addLayout(decimals_container)

        scored_number_widget.setLayout(scored_number_layout)
        self.stack.addWidget(scored_number_widget)

        # Show Score form - simplified to just a button
        show_score_widget = QWidget()
        show_score_layout = QVBoxLayout()

        info_label = QLabel("This will insert [showscore] which displays the total calculated score from all scored elements.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-style: italic; margin: 10px;")
        show_score_layout.addWidget(info_label)

        # Add some vertical spacing to center the content
        show_score_layout.addStretch()
        show_score_layout.addStretch()

        show_score_widget.setLayout(show_score_layout)
        self.stack.addWidget(show_score_widget)

        # Answer by Score form
        answer_score_widget = QWidget()
        answer_score_layout = QVBoxLayout()

        answer_score_layout.addWidget(QLabel("Define score ranges and answers:"))

        # Container for ranges
        self.answer_ranges_container = QWidget()
        self.answer_ranges_layout = QVBoxLayout(self.answer_ranges_container)

        # Dynamic range entries
        self.answer_range_items = []
        self.init_answer_by_score()

        # Add/Remove buttons
        abs_buttons = QHBoxLayout()
        add_abs_btn = QPushButton("Add Range")
        add_abs_btn.clicked.connect(self.add_answer_range_entry)
        abs_buttons.addWidget(add_abs_btn)

        remove_abs_btn = QPushButton("Remove Range")
        remove_abs_btn.clicked.connect(self.remove_answer_range_entry)
        abs_buttons.addWidget(remove_abs_btn)

        answer_score_layout.addWidget(self.answer_ranges_container)
        answer_score_layout.addLayout(abs_buttons)

        answer_score_widget.setLayout(answer_score_layout)
        self.stack.addWidget(answer_score_widget)

        layout.addWidget(self.stack)

        # Buttons - PyQt6 compatible approach
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def add_randomization_tag(self):
        """Add the randomization tag to the current note"""
        try:
            if isinstance(self.parent(), AddCards):
                # We're in an AddCards dialog, modify the existing note directly
                randomization_tag = "#Malleus_CM::#Card_Feature::Randomisation"

                try:
                    # Get the AddCards dialog
                    add_cards_dialog = self.parent()
                    current_note = None

                    # Try to get the current note from the dialog
                    if hasattr(add_cards_dialog, 'editor') and hasattr(add_cards_dialog.editor, 'note'):
                        current_note = add_cards_dialog.editor.note
                    elif hasattr(add_cards_dialog, 'note'):
                        current_note = add_cards_dialog.note

                    if current_note:
                        # Get current tags
                        current_tags = list(current_note.tags) if current_note.tags else []

                        # Add randomization tag if not already present
                        if randomization_tag not in current_tags:
                            current_tags.append(randomization_tag)
                            current_note.tags = current_tags

                            # Try to refresh the tags display in the dialog
                            if hasattr(add_cards_dialog, 'editor') and hasattr(add_cards_dialog.editor, 'loadNote'):
                                add_cards_dialog.editor.loadNote()

                            print(f"Added randomization tag: {randomization_tag}")
                        else:
                            print("Randomization tag already present")
                    else:
                        print("Could not find note in AddCards dialog")

                except Exception as e:
                    print(f"Error modifying note in AddCards dialog: {e}")
                return

            # The tag to add
            randomization_tag = "#Malleus_CM::#Card_Feature::Randomisation"
            # Get current tags
            current_tags = []
            if note.tags:
                current_tags = list(note.tags)
            # Add the randomization tag if it's not already present
            if randomization_tag not in current_tags:
                current_tags.append(randomization_tag)
                # Update the note with new tags
                note.tags = current_tags
                # Save the note
                note.flush()
                # Try to refresh the editor if possible
                try:
                    if hasattr(self.editor, 'loadNote'):
                        self.editor.loadNote()
                    elif hasattr(self.editor.parentWindow, 'editor') and hasattr(self.editor.parentWindow.editor, 'loadNote'):
                        self.editor.parentWindow.editor.loadNote()
                except:
                    pass  # If refresh fails, it's not critical
                print(f"Added randomization tag: {randomization_tag}")
            else:
                print("Randomization tag already present")
        except Exception as e:
            print(f"Error adding randomization tag: {e}")
            # Don't fail the entire operation if tagging fails

    def add_random_list_item(self):
        """Add a new option field to the random list"""
        item_layout = QHBoxLayout()

        item_field = QLineEdit()
        item_field.setPlaceholderText(f"Option {len(self.random_list_items) + 1}")

        item_layout.addWidget(item_field)

        self.random_list_items_layout.addLayout(item_layout)
        self.random_list_items.append(item_field)

    def remove_random_list_item(self):
        """Remove the last option field from the random list"""
        if len(self.random_list_items) <= 1:
            return  # Keep at least one field

        # Get the last item and its layout
        last_item = self.random_list_items.pop()
        last_layout = self.random_list_items_layout.itemAt(len(self.random_list_items))

        # Remove and delete the widget and layout
        last_item.deleteLater()

        # Remove all items from the layout
        while last_layout.count():
            item = last_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Remove the layout
        self.random_list_items_layout.removeItem(last_layout)

    def add_scored_list_item(self):
        """Add a new option with score field to the scored list"""
        item_layout = QHBoxLayout()

        option_field = QLineEdit()
        option_field.setPlaceholderText(f"Option {len(self.scored_list_items) + 1}")

        score_field = QSpinBox()
        score_field.setMinimum(0)
        score_field.setMaximum(100)
        score_field.setValue(len(self.scored_list_items) + 1)  # Default score is the item number

        item_layout.addWidget(option_field, 3)  # Give more space to option text
        item_layout.addWidget(QLabel("Score:"), 0)
        item_layout.addWidget(score_field, 1)   # Less space for score

        self.scored_list_items_layout.addLayout(item_layout)
        self.scored_list_items.append((option_field, score_field))

    def remove_scored_list_item(self):
        """Remove the last option with score field from the scored list"""
        if len(self.scored_list_items) <= 1:
            return  # Keep at least one field

        # Get the last item pair and its layout
        last_item_pair = self.scored_list_items.pop()
        last_layout = self.scored_list_items_layout.itemAt(len(self.scored_list_items))

        # Remove and delete the widgets
        last_item_pair[0].deleteLater()
        last_item_pair[1].deleteLater()

        # Remove all items from the layout
        while last_layout.count():
            item = last_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Remove the layout
        self.scored_list_items_layout.removeItem(last_layout)

    def add_threshold_item(self, initial=False):
        """Add a new threshold range with score"""
        if len(self.threshold_items) >= 5:
            showInfo("Maximum of 5 ranges allowed")
            return

        prev_max = "0"
        if self.threshold_items:
            prev_max = self.threshold_items[-1]["end"].text()

        # Create widgets
        threshold_item = QWidget()
        layout = QHBoxLayout(threshold_item)

        # Start input (only editable for first item)
        start_edit = QLineEdit(prev_max if not initial else "0")
        start_edit.setFixedWidth(80)
        start_edit.setReadOnly(not initial)  # Only first range is editable

        # End input (always editable)
        end_edit = QLineEdit("100" if initial else "")
        end_edit.setFixedWidth(80)

        score_spin = QSpinBox()
        score_spin.setRange(0, 100)
        score_spin.setValue(len(self.threshold_items) + 1)

        # Connect end edits to update next range's start
        if self.threshold_items:
            prev_end = self.threshold_items[-1]["end"]
            prev_end.textChanged.connect(lambda text: start_edit.setText(text))

        # Connect validation
        end_edit.textChanged.connect(self.update_visual_ranges)
        score_spin.valueChanged.connect(self.update_visual_ranges)

        # Add to layout
        layout.addWidget(QLabel("Range:"))
        layout.addWidget(start_edit)
        layout.addWidget(QLabel("to"))
        layout.addWidget(end_edit)
        layout.addWidget(QLabel("Score:"))
        layout.addWidget(score_spin)

        self.thresholds_layout.insertWidget(len(self.threshold_items), threshold_item)

        self.threshold_items.append({
            "start": start_edit,
            "end": end_edit,
            "score": score_spin,
            "widget": threshold_item
        })
        self.update_visual_ranges()

    def remove_threshold_item(self):
        """Remove the last threshold range"""
        if len(self.threshold_items) <= 1:
            return

        last_item = self.threshold_items.pop()
        last_item["widget"].deleteLater()
        self.update_visual_ranges()

    def update_visual_ranges(self):
        """Update the visual range display with theme-aware colors"""
        try:
            # Get theme background color
            palette = self.palette()
            bg_color = palette.color(QPalette.ColorRole.Window)

            # Clear existing display
            if self.range_display.layout():
                QWidget().setLayout(self.range_display.layout())

            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Add colored segments
            for idx, item in enumerate(self.threshold_items):
                try:
                    start = float(item["start"].text() or 0)
                    end = float(item["end"].text() or 0)
                    if start >= end:
                        continue

                    # Format numbers without decimal points
                    start_text = f"{int(start)}" if start.is_integer() else f"{start:.0f}"
                    end_text = f"{int(end)}" if end.is_integer() else f"{end:.0f}"

                    # Create segment with corrected gradient syntax
                    segment = QLabel()
                    segment.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    segment.setStyleSheet(f"""
                        background: qlineargradient(x1:0, y1:0, x1:1, y1:0,
                            stop:0 hsl({240 - idx*40}, 50%, 40%),
                            stop:1 hsl({200 - idx*40}, 50%, 40%));
                        color: white;
                        border-radius: 3px;
                        margin: 1px;
                        font-weight: bold;
                    """)
                    segment.setText(f"{start_text} → {end_text}\nScore: {item['score'].value()}")

                    layout.addWidget(segment, int(end - start))

                except ValueError:
                    continue

            self.range_display.setLayout(layout)
            self.range_display.setStyleSheet(f"""
                background: {bg_color.name()};
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                border-radius: 4px;
            """)

        except Exception as e:
            print("Visual update error:", e)

    # Function to add a new answer range entry with connected ranges
    def add_answer_range_entry(self):
        """Add a new answer range entry with connected ranges"""
        entry_widget = QWidget()
        entry_layout = QHBoxLayout(entry_widget)
        entry_layout.setContentsMargins(0, 0, 0, 0)

        # Determine the start value based on previous ranges
        start_value = 0
        if self.answer_range_items:
            # Get the 'to' value from the last entry
            prev_to_spin = self.answer_range_items[-1][1]  # Second item is the 'to' spinner
            start_value = prev_to_spin.value()

        # Create the 'from' spinner (read-only if not the first entry)
        from_spin = QSpinBox()
        from_spin.setMinimum(0)
        from_spin.setMaximum(9999)
        from_spin.setValue(start_value)
        from_spin.setReadOnly(len(self.answer_range_items) > 0)  # Only first one is editable

        # Create the 'to' spinner
        to_spin = QSpinBox()
        to_spin.setMinimum(start_value)
        to_spin.setMaximum(9999)
        to_spin.setValue(start_value + 1)

        # Answer text field
        text_edit = QLineEdit()
        text_edit.setPlaceholderText("Answer text")

        # Add widgets to layout
        entry_layout.addWidget(QLabel("From:"))
        entry_layout.addWidget(from_spin)
        entry_layout.addWidget(QLabel("To:"))
        entry_layout.addWidget(to_spin)
        entry_layout.addWidget(QLabel("Text:"))
        entry_layout.addWidget(text_edit)

        # Connect the previous 'to' spinner to this 'from' spinner if this isn't the first entry
        if self.answer_range_items:
            prev_to_spin = self.answer_range_items[-1][1]
            # Store the connection function so we can disconnect it later if needed
            update_func = lambda val: self._update_from_value(from_spin, to_spin, val)
            from_spin.prev_connection = update_func
            prev_to_spin.valueChanged.connect(update_func)

        # Store the items and widget for later reference
        self.answer_ranges_layout.addWidget(entry_widget)
        self.answer_range_items.append((from_spin, to_spin, text_edit, entry_widget))

    # Helper function to update a 'from' value when the previous 'to' value changes
    def _update_from_value(self, from_spin, to_spin, value):
        """Update a 'from' spinner value when the previous 'to' spinner changes"""
        # Block signals to prevent cycles
        from_spin.blockSignals(True)

        # Update value and minimum
        from_spin.setMinimum(value)
        from_spin.setValue(value)

        # Update the 'to' spinner's minimum
        to_spin.setMinimum(value)

        # Unblock signals
        from_spin.blockSignals(False)

    # Updated remove function that properly maintains connections
    def remove_answer_range_entry(self):
        """Remove the last answer range entry"""
        if not self.answer_range_items:
            return

        # Remove the last entry
        last_entry = self.answer_range_items.pop()
        from_spin, to_spin, text_edit, entry_widget = last_entry

        # Delete the widget which contains the layout and all controls
        entry_widget.deleteLater()

    # Initialize or reset the answer by score section
    def init_answer_by_score(self):
        """Initialize or reset the answer by score section"""
        # Clear existing items first
        while self.answer_range_items:
            self.remove_answer_range_entry()

        # Add the first entry
        self.add_answer_range_entry()

    def update_form(self, index):
        self.stack.setCurrentIndex(index)

    def accept(self):
        # Generate the appropriate tag based on selection
        index = self.type_combo.currentIndex()
        tag_added = False  # Track if we successfully added a randomization element

        if index == 0:  # Random Number
            try:
                min_val = float(self.min_value.text() or "0")
                max_val = float(self.max_value.text() or "100")
                decimals = self.decimals.value()
                tag = f"[random:{min_val},{max_val},{decimals}]"
                insert_at_cursor(self.editor, tag)
                tag_added = True
            except ValueError:
                showInfo("Invalid number format. Please enter valid numbers.")
                return

        elif index == 1:  # Random List
            options = []
            for field in self.random_list_items:
                option = field.text().strip()
                if option:
                    options.append(option)

            if not options:
                showInfo("Please enter at least one option.")
                return

            tag = f"[randomlist:{','.join(options)}]"
            insert_at_cursor(self.editor, tag)
            tag_added = True

        elif index == 2:  # Scored List
            scored_options = []
            for option_field, score_field in self.scored_list_items:
                text = option_field.text().strip()
                score = score_field.value()

                if text:
                    scored_options.append(f"{text}:{score}")

            if not scored_options:
                showInfo("Please enter at least one valid option with score.")
                return

            tag = f"[scoredlist:{','.join(scored_options)}]"
            insert_at_cursor(self.editor, tag)
            tag_added = True

        elif index == 3:  # Scored Number
            try:
                thresholds = []
                scores = []

                # Collect all threshold points
                for item in self.threshold_items:
                    start = float(item["start"].text() or 0)
                    end = float(item["end"].text() or 0)
                    thresholds.extend([start, end])
                    scores.append(item["score"].value())

                # Remove duplicates and sort
                thresholds = sorted(list(set(thresholds)))
                decimals = self.sn_decimals.value()

                if len(thresholds) < 2:
                    showInfo("Please define at least one valid range")
                    return

                tag = f"[scorednumber:{','.join(map(str, thresholds))}:{decimals}:{','.join(map(str, scores))}]"
                insert_at_cursor(self.editor, tag)
                tag_added = True
            except Exception as e:
                showInfo(f"Invalid input: {str(e)}")
                return

        elif index == 4:  # Show Score
            tag = "[showscore]"
            insert_at_cursor(self.editor, tag)
            tag_added = True

        elif index == 5:  # Answer by Score
            segments = []
            for item in self.answer_range_items:
                # Extract the first 3 elements from each item
                from_spin, to_spin, text_edit = item[0:3]

                from_val = from_spin.value()
                to_val = to_spin.value()
                text = text_edit.text().strip()

                if not text:
                    continue

                if from_val > to_val:
                    showInfo("'From' value must be ≤ 'To' value.")
                    return

                if from_val == to_val:
                    range_part = f"{from_val}"
                else:
                    range_part = f"{from_val},{to_val}"

                segments.append(range_part)
                segments.append(text)

            if not segments:
                showInfo("Please add at least one valid answer range.")
                return

            tag = f"[answerbyscore:{':'.join(segments)}]"
            insert_at_cursor(self.editor, tag)
            tag_added = True

        # If we successfully added a randomization element, add the randomization tag
        print(f"Need to add randomisation tag: {tag_added}")

        if tag_added:
            self.add_randomization_tag()

        super().accept()

# Show the dialog
def show_randomization_dialog(editor):
    dialog = RandomizationDialog(editor.parentWindow, editor)
    dialog.exec()  # Note: In PyQt6, exec_() is deprecated, use exec() instead

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
                "Mechanism of Action",
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
                "Mechanism of Action",
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
            ],
            "Guidelines": [
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

        # Yield selection section
        yield_group = QGroupBox("Yield Level")
        yield_layout = QVBoxLayout()

        # Create a custom title bar for the group box with info icon
        yield_title_layout = QHBoxLayout()
        yield_title_label = QLabel("Yield Level")
        yield_title_label.setStyleSheet("font-weight: bold; font-size: 13px;")

        # Create single info icon with combined tooltip
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

        # Hide the default title and add custom title
        yield_group.setTitle("")
        yield_layout.addLayout(yield_title_layout)

        # Add separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        yield_layout.addWidget(separator)

        # Create yield checkboxes without individual tooltips
        self.yield_checkboxes = {}

        yield_labels = {
            "High": "High Yield",
            "Medium": "Medium Yield",
            "Low": "Low Yield",
            "Beyond": "Beyond medical student level"
        }

        for yield_level, label_text in yield_labels.items():
            checkbox = QCheckBox(label_text)
            self.yield_checkboxes[yield_level] = checkbox
            yield_layout.addWidget(checkbox)

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
            create_cards_button = QPushButton("Add Tags")
        else:
            create_cards_button = QPushButton("Create Cards")
            if self.current_note is not None:
                add_tags_button = QPushButton("Add Tags")
                add_tags_button.clicked.connect(self.add_tags)
                button_layout.addWidget(add_tags_button)

        create_cards_button.clicked.connect(self.create_cards)
        button_layout.addWidget(create_cards_button)

        # Only show these buttons when editing an existing note

        if self.current_note is not None or isinstance(self.parent(), AddCards):
            replace_tags_button = QPushButton("Replace Tags")
            replace_tags_button.clicked.connect(self.replace_tags)
            button_layout.addWidget(replace_tags_button)

        update_database_button = QPushButton("Update database")
        update_database_button.clicked.connect(download_github_cache)
        button_layout.addWidget(update_database_button)

        guidelines_button = QPushButton("Submission Guidelines")
        guidelines_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://malleuscm.notion.site/Submission-Guidelines-24a5964e68a48144901aef2252f91483")
            )
        )
        button_layout.addWidget(guidelines_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def get_selected_yield_tags(self):
        """Get the selected yield tags"""
        selected_yields = []
        for yield_level, checkbox in self.yield_checkboxes.items():
            if checkbox.isChecked():
                # Special case for Beyond to use the correct tag name
                if yield_level == "Beyond":
                    selected_yields.append("#Malleus_CM::#Yield::Beyond_medical_student_level")
                else:
                    selected_yields.append(f"#Malleus_CM::#Yield::{yield_level}")
        return selected_yields

    def get_yield_search_query(self):
        """Get the yield search query for browser"""
        selected_yields = []
        for yield_level, checkbox in self.yield_checkboxes.items():
            if checkbox.isChecked():
                # Special case for Beyond to use the correct tag name
                if yield_level == "Beyond":
                    selected_yields.append("tag:#Malleus_CM::#Yield::Beyond_medical_student_level")
                else:
                    selected_yields.append(f"tag:#Malleus_CM::#Yield::{yield_level}")

        if selected_yields:
            return " or ".join(selected_yields)
        return ""

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
        elif self.database_selector.currentText() == "Textbooks":
            return TEXTBOOKS_DATABASE_ID
        else:
            return GUIDELINES_DATABASE_ID

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
            'deckName': config['deck_name'],
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

    def add_tags(self):
        """Add new tags to existing ones"""
        # Check yield selection
        selected_yields = self.get_selected_yield_tags()
        if len(selected_yields) > 1:
            showInfo("Please select only one yield level when adding tags")
            return

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

        # Add yield tags
        new_tags.update(selected_yields)

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
        # Check yield selection
        selected_yields = self.get_selected_yield_tags()
        if len(selected_yields) > 1:
            showInfo("Please select only one yield level when replacing tags")
            return

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

        # Add yield tags
        all_tags = new_tags + selected_yields

        # Update the note's tags
        note.tags = all_tags

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

# Merge Editor Button Setup
def setup_editor_buttons(buttons, editor):
    # Malleus button
    malleus_btn = editor.addButton(
        icon=None,
        cmd="malleus",
        func=lambda e: show_page_selector(editor.parentWindow),
        tip="Find/Add Malleus Tags",
        label="Add Malleus Tags"
    )
    buttons.append(malleus_btn)

    # Randomization button
    random_btn = editor.addButton(
        icon=None,
        cmd="randomization",
        func=lambda e: show_randomization_dialog(e),
        tip="Add Randomization Elements",
        label="Add Random"
    )
    buttons.append(random_btn)
    return buttons

# Update Hook Registration (replace existing)
addHook("setupEditorButtons", setup_editor_buttons)

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
            (TEXTBOOKS_DATABASE_ID, "Textbooks database"),
            (GUIDELINES_DATABASE_ID, "Guidelines database")
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
            ("Textbooks", TEXTBOOKS_DATABASE_ID),
            ("Guidelines",GUIDELINES_DATABASE_ID)
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
                (TEXTBOOKS_DATABASE_ID, "Textbooks"),
                (GUIDELINES_DATABASE_ID, "Guidelines")
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

