"""
Notion Cache Management
Handles caching of Notion database content with GitHub fallback
"""
import json
import os
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
    # How old a generated DB's local raw graph may get before a normal-click
    # refresh does a *full* rebuild instead of incremental (to purge deleted/
    # archived pages).  Decoupled from cache_expiry so a short expiry doesn't
    # turn every click into a slow rebuild.
    GENERATED_GRAPH_MAX_AGE = 7 * 24 * 60 * 60

    def __init__(self, addon_dir: str, config: dict):
        self.cache_dir = Path(addon_dir) / "cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_lock = threading.Lock()
        self._sync_thread = None
        # Stale-cache warning is shown at most once per session (typing in the
        # search box loads every database and would otherwise fire it repeatedly).
        self._expiry_warning_shown = False
        # Per-database guard so the startup check and a manual 'Update Database'
        # click can't run the same update concurrently.
        self._inflight_lock = threading.Lock()
        self._updates_in_progress = set()
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

    @staticmethod
    def _atomic_write_json(path: Path, obj):
        """Write JSON via a temp file + os.replace so a crash/force-quit mid-write
        can never leave a truncated (corrupt) cache file behind."""
        tmp = path.with_suffix(path.suffix + '.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(obj, f)
        os.replace(tmp, path)

    def _begin_update(self, database_id: str) -> bool:
        """Claim a database for updating.  False if an update is already running."""
        with self._inflight_lock:
            if database_id in self._updates_in_progress:
                return False
            self._updates_in_progress.add(database_id)
            return True

    def _end_update(self, database_id: str):
        with self._inflight_lock:
            self._updates_in_progress.discard(database_id)

    def is_online(self) -> bool:
        """Check if internet connection is available"""
        try:
            requests.head("https://www.google.com", timeout=3)
            return True
        except (requests.ConnectionError, requests.Timeout, Exception):
            return False

    def load_from_cache(self, database_id: str, warn_if_expired: bool = True) -> Tuple[List[Dict], float]:
        """Load cached data if it exists, even if expired (for offline use).
        Returns ([], 0.0) when there is no usable cache file (missing/corrupt)."""
        cache_path = self.get_cache_path(database_id)
        if not cache_path.exists():
            return [], 0.0

        try:
            with cache_path.open('r', encoding='utf-8') as f:
                cache_data = json.load(f)

            current_time = time.time()
            cache_timestamp = float(cache_data.get('timestamp', current_time))

            # Check if cache is expired (a recent GitHub 304 verification counts as fresh)
            freshness = max(cache_timestamp, self.github_verified_at(database_id))
            is_expired = (cache_data.get('version') != self.CACHE_VERSION or
                         current_time - freshness > self.CACHE_EXPIRY)

            # Warn at most once per session, and never do a blocking network
            # check here — this runs on the main thread during search typing.
            if is_expired and warn_if_expired and not self._expiry_warning_shown:
                self._expiry_warning_shown = True
                mw.taskman.run_on_main(
                    lambda: malleus_tooltip("Newer database version available. Click 'Update Database' to update.")
                )

            # Return cached data even if expired (better than crashing)
            return cache_data.get('pages', []), cache_timestamp

        except Exception as e:
            print(f"Error loading cache: {e}")
            return [], 0.0

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
            # The incremental Notion sync returns pages WITHOUT block content;
            # keep the seed's embedded _block_html (Synced Extra) rather than
            # losing it — the daily GitHub seed refresh will bring it current.
            for page in pages:
                old = existing_dict.get(page['id'])
                if old and '_block_html' in old and '_block_html' not in page:
                    page['_block_html'] = old['_block_html']
            new_dict = {page['id']: page for page in pages}
            merged_dict = {**existing_dict, **new_dict}
            cache_data['pages'] = list(merged_dict.values())

        # Save with lock
        with self.cache_lock:
            self._atomic_write_json(cache_path, cache_data)

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

            # "Fresh" = built recently OR confirmed current against GitHub recently
            # (a 304 conditional check updates verified-at without rewriting the file).
            cache_timestamp = float(cache_data.get('timestamp', 0))
            freshness = max(cache_timestamp, self.github_verified_at(database_id))
            return (time.time() - freshness) > self.CACHE_EXPIRY
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

        if not force and not self.is_cache_expired(database_id):
            if callback:
                callback()
            return

        # One update per database at a time — the startup check and a manual
        # 'Update Database' click would otherwise race each other.
        # (Offline detection happens inside the worker threads, never here:
        # this method may be called from the main thread.)
        if not self._begin_update(database_id):
            print(f"{database_name} update already in progress — skipping")
            mw.taskman.run_on_main(
                lambda: malleus_tooltip(f"{database_name} update already in progress")
            )
            if callback:
                mw.taskman.run_on_main(callback)
            return

        # Locally-generated databases.  full=True (Shift+click) → unconditional full
        # regenerate.  Otherwise a "smart" refresh: regenerate from Notion only if
        # something changed since the cache timestamp, else keep the current cache.
        # We never do an incremental page-merge here (it would clobber the generated
        # tags, which depend on the whole graph).
        if database_id in GENERATED_DATABASES:
            if full:
                self._regenerate_generated_db(database_id, database_name, callback)
            else:
                self._incremental_refresh_generated_db(database_id, database_name, callback)
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
                    self._end_update(database_id)
                    if callback:
                        mw.taskman.run_on_main(callback)

            self._sync_thread = threading.Thread(target=download_thread, daemon=True)
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
            finally:
                self._end_update(database_id)

        self._sync_thread = threading.Thread(target=sync_thread, daemon=True)
        self._sync_thread.start()

    # ── Locally-generated databases ──────────────────────────────────────────
    #
    # The committed/downloaded seed (cache/<id>.json) holds only the generated
    # leaves (`pages`, what the add-on searches) — kept small.  To make a normal
    # refresh *incremental*, the full raw graph (every page incl. ancestors) is
    # kept in a SEPARATE local-only file (cache/_raw_<id>.json, gitignored), along
    # with the cross-reference DBs needed for generation — Question Banks (eMedici
    # tags) and Rotation (rotation tags) — in cache/_xref_<id>.json.
    #
    # A normal refresh fetches only pages edited since the last sync, merges them
    # into the stored graph, and re-runs generation in memory (fast).  A full
    # rebuild (Shift+click, first run, or a graph older than the expiry window)
    # refetches everything — that's also what drops deleted/archived pages, which
    # a "last_edited_time >= X" query can't report.

    @staticmethod
    def _iso(ts: float) -> str:
        return datetime.utcfromtimestamp(max(ts, 0)).strftime('%Y-%m-%dT%H:%M:%SZ')

    @staticmethod
    def _merge_by_id(existing: List[Dict], updated: List[Dict]) -> List[Dict]:
        merged = {p['id']: p for p in existing}
        for p in updated:
            merged[p['id']] = p
        return list(merged.values())

    def _xref_path(self, db_id: str) -> Path:
        return self.cache_dir / f"_xref_{db_id}.json"

    def _raw_graph_path(self, db_id: str) -> Path:
        return self.cache_dir / f"_raw_{db_id}.json"

    def _write_generated_cache(self, database_id: str, pages: List[Dict]):
        """Write the generated leaves (seed-shaped) and stamp the generator
        version.  The raw graph lives separately (see _write_raw_graph)."""
        from . import cache_generation
        cache_data = {
            'version': self.CACHE_VERSION,
            'generator_version': cache_generation.GENERATOR_VERSION,
            'timestamp': time.time(),
            'pages': pages,
        }
        with self.cache_lock:
            self._atomic_write_json(self.get_cache_path(database_id), cache_data)

    def _write_raw_graph(self, database_id: str, raw_pages: List[Dict]):
        with self.cache_lock:
            self._atomic_write_json(self._raw_graph_path(database_id),
                                    {'timestamp': time.time(), 'pages': raw_pages})

    def _load_raw_graph(self, database_id: str):
        """(raw_pages, timestamp) for a generated DB's local graph, or ([], 0)."""
        path = self._raw_graph_path(database_id)
        if not path.exists():
            return [], 0.0
        try:
            with path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('pages', []), float(data.get('timestamp', 0))
        except Exception:
            return [], 0.0

    def _load_xref(self, db_id: str):
        """(pages, timestamp) for a cross-ref raw cache, or ([], 0) if none."""
        path = self._xref_path(db_id)
        if not path.exists():
            return [], 0.0
        try:
            with path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('pages', []), float(data.get('timestamp', 0))
        except Exception:
            return [], 0.0

    def _write_xref(self, db_id: str, pages: List[Dict]):
        with self.cache_lock:
            self._atomic_write_json(self._xref_path(db_id),
                                    {'timestamp': time.time(), 'pages': pages})

    def _refresh_xref(self, db_id: str):
        """Bring a cross-ref raw cache up to date.  Returns (pages, changed).
        First time (no cache) it full-fetches a baseline and reports changed=False
        (the seed leaves already reflect the build-time cross-ref state)."""
        from . import cache_generation
        pages, ts = self._load_xref(db_id)
        if not pages:
            pages = cache_generation.fetch_all_pages(db_id, NOTION_TOKEN)
            self._write_xref(db_id, pages)
            return pages, False
        edited = cache_generation.fetch_edited_since(db_id, NOTION_TOKEN, self._iso(ts))
        if edited:
            pages = self._merge_by_id(pages, edited)
            self._write_xref(db_id, pages)
        return pages, bool(edited)

    def _crossref_pages_for(self, cfg: dict):
        """(qb_pages, rotation_pages, changed) — incrementally refreshed."""
        changed = False
        qb_pages = rotation_pages = None
        if cfg.get('qb'):
            qb_pages, c = self._refresh_xref(cfg['qb'])
            changed = changed or c
        if cfg.get('rotation'):
            rotation_pages, c = self._refresh_xref(cfg['rotation'])
            changed = changed or c
        return qb_pages, rotation_pages, changed

    def _regenerate_generated_db_work(self, database_id: str, database_name: str):
        """Full rebuild: fetch the whole DB + cross-refs from Notion, regenerate,
        and store leaves + raw graph + cross-ref baselines.  Raises on failure
        (caller keeps the existing cache)."""
        import concurrent.futures
        cfg = GENERATED_DATABASES[database_id]
        from . import cache_generation
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            main_f = ex.submit(cache_generation.fetch_all_pages, database_id, NOTION_TOKEN)
            qb_f = ex.submit(cache_generation.fetch_all_pages, cfg['qb'], NOTION_TOKEN) if cfg.get('qb') else None
            rot_f = ex.submit(cache_generation.fetch_all_pages, cfg['rotation'], NOTION_TOKEN) if cfg.get('rotation') else None
            main_pages = main_f.result()
            qb_pages = qb_f.result() if qb_f else None
            rotation_pages = rot_f.result() if rot_f else None
        pages = cache_generation.generate_from_pages(
            cfg['kind'], main_pages, qb_pages, rotation_pages
        )
        if pages:
            self._write_generated_cache(database_id, pages)
            self._write_raw_graph(database_id, main_pages)
            if cfg.get('qb'):
                self._write_xref(cfg['qb'], qb_pages)
            if cfg.get('rotation'):
                self._write_xref(cfg['rotation'], rotation_pages)
            mw.taskman.run_on_main(
                lambda: malleus_tooltip(f"{database_name} database updated")
            )

    def _regenerate_generated_db(self, database_id: str, database_name: str,
                                 callback: callable = None):
        """Full rebuild from Notion (Shift+click / fallback)."""
        def worker():
            try:
                if not self.is_online():
                    print(f"Offline: keeping cached {database_name}")
                    return
                self._regenerate_generated_db_work(database_id, database_name)
            except Exception as e:
                # Never wipe — keep whatever is cached.
                print(f"Error regenerating {database_name}; keeping existing cache: {e}")
            finally:
                self._end_update(database_id)
                if callback:
                    mw.taskman.run_on_main(callback)

        self._sync_thread = threading.Thread(target=worker, daemon=True)
        self._sync_thread.start()

    def _incremental_refresh_generated_db(self, database_id: str, database_name: str,
                                          callback: callable = None):
        """Normal-click refresh: fetch only pages edited since the cache timestamp
        (main DB + cross-refs), merge into the stored raw graph, and regenerate in
        memory.  When no usable raw graph is stored (first run after an add-on
        update wipes the addon dir, or a stale graph), the daily-rebuilt GitHub
        seed is downloaded instead — a full runtime rebuild from Notion is the
        last resort (slow, and hard on the shared token)."""
        cfg = GENERATED_DATABASES[database_id]

        def worker():
            try:
                if not self.is_online():
                    print(f"Offline: keeping cached {database_name}")
                    return
                from . import cache_generation
                raw_pages, ts = self._load_raw_graph(database_id)
                graph_stale = bool(raw_pages) and (time.time() - ts) > self.GENERATED_GRAPH_MAX_AGE
                # No stored graph (first run after add-on update) or a stale one
                # (needs deleted/archived pages purged, which incremental can't do).
                if not raw_pages or graph_stale:
                    reason = "no raw graph cached" if not raw_pages else "raw graph stale"
                    # Prefer the GitHub seed: the daily CI build regenerates it
                    # from the full graph, so it is at most ~24h old and the
                    # ETag-conditional download is nearly free.
                    if self.download_cache_from_github(database_id):
                        print(f"{database_name}: {reason} — refreshed from GitHub seed")
                        if graph_stale:
                            # Drop the stale graph so we don't merge edits into
                            # a snapshot that still contains deleted pages.
                            try:
                                self._raw_graph_path(database_id).unlink()
                            except OSError:
                                pass
                        return
                    print(f"{database_name}: {reason}, GitHub unavailable — full rebuild")
                    self._regenerate_generated_db_work(database_id, database_name)
                    return

                edited_main = cache_generation.fetch_edited_since(
                    database_id, NOTION_TOKEN, self._iso(ts))
                qb_pages, rotation_pages, xref_changed = self._crossref_pages_for(cfg)

                if not edited_main and not xref_changed:
                    print(f"{database_name}: no edits since cache — up to date")
                    # Record the successful freshness check so the cache stops
                    # counting as expired (otherwise the user keeps seeing the
                    # "Newer database version available" warning after updating).
                    self._mark_verified(database_id)
                    return

                graph = self._merge_by_id(raw_pages, edited_main)
                print(f"{database_name}: {len(edited_main)} edited page(s)"
                      f"{' + cross-ref changes' if xref_changed else ''} — regenerating in memory")
                pages = cache_generation.generate_from_pages(
                    cfg['kind'], graph, qb_pages, rotation_pages)
                if pages:
                    self._write_generated_cache(database_id, pages)
                    self._write_raw_graph(database_id, graph)
                    mw.taskman.run_on_main(
                        lambda: malleus_tooltip(f"{database_name} database updated"))
            except Exception as e:
                print(f"Error refreshing {database_name}; keeping existing cache: {e}")
            finally:
                self._end_update(database_id)
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

            except Exception as e:
                # Propagate instead of returning a partial list: saving partial
                # results would stamp the cache "fresh as of now" and the pages
                # never fetched would be skipped by every future incremental
                # sync (until a GitHub seed download happens to overwrite them).
                print(f"Error fetching from Notion after {len(pages)} pages: "
                      f"{type(e).__name__}: {e}")
                raise

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

    # ── GitHub conditional-download metadata (ETag + last-verified) ───────────
    def _github_meta_path(self) -> Path:
        return self.cache_dir / "_github_meta.json"

    def _load_github_meta(self) -> dict:
        try:
            with self._github_meta_path().open('r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_github_meta(self, meta: dict):
        try:
            self._atomic_write_json(self._github_meta_path(), meta)
        except Exception as e:
            print(f"Could not write GitHub cache meta: {e}")

    def github_verified_at(self, database_id: str) -> float:
        """When this cache was last confirmed current (GitHub 200/304, or a
        no-change Notion freshness check for a generated database)."""
        try:
            return float(self._load_github_meta().get(database_id, {}).get('verified', 0))
        except Exception:
            return 0.0

    def _mark_verified(self, database_id: str):
        """Record that the cache was just confirmed current without rewriting
        the (potentially multi-MB) cache file itself."""
        meta = self._load_github_meta()
        entry = meta.get(database_id, {})
        entry['verified'] = time.time()
        meta[database_id] = entry
        self._save_github_meta(meta)

    def download_cache_from_github(self, database_id: str) -> bool:
        """Download a cache file from GitHub, conditionally.  Sends the stored
        ETag so an unchanged file returns 304 (no transfer) — the daily re-check
        is then nearly free and only changed seeds are actually downloaded.
        Returns True on success (downloaded OR already current), False on error."""
        cache_filename = f"{database_id}.json"
        url = f"https://raw.githubusercontent.com/{self.github_repo}/{self.github_branch}/cache/{cache_filename}"
        cache_path = self.get_cache_path(database_id)

        meta = self._load_github_meta()
        entry = meta.get(database_id, {})
        headers = {}
        # Only trust the stored ETag if we still have the file it described.
        if entry.get('etag') and cache_path.exists():
            headers['If-None-Match'] = entry['etag']

        try:
            response = requests.get(url, headers=headers, timeout=self.REQUEST_TIMEOUT)

            if response.status_code == 304:
                entry['verified'] = time.time()
                meta[database_id] = entry
                self._save_github_meta(meta)
                return True

            response.raise_for_status()
            cache_data = response.json()
            with self.cache_lock:
                self._atomic_write_json(cache_path, cache_data)
            entry['etag'] = response.headers.get('ETag', '')
            entry['verified'] = time.time()
            meta[database_id] = entry
            self._save_github_meta(meta)
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
