import os
import json
import time
import threading
import concurrent.futures
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path

from hierarchy_tags import GUIDELINES_PREFIX_SEGMENTS
from cache_generation import generate_from_pages, GENERATOR_VERSION
from notion_blocks import blocks_to_html

NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
if not NOTION_TOKEN:
    raise ValueError("NOTION_TOKEN environment variable is required")

# ── Subjects database: switch-point ───────────────────────────────────────
# Tags/search are generated locally (subjects_tags.py) from the relation graph for
# whichever id is active, so the active DB's Notion formulas can be deleted.
# _TESTING is the old duplicated copy, kept only as a fallback.  Keep this in sync
# with config.py.
SUBJECT_DATABASE_ID_ORIGINAL = '2674b67cbdf84a11a057a29cc24c524f'
SUBJECT_DATABASE_ID_TESTING  = '3755964e68a480d29cc3e3ddf808344c'
SUBJECT_DATABASE_ID = SUBJECT_DATABASE_ID_ORIGINAL   # active database (generated)

# Pharmacology database switch-point (same pattern as Subjects; sync with config.py).
PHARMACOLOGY_DATABASE_ID_ORIGINAL = '9ff96451736d43909d49e3b9d60971f8'
PHARMACOLOGY_DATABASE_ID_TESTING  = '3765964e68a480b38067c6e19c08cff4'
PHARMACOLOGY_DATABASE_ID = PHARMACOLOGY_DATABASE_ID_ORIGINAL   # active database (generated)
ETG_DATABASE_ID          = '22282971487f4f559dce199476709b03'
ROTATION_DATABASE_ID     = '69b3e7fdce1548438b26849466d7c18e'
TEXTBOOKS_DATABASE_ID    = '13d5964e68a480bfb07cf7e2f1786075'
GUIDELINES_DATABASE_ID   = '13d5964e68a48056b40de8148dd91a06'
SYNCED_EXTRA_DATABASE_ID = '2dc5964e68a480909c4ac1dc169b16fb'
SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID = '31b5964e68a48023b1c1c7b23fbdec64'

USES_FOR_SEARCH = {
    SUBJECT_DATABASE_ID,
    PHARMACOLOGY_DATABASE_ID,
    ETG_DATABASE_ID,
    ROTATION_DATABASE_ID,
    TEXTBOOKS_DATABASE_ID,
    GUIDELINES_DATABASE_ID,
}

# Large databases time out at page_size=100 even with server-side formula filtering.
# eTG has heavy rollups that make each row expensive to return — use page_size=5 so
# Notion only computes rollups for 5 rows per request instead of 25/100.
PAGE_SIZE_OVERRIDE = {
    # The original Subjects/Pharmacology DBs are throttled while they still carry
    # the heavy Notion formulas (generation fetches every page).  Once those
    # formula properties are deleted the DBs are light — bump these to 100 (or
    # remove the entries) for faster generation.
    SUBJECT_DATABASE_ID_ORIGINAL: 25,
    PHARMACOLOGY_DATABASE_ID_ORIGINAL: 25,
    ETG_DATABASE_ID:          100,
    GUIDELINES_DATABASE_ID:   25,
}
DEFAULT_PAGE_SIZE = 100

# No databases currently need client-side-only filtering, but this set is kept
# for future use if a database's formula scan is too expensive even at page_size=5.
SERVER_SIDE_FILTER_SKIP: set = set()

# Databases whose hierarchy (#…::…) tags are generated locally by traversing the
# `Parent item` graph instead of trusting the Notion `Tag` formula (which drops
# multi-parent paths and truncates names at commas).  For these we must fetch the
# FULL database (ancestors are not `For Search`) to build the graph, inject the
# corrected tags into the `For Search` leaves, then save only the leaves.
HIERARCHY_TAG_DATABASES = {
    GUIDELINES_DATABASE_ID: GUIDELINES_PREFIX_SEGMENTS,
}

# Databases whose FULL tag/search property set is generated locally
# (subjects_tags.generate_and_inject) from the relation graph + cross-ref DBs.
# Keyed by the ACTIVE database id, so generation follows whichever DB config.py
# points at.  We fetch the full database (no `For Search`/leaf filter — leaves are
# pages with no `Sub-item`), plus the Question Banks (eMedici) and Rotation
# databases for cross-references.
QUESTION_BANKS_DATABASE_ID = 'bf443eb7144c46aba3106a4b915959d7'
SUBJECTS_GENERATE = {
    SUBJECT_DATABASE_ID: {
        'qb_database_id':       QUESTION_BANKS_DATABASE_ID,
        'rotation_database_id': ROTATION_DATABASE_ID,
    },
}

