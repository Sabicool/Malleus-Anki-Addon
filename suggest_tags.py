"""
suggest_tags.py
===============
Locally suggests Malleus Subjects tags (and a relevant subtag) based on
card text content.  No AI or network required.

Root causes addressed in this version
--------------------------------------

PROBLEM 1 — Long/mnemonic cloze answers pollute Stage 1 search
    Cards like the Sepsis bundle have cloze answers that are management steps
    ("O2 (aim to keep saturations >94% ...COPD patients)") or mnemonics
    ("FOCCAL(S)").  Feeding these to filter_pages causes COPD, Blood Products
    etc. to rank above Sepsis.
    FIX: _is_useful_cloze_query() adds multi-layer filtering: word count cap,
    management action word detection, mnemonic detection, time expression
    detection, and a minimum meaningful-word requirement.

PROBLEM 2 — Topic lives in the question stem, not the cloze
    "What is the treatment of nonsevere pyelonephritis" → "pyelonephritis" 
    is in the stem.  "The sepsis bundle of care...within sepsis recognition"
    → "sepsis" appears twice in the stem.
    FIX: Two complementary extractors:
      - extract_topic_phrases(): regex "of [X]" patterns (capped at 4 words)
      - stem_frequency_topics(): words appearing 2+ times in the stem are 
        almost certainly the topic; also catches medical-suffix words.

PROBLEM 3 — "of X" regex matched entire sentence
    "bundle of care identifies...can be remembered with the mnemonic" was
    matched as one giant phrase.
    FIX: limit the "of X" group to ≤4 words.

PROBLEM 4 — Over-broad shortlist from generic index tokens
    "Non-Small Cell Lung Cancer" → tokens 'non','small','cell' → all pages
    with any of these words ended up in the shortlist.
    FIX: INDEX_EXTRA_STOPWORDS blocks these from the inverted index.
"""

import re
from collections import Counter
from typing import Dict, List, Set, Tuple, Optional

from .config import get_database_id, DATABASE_PROPERTIES


# ── Tuning ─────────────────────────────────────────────────────────────────────
MIN_WORD_LEN        = 3
MAX_SUGGESTIONS     = 5
MIN_FINAL_SCORE     = 0.2
TOPIC_SEARCH_BONUS  = 4.0
WEIGHT_SINGLE_WORD  = 1.0
WEIGHT_BIGRAM       = 2.0
WEIGHT_CLOZE_BODY   = 1.5
BONUS_FULL_PHRASE   = 3.0
BONUS_MOST_WORDS    = 2.0
BONUS_HALF_WORDS    = 1.3


# ── Stopwords ──────────────────────────────────────────────────────────────────
STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "with", "this", "that",
    "from", "have", "has", "had", "not", "but", "what", "when", "where",
    "who", "which", "its", "into", "than", "then", "they", "them", "their",
    "can", "also", "may", "due", "via", "per", "new", "use", "used",
    "most", "more", "less", "after", "often", "risk", "high", "low",
    "first", "line", "type", "form", "show", "cause", "causes", "caused",
    "lead", "leads", "result", "results", "include", "includes",
    "patients", "patient", "history", "signs", "symptoms",
    "medication", "dose", "dosing", "drug",
    # Generic structural words
    "non", "cell", "small", "large", "acute", "chronic", "severe", "mild",
    "primary", "secondary", "general", "common", "rare", "multiple",
    "children", "child", "adult", "elderly", "age",
    # Question-stem scaffolding
    "empirical", "pharmacologic", "pharmacological", "treatment",
    "management", "describe", "explain", "list", "define",
    "how", "why",
}

INDEX_EXTRA_STOPWORDS = {
    "non", "cell", "small", "large", "mixed", "type", "stage",
    "related", "induced", "mediated", "associated", "based",
}

