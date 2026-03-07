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

Lookup strategy (fastest first)
--------------------------------
1. PRIMARY — Relation-based:  the Subjects page has a "Synced Extra" (or
   "Synced Additional Resources") relation property pointing directly at the
   relevant SE/AR page IDs.  We resolve those IDs against an in-memory
   O(1) index of the SE cache.  No subtag matching needed; the author has
   already encoded the correct links in Notion.

2. FALLBACK — Tag-based:  if a subject page has no relation entries set
   (e.g. older pages that haven't been linked yet), we fall back to the
   original approach: scan the SE cache for entries whose Subject relation
   contains this page's ID and whose Subtag matches the tag's subtag.

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

# Relation property names on the Subjects pages that point to SE/AR entries.
# Confirmed present via probe_relations.py — update here if Notion renames them.
SUBJECTS_SE_RELATION_PROP  = "Synced Extra"
SUBJECTS_AR_RELATION_PROP  = "Synced Additional Resource"

_SUBJECTS_SUBTAGS = [s for s in DATABASE_PROPERTIES.get("Subjects", []) if s]


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Normalise for loose title comparison."""
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.replace('\u2019', "'").replace('\u2018', "'").replace('\u02bc', "'")
    text = re.sub(r'\s*&\s*', ' and ', text)
    return text.replace('_', ' ').lower().strip()


# ── Cache loading & indexing ──────────────────────────────────────────────────

def _load_db_cache(notion_cache, database_id: str) -> List[Dict]:
    """Load all pages from a local cache by database ID."""
    try:
        pages, _ = notion_cache.load_from_cache(database_id, warn_if_expired=False)
        return pages or []
    except Exception as e:
        print(f"[ExtraSync] Cache load error for {database_id}: {e}")
        return []


def _build_id_index(pages: List[Dict]) -> Dict[str, Dict]:
    """
    Build a dict mapping normalised Notion page ID → page dict.
    Allows O(1) lookup when we have an ID from a relation property.
    """
    return {p.get('id', '').replace('-', ''): p for p in pages if p.get('id')}


def _find_subject_page(notion_cache, page_name: str) -> Optional[Dict]:
    """
    Return the full cached Subjects page dict matched by title.
    We return the full page so callers can read any property from it
    (including the Synced Extra / Synced Additional Resources relations).
    """
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
                    return page
            except Exception:
                continue
    except Exception as e:
        print(f"[ExtraSync] Subjects cache error: {e}")
    return None


# ── Relation-based lookup (primary path) ─────────────────────────────────────

def _relation_ids_from_subject(subject_page: Dict, relation_prop: str) -> List[str]:
    """
    Read the named relation property from a Subjects page and return
    a list of normalised (no-hyphens) Notion page IDs.
    """
    relation = (
        subject_page
        .get('properties', {})
        .get(relation_prop, {})
        .get('relation', [])
    )
    return [r.get('id', '').replace('-', '') for r in relation if r.get('id')]


# ── Tag/subtag matching (fallback path) ──────────────────────────────────────

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


def _fallback_se_pages(
    se_pages: List[Dict],
    target_id: str,
    nl: str,
) -> List[Dict]:
    """
    Fallback: scan se_pages for entries whose Subject relation contains
    target_id AND whose Subtag matches the normalised subtag label nl.
    """
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


# ── Entry conversion ──────────────────────────────────────────────────────────

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


# ── Core lookup: one subject page → matching SE/AR entries ───────────────────

def _entries_for_subject_page(
    subject_page: Dict,
    se_id_index: Dict[str, Dict],   # id → page dict for the SE/AR database
    all_se_pages: List[Dict],       # flat list, for fallback scan
    relation_prop: str,             # "Synced Extra" or "Synced Additional Resources"
    raw_subtag: str,                # from the Anki tag, used only in fallback
) -> List[Dict]:
    """
    Return entry dicts for a single subject page, preferring the relation
    property over the legacy tag-scan fallback.
    """
    entries = []

    # ── Primary: relation IDs ─────────────────────────────────────────────────
    relation_ids = _relation_ids_from_subject(subject_page, relation_prop)
    if relation_ids:
        for rid in relation_ids:
            se_page = se_id_index.get(rid)
            if se_page:
                entry = _page_to_entry(se_page)
                if entry:
                    entries.append(entry)
            else:
                print(f"[ExtraSync] Relation ID {rid!r} not found in SE cache "
                      f"(stale cache?)")
        # If we got at least one result from the relation, return it.
        # Only fall through to the fallback if the relation was entirely
        # unresolvable (all IDs missing from local cache).
        if entries:
            return entries

    # ── Fallback: scan by subject ID + subtag ────────────────────────────────
    page_id   = subject_page.get('id', '').replace('-', '')
    nl        = _normalised_subtag_label(raw_subtag)
    for page in _fallback_se_pages(all_se_pages, page_id, nl):
        entry = _page_to_entry(page)
        if entry:
            entries.append(entry)

    return entries


# ── Public: matching entries for Extra (Synced) dialog ───────────────────────

def get_matching_se_entries(
    tags: List[str],
    notion_cache,
    database_id: str,
) -> List[Dict]:
    """
    Return all matching SE entries for the given note tags as a list of dicts:
      {title, content, se_id, tag}

    Uses the Subjects page's "Synced Extra" relation property (fast, O(1) per ID)
    with a fallback to the legacy subtag-scan approach for unlinked pages.
    Deduplicates by se_id.
    """
    subject_tags = [t for t in tags if t.startswith("#Malleus_CM::#Subjects::")]
    if not subject_tags:
        return []

    se_pages = _load_db_cache(notion_cache, database_id)
    if not se_pages:
        return []

    se_id_index   = _build_id_index(se_pages)
    seen_se_ids   = set()
    seen_page_names = set()
    results       = []

    for tag in subject_tags:
        parsed = parse_tag(tag)
        if not parsed:
            continue
        page_name  = parsed.get('page_name')
        raw_subtag = parsed.get('subtag') or ''
        if not page_name or page_name in seen_page_names:
            continue
        seen_page_names.add(page_name)

        subject_page = _find_subject_page(notion_cache, page_name)
        if not subject_page:
            continue

        for entry in _entries_for_subject_page(
            subject_page, se_id_index, se_pages,
            SUBJECTS_SE_RELATION_PROP, raw_subtag
        ):
            se_id = entry['se_id']
            if se_id:
                if se_id in seen_se_ids:
                    continue
                seen_se_ids.add(se_id)
            results.append(entry)

    return results


# ── Public: auto-build for Additional Resources (Synced) ─────────────────────

def _build_synced_content(
    tags: List[str],
    notion_cache,
    database_id: str,
    relation_prop: str,
    label: str,
) -> str:
    """
    Auto-populate builder used for fields that don't show a selection dialog
    (currently Additional Resources (Synced)).

    Uses the same primary/fallback strategy as get_matching_se_entries.
    """
    subject_tags = [t for t in tags if t.startswith("#Malleus_CM::#Subjects::")]
    if not subject_tags:
        return ""

    se_pages = _load_db_cache(notion_cache, database_id)
    if not se_pages:
        print(f"[ExtraSync] {label} cache is empty — run 'Update Database Cache'")
        return ""

    se_id_index     = _build_id_index(se_pages)
    seen_se_ids     = set()
    seen_content    = set()
    seen_page_names = set()
    parts           = []

    for tag in subject_tags:
        parsed = parse_tag(tag)
        if not parsed:
            continue
        page_name  = parsed.get('page_name')
        raw_subtag = parsed.get('subtag') or ''
        if not page_name or page_name in seen_page_names:
            continue
        seen_page_names.add(page_name)

        subject_page = _find_subject_page(notion_cache, page_name)
        if not subject_page:
            continue

        for entry in _entries_for_subject_page(
            subject_page, se_id_index, se_pages,
            relation_prop, raw_subtag
        ):
            se_id   = entry['se_id']
            content = entry['content']

            if se_id:
                if se_id in seen_se_ids:
                    continue
                seen_se_ids.add(se_id)
            else:
                if content in seen_content:
                    continue
                seen_content.add(content)

            marker = f'<!-- se:{se_id} -->' if se_id else ''
            parts.append(marker + content)

    return "<br>\n".join(parts)


def build_additional_resources_content(tags: List[str], notion_cache) -> str:
    """Build content for 'Additional Resources (Synced)' field (auto, no dialog)."""
    return _build_synced_content(
        tags, notion_cache,
        SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID,
        SUBJECTS_AR_RELATION_PROP,
        "Synced Additional Resources",
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
