"""
Configuration management for Malleus Addon
"""
from aqt import mw

# Hard coded environment variables
# Subjects database switch-point (keep in sync with update_cache.py).
# Tags/search are generated locally (subjects_tags.py) from the relation graph for
# whichever id is active — so the active DB's Notion formulas can be deleted.
# _TESTING is the old duplicated copy, kept only as a fallback.
SUBJECT_DATABASE_ID_ORIGINAL = '2674b67cbdf84a11a057a29cc24c524f'
SUBJECT_DATABASE_ID_TESTING  = '3755964e68a480d29cc3e3ddf808344c'
SUBJECT_DATABASE_ID = SUBJECT_DATABASE_ID_ORIGINAL   # active database (generated)
SYNCED_EXTRA_DATABASE_ID = '2dc5964e68a480909c4ac1dc169b16fb'
SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID = '31b5964e68a48023b1c1c7b23fbdec64'
# Pharmacology database switch-point (keep in sync with update_cache.py).
PHARMACOLOGY_DATABASE_ID_ORIGINAL = '9ff96451736d43909d49e3b9d60971f8'
PHARMACOLOGY_DATABASE_ID_TESTING  = '3765964e68a480b38067c6e19c08cff4'
PHARMACOLOGY_DATABASE_ID = PHARMACOLOGY_DATABASE_ID_ORIGINAL   # active database (generated)
ETG_DATABASE_ID = '22282971487f4f559dce199476709b03'
ROTATION_DATABASE_ID = '69b3e7fdce1548438b26849466d7c18e'
TEXTBOOKS_DATABASE_ID = '13d5964e68a480bfb07cf7e2f1786075'
GUIDELINES_DATABASE_ID = '13d5964e68a48056b40de8148dd91a06'
QUESTION_BANKS_DATABASE_ID = 'bf443eb7144c46aba3106a4b915959d7'   # eMedici etc.

# Databases whose cache is generated locally (in Python) from the relation graph
# rather than trusting Notion formulas.  For these, "update" = a FULL fetch from
# Notion + regeneration (not an incremental fetch or GitHub download), because an
# incremental fetch would re-pull the raw/stripped pages and clobber the cache.
# Each entry: kind + the extra databases the generator needs.
GENERATED_DATABASES = {
    SUBJECT_DATABASE_ID: {
        'kind': 'subjects',
        'qb': QUESTION_BANKS_DATABASE_ID,
        'rotation': ROTATION_DATABASE_ID,
    },
    PHARMACOLOGY_DATABASE_ID: {
        'kind': 'pharmacology',
        'qb': QUESTION_BANKS_DATABASE_ID,
    },
    GUIDELINES_DATABASE_ID: {
        'kind': 'guidelines',
    },
}

# Databases that still have a "For Search" formula property, so the runtime
# incremental sync can pre-filter on it.  Others (the locally-generated DBs and the
# synced DBs) lack it — filtering them would 400, so we don't try.
FOR_SEARCH_DATABASES = {
    ETG_DATABASE_ID,
    ROTATION_DATABASE_ID,
    TEXTBOOKS_DATABASE_ID,
}

# List of all databases with their IDs and names
DATABASES = [
    (SUBJECT_DATABASE_ID, "Subjects"),
    (PHARMACOLOGY_DATABASE_ID, "Pharmacology"),
    (ETG_DATABASE_ID, "eTG"),
    (ROTATION_DATABASE_ID, "Rotation"),
    (TEXTBOOKS_DATABASE_ID, "Textbooks"),
    (GUIDELINES_DATABASE_ID, "Guidelines"),
    (SYNCED_EXTRA_DATABASE_ID, "Synced Extra"),
    (SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID, "Synced Additional Resources"),
]