MEDICAL_SUFFIXES = (
    "itis", "osis", "emia", "aemia", "oma", "pathy", "plasty",
    "ectomy", "otomy", "ostomy", "scopy", "graphy", "lysis",
    "genic", "logic", "mania", "phobia", "plegia", "paresis",
    "uria", "rrhea", "rrhoea", "algia", "trophy",
)

# Medical suffix patterns used for disease-name detection
MEDICAL_SUFFIXES_DETECT = MEDICAL_SUFFIXES + ("sis", "genesis",)

ABBREVIATIONS: Dict[str, str] = {
    "mi":     "myocardial infarction",
    "stemi":  "myocardial infarction",
    "nstemi": "myocardial infarction",
    "af":     "atrial fibrillation",
    "hf":     "heart failure",
    "chf":    "heart failure",
    "pe":     "pulmonary embolism",
    "dvt":    "deep vein thrombosis",
    "uti":    "urinary tract infection",
    "urti":   "upper respiratory tract infection",
    "lrti":   "lower respiratory tract infection",
    "copd":   "chronic obstructive pulmonary",
    "dm":     "diabetes mellitus",
    "t1dm":   "type 1 diabetes",
    "t2dm":   "type 2 diabetes",
    "htn":    "hypertension",
    "cvd":    "cardiovascular disease",
    "cva":    "stroke cerebrovascular",
    "tia":    "transient ischaemic attack",
    "sah":    "subarachnoid haemorrhage",
    "ich":    "intracranial haemorrhage",
    "ibd":    "inflammatory bowel disease",
    "uc":     "ulcerative colitis",
    "ra":     "rheumatoid arthritis",
    "sle":    "systemic lupus erythematosus",
    "ckd":    "chronic kidney disease",
    "aki":    "acute kidney injury",
    "ms":     "multiple sclerosis",
    "pd":     "parkinson disease",
    "bph":    "benign prostatic hyperplasia",
    "hiv":    "human immunodeficiency virus",
    "tb":     "tuberculosis",
}


# ── Cloze answer quality filtering ────────────────────────────────────────────
_DRUG_ROUTES = {'po', 'iv', 'im', 'sc', 'sq', 'sl', 'pr', 'inh', 'top',
                'od', 'bd', 'tds', 'qid', 'nocte', 'mane', 'stat'}
_DRUG_SUFFIXES = (
    'cillin', 'mycin', 'oxazole', 'azole', 'floxacin', 'cycline',
    'olol', 'pril', 'sartan', 'statin', 'prazole', 'tidine', 'mab',
    'nib', 'parin', 'vir', 'mide', 'thiazide', 'dipine',
)
# Clinical action words — answers containing these are management steps, not
# disease names.  Keep conservative: don't block legitimate disease-name words.
_ACTION_WORDS = {
    'fluids', 'fluid', 'oxygen', 'cultures', 'culture', 'catheter',
    'antibiotics', 'antibiotic', 'lactate', 'monitor', 'saturations',
    'saturation', 'retention', 'spectrum', 'output', 'urine', 'blood',
    'sputum', 'faeces', 'feces', 'administer', 'maintain', 'aim', 'keep',
    'broad', 'usually', 'sets', 'dose', 'infuse', 'inject',
}


def _is_drug_answer(text: str) -> bool:
    """True if the cloze answer looks like a drug/treatment regimen."""
    lower = text.lower()
    words = re.sub(r'[^\w\s]', ' ', lower).split()
    if any(w in _DRUG_ROUTES for w in words):
        return True
    if '+' in text:
        return True
    if ' or ' in lower or re.search(r'\bor\b', lower):
        return True
    if re.search(r'\d+\s*(mg|mcg|g|ml|mmol|units?)', lower):
        return True
    if any(lower.rstrip().endswith(s) or f' {s}' in lower for s in _DRUG_SUFFIXES):
        return True
    return False


