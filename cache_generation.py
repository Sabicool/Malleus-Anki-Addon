"""
Shared cache generation for the locally-generated databases (Subjects,
Pharmacology, Guidelines).

This is the single place that knows how to turn a Notion database (+ its
cross-reference databases) into the add-on's cached page list, by traversing the
relation graph with the dependency-free generators (subjects_tags /
pharmacology_tags / hierarchy_tags).

Two entry points so both callers can reuse the dispatch:
  * generate_from_pages(kind, all_pages, qb_pages, rotation_pages)
        — pure: given already-fetched pages, return the leaf pages to cache.
          Used by update_cache.py (which does its own rate-limited CI fetching).
  * fetch_and_generate(kind, token, db_id, qb_id, rotation_id)
        — fetches the full database(s) from Notion (data sources API) then
          generates.  Used by the add-on at runtime (notion_cache.py).

Dependency-free (only `requests`); safe to import in CI and inside Anki.

Bump GENERATOR_VERSION whenever the generated tag/search output changes so the
add-on can detect caches built by older logic and regenerate them.
"""
import time
import threading
import requests

try:  # standalone (CI) vs add-on package context
    from subjects_tags import generate_and_inject as _gen_subjects
    from pharmacology_tags import generate_and_inject as _gen_pharmacology
    from guidelines_tags import generate_and_inject as _gen_guidelines
except ImportError:
    from .subjects_tags import generate_and_inject as _gen_subjects
    from .pharmacology_tags import generate_and_inject as _gen_pharmacology
    from .guidelines_tags import generate_and_inject as _gen_guidelines

GENERATOR_VERSION = 1

_API_VERSION = "2025-09-03"
_PAGE_SIZE = 100
_TIMEOUT = 120

# Per-database page-size throttle, mirroring update_cache.py's PAGE_SIZE_OVERRIDE:
# while the original DBs still carry the heavy legacy Notion formulas, querying
# them at page_size=100 makes Notion time out (504).  Keyed by dash-less id.
# Remove once the formula properties are deleted from the Notion databases.
_PAGE_SIZE_OVERRIDE = {
    '2674b67cbdf84a11a057a29cc24c524f': 25,   # Subjects (original)
    '9ff96451736d43909d49e3b9d60971f8': 25,   # Pharmacology (original)
    '13d5964e68a48056b40de8148dd91a06': 25,   # Guidelines
}

# Global rate limiter (3 req/s across all threads) — the token is shared by every
# add-on install, so runtime fetches must be polite.
_rate_lock = threading.Lock()
_last_request_time = 0.0
_MIN_INTERVAL = 1.0 / 3


def _rate_limited_wait():
    global _last_request_time
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()


# ── pure dispatch ────────────────────────────────────────────────────────────
def generate_from_pages(kind: str, all_pages: list,
                        qb_pages: list = None, rotation_pages: list = None) -> list:
    """Run the right generator over already-fetched pages; return cache pages."""
    if kind == "subjects":
        return _gen_subjects(all_pages, qb_pages or [], rotation_pages or [])
    if kind == "pharmacology":
        return _gen_pharmacology(all_pages, qb_pages or [])
    if kind == "guidelines":
        return _gen_guidelines(all_pages)
    raise ValueError(f"unknown generated-database kind: {kind!r}")


# ── Notion fetching (runtime) ────────────────────────────────────────────────
def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _API_VERSION,
        "Content-Type": "application/json",
    }


def _data_source_id(database_id: str, headers: dict) -> str:
    _rate_limited_wait()
    r = requests.get(f"https://api.notion.com/v1/databases/{database_id}",
                     headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    sources = r.json().get("data_sources", [])
    return sources[0]["id"] if sources else database_id


def fetch_all_pages(database_id: str, token: str, filter_: dict = None) -> list:
    """Fetch every page of a database via the data sources query API (paginated,
    rate-limited, with retry/backoff honouring Retry-After on transient errors).
    An optional Notion `filter` object narrows the query (e.g. only pages edited
    since a timestamp)."""
    headers = _headers(token)
    ds_id = _data_source_id(database_id, headers)
    url = f"https://api.notion.com/v1/data_sources/{ds_id}/query"
    page_size = _PAGE_SIZE_OVERRIDE.get(database_id.replace('-', ''), _PAGE_SIZE)
    pages, cursor, has_more = [], None, True
    while has_more:
        payload = {"page_size": page_size}
        if filter_:
            payload["filter"] = filter_
        if cursor:
            payload["start_cursor"] = cursor
        data = None
        for attempt in range(6):
            _rate_limited_wait()
            resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else min(2 ** attempt, 30)
                except ValueError:
                    wait = min(2 ** attempt, 30)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        if data is None:
            raise RuntimeError(f"repeated server errors fetching {database_id}")
        pages.extend(data["results"])
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")
    return pages


def fetch_edited_since(database_id: str, token: str, since_iso: str) -> list:
    """Every page of the database whose last_edited_time is on or after
    `since_iso` (full page objects).  Used for incremental graph updates."""
    return fetch_all_pages(database_id, token, filter_={
        "timestamp": "last_edited_time",
        "last_edited_time": {"on_or_after": since_iso},
    })


def any_edits_since(database_id: str, token: str, since_iso: str) -> bool:
    """Cheap probe: does the database have at least one page whose
    last_edited_time is on or after `since_iso`?  One page_size=1 query."""
    headers = _headers(token)
    ds_id = _data_source_id(database_id, headers)
    url = f"https://api.notion.com/v1/data_sources/{ds_id}/query"
    payload = {
        "page_size": 1,
        "filter": {"timestamp": "last_edited_time",
                   "last_edited_time": {"on_or_after": since_iso}},
    }
    _rate_limited_wait()
    resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return bool(resp.json().get("results"))


def fetch_and_generate(kind: str, token: str, db_id: str,
                       qb_id: str = None, rotation_id: str = None) -> list:
    """Fetch the database (+ cross-ref databases) from Notion and generate the
    cache pages.  The main DB and its cross-ref DBs are fetched concurrently so
    the (smaller) Question Banks / Rotation fetches overlap the main one rather
    than adding to it.  Raises on network/Notion failure (caller keeps old cache)."""
    import concurrent.futures

    targets = {'main': db_id}
    if qb_id:
        targets['qb'] = qb_id
    if rotation_id:
        targets['rotation'] = rotation_id

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as ex:
        futures = {ex.submit(fetch_all_pages, dbid, token): key
                   for key, dbid in targets.items()}
        for fut in concurrent.futures.as_completed(futures):
            results[futures[fut]] = fut.result()   # re-raises any fetch error

    return generate_from_pages(
        kind, results['main'], results.get('qb'), results.get('rotation')
    )
