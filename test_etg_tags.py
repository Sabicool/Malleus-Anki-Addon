"""
Standalone test for eTG cross-database tag lookup logic.
Run from the repo root with:
    python test_etg_tags.py
No Anki installation required — reads directly from the cache/ JSON files.
"""
import json
from pathlib import Path

CACHE_DIR = Path("cache")
ETG_DB_ID    = '22282971487f4f559dce199476709b03'
SUBJECT_DB_ID = '2674b67cbdf84a11a057a29cc24c524f'
PHARM_DB_ID  = '9ff96451736d43909d49e3b9d60971f8'

SUBJECTS_SUBTAGS = {
    "Epidemiology", "Aetiology", "Risk Factors", "Physiology/Anatomy",
    "Pathophysiology", "Clinical Features", "Pathology", "Diagnosis/Investigations",
    "Scoring Criteria", "Management", "Complications/Prognosis", "Screening/Prevention",
}
PHARMACOLOGY_SUBTAGS = {
    "Generic Names", "Mechanism of Action", "Indications", "Contraindications/Precautions",
    "Route/Frequency", "Adverse Effects", "Toxicity & Reversal", "Advantages/Disadvantages",
    "Monitoring",
}


def load_cache(db_id):
    path = CACHE_DIR / f"{db_id}.json"
    with path.open() as f:
        return json.load(f)['pages']


def build_lookup(pages):
    lookup = {}
    for p in pages:
        lookup[p['id']] = p
        lookup[p['id'].replace('-', '')] = p
    return lookup


def get_formula_string(page, prop_name):
    prop = page.get('properties', {}).get(prop_name, {})
    if prop.get('type') == 'formula':
        return prop['formula'].get('string', '').strip()
    return ''


def get_etg_tags(page, property_name, subjects_lookup, pharm_lookup):
    tags = []

    # 1. Always add eTG Tag
    etg_tag = get_formula_string(page, 'Tag')
    if etg_tag:
        tags.extend(etg_tag.split())

    if not property_name or property_name in ('Tag', 'Main Tag'):
        return tags

    # 2. Subjects subtag
    if property_name in SUBJECTS_SUBTAGS:
        for rel in page['properties'].get('Subject', {}).get('relation', []):
            subj = subjects_lookup.get(rel['id'])
            if subj:
                val = get_formula_string(subj, property_name)
                if val:
                    tags.extend(val.split())
            else:
                print(f"  [WARN] Subject page not found: {rel['id']}")

    # 3. Pharmacology subtag
    elif property_name in PHARMACOLOGY_SUBTAGS:
        for rel in page['properties'].get('Pharmacology', {}).get('relation', []):
            pharm = pharm_lookup.get(rel['id'])
            if pharm:
                val = get_formula_string(pharm, property_name)
                if val:
                    tags.extend(val.split())
            else:
                print(f"  [WARN] Pharmacology page not found: {rel['id']}")

    return tags


def main():
    print("Loading caches...")
    etg_pages = load_cache(ETG_DB_ID)
    subjects_lookup = build_lookup(load_cache(SUBJECT_DB_ID))
    pharm_lookup = build_lookup(load_cache(PHARM_DB_ID))
    print(f"Loaded {len(etg_pages)} eTG pages, "
          f"{len(subjects_lookup)//2} Subject pages, "
          f"{len(pharm_lookup)//2} Pharmacology pages\n")

    # --- Test 1: eTG page with Subjects relation + Clinical Features ---
    print("=" * 60)
    print("TEST 1: Subjects subtag (Clinical Features)")
    print("=" * 60)
    for page in etg_pages:
        if page['properties'].get('Subject', {}).get('relation'):
            tags = get_etg_tags(page, 'Clinical Features', subjects_lookup, pharm_lookup)
            print(f"eTG page: {get_formula_string(page, 'Tag')}")
            print(f"Tags generated:")
            for t in tags:
                print(f"  {t}")
            break

    print()

    # --- Test 2: eTG page with Pharmacology relation + Indications ---
    print("=" * 60)
    print("TEST 2: Pharmacology subtag (Indications)")
    print("=" * 60)
    for page in etg_pages:
        if page['properties'].get('Pharmacology', {}).get('relation'):
            tags = get_etg_tags(page, 'Indications', subjects_lookup, pharm_lookup)
            print(f"eTG page: {get_formula_string(page, 'Tag')}")
            print(f"Tags generated:")
            for t in tags:
                print(f"  {t}")
            break

    print()

    # --- Test 3: No subtag selected (empty) — should return only eTG Tag ---
    print("=" * 60)
    print("TEST 3: No subtag (Tag only)")
    print("=" * 60)
    page = etg_pages[0]
    tags = get_etg_tags(page, 'Tag', subjects_lookup, pharm_lookup)
    print(f"eTG page: {get_formula_string(page, 'Tag')}")
    print(f"Tags generated:")
    for t in tags:
        print(f"  {t}")


if __name__ == '__main__':
    main()