def _is_useful_cloze_query(answer: str) -> bool:
    """
    Multi-layer filter.  Returns True only when the cloze answer looks like
    a disease, condition, or organism name suitable for a Subjects search.

    Rejects:
    - Drug regimens (via _is_drug_answer)
    - Mnemonics: all-caps short strings like "FOCCAL(S)", "LMNOP"
    - Time expressions: "1 hour of", "within 30 minutes"
    - Long management step descriptions (> 6 words)
    - Answers containing clinical action words (fluids, catheter, antibiotics…)
    - Answers with no meaningful (≥5 char, non-stopword) words
    """
    if _is_drug_answer(answer):
        return False

    clean = re.sub(r'[^\w\s]', ' ', answer).strip()
    words = clean.lower().split()

    if not words:
        return False

    # Mnemonics: short all-caps word optionally with bracketed suffix
    if re.match(r'^[A-Z]{2,12}(?:\s*\([A-Z0-9]+\))?$', answer.strip()):
        return False

    # Time expressions
    if re.match(r'^\d+\s*(hour|hr|min|second|day|week|month|year)', answer.lower()):
        return False
    if len(words) <= 3 and any(w in ('hour', 'hr', 'min', 'minutes', 'hours') for w in words):
        return False

    # Too long → probably a clinical description, not a disease name
    if len(words) > 6:
        return False

    # Contains management/clinical action words
    if any(w in _ACTION_WORDS for w in words):
        return False

    # Must have at least one meaningful word (≥5 chars, not a stopword)
    meaningful = [w for w in words if len(w) >= 5 and w not in STOPWORDS]
    if not meaningful:
        return False

    return True


# ── Stage 1: Topic extraction and search ──────────────────────────────────────

def _extract_cloze_answers(text: str) -> List[str]:
    return re.findall(r'\{\{c\d+::([^}:]+)(?:::[^}]*)?\}\}', text)


def _extract_question_stem(card_text: str) -> str:
    """Return card text with all cloze markers removed — the 'question frame'."""
    return re.sub(r'\{\{c\d+::[^}]*\}\}', ' ', card_text).strip()


def _extract_topic_phrases(question_stem: str) -> List[str]:
    """
    Extract disease/condition phrases from the question stem.

    Patterns:
      "of [topic]" — capped at 4 words to avoid matching entire sentences
      "diagnose [topic]"

    Leading modifier words (nonsevere, acute, chronic…) are stripped to
    produce a cleaner search query alongside the original.
    """
    phrases: List[str] = []

    # "of X" — limit to 4 words to prevent matching whole sentences
    m1 = re.findall(
        r'\bof\s+((?:\w+)(?:\s+\w+){0,3}?)(?=\s+in\b|\s+for\b|\s+with\b|\s+at\b|\?|$)',
        question_stem, re.I
    )
    phrases.extend(m1)

    # "diagnose/diagnosis [topic]"
    m2 = re.findall(
        r'\bdiagnos\w*\s+([\w\s]{3,40}?)(?:\?|$|\s+in\b|\s+with\b)',
        question_stem, re.I
    )
    phrases.extend(m2)

    MODIFIERS = {
        'nonsevere', 'severe', 'mild', 'moderate', 'acute', 'chronic',
        'primary', 'secondary', 'complicated', 'uncomplicated', 'recurrent',
        'bilateral', 'unilateral', 'new', 'early', 'late',
    }
    TRAILING_STRIP = re.compile(
        r'\s+(?:the|a|an|with|in|for|of|and|or|at)\s*$', re.I
    )

    results: List[str] = []
    for p in phrases:
        p = TRAILING_STRIP.sub('', p.strip()).strip()
        if len(p) < 3:
            continue
        if len(p) >= 5:
            results.append(p)
        # Also yield modifier-stripped version
        words = p.split()
        stripped = ' '.join(w for w in words if w.lower() not in MODIFIERS).strip()
        if stripped and stripped != p and len(stripped) >= 3:
            results.append(stripped)

    seen: Set[str] = set()
    return [r for r in results if not (r.lower() in seen or seen.add(r.lower()))]


