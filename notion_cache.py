"""
Notion Cache Management
Handles caching of Notion database content with GitHub fallback
"""
import json
import time
import threading
import requests
from pathlib import Path
from typing import List, Dict, Tuple
from datetime import datetime
from aqt import mw
from .utils import malleus_tooltip
from .config import NOTION_TOKEN, get_database_name, GENERATED_DATABASES, FOR_SEARCH_DATABASES

class NotionCache:
    """Handles caching of Notion database content"""
    CACHE_VERSION = 1

    def __init__(self, addon_dir: str, config: dict):
        self.cache_dir = Path(addon_dir) / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_lock = threading.Lock()
        self._sync_thread = None
        # Short-lived cache of fetched cross-reference DBs (Question Banks,
        # Rotation) so a multi-database update reuses them instead of re-fetching.
        self._crossref_cache = {}
        self._crossref_lock = threading.Lock()
        self.headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        self.config = config
        self.CACHE_EXPIRY = config['cache_expiry'] * 24 * 60 * 60 + 1 * 60 * 60
        self.REQUEST_TIMEOUT = config.get('request_timeout', 30)  # Use config value
        self.github_repo = "Sabicool/Malleus-Anki-Addon"
        self.github_branch = "main"

    def get_cache_path(self, database_id: str) -> Path:
        """Get the path for a specific database's cache file"""
        return self.cache_dir / f"{database_id}.json"

    def is_online(self) -> bool:
        """Check if internet connection is available"""
        try:
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
                    lambda: malleus_tooltip("Newer database version available. Click 'Update Database' to update.")
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
        """Check if cache is expired (time-based, plus generator-version mismatch
        for locally-generated databases so an add-on update that changes the tag
        logic forces a regeneration)."""
        cache_path = self.get_cache_path(database_id)
        if not cache_path.exists():
            return True

        try:
            with cache_path.open('r', encoding='utf-8') as f:
                cache_data = json.load(f)

            if database_id in GENERATED_DATABASES:
                from . import cache_generation
                if cache_data.get('generator_version') != cache_generation.GENERATOR_VERSION:
                    return True

            cache_timestamp = float(cache_data.get('timestamp', 0))
            return (time.time() - cache_timestamp) > self.CACHE_EXPIRY
        except Exception:
            return True

    def update_cache_async(self, database_id: str, force: bool = False,
                           callback: callable = None, full: bool = False):
        """Update cache asynchronously with optional callback.

        ``full=True`` forces a complete rebuild directly from Notion (Shift+click
        on 'Update Database'): for locally-generated DBs this fetches the whole
        graph and regenerates the tags; for ordinary DBs it re-fetches every page.
        Without it, generated DBs are served from the committed GitHub seed (kept
        fresh by the daily CI build) and ordinary DBs do an incremental sync."""
        database_name = get_database_name(database_id)

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

        # Locally-generated databases.  Only do the slow full fetch-from-Notion +
        # regenerate when explicitly asked (full=True / Shift+click).  Otherwise
        # download the freshly-built seed from GitHub — never an incremental fetch
        # (which would re-pull raw/stripped pages and clobber the generated cache).
        if database_id in GENERATED_DATABASES:
            if full:
                self._regenerate_generated_db(database_id, database_name, callback)
            else:
                self._github_download_thread(database_id, database_name, callback)
            return

        if force:
            # Direct update from Notion (incremental, or full re-fetch when full=True)
            self._update_cache_thread(database_id, database_name, callback, full=full)
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

    def _github_download_thread(self, database_id: str, database_name: str,
                                callback: callable = None):
        """Download a single database's committed cache from GitHub in a thread."""
        def worker():
            try:
                if self.download_cache_from_github(database_id):
                    mw.taskman.run_on_main(
                        lambda: malleus_tooltip(f"{database_name} database updated")
                    )
                else:
                    print(f"Failed to download cache from GitHub for {database_name}")
            except Exception as e:
                print(f"Error during GitHub cache download for {database_name}: {e}")
            finally:
                if callback:
                    mw.taskman.run_on_main(callback)

        self._sync_thread = threading.Thread(target=worker, daemon=True)
        self._sync_thread.start()

    def _update_cache_thread(self, database_id: str, database_name: str,
                             callback: callable = None, full: bool = False):
        """Internal method to update cache in a thread.  ``full=True`` ignores the
        last-sync timestamp and re-fetches every page from Notion."""
        def sync_thread():
            try:
                # Check online status
                if not self.is_online():
                    print(f"Offline: Cannot update {database_name}")
                    if callback:
                        mw.taskman.run_on_main(callback)
                    return

                cached_pages, last_sync_timestamp = self.load_from_cache(database_id, warn_if_expired=False)
                if full:
                    last_sync_timestamp = 0
                pages = self.fetch_updated_pages(database_id, last_sync_timestamp)
                
                if pages:
                    self.save_to_cache(database_id, pages)
                    mw.taskman.run_on_main(lambda: malleus_tooltip(f"{database_name} database updated"))
                
                if callback:
                    mw.taskman.run_on_main(callback)
                    
            except requests.exceptions.RequestException as e:
                print(f"Network error during {database_name} sync: {e}")
                mw.taskman.run_on_main(
                    lambda: malleus_tooltip(f"Offline: Using cached {database_name} data")
                )
                if callback:
                    mw.taskman.run_on_main(callback)
            except Exception as e:
                print(f"Error during {database_name} sync: {e}")
                if callback:
                    mw.taskman.run_on_main(callback)

        self._sync_thread = threading.Thread(target=sync_thread, daemon=True)
        self._sync_thread.start()

    # ── Locally-generated databases (full fetch + regenerate) ─────────────────

    def _write_generated_cache(self, database_id: str, pages: List[Dict]):
        """Replace a generated database's cache outright (no merge) and stamp the
        generator version."""
        from . import cache_generation
        cache_data = {
            'version': self.CACHE_VERSION,
            'generator_version': cache_generation.GENERATOR_VERSION,
            'timestamp': time.time(),
            'pages': pages,
        }
        with self.cache_lock:
            with self.get_cache_path(database_id).open('w', encoding='utf-8') as f:
                json.dump(cache_data, f)

    def _get_crossref_pages(self, db_id: str, max_age: float = 600) -> List[Dict]:
        """Fetch a cross-reference database (Question Banks / Rotation), reusing a
        recent in-memory copy so it isn't re-fetched once per generated DB."""
        from . import cache_generation
        with self._crossref_lock:
            entry = self._crossref_cache.get(db_id)
            if entry and (time.time() - entry[1]) < max_age:
                return entry[0]
        pages = cache_generation.fetch_all_pages(db_id, NOTION_TOKEN)
        with self._crossref_lock:
            self._crossref_cache[db_id] = (pages, time.time())
        return pages

    def _regenerate_generated_db(self, database_id: str, database_name: str,
                                 callback: callable = None):
        """Fetch the full database (+ its cross-ref databases) from Notion and
        rebuild the cache locally.  The main DB and cross-ref DBs are fetched
        concurrently, and cross-ref DBs are cached across generated DBs.  On any
        failure the existing cache is kept."""
        import concurrent.futures
        cfg = GENERATED_DATABASES[database_id]

        def worker():
            try:
                if not self.is_online():
                    print(f"Offline: keeping cached {database_name}")
                    if callback:
                        mw.taskman.run_on_main(callback)
                    return
                from . import cache_generation
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                    main_f = ex.submit(cache_generation.fetch_all_pages, database_id, NOTION_TOKEN)
                    qb_f = ex.submit(self._get_crossref_pages, cfg['qb']) if cfg.get('qb') else None
                    rot_f = ex.submit(self._get_crossref_pages, cfg['rotation']) if cfg.get('rotation') else None
                    main_pages = main_f.result()
                    qb_pages = qb_f.result() if qb_f else None
                    rotation_pages = rot_f.result() if rot_f else None
                pages = cache_generation.generate_from_pages(
                    cfg['kind'], main_pages, qb_pages, rotation_pages
                )
                if pages:
                    self._write_generated_cache(database_id, pages)
                    mw.taskman.run_on_main(
                        lambda: malleus_tooltip(f"{database_name} database updated")
                    )
            except Exception as e:
                # Offline fallback: never wipe — keep whatever is cached.
                print(f"Error regenerating {database_name}; keeping existing cache: {e}")
            finally:
                if callback:
                    mw.taskman.run_on_main(callback)

        self._sync_thread = threading.Thread(target=worker, daemon=True)
        self._sync_thread.start()

    def _build_query_payload(
        self, last_sync_date: str, use_for_search_filter: bool, start_cursor: str = None
    ) -> dict:
        """Build the Notion database query payload."""
        if use_for_search_filter:
            payload = {
                "filter": {
                    "and": [
                        {
                            "property": "For Search",
                            "formula": {"checkbox": {"equals": True}}
                        },
                        {
                            "timestamp": "last_edited_time",
                            "last_edited_time": {"on_or_after": last_sync_date}
                        }
                    ]
                },
                "page_size": 100
            }
        else:
            payload = {
                "filter": {
                    "timestamp": "last_edited_time",
                    "last_edited_time": {"on_or_after": last_sync_date}
                },
                "page_size": 100
            }
        if start_cursor:
            payload["start_cursor"] = start_cursor
        return payload

    def fetch_updated_pages(self, database_id: str, last_sync_timestamp: float) -> List[Dict]:
        """
        Fetch all pages from a Notion database updated since last_sync_timestamp.

        Tries with the "For Search" formula filter first (used by main content
        databases).  If the database returns a 400 (e.g. Synced Extra /
        Additional Resources which lack that property), automatically retries
        with only the last_edited_time filter.
        """
        pages = []
        has_more = True
        start_cursor = None
        # Only attempt the "For Search" filter on databases that have that property
        # (others would 400); the 400 fallback below remains as a safety net.
        use_for_search_filter = database_id in FOR_SEARCH_DATABASES

        if last_sync_timestamp <= 0:
            last_sync_timestamp = time.time() - self.CACHE_EXPIRY

        last_sync_date = datetime.fromtimestamp(last_sync_timestamp).strftime('%Y-%m-%d')

        while has_more:
            payload = self._build_query_payload(
                last_sync_date, use_for_search_filter, start_cursor
            )

            try:
                response = requests.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    headers=self.headers,
                    json=payload,
                    timeout=self.REQUEST_TIMEOUT
                )

                # If the "For Search" property doesn't exist on this database,
                # Notion returns 400.  Retry without that filter.
                if response.status_code == 400 and use_for_search_filter:
                    print(
                        f"Database {database_id} returned 400 with 'For Search' filter — "
                        f"retrying without it (database may not have that property)"
                    )
                    use_for_search_filter = False
                    start_cursor = None   # reset pagination for the retry
                    pages = []
                    continue

                response.raise_for_status()
                data = response.json()

                pages.extend(data['results'])
                has_more = data.get('has_more', False)
                start_cursor = data.get('next_cursor')

            except requests.exceptions.Timeout:
                print(f"Timeout fetching from Notion (waited {self.REQUEST_TIMEOUT}s)")
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

        # Normalise the query to mirror what normalize_text does to page words:
        #   • & is removed (not a word char, can never match any page token)
        #   • apostrophes become spaces so "Barrett's" → ["barrett", "s"],
        #     matching the page-word split produced by normalize_text
        #   • remaining punctuation is stripped per-token
        _query_lower = re.sub(r'\s*&\s*', ' ', search_term.lower())
        _query_lower = re.sub(r"['\u2019\u2018\u02bc]", ' ', _query_lower).strip()
        search_terms = [re.sub(r'[^\w]', '', t) for t in _query_lower.split()
                        if re.sub(r'[^\w]', '', t)]
        normalized_search_term = ' '.join(search_terms)

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
            print(f"Timeout downloading cache from GitHub: {database_id} (waited {self.REQUEST_TIMEOUT}s)")
            return False
        except requests.exceptions.ConnectionError:
            print(f"Connection error downloading cache from GitHub: {database_id}")
            return False
        except Exception as e:
            print(f"Error downloading cache from GitHub: {e}")
            return False

    def download_all_caches_from_github(self) -> bool:
        """Download all cache files from GitHub"""
        from .config import DATABASES
        
        if not self.is_online():
            print("Offline: Cannot download caches from GitHub")
            return False

        success = True
        for database_id, _ in DATABASES:
            # Generated DBs are committed to GitHub by the daily CI build too, so
            # they download like any other (full regenerate is Shift+click only).
            if not self.download_cache_from_github(database_id):
                success = False

        return success
