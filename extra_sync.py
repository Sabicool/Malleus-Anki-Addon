"""
Extra Sync
Populates the 'Extra (Synced)' note field from the locally cached
Synced Extra database.

The Synced Extra database has:
  - Subject  (relation)  → one or more Subjects page IDs
  - Subtag   (select)    → e.g. "Clinical Features"
  - Content  (rich_text) → HTML/text to insert into Extra (Synced)

Workflow:
  1. Parse #Subjects tags from the note to get (page_name, raw_subtag) pairs.
  2. Look up each page_name in the local Subjects cache to get its Notion page ID.
  3. Scan the local Synced Extra cache for entries whose Subject relation
     contains that page ID and whose Subtag matches raw_subtag.
  4. Collect matching Content values, deduplicate, join with <br> and write
     to Extra (Synced).

No live API calls — everything reads from the local JSON cache files.
"""

import re
import unicodedata
from typing import List, Optional, Dict

from .config import get_database_id, SYNCED_EXTRA_DATABASE_ID, DATABASE_PROPERTIES
from .tag_utils import parse_tag, normalize_subtag_for_matching

EXTRA_FIELD = "Extra (Synced)"
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


def _load_synced_extra_cache(notion_cache) -> List[Dict]:
    """Load all pages from the local Synced Extra cache."""
    try:
        pages, _ = notion_cache.load_from_cache(SYNCED_EXTRA_DATABASE_ID, warn_if_expired=False)
        return pages or []
    except Exception as e:
        print(f"[ExtraSync] Synced Extra cache error: {e}")
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

    # Sort longest first so "Diagnosis/Investigations" matches before "Diagnosis"
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
            # Skip one word and keep trying (handles unknown words)
            parts = remaining.split(' ', 1)
            remaining = parts[1].strip() if len(parts) > 1 else ''

    return found if found else [compound]


def _find_content_in_cache(se_pages: List[Dict], subject_page_id: str, raw_subtag: str) -> Optional[str]:
    """
    Find matching Content from Synced Extra cache pages.
    Matches on Subject relation containing subject_page_id AND Subtag covers raw_subtag.

    The SE Subtag field may be a compound value like "Clinical Features Management"
    meaning the entry applies to cards with either subtag.
    """
    if not raw_subtag or raw_subtag.startswith('*'):
        normalised_subtag = "Main Tag"
    else:
        normalised_subtag = normalize_subtag_for_matching(raw_subtag, _SUBJECTS_SUBTAGS) or raw_subtag

    nl = normalised_subtag.lower().strip()

    # Normalise the subject_page_id format (Notion sometimes returns with/without dashes)
    target_id = subject_page_id.replace('-', '')

    for page in se_pages:
        props = page.get('properties', {})

        # Check Subject relation
        relation_ids = [
            r.get('id', '').replace('-', '')
            for r in props.get('Subject', {}).get('relation', [])
        ]
        if target_id not in relation_ids:
            continue

        # Check Subtag match — supports compound subtags like "Clinical Features Management"
        raw_se_subtag = (props.get('Subtag', {}).get('select') or {}).get('name', '').strip()
        parsed_subtags = [s.lower() for s in _parse_compound_subtag(raw_se_subtag)]

        if nl not in parsed_subtags:
            continue

        # Prefer block HTML (WYSIWYG page body) over Content property
        content = page.get('_block_html', '').strip()
        if not content:
            content_segs = props.get('Content', {}).get('rich_text', [])
            content = ''.join(seg.get('plain_text', '') for seg in content_segs).strip()
        if content:
            return content

    return None


# ── Public API ────────────────────────────────────────────────────────────────

def build_extra_synced_content(tags: List[str], notion_cache) -> str:
    """
    Build the content for 'Extra (Synced)' from a list of Anki tags.
    Reads entirely from local cache — no network calls.
    Deduplicates content so identical entries are never added twice.
    """
    subject_tags = [t for t in tags if t.startswith("#Malleus_CM::#Subjects::")]
    if not subject_tags:
        return ""

    # Load Synced Extra cache once
    se_pages = _load_synced_extra_cache(notion_cache)
    if not se_pages:
        print(f"[ExtraSync] Synced Extra cache is empty — run 'Update Database Cache'")
        return ""

    seen_content = []   # ordered, deduped list of content strings
    seen_page_ids = {}  # page_name → page_id (avoid redundant cache lookups)

    for tag in subject_tags:
        parsed = parse_tag(tag)
        if not parsed:
            continue
        page_name  = parsed.get('page_name')
        raw_subtag = parsed.get('subtag')
        if not page_name:
            continue

        # Look up subject page ID (cache result per page_name)
        if page_name not in seen_page_ids:
            seen_page_ids[page_name] = _find_subject_page_id(notion_cache, page_name)
        page_id = seen_page_ids[page_name]

        if not page_id:
            print(f"[ExtraSync] Subject page not found in cache: {page_name!r}")
            continue

        print(f"[ExtraSync] {page_name!r} ({page_id}) subtag={raw_subtag!r}")
        content = _find_content_in_cache(se_pages, page_id, raw_subtag)
        print(f"[ExtraSync] matched: {content!r}")

        # Deduplicate
        if content and content not in seen_content:
            seen_content.append(content)

    return "<br>\n".join(seen_content)


def set_extra_synced_on_note(anki_note, notion_cache) -> bool:
    """
    Set anki_note['Extra (Synced)'] from its current .tags.
    Call AFTER note.tags is set, BEFORE note.flush().
    """
    try:
        _ = anki_note[EXTRA_FIELD]
    except Exception:
        print(f"[ExtraSync] Field '{EXTRA_FIELD}' not found on note type — skipping")
        return False

    new_content = build_extra_synced_content(list(anki_note.tags), notion_cache)
    print(f"[ExtraSync] set_extra_synced_on_note → {new_content!r}")
    if anki_note[EXTRA_FIELD] == new_content:
        return False

    anki_note[EXTRA_FIELD] = new_content
    return True
