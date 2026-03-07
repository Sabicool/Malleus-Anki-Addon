"""
Extra Sync
Populates synced note fields from locally cached Notion databases.

Currently handles two fields/databases:
  - 'Extra (Synced)'                 ← Synced Extra database (dialog-driven)
  - 'Additional Resources (Synced)'  ← Synced Additional Resources database (auto)

Both databases share the same schema:
  - Subject  (relation)  → one or more Subjects page IDs
  - Subtag   (select)    → e.g. "Clinical Features"
  - Content  (rich_text) → fallback HTML/text (block body is preferred)
  - ID       (unique_id) → stable numeric ID for deduplication
  - Tag      (formula)   → Anki tag to attach when entry is selected (Extra only)

No live API calls — everything reads from the local JSON cache files.
"""

import re
import unicodedata
from typing import List, Optional, Dict, Tuple, Set

from .config import (get_database_id, SYNCED_EXTRA_DATABASE_ID,
                     SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID, DATABASE_PROPERTIES)
from .tag_utils import parse_tag, normalize_subtag_for_matching

EXTRA_FIELD                = "Extra (Synced)"
ADDITIONAL_RESOURCES_FIELD = "Additional Resources (Synced)"

# Prefix used by all SE Anki tags — used to strip/add them cleanly
SE_EXTRA_TAG_PREFIX = "#Malleus_CM::#Card_Feature::Synced::Extra::"

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
    into individual known subtag names.
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


def _normalised_subtag_label(raw_subtag: str) -> str:
    """Convert a raw tag subtag to a normalised lowercase label for matching."""
    if not raw_subtag or raw_subtag.startswith('*'):
        return "main tag"
    ns = normalize_subtag_for_matching(raw_subtag, _SUBJECTS_SUBTAGS) or raw_subtag
    return ns.lower().strip()


def _find_matching_se_pages(se_pages: List[Dict], target_id: str, nl: str) -> List[Dict]:
    """Return all SE pages matching the given subject ID and normalised subtag label."""
    results = []
    for page in se_pages:
        props = page.get('properties', {})
        relation_ids = [
            r.get('id', '').replace('-', '')
            for r in props.get('Subject', {}).get('relation', [])
        ]
        if target_id not in relation_ids:
            continue
        raw_se_subtag = (props.get('Subtag', {}).get('select') or {}).get('name', '').strip()
        parsed = [s.lower() for s in _parse_compound_subtag(raw_se_subtag)]
        if nl not in parsed:
            continue
        results.append(page)
    return results


def _page_to_entry(page: Dict) -> Optional[Dict]:
    """Convert a raw SE cache page dict to a clean entry dict."""
    props = page.get('properties', {})
    content = page.get('_block_html', '').strip()
    if not content:
        segs = props.get('Content', {}).get('rich_text', [])
        content = ''.join(s.get('plain_text', '') for s in segs).strip()
    if not content:
        return None
    se_id_raw = props.get('ID', {}).get('unique_id', {})
    se_id_str = str(se_id_raw.get('number', '')) if se_id_raw else ''
    try:
        title_list = props.get('Name', {}).get('title', [])
        title = title_list[0]['text']['content'] if title_list else 'Untitled'
    except Exception:
        title = 'Untitled'
    tag_val = props.get('Tag', {}).get('formula', {}).get('string', '')
    return {'title': title, 'content': content, 'se_id': se_id_str, 'tag': tag_val}


# ── Field helpers ─────────────────────────────────────────────────────────────

def get_existing_se_ids_from_field(field_content: str) -> Set[str]:
    """Parse <!-- se:N --> markers from a field and return the set of se_id strings."""
    return set(re.findall(r'<!-- se:(\d+) -->', field_content or ''))


def build_field_from_selected_entries(selected: List[Dict]) -> str:
    """Build Extra (Synced) field HTML from a list of selected entry dicts."""
    parts = []
    for entry in selected:
        se_id   = entry.get('se_id', '')
        content = entry.get('content', '')
        marker  = f'<!-- se:{se_id} -->' if se_id else ''
        parts.append(marker + content)
    return '<br>\n'.join(parts)


# ── Public: matching entries for Extra (Synced) dialog ───────────────────────

