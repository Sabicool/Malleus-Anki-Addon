"""
Subjects tag generation from the Notion `Parent item` graph + cross-ref relations.

Replaces the Notion-formula machinery (GP/GGP chain, Hierarchy, per-subtag Tag
formulas, Search Term/Suffix/Prefix) with a Python build-time generator that
traverses the relation graph itself — fixing multi-parent fan-out and removing
the heavy formulas/rollups that made the database slow to download.

It injects the SAME property names the old Notion formulas produced, so the Anki
add-on reads them unchanged.  That means switching back to the original Subjects
database (which still has the formulas) is just a database-ID change — the cache
shape is identical either way.

Dependency-free (no aqt) so the cache builder / CI can import it.

Generated per leaf page (a page with no `Sub-item`):
    Tag           base #Malleus_CM::#Subjects::… hierarchy tag(s)
    Main Tag      general/non-disease pages: base + rotation + all eMedici
    <12 subtags>  disease pages: base::NN_Subtag + rotation + subtag-gated eMedici
                  (Epidemiology … Screening/Prevention)
    Search Term / Search Suffix / Search Prefix

Out of scope (handled elsewhere / deferred): SE/SA (addon Synced-Extra feature),
OSCE tags, related-subject tags (feature removed).
"""
import re
from typing import Dict, List

try:  # standalone (CI) vs add-on package context
    from hierarchy_tags import build_index, enumerate_paths, page_name, _norm_id
except ImportError:
    from .hierarchy_tags import build_index, enumerate_paths, page_name, _norm_id

PREFIX = ["#Malleus_CM", "#Subjects"]
PREFIX_STR = "::".join(PREFIX)
ROT_PREFIX = "#Malleus_CM::#Resources_by_Rotation"

# Human subtag name (matches config.DATABASE_PROPERTIES["Subjects"]) -> NN_Suffix
SUBTAGS = ["Epidemiology", "Aetiology", "Risk Factors", "Physiology/Anatomy",
           "Pathophysiology", "Clinical Features", "Pathology",
           "Diagnosis/Investigations", "Scoring Criteria", "Management",
           "Complications/Prognosis", "Screening/Prevention"]
SUBTAG_SUFFIX = {name: f"{i:02d}_" + name.replace(" ", "_")
                 for i, name in enumerate(SUBTAGS, start=1)}


# ── small accessors ────────────────────────────────────────────────────────
def _rich(page: dict, key: str) -> str:
    v = page.get("properties", {}).get(key, {})
    t = v.get("type")
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in v.get("rich_text", []))
    if t == "title":
        return "".join(x.get("plain_text", "") for x in v.get("title", []))
    if t == "formula":
        return v.get("formula", {}).get("string", "") or ""
    return ""


def _checkbox(page: dict, key: str) -> bool:
    v = page.get("properties", {}).get(key, {})
    return bool(v.get("checkbox")) if v.get("type") == "checkbox" else False


def _relation_ids(page: dict, key: str) -> List[str]:
    return [r["id"] for r in page.get("properties", {}).get(key, {}).get("relation", [])]


def _select_name(page: dict, key: str):
    v = page.get("properties", {}).get(key, {})
    return (v.get("select") or {}).get("name") if v.get("type") == "select" else None


def _multi_select(page: dict, key: str) -> List[str]:
    v = page.get("properties", {}).get(key, {})
    return [x["name"] for x in v.get("multi_select", [])] if v.get("type") == "multi_select" else []


def _formula_prop(string: str) -> dict:
    """Build a Notion-formula-shaped property value (what the add-on expects)."""
    return {"type": "formula", "formula": {"type": "string", "string": string}}


# ── normalisation / structure ───────────────────────────────────────────────
def normalize_segment(name: str) -> str:
    """Subjects keep commas/colons/slashes/number-prefixes/& — only spaces->_
    and curly->straight apostrophes."""
    name = name.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    name = re.sub(r"\s+", " ", name).strip()
    return name.replace(" ", "_")


def sub_items(page: dict) -> List[str]:
    return _relation_ids(page, "Sub-item")


def parent_ids(page: dict) -> List[str]:
    return _relation_ids(page, "Parent item")


def base_tags(page: dict, index: Dict[str, dict]) -> List[str]:
    out, seen = [], set()
    for path in enumerate_paths(page, index):
        tag = "::".join(PREFIX + [normalize_segment(page_name(p)) for p in path])
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def _ancestors(page: dict, index: Dict[str, dict]) -> List[dict]:
    seen, out = set(), []
    for path in enumerate_paths(page, index):
        for node in path[:-1]:
            nid = _norm_id(node["id"])
            if nid not in seen:
                seen.add(nid)
                out.append(node)
    return out


# ── disease / general logic ──────────────────────────────────────────────────
def is_disease(page: dict, base: List[str]) -> bool:
    if sub_items(page):
        return False
    first2 = page_name(page)[:2]
    if "0" in first2 or "1" in first2:
        return False
    joined = " ".join(base)
    return "Procedures" not in joined and "*General" not in joined


def gets_subtags(page: dict, base: List[str]) -> bool:
    """True => disease page (gets the 12 subtag tags); False => Main Tag only."""
    checker = (not parent_ids(page)) or (not is_disease(page, base)) or ("*General" in page_name(page))
    return checker if _checkbox(page, "Subtag override") else (not checker)


