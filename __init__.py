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
    REQUEST_TIMEOUT = 10  # seconds - prevent hanging on network issues

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
        self.CACHE_EXPIRY = config['cache_expiry'] * 24 * 60 * 60 + 1 * 60 * 60
        self.github_repo = "Sabicool/Malleus-Anki-Addon"
        self.github_branch = "main"

    def get_cache_path(self, database_id: str) -> Path:
        """Get the path for a specific database's cache file"""
        return self.cache_dir / f"{database_id}.json"

    def is_online(self) -> bool:
        """Check if internet connection is available"""
        try:
            # Try to connect to a reliable host
            requests.head("https://www.google.com", timeout=3)
            return True
        except (requests.ConnectionError, requests.Timeout, Exception):
            return False

    def load_from_cache(self, database_id: str, warn_if_expired: bool = True) -> Tuple[List[Dict], float]:
        """Load cached data if it exists, even if expired (for offline use)"""
        cache_path = self.get_cache_path(database_id)
        if not cache_path.exists():
            return [], time.time()

        try:
            with cache_path.open('r', encoding='utf-8') as f:
                cache_data = json.load(f)

            current_time = time.time()
            cache_timestamp = float(cache_data.get('timestamp', current_time))

            # Check if cache is expired
            is_expired = (cache_data.get('version') != self.CACHE_VERSION or
                         current_time - cache_timestamp > self.CACHE_EXPIRY)

            # Only warn if expired and online (can update)
            if is_expired and warn_if_expired and self.is_online():
                mw.taskman.run_on_main(
                    lambda: tooltip("Newer database version available. Click 'Update Database' to update.")
                )

            # Return cached data even if expired (better than crashing)
            return cache_data.get('pages', []), cache_timestamp

        except Exception as e:
            print(f"Error loading cache: {e}")
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
            cache_data = {
                'version': self.CACHE_VERSION,
                'pages': []
            }
            existing_pages = []

        # Always update the timestamp
        cache_data['timestamp'] = current_time

        # Only merge pages if there are new ones
        if pages:
            existing_dict = {page['id']: page for page in existing_pages}
            new_dict = {page['id']: page for page in pages}
            merged_dict = {**existing_dict, **new_dict}
            cache_data['pages'] = list(merged_dict.values())

        # Save with lock
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

        # Check if online first
        if not self.is_online():
            print(f"Offline: Using cached data for {database_name}")
            if callback:
                mw.taskman.run_on_main(callback)
            return

        if not force and not self.is_cache_expired(database_id):
            if callback:
                callback()
            return

        if force:
            # Direct update without GitHub download
            self._update_cache_thread(database_id, database_name, callback)
        else:
            # Download from GitHub
            def download_thread():
                try:
                    success = self.download_all_caches_from_github()
                    if not success:
                        print(f"Failed to download cache from GitHub for {database_name}")
                except Exception as e:
                    print(f"Error during GitHub cache download: {e}")
                finally:
                    if callback:
                        mw.taskman.run_on_main(callback)

            self._sync_thread = threading.Thread(target=download_thread, daemon=True)
            self._sync_thread.start()

    def get_database_name(self, database_id: str) -> str:
        """Helper method to get database name based on ID"""
        database_names = {
            SUBJECT_DATABASE_ID: "Subjects",
            PHARMACOLOGY_DATABASE_ID: "Pharmacology",
            ETG_DATABASE_ID: "eTG",
            ROTATION_DATABASE_ID: "Rotation",
            TEXTBOOKS_DATABASE_ID: "Textbooks",
            GUIDELINES_DATABASE_ID: "Guidelines"
        }
        return database_names.get(database_id, "Unknown Database")

    def _update_cache_thread(self, database_id: str, database_name: str, callback: callable = None):
        """Internal method to update cache in a thread"""
        def sync_thread():
            try:
                # Check online status
                if not self.is_online():
                    print(f"Offline: Cannot update {database_name}")
                    if callback:
                        mw.taskman.run_on_main(callback)
                    return

                cached_pages, last_sync_timestamp = self.load_from_cache(database_id, warn_if_expired=False)
                pages = self.fetch_updated_pages(database_id, last_sync_timestamp)
                
                if pages:
                    self.save_to_cache(database_id, pages)
                    mw.taskman.run_on_main(lambda: tooltip(f"{database_name} database updated"))
                
                if callback:
                    mw.taskman.run_on_main(callback)
                    
            except requests.exceptions.RequestException as e:
                print(f"Network error during {database_name} sync: {e}")
                mw.taskman.run_on_main(
                    lambda: tooltip(f"Offline: Using cached {database_name} data")
                )
                if callback:
                    mw.taskman.run_on_main(callback)
            except Exception as e:
                print(f"Error during {database_name} sync: {e}")
                if callback:
                    mw.taskman.run_on_main(callback)

        self._sync_thread = threading.Thread(target=sync_thread, daemon=True)
        self._sync_thread.start()

    def fetch_updated_pages(self, database_id: str, last_sync_timestamp: float) -> List[Dict]:
        """Fetch all pages from Notion database that have been updated since last sync"""
        pages = []
        has_more = True
        start_cursor = None

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
                response = requests.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    headers=self.headers,
                    json=payload,
                    timeout=self.REQUEST_TIMEOUT  # Critical: prevent hanging
                )
                response.raise_for_status()
                data = response.json()

                pages.extend(data['results'])
                has_more = data.get('has_more', False)
                start_cursor = data.get('next_cursor')

            except requests.exceptions.Timeout:
                print(f"Timeout fetching from Notion")
                break
            except requests.exceptions.ConnectionError:
                print(f"Connection error fetching from Notion")
                break
            except Exception as e:
                print(f"Error fetching from Notion: {e}")
                break

        print(f"Found {len(pages)} updated pages")
        return pages

    def filter_pages(self, pages: List[Dict], search_term: str) -> List[Dict]:
        """Filter pages based on search term using fuzzy matching with multi-tier sorting"""
        from difflib import SequenceMatcher
        import re
        from functools import lru_cache

        if len(search_term.replace(' ', '')) < 3:
            return []

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
            words = re.sub(r'[^\w\s]', ' ', text.lower()).split()
            normalized = set()
            
            for word in words:
                normalized.add(word)
                normalized.add(word.lower())

                if word.endswith('y'):
                    normalized.add(word[:-1] + 'ies')
                elif word.endswith('s') and not word.endswith('ss'):
                    normalized.add(word[:-1])

                for key, variations in MEDICAL_VARIATIONS.items():
                    if word.lower() == key or word.lower() in variations:
                        normalized.update(variations)
                        normalized.add(key)

            return normalized

        def page_matches_all_terms(page_words: list, search_terms: set) -> bool:
            for search_term in search_terms:
                term_matched = False
                for page_word in page_words:
                    page_variations = normalize_text(page_word)
                    if any(var.startswith(search_term) for var in page_variations):
                        term_matched = True
                        break
                if not term_matched:
                    return False
            return True

        search_terms = search_term.lower().split()
        normalized_search_term = search_term.lower()

        filtered_pages = []
        for page in pages:
            if not page.get('properties'):
                continue

            page_search_terms_prop = page['properties'].get('Search Term', {})
            if not page_search_terms_prop or page_search_terms_prop.get('type') != 'formula':
                continue

            page_search_term = page_search_terms_prop.get('formula', {}).get('string', '').lower()
            if not page_search_term:
                continue

            page_terms = normalize_text(page_search_term)

            if page_matches_all_terms(page_terms, search_terms):
                title_prop = page['properties'].get('Name', {})
                title = title_prop['title'][0]['text']['content'] if title_prop.get('title') else ""
                title_lower = title.lower()

                exact_match_score = 1.0 if normalized_search_term in page_search_term else 0.0
                title_match_score = 1.0 if normalized_search_term in title_lower else (
                    0.9 if any(term in title_lower for term in search_terms) else 0.0
                )
                term_freq_score = sum(
                    page_search_term.count(term) for term in search_terms
                ) / len(search_terms)
                sequence_similarity = SequenceMatcher(
                    None, normalized_search_term, page_search_term
                ).ratio()

                composite_score = (
                    exact_match_score * 0.4 +
                    title_match_score * 0.3 +
                    term_freq_score * 0.2 +
                    sequence_similarity * 0.1
                )

                page['_composite_score'] = composite_score
                page['_title'] = title_lower
                page['_exact_match'] = exact_match_score

                filtered_pages.append(page)

        filtered_pages.sort(
            key=lambda x: (
                -x.get('_exact_match', 0),
                -x.get('_composite_score', 0),
                x.get('_title', '')
            )
        )

        return filtered_pages

    def download_cache_from_github(self, database_id: str) -> bool:
        """Download cache file from GitHub"""
        cache_filename = f"{database_id}.json"
        url = f"https://raw.githubusercontent.com/{self.github_repo}/{self.github_branch}/cache/{cache_filename}"

        try:
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            cache_data = response.json()

            with self.cache_lock:
                cache_path = self.get_cache_path(database_id)
                with cache_path.open('w', encoding='utf-8') as f:
                    json.dump(cache_data, f)

            return True
        except requests.exceptions.Timeout:
            print(f"Timeout downloading cache from GitHub: {database_id}")
            return False
        except requests.exceptions.ConnectionError:
            print(f"Connection error downloading cache from GitHub: {database_id}")
            return False
        except Exception as e:
            print(f"Error downloading cache from GitHub: {e}")
            return False

    def download_all_caches_from_github(self) -> bool:
        """Download all cache files from GitHub"""
        if not self.is_online():
            print("Offline: Cannot download caches from GitHub")
            return False

        success = True
        database_ids = [
            '2674b67cbdf84a11a057a29cc24c524f',
            '9ff96451736d43909d49e3b9d60971f8',
            '22282971487f4f559dce199476709b03',
            '69b3e7fdce1548438b26849466d7c18e',
            '13d5964e68a480bfb07cf7e2f1786075',
            '13d5964e68a48056b40de8148dd91a06'
        ]

        for database_id in database_ids:
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
    last_yield_selection = ""  # Class variable to remember last selection

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

        # Create a horizontal layout with title, info icon, and dropdown
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

        # Create yield dropdown selector
        self.yield_selector = QComboBox()
        self.yield_selector.addItems([
            "",  # Empty default
            "High Yield",
            "Medium Yield",
            "Low Yield",
            "Beyond medical student level"
        ])

        # Restore last selection
        if NotionPageSelector.last_yield_selection:
            index = self.yield_selector.findText(NotionPageSelector.last_yield_selection)
            if index >= 0:
                self.yield_selector.setCurrentIndex(index)

        # Save selection when it changes
        self.yield_selector.currentTextChanged.connect(self.save_yield_selection)

        # Add all elements to the horizontal layout
        yield_title_layout.addWidget(yield_title_label)
        yield_title_layout.addWidget(info_label)
        yield_title_layout.addStretch()
        yield_title_layout.addWidget(self.yield_selector)

        # Hide the default title and add custom layout
        yield_group.setTitle("")
        yield_layout.addLayout(yield_title_layout)

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
        update_database_button.clicked.connect(download_github_cache)
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

    def save_yield_selection(self, text):
        """Save the current yield selection"""
        NotionPageSelector.last_yield_selection = text

    def get_selected_yield_tags(self):
        """Get the selected yield tags from the dropdown"""
        selected_yield = self.yield_selector.currentText()

        if not selected_yield or selected_yield == "":
            return []

        # Map the display text to the actual tag
        yield_tag_mapping = {
            "High Yield": "#Malleus_CM::#Yield::High",
            "Medium Yield": "#Malleus_CM::#Yield::Medium",
            "Low Yield": "#Malleus_CM::#Yield::Low",
            "Beyond medical student level": "#Malleus_CM::#Yield::Beyond_medical_student_level"
        }

        tag = yield_tag_mapping.get(selected_yield)
        return [tag] if tag else []

    def get_existing_yield_tags(self, tags):
        """Extract existing yield tags from a list of tags"""
        yield_pattern = "#Malleus_CM::#Yield::"
        existing_yields = [tag for tag in tags if tag.startswith(yield_pattern)]
        return existing_yields

    def get_yield_search_query(self):
        """Get the yield search query for browser"""
        selected_yield = self.yield_selector.currentText()

        if not selected_yield or selected_yield == "":
            return ""

        # Map the display text to the search query format
        yield_search_mapping = {
            "High Yield": "tag:#Malleus_CM::#Yield::High",
            "Medium Yield": "tag:#Malleus_CM::#Yield::Medium",
            "Low Yield": "tag:#Malleus_CM::#Yield::Low",
            "Beyond medical student level": "tag:#Malleus_CM::#Yield::Beyond_medical_student_level"
        }

        search_query = yield_search_mapping.get(selected_yield, "")
        return search_query

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