def get_matching_se_entries(tags: List[str], notion_cache, database_id: str) -> List[Dict]:
    """
    Return all matching SE entries for the given note tags as a list of dicts:
      {title, content, se_id, tag}
    Deduplicates by se_id. Used to populate the SyncedExtraSelectionDialog.
    """
    subject_tags = [t for t in tags if t.startswith("#Malleus_CM::#Subjects::")]
    if not subject_tags:
        return []

    se_pages = _load_db_cache(notion_cache, database_id)
    if not se_pages:
        return []

    seen_se_ids    = set()
    results        = []
    seen_subj_ids  = {}

    for tag in subject_tags:
        parsed = parse_tag(tag)
        if not parsed:
            continue
        page_name  = parsed.get('page_name')
        raw_subtag = parsed.get('subtag')
        if not page_name:
            continue

        if page_name not in seen_subj_ids:
            seen_subj_ids[page_name] = _find_subject_page_id(notion_cache, page_name)
        page_id = seen_subj_ids[page_name]
        if not page_id:
            continue

        nl        = _normalised_subtag_label(raw_subtag)
        target_id = page_id.replace('-', '')

        for page in _find_matching_se_pages(se_pages, target_id, nl):
            entry = _page_to_entry(page)
            if not entry:
                continue
            se_id = entry['se_id']
            if se_id:
                if se_id in seen_se_ids:
                    continue
                seen_se_ids.add(se_id)
            results.append(entry)

    return results


# ── Internal: auto-populate for Additional Resources ─────────────────────────

def _find_content_in_cache(
    se_pages: List[Dict], subject_page_id: str, raw_subtag: str
) -> Optional[Tuple[str, str]]:
    """Return (html_content, se_id_str) for the first matching SE page, or None."""
    nl        = _normalised_subtag_label(raw_subtag)
    target_id = subject_page_id.replace('-', '')
    for page in _find_matching_se_pages(se_pages, target_id, nl):
        entry = _page_to_entry(page)
        if entry:
            return (entry['content'], entry['se_id'])
    return None


def _build_synced_content(tags: List[str], notion_cache, database_id: str, label: str) -> str:
    """
    Auto-populate builder for databases that don't use the selection dialog
    (currently used only for Additional Resources (Synced)).
    """
    subject_tags = [t for t in tags if t.startswith("#Malleus_CM::#Subjects::")]
    if not subject_tags:
        return ""

    se_pages = _load_db_cache(notion_cache, database_id)
    if not se_pages:
        print(f"[ExtraSync] {label} cache is empty — run 'Update Database Cache'")
        return ""

    seen_se_ids   = set()
    seen_content  = []
    seen_subj_ids = {}

    for tag in subject_tags:
        parsed = parse_tag(tag)
        if not parsed:
            continue
        page_name  = parsed.get('page_name')
        raw_subtag = parsed.get('subtag')
        if not page_name:
            continue

        if page_name not in seen_subj_ids:
            seen_subj_ids[page_name] = _find_subject_page_id(notion_cache, page_name)
        page_id = seen_subj_ids[page_name]
        if not page_id:
            continue

        result = _find_content_in_cache(se_pages, page_id, raw_subtag)
        if not result:
            continue
        content, se_id = result

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

def build_additional_resources_content(tags: List[str], notion_cache) -> str:
    """Build content for 'Additional Resources (Synced)' field (auto, no dialog)."""
    return _build_synced_content(
        tags, notion_cache,
        SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID,
        "Synced Additional Resources"
    )


def set_additional_resources_on_note(anki_note, notion_cache) -> bool:
    """Set anki_note['Additional Resources (Synced)'] from its current .tags."""
    try:
        _ = anki_note[ADDITIONAL_RESOURCES_FIELD]
    except Exception:
        print(f"[ExtraSync] Field '{ADDITIONAL_RESOURCES_FIELD}' not found — skipping")
        return False
    new_content = build_additional_resources_content(list(anki_note.tags), notion_cache)
    if anki_note[ADDITIONAL_RESOURCES_FIELD] == new_content:
        return False
    anki_note[ADDITIONAL_RESOURCES_FIELD] = new_content
    return True