def _stem_frequency_topics(question_stem: str) -> List[str]:
    """
    Find high-confidence topic words from the question stem by:
    1. Words that appear 2+ times in the stem → almost certainly the topic
       (e.g. "sepsis" appears twice in "The sepsis bundle... within sepsis recognition")
    2. Words with medical suffixes (-sis, -itis, -emia…) → likely disease names

    This catches cards where the topic isn't in a neat "of X" structure.
    """
    words = [
        w for w in re.sub(r'[^\w\s]', ' ', question_stem.lower()).split()
        if len(w) >= 4 and w not in STOPWORDS
    ]
    freq = Counter(words)
    topics: List[str] = []

    for word, count in freq.items():
        if count >= 2:
            topics.append(word)

    for word in set(words):
        if word not in topics:
            if any(word.endswith(s) for s in MEDICAL_SUFFIXES_DETECT):
                topics.append(word)

    return topics


def _topic_search_scores(
    card_text: str,
    all_pages: List[Dict],
    notion_cache,
) -> Dict[str, float]:
    """
    Stage 1: search for the card topic using:
      (a) topic phrases extracted from the question stem
      (b) high-frequency and medical-suffix words in the stem
      (c) cloze answers that pass the quality filter

    Runs filter_pages on ALL pages for maximum precision.
    Scores are multiplied by TOPIC_SEARCH_BONUS.
    """
    scores: Dict[str, float] = {}
    queries: List[str] = []

    stem = _extract_question_stem(card_text)
    queries.extend(_extract_topic_phrases(stem))
    queries.extend(_stem_frequency_topics(stem))

    for answer in _extract_cloze_answers(card_text):
        if _is_useful_cloze_query(answer):
            clean = re.sub(r'[^\w\s]', ' ', answer).strip()
            if len(clean.replace(' ', '')) >= 3:
                queries.append(clean)

    # Deduplicate
    seen: Set[str] = set()
    queries = [q for q in queries if not (q.lower() in seen or seen.add(q.lower()))]

    print(f"[SuggestTags] Stage 1 queries: {queries}")
    for query in queries:
        if len(query.replace(' ', '')) < 3:
            continue
        for page in notion_cache.filter_pages(all_pages, query):
            pid = page.get('id', '')
            if pid:
                s = page.get('_composite_score', 0.0) * TOPIC_SEARCH_BONUS
                scores[pid] = max(scores.get(pid, 0.0), s)

    print(f"[SuggestTags] Stage 1: {len(scores)} pages matched")
    return scores


# ── Stage 2: Inverted index + body candidate scoring ──────────────────────────

_index_cache: Optional[Dict[str, Set[str]]] = None
_index_page_count: int = 0


def _tokenise_for_index(text: str) -> Set[str]:
    index_stopwords = STOPWORDS | INDEX_EXTRA_STOPWORDS
    tokens: Set[str] = set()
    for word in re.sub(r'[^\w\s]', ' ', text.lower()).split():
        expanded = ABBREVIATIONS.get(word, word)
        for w in expanded.split():
            if len(w) < MIN_WORD_LEN or w in index_stopwords:
                continue
            tokens.add(w)
            if w.endswith('s') and not w.endswith('ss') and len(w) > 4:
                tokens.add(w[:-1])
            elif w.endswith('y') and len(w) > 4:
                tokens.add(w[:-1] + 'ies')
    return tokens


def _build_index(pages: List[Dict]) -> Dict[str, Set[str]]:
    index: Dict[str, Set[str]] = {}
    for page in pages:
        pid = page.get('id', '')
        if not pid:
            continue
        st = (page.get('properties', {})
                  .get('Search Term', {})
                  .get('formula', {})
                  .get('string', ''))
        if not st:
            try:
                tl = page['properties']['Name']['title']
                st = tl[0]['text']['content'] if tl else ''
            except Exception:
                continue
        for token in _tokenise_for_index(st):
            index.setdefault(token, set()).add(pid)
    return index


