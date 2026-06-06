"""
Guidelines tag/search generation from the `Parent item` graph.

Like subjects_tags / pharmacology_tags, this replaces the Notion formulas (the
GP/GGP chain, Hierarchy, Tag, Search Term/Suffix, Source, For Search) with a
Python build-time generator so those properties can be deleted.  Injects the same
property names the add-on reads.

Generated per For-Search page (a page where Sub-item-empty XOR Search Override):
    Tag           #Malleus_CM::#Guidelines::… hierarchy tag(s) (multi-parent)
    Search Term   key-ancestors (org under National, else immediate parent) + Name
    Search Suffix (Extra Suffix + " ") + ", ".join(org abbreviations)
    Source        "<seg3> Guideline: <seg4..>. Last updated <yr>. Accessed <date>. Available at <URL>"

Linked Rotation/Subjects relations are preserved (used at runtime).  Dependency-free.
"""
import re
import datetime

try:  # standalone (CI) vs add-on package context
    from hierarchy_tags import (build_index, enumerate_paths, page_name,
                                tags_for_page, GUIDELINES_PREFIX_SEGMENTS)
except ImportError:
    from .hierarchy_tags import (build_index, enumerate_paths, page_name,
                                 tags_for_page, GUIDELINES_PREFIX_SEGMENTS)

PREFIX = GUIDELINES_PREFIX_SEGMENTS   # ['#Malleus_CM', '#Guidelines']


# ── accessors ────────────────────────────────────────────────────────────────
def _rich(p, k):
    v = p.get("properties", {}).get(k, {})
    return "".join(x.get("plain_text", "") for x in v.get("rich_text", [])) \
        if v.get("type") == "rich_text" else ""


def _checkbox(p, k):
    v = p.get("properties", {}).get(k, {})
    return bool(v.get("checkbox")) if v.get("type") == "checkbox" else False


def _date_start(p, k):
    v = p.get("properties", {}).get(k, {})
    return ((v.get("date") or {}).get("start") or "") if v.get("type") == "date" else ""


def _sub_items(p):
    return [r["id"] for r in p.get("properties", {}).get("Sub-item", {}).get("relation", [])]


def _formula_prop(s):
    return {"type": "formula", "formula": {"type": "string", "string": s}}


def _paths(page, index):
    """Each BFS path as full segments: [#Malleus_CM, #Guidelines, country, …, leaf]."""
    return [PREFIX + [page_name(n) for n in path] for path in enumerate_paths(page, index)]


# ── For Search ───────────────────────────────────────────────────────────────
def for_search(page):
    empty = not _sub_items(page)
    return (not empty) if _checkbox(page, "Search Override") else empty


# ── Search Term / Suffix ─────────────────────────────────────────────────────
def _key_org(full):
    """The 'key ancestor' for a path: the org under National, else the immediate
    parent; nothing when the page sits directly under a country."""
    anc = full[2:-1]                    # country … immediate parent
    if len(anc) <= 1:
        return None
    if "National" in anc:
        i = anc.index("National") + 1
        return anc[i] if i < len(anc) else None
    return anc[-1]


def search_term(page, index):
    keys = []
    for full in _paths(page, index):
        k = _key_org(full)
        if k and k not in keys:
            keys.append(k)
    return re.sub(r"\s+", " ", " ".join(keys) + " " + page_name(page)).strip()


def _org_abbreviations(page, index):
    out = []
    for full in _paths(page, index):
        k = _key_org(full)
        if k:
            ab = k.split(" (")[0]
            if ab not in out:
                out.append(ab)
    return out


def search_suffix(page, index):
    if not for_search(page):
        return ""
    es = _rich(page, "Extra Suffix")
    return (es + " " if es else "") + ", ".join(_org_abbreviations(page, index))


# ── Source ───────────────────────────────────────────────────────────────────
def source(page, index, accessed):
    ps = _paths(page, index)
    if not ps:
        return ""
    first = ps[0]
    part1 = first[3] if len(first) > 3 else (first[-1] if first else "")
    body = ". ".join(first[4:])
    src = part1 + " Guideline: " + body
    lu = _date_start(page, "Last Updated")
    if lu:
        src += ". Last updated " + lu[:4]
    src += ". Accessed " + accessed
    url = _rich(page, "URL")
    if url:
        src += ". Available at " + url
    return src


def _accessed_today() -> str:
    d = datetime.date.today()
    return f"{d.strftime('%b')} {d.day}, {d.year}"   # e.g. "Jun 2, 2026"


# ── main entry point ─────────────────────────────────────────────────────────
def generate_and_inject(all_pages: list) -> list:
    index = build_index(all_pages)
    accessed = _accessed_today()
    leaves = [p for p in all_pages if for_search(p)]

    for page in leaves:
        props = page.setdefault("properties", {})
        props["Tag"] = _formula_prop(" ".join(tags_for_page(page, index, PREFIX)))
        props["Search Term"] = _formula_prop(search_term(page, index))
        props["Search Suffix"] = _formula_prop(search_suffix(page, index))
        props["Source"] = _formula_prop(source(page, index, accessed))

    return leaves
