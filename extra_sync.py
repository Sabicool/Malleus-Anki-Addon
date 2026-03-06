"""
Extra Sync
Populates synced note fields from locally cached Notion databases.

Currently handles two fields/databases:
  - 'Extra (Synced)'                 ← Synced Extra database
  - 'Additional Resources (Synced)'  ← Synced Additional Resources database

Both databases share the same schema:
  - Subject  (relation)  → one or more Subjects page IDs
  - Subtag   (select)    → e.g. "Clinical Features"
  - Content  (rich_text) → fallback HTML/text (block body is preferred)
  - ID       (unique_id) → stable numeric ID for deduplication

No live API calls — everything reads from the local JSON cache files.
"""

import re
import unicodedata
from typing import List, Optional, Dict, Tuple

from .config import get_database_id, SYNCED_EXTRA_DATABASE_ID, SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID, DATABASE_PROPERTIES
from .tag_utils import parse_tag, normalize_subtag_for_matching

EXTRA_FIELD               = "Extra (Synced)"
ADDITIONAL_RESOURCES_FIELD = "Additional Resources (Synced)"

_SUBJECTS_SUBTAGS = [s for s in DATABASE_PROPERTIES.get("Subjects", []) if s]


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Normalise for loose title comparison."""
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.replace('\u2019', "'").replace('\u2018', "'").replace('\u02bc', "'")
    text = re.sub(r'\s*&\s*', ' and ', text)
    return text.replace('_', ' ').lower().strip()


# ── Cache lookups ─────────────────────────────────────────────────────────────

def _find_subject_page_id(notion_cache, page_name: str) -> Optional[str]:
    """Return the Notion page ID for a Subjects page matched by title."""
    database_id = get_database_id("Subjects")
    try:
        cached_pages, _ = notion_cache.load_from_cache(database_id, warn_if_expired=False)
        if not cached_pages:
            return None
        target = _norm(page_name)
        for page in cached_pages:
            try:
                title_list = page['properties']['Name']['title']
                if title_list and _norm(title_list[0]['text']['content']) == target:
                    return page.get('id')
            except Exception:
                continue
    except Exception as e:
        print(f"[ExtraSync] Subjects cache error: {e}")
    return None


def _load_db_cache(notion_cache, database_id: str) -> List[Dict]:
    """Load all pages from a local cache by database ID."""
    try:
        pages, _ = notion_cache.load_from_cache(database_id, warn_if_expired=False)
        return pages or []
    except Exception as e:
        print(f"[ExtraSync] Cache load error for {database_id}: {e}")
        return []


def _parse_compound_subtag(compound: str) -> List[str]:
    """
    Parse a compound SE Subtag value like "Clinical Features Management"
    into individual known subtag names by greedily matching against _SUBJECTS_SUBTAGS.

    Examples:
        "Clinical Features Management"  → ["Clinical Features", "Management"]
        "Clinical Features"             → ["Clinical Features"]
        "Diagnosis/Investigations"      → ["Diagnosis/Investigations"]
    """
    remaining = compound.strip()
    found = []
    candidates = sorted(_SUBJECTS_SUBTAGS, key=len, reverse=True)
    while remaining:
        matched = False
        for candidate in candidates:
            if remaining.lower().startswith(candidate.lower()):
                found.append(candidate)
                remaining = remaining[len(candidate):].strip()
                matched = True
                break
        if not matched:
            parts = remaining.split(' ', 1)
            remaining = parts[1].strip() if len(parts) > 1 else ''
    return found if found else [compound]


def _find_content_in_cache(
    se_pages: List[Dict],
    subject_page_id: str,
    raw_subtag: str
) -> Optional[Tuple[str, str]]:
    """
    Find matching content from a synced database cache.
    Returns (html_content, se_id_str) or None.

    Matches on Subject relation containing subject_page_id AND Subtag covers
    raw_subtag (supports compound subtags like "Clinical Features Management").
    """
    if not raw_subtag or raw_subtag.startswith('*'):
        normalised_subtag = "Main Tag"
    else:
        normalised_subtag = normalize_subtag_for_matching(raw_subtag, _SUBJECTS_SUBTAGS) or raw_subtag

    nl = normalised_subtag.lower().strip()
    target_id = subject_page_id.replace('-', '')

    for page in se_pages:
        props = page.get('properties', {})

        relation_ids = [
            r.get('id', '').replace('-', '')
            for r in props.get('Subject', {}).get('relation', [])
        ]
        if target_id not in relation_ids:
            continue

        raw_se_subtag = (props.get('Subtag', {}).get('select') or {}).get('name', '').strip()
        parsed_subtags = [s.lower() for s in _parse_compound_subtag(raw_se_subtag)]
        if nl not in parsed_subtags:
            continue

        content = page.get('_block_html', '').strip()
        if not content:
            content_segs = props.get('Content', {}).get('rich_text', [])
            content = ''.join(seg.get('plain_text', '') for seg in content_segs).strip()
        if content:
            se_id = props.get('ID', {}).get('unique_id', {})
            se_id_str = str(se_id.get('number', '')) if se_id else ''
            return (content, se_id_str)

    return None


# ── Generic builder ───────────────────────────────────────────────────────────

def _build_synced_content(tags: List[str], notion_cache, database_id: str, label: str) -> str:
    """
    Generic builder for any synced field backed by a database with the same schema.
    Reads entirely from local cache — no network calls.
    Deduplicates by ID property, falling back to content equality.
    """
    subject_tags = [t for t in tags if t.startswith("#Malleus_CM::#Subjects::")]
    if not subject_tags:
        return ""

    se_pages = _load_db_cache(notion_cache, database_id)
    if not se_pages:
        print(f"[ExtraSync] {label} cache is empty — run 'Update Database Cache'")
        return ""

    seen_se_ids    = set()
    seen_content   = []
    seen_subject_ids = {}

    for tag in subject_tags:
        parsed = parse_tag(tag)
        if not parsed:
            continue
        page_name  = parsed.get('page_name')
        raw_subtag = parsed.get('subtag')
        if not page_name:
            continue

        if page_name not in seen_subject_ids:
            seen_subject_ids[page_name] = _find_subject_page_id(notion_cache, page_name)
        page_id = seen_subject_ids[page_name]

        if not page_id:
            print(f"[ExtraSync] Subject page not found in cache: {page_name!r}")
            continue

        print(f"[ExtraSync:{label}] {page_name!r} ({page_id}) subtag={raw_subtag!r}")
        result = _find_content_in_cache(se_pages, page_id, raw_subtag)
        if not result:
            continue
        content, se_id = result
        print(f"[ExtraSync:{label}] matched se_id={se_id!r} ({len(content)} chars)")

        if se_id:
            if se_id in seen_se_ids:
                continue
            seen_se_ids.add(se_id)
        else:
            if content in seen_content:
                continue

        marker = f'<!-- se:{se_id} -->' if se_id else ""
        seen_content.append(marker + content)

    return "<br>\n".join(seen_content)


# ── Public API ────────────────────────────────────────────────────────────────

def build_extra_synced_content(tags: List[str], notion_cache) -> str:
    """Build content for 'Extra (Synced)' field."""
    return _build_synced_content(tags, notion_cache, SYNCED_EXTRA_DATABASE_ID, "Synced Extra")


def build_additional_resources_content(tags: List[str], notion_cache) -> str:
    """Build content for 'Additional Resources (Synced)' field."""
    return _build_synced_content(tags, notion_cache, SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID, "Synced Additional Resources")


def set_extra_synced_on_note(anki_note, notion_cache) -> bool:
    """Set anki_note['Extra (Synced)'] from its current .tags."""
    return _set_field_on_note(anki_note, notion_cache, EXTRA_FIELD, build_extra_synced_content)


def set_additional_resources_on_note(anki_note, notion_cache) -> bool:
    """Set anki_note['Additional Resources (Synced)'] from its current .tags."""
    return _set_field_on_note(anki_note, notion_cache, ADDITIONAL_RESOURCES_FIELD, build_additional_resources_content)


def set_all_synced_fields_on_note(anki_note, notion_cache) -> None:
    """Convenience: update both synced fields at once."""
    set_extra_synced_on_note(anki_note, notion_cache)
    set_additional_resources_on_note(anki_note, notion_cache)


def _set_field_on_note(anki_note, notion_cache, field_name: str, builder_fn) -> bool:
    """Internal: populate a single named field using the given builder function."""
    try:
        _ = anki_note[field_name]
    except Exception:
        print(f"[ExtraSync] Field '{field_name}' not found on note type — skipping")
        return False

    new_content = builder_fn(list(anki_note.tags), notion_cache)
    print(f"[ExtraSync] {field_name} → {new_content!r}")
    if anki_note[field_name] == new_content:
        return False

    anki_note[field_name] = new_content
    return True