def _get_index_and_pages(notion_cache) -> Tuple[Dict[str, Set[str]], List[Dict]]:
    global _index_cache, _index_page_count
    database_id = get_database_id("Subjects")
    try:
        pages, _ = notion_cache.load_from_cache(database_id, warn_if_expired=False)
    except Exception:
        pages = []
    if not pages:
        return {}, []
    if _index_cache is None or len(pages) != _index_page_count:
        print(f"[SuggestTags] Building inverted index over {len(pages)} pages...")
        _index_cache = _build_index(pages)
        _index_page_count = len(pages)
        print(f"[SuggestTags] Index ready — {len(_index_cache)} unique tokens")
    return _index_cache, pages


def _is_worth_keeping(word: str) -> bool:
    if len(word) < MIN_WORD_LEN:
        return False
    if word in STOPWORDS:
        return any(word.endswith(s) for s in MEDICAL_SUFFIXES)
    return True


def _expand(word: str) -> List[str]:
    return [w for w in ABBREVIATIONS.get(word, word).split()
            if len(w) >= MIN_WORD_LEN]


def _body_candidates(card_text: str) -> List[Tuple[str, float]]:
    """Stage 2 body candidate extraction — skips drug-type cloze answers."""
    candidates: List[Tuple[str, float]] = []
    seen: Set[str] = set()

    def add(phrase: str, weight: float):
        if phrase not in seen:
            seen.add(phrase)
            candidates.append((phrase, weight))

    for answer in _extract_cloze_answers(card_text):
        if _is_drug_answer(answer):
            continue
        words_raw = re.sub(r'[^\w\s]', ' ', answer.lower()).split()
        kept = [w for raw in words_raw for w in _expand(raw) if _is_worth_keeping(w)]
        for w in kept:
            add(w, WEIGHT_CLOZE_BODY * WEIGHT_SINGLE_WORD)
        for i in range(len(kept) - 1):
            add(kept[i] + ' ' + kept[i + 1], WEIGHT_CLOZE_BODY * WEIGHT_BIGRAM)

    clean = re.sub(r'\{\{c\d+::([^}:]+)(?:::[^}]*)?\}\}', r'\1', card_text)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    words_raw = re.sub(r'[^\w\s]', ' ', clean.lower()).split()
    kept_body = [w for raw in words_raw for w in _expand(raw) if _is_worth_keeping(w)]

    for w in kept_body:
        add(w, WEIGHT_SINGLE_WORD)
    for i in range(len(kept_body) - 1):
        add(kept_body[i] + ' ' + kept_body[i + 1], WEIGHT_BIGRAM)

    return candidates


def _shortlist_and_score(
    candidates: List[Tuple[str, float]],
    index: Dict[str, Set[str]],
    page_by_id: Dict[str, Dict],
    notion_cache,
) -> Dict[str, float]:
    matched_ids: Set[str] = set()
    for phrase, _ in candidates:
        for word in phrase.split():
            matched_ids.update(index.get(word, set()))

    shortlist = [page_by_id[pid] for pid in matched_ids if pid in page_by_id]
    if not shortlist:
        return {}

    print(f"[SuggestTags] Stage 2 shortlist: {len(shortlist)} pages")
    scores: Dict[str, float] = {}
    for phrase, weight in candidates:
        for page in notion_cache.filter_pages(shortlist, phrase):
            pid = page.get('id', '')
            if pid:
                scores[pid] = (scores.get(pid, 0.0)
                               + page.get('_composite_score', 0.0) * weight)
    return scores


# ── Stage 3: Title match bonus ─────────────────────────────────────────────────

def _page_display_name(page: Dict) -> str:
    try:
        tl = page['properties']['Name']['title']
        return tl[0]['text']['content'] if tl else 'Untitled'
    except Exception:
        return 'Untitled'


