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
    r = requests.get(f"https://api.notion.com/v1/databases/{database_id}",
                     headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    sources = r.json().get("data_sources", [])
    return sources[0]["id"] if sources else database_id


def fetch_all_pages(database_id: str, token: str) -> list:
    """Fetch every page of a database via the data sources query API (paginated,
    with simple retry/backoff on transient errors)."""
    headers = _headers(token)
    ds_id = _data_source_id(database_id, headers)
    url = f"https://api.notion.com/v1/data_sources/{ds_id}/query"
    pages, cursor, has_more = [], None, True
    while has_more:
        payload = {"page_size": _PAGE_SIZE}
        if cursor:
            payload["start_cursor"] = cursor
        data = None
        for attempt in range(6):
            resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2 ** attempt, 30))
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
