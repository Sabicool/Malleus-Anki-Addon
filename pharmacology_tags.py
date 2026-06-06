"""
Pharmacology tag generation from the Notion `Parent item` graph + eMedici relation.

Like subjects_tags.py: replaces the stripped Notion formulas (GP/GGP chain,
Hierachy, per-subtag Tag formulas, Search Term/Suffix/Prefix, For Search,
Grandchild, Subtag hierachies, eMedici rollups) with a Python build-time
generator that traverses the relation graph itself.  Injects the SAME property
names the old formulas produced, so the add-on is unchanged and switching back to
the original Pharmacology database is just a database-id change.

Dependency-free (no aqt).

Pharmacology specifics (vs Subjects):
  * Searchable set = leaf drugs OR one-level-above-leaf categories
    (`For Search` = empty(Sub-item) OR Grandchild==0).
  * Category pages (have Sub-item, no grandchildren) roll DOWN: their subtag
    tags are their children's base tags + "::"+subtag.
  * Three Search Prefixes: ℹ️ (*General), 🧪 (category w/ Sub-item), 💊 (leaf).
  * Search Term keeps the raw hierarchy (→, numbers); Search Suffix = top category.
  * eMedici gated by the Question Bank `Pharm Subtag` (per-QB, + ancestors).
  * No Main Tag property — general (ℹ️) pages fall back to base `Tag`.

Out of scope for now: rotation (disabled in the source formula), related-subject
(handled in the add-on as separate selectable rows), SE/SA.
"""
import re
from typing import Dict, List

try:  # standalone (CI) vs add-on package context
    from hierarchy_tags import build_index, enumerate_paths, page_name, _norm_id
except ImportError:
    from .hierarchy_tags import build_index, enumerate_paths, page_name, _norm_id

PREFIX = ["#Malleus_CM", "#Pharmacology"]

# config.DATABASE_PROPERTIES["Pharmacology"] order -> NN_Suffix
SUBTAGS = ["Generic Names", "Mechanism of Action", "Indications",
           "Contraindications/Precautions", "Route/Frequency", "Adverse Effects",
           "Toxicity & Reversal", "Advantages/Disadvantages", "Monitoring"]
SUBTAG_SUFFIX = {name: f"{i:02d}_" + name.replace(" ", "_")
                 for i, name in enumerate(SUBTAGS, start=1)}


# ── accessors ────────────────────────────────────────────────────────────────
def _rich(page, key):
    v = page.get("properties", {}).get(key, {})
    t = v.get("type")
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in v.get("rich_text", []))
    if t == "formula":
        return v.get("formula", {}).get("string", "") or ""
    return ""


def _relation_ids(page, key):
    return [r["id"] for r in page.get("properties", {}).get(key, {}).get("relation", [])]


def _multi_select(page, key):
    v = page.get("properties", {}).get(key, {})
    return [x["name"] for x in v.get("multi_select", [])] if v.get("type") == "multi_select" else []


def _formula_prop(string):
    return {"type": "formula", "formula": {"type": "string", "string": string}}


def normalize_segment(name):
    # Curly -> straight apostrophes (consistent with Subjects; user preference).
    name = name.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    return re.sub(r"\s+", " ", name).strip().replace(" ", "_")


def sub_items(page):
    return _relation_ids(page, "Sub-item")


def base_tags(page, index):
    out, seen = [], set()
    for path in enumerate_paths(page, index):
        tag = "::".join(PREFIX + [normalize_segment(page_name(p)) for p in path])
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def _children(page, index):
    out = []
    for cid in sub_items(page):
        c = index.get(cid) or index.get(_norm_id(cid))
        if c is not None:
            out.append(c)
    return out


def grandchild_count(page, index):
    """Number of grandchildren = sum over children of their child-count."""
    return sum(len(sub_items(c)) for c in _children(page, index))


def is_for_search(page, index):
    return (not sub_items(page)) or grandchild_count(page, index) == 0


def _ancestors(page, index):
    seen, out = set(), []
    for path in enumerate_paths(page, index):
        for node in path[:-1]:
            nid = _norm_id(node["id"])
            if nid not in seen:
                seen.add(nid)
                out.append(node)
    return out