# Same idea for Pharmacology (pharmacology_tags.generate_and_inject).  Needs the
# full graph + Question Banks (eMedici).  No rotation/related-subject in the tag.
PHARMACOLOGY_GENERATE = {
    PHARMACOLOGY_DATABASE_ID: {
        'qb_database_id': QUESTION_BANKS_DATABASE_ID,
    },
}

TIMEOUT = 120  # seconds — large databases need time

# ── Global rate limiter (3 req/s across all threads) ──────────────────

_rate_lock = threading.Lock()
_last_request_time = 0.0
_MIN_INTERVAL = 1.0 / 3  # enforce max 3 requests/second

def _rate_limited_wait():
    global _last_request_time
    with _rate_lock:
        now = time.time()
        wait = _MIN_INTERVAL - (now - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()

# ── Session factory ───────────────────────────────────────────────────

def make_session():
    """Create a requests session with built-in retry/backoff at the transport level."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2025-09-03",
        "Content-Type": "application/json",
    })
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST", "GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session

# ── NotionCache ───────────────────────────────────────────────────────

class NotionCache:
    def __init__(self):
        self.cache_dir = Path("cache")
        self.cache_dir.mkdir(exist_ok=True)
        self.session = make_session()  # each instance gets its own session (thread-safe)
        self._data_source_id_cache = {}

    def _save_raw_seed(self, filename: str, pages: list, fetched_at: float):
        """Save a raw-graph / cross-ref seed (committed to GitHub so the add-on
        can re-seed its incremental refresh instead of full-rebuilding from
        Notion).  ``fetched_at`` must be the time the fetch STARTED so the
        add-on's edits-since sync can't miss pages edited mid-build.  Written
        atomically via a per-thread temp file — Subjects and Pharmacology run
        in parallel and both save the shared Question Banks xref."""
        path = self.cache_dir / filename
        tmp = path.with_suffix(f".tmp{os.getpid()}-{threading.get_ident()}")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({"timestamp": fetched_at, "pages": pages}, f)
        os.replace(tmp, path)

    def _get_data_source_id(self, database_id: str) -> str:
        if database_id in self._data_source_id_cache:
            return self._data_source_id_cache[database_id]
        data = self.get(f"https://api.notion.com/v1/databases/{database_id}")
        sources = data.get("data_sources", [])
        ds_id = sources[0].get("id", database_id) if sources else database_id
        self._data_source_id_cache[database_id] = ds_id
        print(f"  data_source_id for {database_id}: {ds_id}")
        return ds_id

    def get(self, url: str) -> dict:
        for attempt in range(1, 6):
            try:
                _rate_limited_wait()
                r = self.session.get(url, timeout=TIMEOUT)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                print(f"  GET attempt {attempt}/5 failed: {type(e).__name__}: {e}")
                if attempt < 5:
                    time.sleep(2 ** attempt)
                else:
                    raise

    def _fetch_children_flat(self, block_id: str) -> list:
        """Fetch direct children of a block, handling pagination."""
        blocks = []
        url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
        while url:
            data = self.get(url)
            blocks.extend(data.get("results", []))
            if data.get("has_more") and data.get("next_cursor"):
                cursor = data["next_cursor"]
                url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100&start_cursor={cursor}"
            else:
                url = None
        return blocks

    def fetch_blocks(self, page_id: str) -> list:
        """Fetch all blocks for a page recursively, embedding children as '_children' key."""
        blocks = self._fetch_children_flat(page_id)
        for block in blocks:
            if block.get("has_children"):
                block["_children"] = self.fetch_blocks(block["id"])
        return blocks

    def fetch_pages_batch(self, database_id: str, start_cursor: str = None, use_for_search: bool = False) -> dict:
        page_size = PAGE_SIZE_OVERRIDE.get(database_id, DEFAULT_PAGE_SIZE)
        if use_for_search:
            payload = {
                "filter": {
                    "property": "For Search",
                    "formula": {"checkbox": {"equals": True}}
                },
                "page_size": page_size,
            }
        else:
            payload = {"page_size": page_size}

        if start_cursor:
            payload["start_cursor"] = start_cursor

        ds_id = self._get_data_source_id(database_id)
        url = f"https://api.notion.com/v1/data_sources/{ds_id}/query"

        for attempt in range(1, 9):
            try:
                _rate_limited_wait()
                response = self.session.post(url, json=payload, timeout=TIMEOUT)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                print(f"  Attempt {attempt}/8 failed: {type(e).__name__}: {e}")
                if attempt < 8:
                    wait = 2 ** attempt
                    print(f"  Waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    raise

    def fetch_all(self, database_id: str, use_for_search: bool = False) -> list:
        """Fetch every page of a database (handling pagination)."""
        pages, start_cursor, has_more = [], None, True
        while has_more:
            data = self.fetch_pages_batch(database_id, start_cursor, use_for_search=use_for_search)
            pages.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        return pages

    def update_subjects(self, database_id: str, name: str):
        """Generate the Subjects cache locally from the relation graph + the
        Question Banks (eMedici) and Rotation databases, then save the leaf pages
        (those with no `Sub-item`).  Injects the same property names the old
        Notion formulas produced, so the add-on is unchanged."""
        cfg = SUBJECTS_GENERATE[database_id]
        fetch_start = time.time()
        print(f"  [{name}] Fetching full Subjects graph...")
        all_pages = self.fetch_all(database_id)
        print(f"  [{name}] Fetched {len(all_pages)} pages; fetching Question Banks...")
        qb_pages = self.fetch_all(cfg["qb_database_id"])
        print(f"  [{name}] Fetched {len(qb_pages)} QB pages; fetching Rotation...")
        rotation_pages = self.fetch_all(cfg["rotation_database_id"])
        print(f"  [{name}] Generating tags from {len(all_pages)} pages...")
        leaves = generate_from_pages('subjects', all_pages, qb_pages, rotation_pages)
        cache_path = self.cache_dir / f"{database_id}.json"
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump({"version": 1, "generator_version": GENERATOR_VERSION,
                       "timestamp": time.time(), "pages": leaves}, f)
        # Publish the raw graph + cross-ref baselines so the add-on can seed
        # its incremental refresh from GitHub (edits since fetch_start are then
        # synced at runtime) instead of dead-ending on the leaves seed.
        self._save_raw_seed(f"_raw_{database_id}.json", all_pages, fetch_start)
        self._save_raw_seed(f"_xref_{cfg['qb_database_id']}.json", qb_pages, fetch_start)
        self._save_raw_seed(f"_xref_{cfg['rotation_database_id']}.json", rotation_pages, fetch_start)
        print(f"  [{name}] Generated + saved {len(leaves)} leaf pages → {cache_path}")

    def update_pharmacology(self, database_id: str, name: str):
        """Generate the Pharmacology cache locally from the relation graph + the
        Question Banks (eMedici).  Saves the For-Search pages (leaf drugs +
        one-level-above-leaf categories).  Same injected property names as the
        old Notion formulas, so the add-on is unchanged."""
        cfg = PHARMACOLOGY_GENERATE[database_id]
        fetch_start = time.time()
        print(f"  [{name}] Fetching full Pharmacology graph...")
        all_pages = self.fetch_all(database_id)
        print(f"  [{name}] Fetched {len(all_pages)} pages; fetching Question Banks...")
        qb_pages = self.fetch_all(cfg["qb_database_id"])
        print(f"  [{name}] Generating tags from {len(all_pages)} pages...")
        leaves = generate_from_pages('pharmacology', all_pages, qb_pages)
        cache_path = self.cache_dir / f"{database_id}.json"
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump({"version": 1, "generator_version": GENERATOR_VERSION,
                       "timestamp": time.time(), "pages": leaves}, f)
        # Publish the raw graph + QB baseline for the add-on's incremental
        # refresh (see update_subjects).
        self._save_raw_seed(f"_raw_{database_id}.json", all_pages, fetch_start)
        self._save_raw_seed(f"_xref_{cfg['qb_database_id']}.json", qb_pages, fetch_start)
        print(f"  [{name}] Generated + saved {len(leaves)} pages → {cache_path}")

    def update_cache(self, database_id: str, name: str):
        # Locally-generated databases (formulas stripped) — dedicated paths.
        if database_id in SUBJECTS_GENERATE:
            self.update_subjects(database_id, name)
            return
        if database_id in PHARMACOLOGY_GENERATE:
            self.update_pharmacology(database_id, name)
            return

        use_for_search = database_id in USES_FOR_SEARCH
        generate_hierarchy = database_id in HIERARCHY_TAG_DATABASES
        # When generating hierarchy tags locally we need the FULL graph (ancestor
        # pages are not `For Search`), so disable both server- and client-side
        # `For Search` filtering during the fetch and re-apply it after injection.
        server_side_filter = (
            use_for_search
            and not generate_hierarchy
            and database_id not in SERVER_SIDE_FILTER_SKIP
        )
        is_synced_extra = database_id in (SYNCED_EXTRA_DATABASE_ID, SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID)
        fetch_start = time.time()
        pages = []
        has_more = True
        start_cursor = None
        batch = 0

        while has_more:
            batch += 1
            print(f"  [{name}] Fetching batch {batch}...")
            data = self.fetch_pages_batch(database_id, start_cursor, use_for_search=server_side_filter)
            results = data['results']
            if use_for_search and not generate_hierarchy:
                # Always filter client-side — for server_side_filter DBs this is a no-op
                # (server already filtered), for SERVER_SIDE_FILTER_SKIP DBs this does the work
                results = [
                    p for p in results
                    if p.get("properties", {}).get("For Search", {}).get("formula", {}).get("boolean", False)
                ]
            pages.extend(results)
            has_more = data.get('has_more', False)
            start_cursor = data.get('next_cursor')
            print(f"  [{name}] Batch {batch}: {len(results)} pages (total: {len(pages)})")

        # Generate hierarchy tags from the full `Parent item` graph (shared
        # dispatch), keeping only the `For Search` leaves.
        if generate_hierarchy:
            leaves = generate_from_pages('guidelines', pages)
            print(f"  [{name}] Built hierarchy tags from {len(pages)} pages; "
                  f"{len(leaves)} leaves kept")
            # Publish the full graph for the add-on's incremental refresh
            # (see update_subjects).
            self._save_raw_seed(f"_raw_{database_id}.json", pages, fetch_start)
            pages = leaves

        # For Synced Extra, fetch block content and embed as HTML
        if is_synced_extra:
            print(f"  [{name}] Fetching block content for {len(pages)} synced pages...")
            for page in pages:
                page_id = page["id"]
                try:
                    blocks = self.fetch_blocks(page_id)
                    html = blocks_to_html(blocks).strip()
                    if html:
                        page["_block_html"] = html
                        print(f"    [{name}] {page_id}: {len(blocks)} blocks → {len(html)} chars HTML")
                except Exception as e:
                    print(f"    [{name}] Warning: could not fetch blocks for {page_id}: {e}")

        cache_path = self.cache_dir / f"{database_id}.json"
        cache_obj = {'version': 1, 'timestamp': time.time(), 'pages': pages}
        if generate_hierarchy:   # locally-generated (Guidelines) — stamp the generator
            cache_obj['generator_version'] = GENERATOR_VERSION
        with cache_path.open('w', encoding='utf-8') as f:
            json.dump(cache_obj, f)
        print(f"  [{name}] Saved {len(pages)} pages → {cache_path}")

def _run_one(db_id: str, name: str):
    """Worker function — each database gets its own NotionCache (and session).
    Retries the full database update up to 3 times on failure with increasing waits."""
    for db_attempt in range(1, 4):
        try:
            print(f"\n--- Starting {name} (attempt {db_attempt}/3) ---")
            cache = NotionCache()
            cache.update_cache(db_id, name)
            print(f"--- Done: {name} ---")
            return
        except Exception as e:
            print(f"\n  [{name}] DB-level attempt {db_attempt}/3 failed: {e}")
            if db_attempt < 3:
                wait = 60 * db_attempt  # 60s, then 120s
                print(f"  [{name}] Waiting {wait}s before retrying entire database...")
                time.sleep(wait)
            else:
                raise

def update_notion_cache():
    # The daily CI run builds ALL databases — including the locally-generated ones
    # (Subjects/Pharmacology/Guidelines) — and commits their caches to GitHub, so
    # the add-on can download a fresh generated seed cheaply instead of doing the
    # slow full regenerate-from-Notion at runtime (that's reserved for Shift+click).
    databases = [
        (SUBJECT_DATABASE_ID,      "Subjects"),
        (PHARMACOLOGY_DATABASE_ID, "Pharmacology"),
        (ETG_DATABASE_ID,          "eTG"),
        (ROTATION_DATABASE_ID,     "Rotation"),
        (TEXTBOOKS_DATABASE_ID,    "Textbooks"),
        (GUIDELINES_DATABASE_ID,   "Guidelines"),
        (SYNCED_EXTRA_DATABASE_ID, "Synced Extra"),
        (SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID, "Synced Additional Resources"),
    ]

    print(f"Running {len(databases)} database updates in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(databases)) as executor:
        futures = {executor.submit(_run_one, db_id, name): name for db_id, name in databases}
        failed = []
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"\nERROR updating {name}: {e}")
                failed.append(name)

    if failed:
        raise RuntimeError(f"The following databases failed to update: {', '.join(failed)}")
    print("\nAll databases updated successfully.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target = sys.argv[1].lower()
        databases = [
            (SUBJECT_DATABASE_ID,      "Subjects"),
            (PHARMACOLOGY_DATABASE_ID, "Pharmacology"),
            (ETG_DATABASE_ID,          "eTG"),
            (ROTATION_DATABASE_ID,     "Rotation"),
            (TEXTBOOKS_DATABASE_ID,    "Textbooks"),
            (GUIDELINES_DATABASE_ID,   "Guidelines"),
            (SYNCED_EXTRA_DATABASE_ID, "Synced Extra"),
            (SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID, "Synced Additional Resources"),
        ]
        matched = [(db_id, name) for db_id, name in databases if target in name.lower()]
        if not matched:
            raise SystemExit(f"No database matching '{sys.argv[1]}'. Options: {[n for _, n in databases]}")
        for db_id, name in matched:
            _run_one(db_id, name)
    else:
        update_notion_cache()