# Database properties for each database type
DATABASE_PROPERTIES = {
    "Subjects": [
        "",
        "Epidemiology",
        "Aetiology",
        "Risk Factors",
        "Physiology/Anatomy",
        "Pathophysiology",
        "Clinical Features",
        "Pathology",
        "Diagnosis/Investigations",
        "Scoring Criteria",
        "Management",
        "Complications/Prognosis",
        "Screening/Prevention"
    ],
    "Pharmacology": [
        "",
        "Generic Names",
        "Mechanism of Action",
        "Indications",
        "Contraindications/Precautions",
        "Route/Frequency",
        "Adverse Effects",
        "Toxicity & Reversal",
        "Advantages/Disadvantages",
        "Monitoring"
    ],
    "eTG": [
        "",
        "Epidemiology",
        "Aetiology",
        "Risk Factors",
        "Physiology/Anatomy",
        "Pathophysiology",
        "Clinical Features",
        "Pathology",
        "Diagnosis/Investigations",
        "Scoring Criteria",
        "Management",
        "Complications/Prognosis",
        "Screening/Prevention",
        "Generic Names",
        "Mechanism of Action",
        "Indications",
        "Contraindications/Precautions",
        "Route/Frequency",
        "Adverse Effects",
        "Toxicity & Reversal",
        "Advantages/Disadvantages",
        "Monitoring"
    ],
    "Rotation": [""],
    "Textbooks": [""],
    "Guidelines": [""]
}

def load_config():
    """Load configuration and set defaults if needed"""
    config = mw.addonManager.getConfig(__name__.split('.')[0])
    
    # Set defaults if not present
    if 'shortcut' not in config:
        config['shortcut'] = 'Ctrl+Alt+M'
        mw.addonManager.writeConfig(__name__.split('.')[0], config)
    
    if 'cache_expiry' not in config:
        config['cache_expiry'] = 1  # days (matches the daily GitHub rebuild)
        config['_expiry_default_migrated'] = True
        mw.addonManager.writeConfig(__name__.split('.')[0], config)
    elif config['cache_expiry'] == 7 and not config.get('_expiry_default_migrated'):
        # One-time migration: the old default was 7 days, predating the daily build.
        config['cache_expiry'] = 1
        config['_expiry_default_migrated'] = True
        mw.addonManager.writeConfig(__name__.split('.')[0], config)
    
    if 'autosearch' not in config:
        config['autosearch'] = True
        mw.addonManager.writeConfig(__name__.split('.')[0], config)
    
    if 'search_delay' not in config:
        config['search_delay'] = 300  # milliseconds
        mw.addonManager.writeConfig(__name__.split('.')[0], config)
    
    if 'deck_name' not in config:
        config['deck_name'] = 'Default'
        mw.addonManager.writeConfig(__name__.split('.')[0], config)
    
    if 'request_timeout' not in config:
        config['request_timeout'] = 30  # seconds - increased from 10
        mw.addonManager.writeConfig(__name__.split('.')[0], config)

    if 'show_card_counts' not in config:
        config['show_card_counts'] = False  # per-result note counts are opt-in (slow on big collections)
        mw.addonManager.writeConfig(__name__.split('.')[0], config)

    if 'card_count_threshold' not in config:
        config['card_count_threshold'] = 10  # show card counts only when results ≤ this
        mw.addonManager.writeConfig(__name__.split('.')[0], config)

    return config

def get_database_id(database_name):
    """Get database ID from database name"""
    database_map = {
        "Subjects": SUBJECT_DATABASE_ID,
        "Pharmacology": PHARMACOLOGY_DATABASE_ID,
        "eTG": ETG_DATABASE_ID,
        "Rotation": ROTATION_DATABASE_ID,
        "Textbooks": TEXTBOOKS_DATABASE_ID,
        "Guidelines": GUIDELINES_DATABASE_ID
    }
    return database_map.get(database_name)

def get_database_name(database_id):
    """Get database name from database ID"""
    database_names = {
        SUBJECT_DATABASE_ID: "Subjects",
        PHARMACOLOGY_DATABASE_ID: "Pharmacology",
        ETG_DATABASE_ID: "eTG",
        ROTATION_DATABASE_ID: "Rotation",
        TEXTBOOKS_DATABASE_ID: "Textbooks",
        GUIDELINES_DATABASE_ID: "Guidelines"
    }
    return database_names.get(database_id, "Unknown Database")
