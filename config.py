"""
Configuration management for Malleus Addon
"""
from aqt import mw

# Hard coded environment variables
SUBJECT_DATABASE_ID = '2674b67cbdf84a11a057a29cc24c524f'
PHARMACOLOGY_DATABASE_ID = '9ff96451736d43909d49e3b9d60971f8'
ETG_DATABASE_ID = '22282971487f4f559dce199476709b03'
ROTATION_DATABASE_ID = '69b3e7fdce1548438b26849466d7c18e'
TEXTBOOKS_DATABASE_ID = '13d5964e68a480bfb07cf7e2f1786075'
GUIDELINES_DATABASE_ID = '13d5964e68a48056b40de8148dd91a06'

# List of all databases with their IDs and names
DATABASES = [
    (SUBJECT_DATABASE_ID, "Subjects"),
    (PHARMACOLOGY_DATABASE_ID, "Pharmacology"),
    (ETG_DATABASE_ID, "eTG"),
    (ROTATION_DATABASE_ID, "Rotation"),
    (TEXTBOOKS_DATABASE_ID, "Textbooks"),
    (GUIDELINES_DATABASE_ID, "Guidelines")
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
        config['cache_expiry'] = 7  # days
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