def _apply_title_bonus(
    page_scores: Dict[str, float],
    page_by_id: Dict[str, Dict],
    clean_card_text: str,
) -> Dict[str, float]:
    boosted = dict(page_scores)
    text = clean_card_text.lower()

    for pid, score in page_scores.items():
        page = page_by_id.get(pid)
        if not page:
            continue
        title = re.sub(r'[^\w\s]', ' ', _page_display_name(page).lower())

        if title.strip() in text:
            boosted[pid] = score * BONUS_FULL_PHRASE
            continue

        title_words = [w for w in title.split()
                       if len(w) >= MIN_WORD_LEN and w not in STOPWORDS]
        if not title_words:
            continue

        hits = sum(1 for w in title_words if w in text)
        fraction = hits / len(title_words)
        if fraction >= 0.8:
            boosted[pid] = score * BONUS_MOST_WORDS
        elif fraction >= 0.5:
            boosted[pid] = score * BONUS_HALF_WORDS

    return boosted


# ── Stage 4: Subtag suggestion ─────────────────────────────────────────────────
# User-maintained keyword map.  Each entry maps a subtag name to substring
# keywords to look for in the cleaned card text.

SUBTAG_KEYWORDS: Dict[str, List[str]] = {
    "Management": [
        "manag", "treatment", "treat", "therapy", "therapeut", "prescrib",
        "antibiotic", "antiviral", "antifungal", "first line", "second line",
        "surgical", "procedure", "intervention", "admit", "resuscitat",
        "empirical", "analgesi", "transfus", "anticoagul", "thrombolys",
        "cardiovers", "ablat", "resect", "bypass", "transplant",
        "immunosuppress", "steroid", "corticosteroid",
        # Drug-context words that only appear in management cards
        "trimethoprim", "amoxicillin", "metronidazole", "cefalexin",
        # Bundle/protocol keywords
        "bundle", "protocol", "regimen", "guideline",
    ],
    "Clinical Features": [
        "present", "symptom", "clinical feature", "complain", "manifest",
        "classic", "typical", "triad", "sign of", "feature of",
        "fever", "pain", "dyspnoea", "dyspnea", "cough", "wheez",
        "oedema", "nausea", "vomit", "fatigue", "malaise",
        "palpitat", "syncope", "dizzi", "headache", "weakness",
        "swelling", "redness", "jaundice", "pallor", "rash",
        "itching", "pruritus", "numbness", "tingling",
        "appearance",
    ],
    "Diagnosis/Investigations": [
        "diagnos", "investigat", "imaging", "scan", "biopsy",
        "culture", "x-ray", "xray", "ct scan", "mri", "ultrasound",
        "ecg", "echo", "spirometr", "sensitivity", "specificity",
        "gold standard", "criteria", "blood test", "serology",
        "pcr", "urinalysis", "lumbar puncture", "endoscopy",
        "microscopy", "staining", "gram stain",
    ],
    "Complications/Prognosis": [
        "complication", "prognosis", "outcome", "mortality",
        "morbidity", "sequelae", "long term", "survival",
        "recurrence", "relapse", "remission",
    ],
    "Aetiology": [
        "caus", "aetiolog", "etiolog", "due to", "result from",
        "secondary to", "precipitat", "trigger", "organism",
        "bacteria", "virus", "fungus", "parasite", "pathogen",
    ],
    "Risk Factors": [
        "risk factor", "predispos", "increase risk", "vulnerable",
        "susceptib", "likelihood", "associated with", "history of",
    ],
    "Pathophysiology": [
        "pathophysiology", "mechanism", "cascade", "mediator",
        "receptor", "pathway", "downstream", "upstream",
        "inflamm", "autoimmun", "oxidativ", "fibrosis",
    ],
    "Epidemiology": [
        "epidemiology", "prevalence", "incidence", "rate", "population",
    ],
    "Pathology": [
        "pathology", "histolog", "microscop", "biopsy find",
        "gross appear", "cellular", "necrosis", "granuloma",
    ],
    "Screening/Prevention": [
        "screen", "prevent", "prophylax", "vaccine", "immunis",
        "immuniz", "reduce risk",
    ],
    "Scoring Criteria": [
        "score", "scoring", "criteria", "criterion", "scale",
        "wells", "curb", "glasgow", "apache", "sofa", "qsofa",
        "hasbled", "chads", "audit",
    ],
    "Physiology/Anatomy": [
        "anatomy", "anatomical", "structure", "physiolog",
        "normal function", "nerve supply", "blood supply", "embryolog",
    ],
}