def _hierarchy_has_general(page, index):
    # Mirrors `Hierachy.contains("*General")` — self-inclusive (used for the
    # subtag general-skip).
    for path in enumerate_paths(page, index):
        if any("*General" in page_name(n) for n in path):
            return True
    return False


def _general_in_ancestors(page, index):
    # Mirrors `Parent Hierachy.contains("*General")` — ANCESTORS only (used for
    # Search Prefix / Suffix, so the *General root itself is 🧪 not ℹ️).
    for path in enumerate_paths(page, index):
        if any("*General" in page_name(n) for n in path[:-1]):
            return True
    return False


# ── eMedici (gated by Question Bank Pharm Subtag) ────────────────────────────
def _emedici_links(page, index, qb_lookup):
    qb_ids = set(_relation_ids(page, "eMedici"))
    for anc in _ancestors(page, index):
        qb_ids |= set(_relation_ids(anc, "eMedici"))
    out = []
    for qid in qb_ids:
        q = qb_lookup.get(qid) or qb_lookup.get(_norm_id(qid))
        if not q:
            continue
        tag = _rich(q, "Tag").strip()
        if tag:
            out.append((tag, _multi_select(q, "Pharm Subtag")))
    return out


def _emedici_for_suffix(links, suffix):
    tags, seen = [], set()
    for tag, subs in links:
        if any(s.replace(" ", "_") in suffix for s in subs):
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tags


# ── search properties ────────────────────────────────────────────────────────
def search_term(page, index):
    # Mirrors `Parent Hierachy.map(current + " → " + Name)` — root pages have an
    # empty Parent Hierachy, so they contribute no entry (Search Term = alias only).
    nm = page_name(page)
    entries = []
    for path in enumerate_paths(page, index):
        if len(path) < 2:
            continue
        entries.append("#Pharmacology → " + " → ".join(
            page_name(a) for a in path[:-1]) + " → " + nm)
    alias = _rich(page, "Search Alias")
    return " ".join(entries + ([alias] if alias else []))


def search_suffix(page, index):
    # `Parent Hierachy.map(at(split(" → "),1)).join("/")` — index-1 of each parent
    # hierarchy == the top category (== path[0] for non-roots); no de-dup; roots empty.
    tops = [page_name(path[0]) for path in enumerate_paths(page, index) if len(path) >= 2]
    joined = "/".join(tops)
    if joined == "*General":
        return "General Pharmacology"
    suffix = " - General" if _general_in_ancestors(page, index) else ""
    return joined + suffix


def search_prefix(page, index):
    if _general_in_ancestors(page, index):
        return "ℹ️"
    return "🧪" if sub_items(page) else "\U0001f48a"   # 💊


# ── main entry point ─────────────────────────────────────────────────────────
def _index_by_id(pages):
    d = {}
    for p in pages:
        d[p["id"]] = p
        d[_norm_id(p["id"])] = p
    return d


def generate_and_inject(all_pages: List[dict], qb_pages: List[dict]) -> List[dict]:
    """
    Inject generated tag/search properties into the For-Search pages (leaf drugs
    + one-level-above-leaf categories) and return that set (the add-on cache).
    """
    index = build_index(all_pages)
    qb_lookup = _index_by_id(qb_pages)

    searchable = [p for p in all_pages if is_for_search(p, index)]

    for page in searchable:
        base = base_tags(page, index)
        links = _emedici_links(page, index, qb_lookup)
        general = _hierarchy_has_general(page, index)
        leaf = not sub_items(page)
        child_bases = [] if leaf else [bt for c in _children(page, index)
                                       for bt in base_tags(c, index)]

        props = page.setdefault("properties", {})
        props["Tag"] = _formula_prop(" ".join(base))
        props["Search Term"] = _formula_prop(search_term(page, index))
        props["Search Suffix"] = _formula_prop(search_suffix(page, index))
        props["Search Prefix"] = _formula_prop(search_prefix(page, index))

        for human, suffix in SUBTAG_SUFFIX.items():
            if general:
                props[human] = _formula_prop("")
                continue
            roots = base if leaf else child_bases
            tokens = [t + "::" + suffix for t in roots] + _emedici_for_suffix(links, suffix)
            # de-dupe preserve order
            seen, out = set(), []
            for t in tokens:
                if t not in seen:
                    seen.add(t)
                    out.append(t)
            props[human] = _formula_prop(" ".join(out))

    return searchable
