"""
Hierarchy tag generation from the Notion `Parent item` relation graph.

This replaces the brittle Notion `Tag` formula (which chained GP/GGP rollups and
silently dropped paths whenever an ancestor had >1 parent, and truncated any
name containing a comma).  Instead we traverse the `Parent item` graph directly,
enumerating *every* root-to-leaf path and building one fully-qualified Anki tag
per path.

The module is intentionally dependency-free (no `aqt`) so the cache builder
(`update_cache.py`) can import it in CI.

Currently wired up for the Guidelines database only; the traversal/normalisation
are generic so other databases can reuse it later via different prefix segments.

Segment normalisation:
    * curly apostrophes ’ ‘ ʼ  ->  straight '
    * commas      -> removed
    * colons      -> removed
    * & kept
    * whitespace collapsed, then spaces -> underscores
"""
import re
from typing import Dict, List

# The Guidelines DB has no literal #Malleus_CM / #Guidelines pages; its real
# roots are nodes like "Australia" / "International" / "New Zealand", so this
# prefix is prepended to every enumerated path.
GUIDELINES_PREFIX_SEGMENTS = ["#Malleus_CM", "#Guidelines"]


def _norm_id(page_id: str) -> str:
    """Notion IDs appear with and without dashes; normalise for lookups."""
    return page_id.replace("-", "")


def page_name(page: dict) -> str:
    # A Notion title can be split across several rich-text runs (e.g. when part
    # of it is a link), so concatenate every segment rather than just title[0].
    title = page.get("properties", {}).get("Name", {}).get("title", [])
    return "".join(seg.get("plain_text", "") for seg in title)


def parent_ids(page: dict) -> List[str]:
    rel = page.get("properties", {}).get("Parent item", {}).get("relation", [])
    return [r["id"] for r in rel]


def normalize_segment(name: str) -> str:
    """Convert a page Name into a single Anki tag segment."""
    # curly -> straight apostrophes
    name = name.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    # drop commas and colons
    name = name.replace(",", "").replace(":", "")
    # collapse runs of whitespace, trim, then spaces -> underscores
    name = re.sub(r"\s+", " ", name).strip()
    return name.replace(" ", "_")


def build_index(pages: List[dict]) -> Dict[str, dict]:
    """Map every page id (dash and dash-less form) -> page."""
    index: Dict[str, dict] = {}
    for p in pages:
        index[p["id"]] = p
        index[_norm_id(p["id"])] = p
    return index


def enumerate_paths(page: dict, index: Dict[str, dict], _stack: tuple = ()) -> List[List[dict]]:
    """
    Return every root-to-leaf path of page objects ending at `page`.

    Fans out across multiple parents.  Guards against cycles and parents that
    are absent from the index (returns the partial path rooted at the deepest
    resolvable ancestor).
    """
    pid = _norm_id(page["id"])
    if pid in _stack:  # cycle guard
        return [[page]]

    parents = []
    for raw in parent_ids(page):
        parent = index.get(raw) or index.get(_norm_id(raw))
        if parent is not None:
            parents.append(parent)

    if not parents:  # root
        return [[page]]

    paths: List[List[dict]] = []
    for parent in parents:
        for ppath in enumerate_paths(parent, index, _stack + (pid,)):
            paths.append(ppath + [page])
    return paths


def tags_for_page(page: dict, index: Dict[str, dict],
                  prefix_segments: List[str] = GUIDELINES_PREFIX_SEGMENTS) -> List[str]:
    """All fully-qualified hierarchy tags for one page (deduped, ordered)."""
    tags = []
    seen = set()
    for path in enumerate_paths(page, index):
        segments = list(prefix_segments) + [normalize_segment(page_name(p)) for p in path]
        tag = "::".join(segments)
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _tag_string(page: dict) -> str:
    prop = page.get("properties", {}).get("Tag", {})
    if prop.get("type") == "formula":
        return prop.get("formula", {}).get("string", "") or ""
    return ""


def inject_hierarchy_tags(leaf_pages: List[dict], index: Dict[str, dict],
                          prefix_segments: List[str] = GUIDELINES_PREFIX_SEGMENTS) -> int:
    """
    Rewrite each leaf page's `Tag` formula string in place to hold *only* the
    BFS-generated hierarchy tags for this database.

    The cross-reference tags (#Resources_by_Rotation:: / #Subjects::) are NOT
    stored here — they are generated at runtime in the page selector by
    following the page's `Related Rotation` / `Related Subjects` relations (the
    subject ones need an interactive subtag choice).  Storing only the hierarchy
    here keeps a single source of truth and avoids the stale / truncated
    cross-ref garbage the old Notion `Tag` formula produced.

    Returns the number of pages whose hierarchy tags differ from whatever the
    old formula had emitted (informational only).
    """
    prefix = "::".join(prefix_segments)              # e.g. "#Malleus_CM::#Guidelines"
    prefix_colon = prefix + "::"
    changed = 0

    for page in leaf_pages:
        bfs_tags = tags_for_page(page, index, prefix_segments)

        old_hierarchy = [t for t in _tag_string(page).split()
                         if t == prefix or t.startswith(prefix_colon)]
        if set(bfs_tags) != set(old_hierarchy):
            changed += 1

        page.setdefault("properties", {})
        existing = page["properties"].get("Tag", {})
        page["properties"]["Tag"] = {
            "id": existing.get("id", "tag"),
            "type": "formula",
            "formula": {"type": "string", "string": " ".join(bfs_tags)},
        }

    return changed