SUBTAG_PRIORITY = [
    "Management", "Clinical Features", "Diagnosis/Investigations",
    "Aetiology", "Pathophysiology", "Risk Factors", "Epidemiology",
    "Complications/Prognosis", "Pathology", "Screening/Prevention",
    "Scoring Criteria", "Physiology/Anatomy",
]


def suggest_subtag(card_text: str) -> Optional[str]:
    """
    Return the most relevant Subjects subtag, or None.

    Scores each subtag by counting keyword substring hits in the cleaned
    card text (cloze markers stripped).  Ties broken by SUBTAG_PRIORITY.
    """
    clean = re.sub(r'\{\{c\d+::([^}:]+)(?:::[^}]*)?\}\}', r'\1', card_text)
    clean = re.sub(r'<[^>]+>', ' ', clean).lower()

    scores: Dict[str, int] = {}
    for subtag, keywords in SUBTAG_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in clean)
        if hits > 0:
            scores[subtag] = hits

    if not scores:
        return None

    max_hits = max(scores.values())
    tied = [s for s, h in scores.items() if h == max_hits]
    for preferred in SUBTAG_PRIORITY:
        if preferred in tied:
            return preferred
    return tied[0]


# ── Public API ─────────────────────────────────────────────────────────────────

def suggest_subject_tags(
    card_text: str,
    notion_cache,
    max_results: int = MAX_SUGGESTIONS,
) -> List[Dict]:
    """
    Return a ranked list of suggested Subjects pages.

    Each result dict:
        title            — human-readable page name
        page             — full cache page dict
        score            — accumulated weighted score
        suggested_subtag — e.g. "Management" or None
    """
    if not card_text or not card_text.strip():
        return []

    index, pages = _get_index_and_pages(notion_cache)
    if not pages:
        print("[SuggestTags] No pages in Subjects cache.")
        return []

    page_by_id: Dict[str, Dict] = {p.get('id', ''): p for p in pages}

    stage1 = _topic_search_scores(card_text, pages, notion_cache)
    candidates = _body_candidates(card_text)
    stage2 = _shortlist_and_score(candidates, index, page_by_id, notion_cache) if candidates else {}

    merged: Dict[str, float] = {}
    for pid, s in stage1.items():
        merged[pid] = merged.get(pid, 0.0) + s
    for pid, s in stage2.items():
        merged[pid] = merged.get(pid, 0.0) + s

    if not merged:
        return []

    clean_card = re.sub(r'<[^>]+>', ' ',
        re.sub(r'\{\{c\d+::([^}:]+)(?:::[^}]*)?\}\}', r'\1', card_text))
    merged = _apply_title_bonus(merged, page_by_id, clean_card)

    ranked = sorted(
        [(score, pid) for pid, score in merged.items() if score >= MIN_FINAL_SCORE],
        reverse=True,
    )

    subtag = suggest_subtag(card_text)

    results = []
    for score, pid in ranked[:max_results]:
        page = page_by_id.get(pid)
        if page:
            results.append({
                'title':            _page_display_name(page),
                'page':             page,
                'score':            round(score, 2),
                'suggested_subtag': subtag,
            })
    return results


def invalidate_index():
    """Force a full index rebuild on the next call. Call after cache update."""
    global _index_cache, _index_page_count
    _index_cache = None
    _index_page_count = 0