# ── cross references ─────────────────────────────────────────────────────────
def rotation_tags(page: dict, index, rotation_lookup: Dict[str, dict]) -> List[str]:
    """All Relevant Rotations = direct + all ancestors' Rotation, minus the
    page's Remove Rotation Tags."""
    rot_ids = set(_relation_ids(page, "Rotation"))
    for anc in _ancestors(page, index):
        rot_ids |= set(_relation_ids(anc, "Rotation"))
    rot_ids -= set(_relation_ids(page, "Remove Rotation Tags"))
    tags, seen = [], set()
    for rid in rot_ids:
        rp = rotation_lookup.get(rid) or rotation_lookup.get(_norm_id(rid))
        if not rp:
            continue
        for t in _rich(rp, "Tag").split():
            if t not in seen:
                seen.add(t)
                tags.append(t)
    return tags


def _emedici_links(page: dict, index, qb_lookup: Dict[str, dict]):
    """(qb_tag, [disease_subtag names]) for the page + all ancestors (Parent eMedici)."""
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
            out.append((tag, _multi_select(q, "Disease Subtag")))
    return out


def _emedici_for_suffix(links, suffix):
    """suffix like '10_Management'; None => Main Tag (all eMedici tags)."""
    tags, seen = [], set()
    for tag, subs in links:
        if suffix is None or any(s.replace(" ", "_") in suffix for s in subs):
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tags


# ── search properties ────────────────────────────────────────────────────────
def _search_alias(page, index, memo, _stack=()):
    pid = _norm_id(page["id"])
    if pid in memo:
        return memo[pid]
    if pid in _stack:
        return ""
    parent_aliases = []
    for raw in parent_ids(page):
        par = index.get(raw) or index.get(_norm_id(raw))
        if par is not None:
            parent_aliases.append(_search_alias(par, index, memo, _stack + (pid,)))
    manual = _rich(page, "Manual search alias")
    passmed = _rich(page, "PassMed Included Topics").replace("\n", " ")
    seen, uniq = set(), []
    for it in parent_aliases + [manual, passmed]:
        if it not in seen:
            seen.add(it)
            uniq.append(it)
    memo[pid] = ",".join(uniq)
    return memo[pid]


def search_term(page, index, alias_memo) -> str:
    nm = page_name(page)
    entries = []
    for path in enumerate_paths(page, index):
        ph = "#Subjects → " + " → ".join(page_name(a) for a in path[:-1])
        e = re.sub(r"[0-9]", "", ph.replace(" → ", " ")) + " " + nm
        entries.append(e)
    s = " ".join(entries + [_search_alias(page, index, alias_memo)])
    s = s.replace("#Subjects", "").replace("  ", " ")[1:].replace(",", " ").replace("’", "'")
    return s


def _strip_digits(s):
    return re.sub(r"\d", "", s)


def search_suffix(page, index) -> str:
    par_names = [re.sub(r"^\s+", "", _strip_digits(page_name(index.get(r) or index.get(_norm_id(r)))))
                 for r in parent_ids(page)
                 if (index.get(r) or index.get(_norm_id(r)))]
    out = "/".join(par_names)
    paths = enumerate_paths(page, index)
    if "*General" in page_name(page):
        lasts = []
        for path in paths:
            if len(path) >= 2:
                seg = _strip_digits(page_name(path[-2]))
                if seg not in lasts:
                    lasts.append(seg)
        out += " - " + "".join(lasts)
    specialties = []
    for path in paths:
        sp = page_name(path[0])
        if sp not in specialties:
            specialties.append(sp)
    return out + " (" + "/".join(specialties) + ")"


def search_prefix(page, index) -> str:
    joined = "".join(page_name(a) for path in enumerate_paths(page, index) for a in path[:-1])
    base_general = "*General" in joined
    cond = (not base_general) if _checkbox(page, "Subtag override") else base_general
    emoji = "ℹ️" if cond else "\U0001fa7a"
    extra = _rich(page, "Extra Prefix")
    return emoji + ((" " + extra) if extra else "")


# ── main entry point ─────────────────────────────────────────────────────────
def _index_by_id(pages: List[dict]) -> Dict[str, dict]:
    d = {}
    for p in pages:
        d[p["id"]] = p
        d[_norm_id(p["id"])] = p
    return d


def generate_and_inject(all_pages: List[dict],
                        qb_pages: List[dict],
                        rotation_pages: List[dict]) -> List[dict]:
    """
    Compute every generated property and inject it (formula-shaped) into each
    leaf page.  Returns the list of leaf pages (those with no `Sub-item`) — the
    set the add-on cache should contain.
    """
    index = build_index(all_pages)
    qb_lookup = _index_by_id(qb_pages)
    rotation_lookup = _index_by_id(rotation_pages)
    alias_memo: Dict[str, str] = {}

    leaves = [p for p in all_pages if not sub_items(p)]

    for page in leaves:
        base = base_tags(page, index)
        rot = rotation_tags(page, index, rotation_lookup)
        links = _emedici_links(page, index, qb_lookup)
        disease = gets_subtags(page, base)

        props = page.setdefault("properties", {})
        props["Tag"] = _formula_prop(" ".join(base))
        props["Search Term"] = _formula_prop(search_term(page, index, alias_memo))
        props["Search Suffix"] = _formula_prop(search_suffix(page, index))
        props["Search Prefix"] = _formula_prop(search_prefix(page, index))

        if disease:
            props["Main Tag"] = _formula_prop("")
            for human, suffix in SUBTAG_SUFFIX.items():
                subject = [t + "::" + suffix for t in base]
                emed = _emedici_for_suffix(links, suffix)
                props[human] = _formula_prop(" ".join(subject + rot + emed))
        else:
            emed_all = _emedici_for_suffix(links, None)
            props["Main Tag"] = _formula_prop(" ".join(base + rot + emed_all))
            for human in SUBTAG_SUFFIX:
                props[human] = _formula_prop("")

    return leaves
