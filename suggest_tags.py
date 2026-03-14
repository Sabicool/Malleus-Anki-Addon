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
# Per-field weights for supplementary signals (fraction of primary weight).
# Extra:                stages 1–4  — rich clinical prose, high value
# Source:               stages 1–3  — URL titles/slugs, strong topic signal
# Additional Resources: stages 2–3  — reference list, weaker topic signal
EXTRA_WEIGHT        = 0.6
SOURCE_WEIGHT       = 0.5
ADDL_WEIGHT         = 0.2
# Cloze answers as Stage 1 topic queries: slightly lower than stem-extracted
# phrases.  Cards like "What effects are caused by ethylene glycol poisoning?
# → AKI" should rank the disease (stem) above the effect (cloze answer).
CLOZE_TOPIC_WEIGHT  = 0.65


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


# ── Source field preprocessing ────────────────────────────────────────────────

def _extract_source_topics(source_text: str) -> str:
    """
    Extract useful topic text from a Source field that may contain raw URLs.

    Two complementary signals:
    1. Non-URL descriptive text (the label the author typed next to the link).
    2. URL query-param values and path segments that look like page title slugs
       (hyphens/underscores converted to spaces).

    Example input:
        "eTG - Acute management of seizures and status epilepticus
         https://...viewTopic?guidelinePage=Neurology&topicfile=acute-management-...
         Published/Amended May 2022, Accessed 21 August 2025"

    Example output (roughly):
        "Neurology acute management of seizures and status epilepticus
         eTG - Acute management of seizures and status epilepticus"

    Noise stripped: bare domains, protocol prefixes, date strings.
    """
    from urllib.parse import urlparse, parse_qs

    URL_RE = re.compile(r'https?://\S+')
    DATE_RE = re.compile(
        r'\b(?:published|amended|accessed|updated|reviewed|cited)'
        r'[^,.\n]*(?:,\s*|\s+)(?:\d{1,2}\s+)?'
        r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*\d{4}\b',
        re.I
    )

    parts: list = []

    for m in URL_RE.finditer(source_text):
        url = m.group()
        try:
            parsed = urlparse(url)
            # Query-param values often carry page titles as slugs
            qs = parse_qs(parsed.query)
            for vals in qs.values():
                for val in vals:
                    words = re.sub(r'[-_]', ' ', val).strip()
                    if len(words) >= 5 and not re.match(r'^(true|false|\d+)$', words):
                        parts.append(words)
            # Path segments: only keep obvious slugs (contain hyphens, long enough)
            for seg in parsed.path.split('/'):
                if '-' in seg and len(seg) > 8:
                    parts.append(re.sub(r'[-_]', ' ', seg))
        except Exception:
            pass

    # Non-URL text after stripping URLs and date noise
    non_url = URL_RE.sub(' ', source_text)
    non_url = DATE_RE.sub(' ', non_url)
    non_url = re.sub(r'\b\d{4}\b', ' ', non_url)
    non_url = re.sub(r'[|/\\]', ' ', non_url)
    non_url = re.sub(r'\s+', ' ', non_url).strip()
    if non_url:
        parts.append(non_url)

    return ' '.join(parts)


# ── Stage 1: Topic extraction and search ──────────────────────────────────────

def _extract_cloze_answers(text: str) -> List[str]:
    return re.findall(r'\{\{c\d+::([^}:]+)(?:::[^}]*)?\}\}', text)


def _extract_question_stem(card_text: str) -> str:
    """Return card text with all cloze markers removed — the 'question frame'."""
    return re.sub(r'\{\{c\d+::[^}]*\}\}', ' ', card_text).strip()


def _extract_topic_phrases(question_stem: str) -> List[str]:
    """
    Extract disease/condition phrases from the question stem.

    Patterns (all capped at 4 words to avoid matching whole sentences):
      "of [X]"                             — "management of heart failure"
      "caused by [X]" / "due to [X]"       — "caused by ethylene glycol poisoning"
      "secondary to [X]" / "following [X]" — "secondary to rhabdomyolysis"
      "resulting from [X]" / "from [X]"    — "resulting from liver failure"
      "in [X] poisoning/toxicity/…"        — "in carbon monoxide poisoning"
      "diagnose/diagnosis [X]"

    Leading modifier words (nonsevere, acute, chronic…) are stripped to
    produce a cleaner search query alongside the original.
    """
    phrases: List[str] = []
    PHRASE_CAP = r'(?:\w+)(?:\s+\w+){0,3}?'
    PHRASE_END  = r'(?=\s+in\b|\s+for\b|\s+with\b|\s+at\b|\s+are\b|\s+is\b|\?|$)'

    # "of X"
    phrases.extend(re.findall(
        rf'\bof\s+({PHRASE_CAP}){PHRASE_END}', question_stem, re.I))

    # Causal / relational triggers
    for trigger in (
        r'caused\s+by', r'due\s+to', r'secondary\s+to',
        r'following', r'resulting\s+from', r'from',
    ):
        phrases.extend(re.findall(
            rf'\b{trigger}\s+({PHRASE_CAP}){PHRASE_END}', question_stem, re.I))

    # "in X poisoning/toxicity/overdose/disease/syndrome/infection/injury"
    phrases.extend(re.findall(
        r'\bin\s+((?:\w+\s+){0,2}\w+)\s+'
        r'(?:poisoning|toxicity|overdose|disease|syndrome|infection|injury)',
        question_stem, re.I))

    # "diagnose/diagnosis [topic]"
    phrases.extend(re.findall(
        r'\bdiagnos\w*\s+([\w\s]{3,40}?)(?:\?|$|\s+in\b|\s+with\b)',
        question_stem, re.I))

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
    extra: str = '',
    source: str = '',
) -> Dict[str, float]:
    """
    Stage 1: search for the card topic using topic phrases, stem-frequency
    words, useful cloze answers, plus signals from the Extra and Source fields.

    Weight hierarchy (fraction of TOPIC_SEARCH_BONUS):
      Text field     1.0×  — primary signal
      Extra          EXTRA_WEIGHT (0.6×) — rich clinical prose
      Source         SOURCE_WEIGHT (0.5×) — URL titles / slugs (very precise)

    Scores are combined with max(), so a strong primary hit is never downgraded
    by a weaker supplementary one.
    """
    scores: Dict[str, float] = {}

    # Each entry: (query_list, weight_multiplier)
    query_groups: List[Tuple[List[str], float]] = []

    # ── Primary: stem-extracted topics (full weight) ────────────────────────
    # These come from the question frame ("caused by X", "of X", etc.) and
    # reliably name the disease/condition being asked about.
    stem = _extract_question_stem(card_text)
    stem_queries: List[str] = []
    stem_queries.extend(_extract_topic_phrases(stem))
    stem_queries.extend(_stem_frequency_topics(stem))
    query_groups.append((stem_queries, 1.0))

    # ── Primary: cloze answers (reduced weight) ──────────────────────────────
    # Cloze answers name real conditions too, but on "What effects are caused
    # by ethylene glycol poisoning? → AKI" the answer is a *consequence*, not
    # the topic.  Weighting below 1.0 ensures stem topics rank above them when
    # both map to different pages.
    cloze_queries: List[str] = []
    for answer in _extract_cloze_answers(card_text):
        if _is_useful_cloze_query(answer):
            clean = re.sub(r'[^\w\s]', ' ', answer).strip()
            if len(clean.replace(' ', '')) >= 3:
                cloze_queries.append(clean)
    if cloze_queries:
        query_groups.append((cloze_queries, CLOZE_TOPIC_WEIGHT))

    # ── Extra field ──────────────────────────────────────────────────────────
    if extra and extra.strip():
        extra_clean = re.sub(r'<[^>]+>', ' ', extra)
        extra_q: List[str] = []
        extra_q.extend(_extract_topic_phrases(extra_clean))
        extra_q.extend(_stem_frequency_topics(extra_clean))
        query_groups.append((extra_q, EXTRA_WEIGHT))

    # ── Source field (URL titles / slugs) ────────────────────────────────────
    if source and source.strip():
        source_clean = _extract_source_topics(source)
        source_q: List[str] = []
        source_q.extend(_extract_topic_phrases(source_clean))
        source_q.extend(_stem_frequency_topics(source_clean))
        query_groups.append((source_q, SOURCE_WEIGHT))

    # Deduplicate within each group; across groups keep all (different weights)
    for i, (queries, _) in enumerate(query_groups):
        seen: Set[str] = set()
        query_groups[i] = (
            [q for q in queries if not (q.lower() in seen or seen.add(q.lower()))],
            query_groups[i][1],
        )

    # query_groups order: stem, cloze, extra, source (only non-empty ones appended)
    print(f"[SuggestTags] Stage 1: {sum(len(q) for q, _ in query_groups)} queries "
          f"across {len(query_groups)} groups "
          f"(stem={len(query_groups[0][0])}, "
          f"cloze={len(query_groups[1][0]) if len(query_groups) > 1 and query_groups[1][1] == CLOZE_TOPIC_WEIGHT else 0})")

    for queries, weight in query_groups:
        for query in queries:
            if len(query.replace(' ', '')) < 3:
                continue
            for page in notion_cache.filter_pages(all_pages, query):
                pid = page.get('id', '')
                if pid:
                    s = page.get('_composite_score', 0.0) * TOPIC_SEARCH_BONUS * weight
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


def _body_candidates(
    card_text: str,
    extra: str = '',
    additional_resources: str = '',
    source: str = '',
) -> List[Tuple[str, float]]:
    """
    Stage 2 body candidate extraction.

    Weight hierarchy:
      Text field            1.0×  (+ WEIGHT_CLOZE_BODY for cloze answers)
      Extra                 EXTRA_WEIGHT (0.6×)
      Source (URL titles)   SOURCE_WEIGHT (0.5×)
      Additional Resources  ADDL_WEIGHT (0.2×)

    Higher weight for the same phrase wins — a phrase seen in the Text field
    at 1.0× is never downgraded if it also appears in a supplementary field.
    """
    candidates: List[Tuple[str, float]] = []

    def add(phrase: str, weight: float):
        for i, (p, w) in enumerate(candidates):
            if p == phrase:
                if weight > w:
                    candidates[i] = (phrase, weight)
                return
        candidates.append((phrase, weight))

    def _tokenise_field(text: str, weight: float):
        clean = re.sub(r'<[^>]+>', ' ', text)
        words_raw = re.sub(r'[^\w\s]', ' ', clean.lower()).split()
        kept = [w for raw in words_raw for w in _expand(raw) if _is_worth_keeping(w)]
        for w in kept:
            add(w, WEIGHT_SINGLE_WORD * weight)
        for i in range(len(kept) - 1):
            add(kept[i] + ' ' + kept[i + 1], WEIGHT_BIGRAM * weight)

    # ── Text: cloze answers (boosted) ───────────────────────────────────────
    for answer in _extract_cloze_answers(card_text):
        if _is_drug_answer(answer):
            continue
        words_raw = re.sub(r'[^\w\s]', ' ', answer.lower()).split()
        kept = [w for raw in words_raw for w in _expand(raw) if _is_worth_keeping(w)]
        for w in kept:
            add(w, WEIGHT_CLOZE_BODY * WEIGHT_SINGLE_WORD)
        for i in range(len(kept) - 1):
            add(kept[i] + ' ' + kept[i + 1], WEIGHT_CLOZE_BODY * WEIGHT_BIGRAM)

    # ── Text: full body ──────────────────────────────────────────────────────
    _tokenise_field(
        re.sub(r'\{\{c\d+::([^}:]+)(?:::[^}]*)?\}\}', r'\1', card_text),
        1.0,
    )

    # ── Extra ────────────────────────────────────────────────────────────────
    if extra and extra.strip():
        _tokenise_field(extra, EXTRA_WEIGHT)

    # ── Source (URL titles / slugs) ──────────────────────────────────────────
    if source and source.strip():
        _tokenise_field(_extract_source_topics(source), SOURCE_WEIGHT)

    # ── Additional Resources ─────────────────────────────────────────────────
    if additional_resources and additional_resources.strip():
        _tokenise_field(additional_resources, ADDL_WEIGHT)

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
        "appearance", "suggestive"
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


def suggest_subtag(card_text: str, extra: str = '') -> Optional[str]:
    """
    Return the most relevant Subjects subtag, or None.

    Scores each subtag by counting keyword hits in the cleaned card text
    plus the Extra field (at full weight — Extra often explicitly labels
    the clinical aspect: "Management:", "Investigations:" etc.).

    Source and Additional Resources are excluded here: URL slugs don't
    reliably indicate subtag type, and reference lists add noise.
    Ties broken by SUBTAG_PRIORITY.
    """
    clean = re.sub(r'\{\{c\d+::([^}:]+)(?:::[^}]*)?\}\}', r'\1', card_text)
    clean = re.sub(r'<[^>]+>', ' ', clean).lower()

    if extra and extra.strip():
        extra_clean = re.sub(r'<[^>]+>', ' ', extra).lower()
        clean = clean + ' ' + extra_clean

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
    extra: str = '',
    additional_resources: str = '',
    source: str = '',
) -> List[Dict]:
    """
    Return a ranked list of suggested Subjects pages.

    Args:
        card_text:            The note's Text field (cloze-formatted).
        notion_cache:         NotionCache instance.
        max_results:          Maximum number of suggestions to return.
        extra:                Note's Extra field.  Used in all four stages
                              at EXTRA_WEIGHT (0.6×); subtag detection uses
                              it at full weight since it often explicitly
                              labels the clinical aspect.
        additional_resources: Note's Additional Resources field.  Used in
                              Stages 2–3 at ADDL_WEIGHT (0.2×).
        source:               Note's Source field.  URL titles and path
                              slugs extracted and used in Stages 1–3 at
                              SOURCE_WEIGHT (0.5×).  Not used for subtag
                              detection (URL slugs are poor subtag signals).

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

    stage1 = _topic_search_scores(card_text, pages, notion_cache,
                                   extra=extra, source=source)
    candidates = _body_candidates(card_text,
                                  extra=extra,
                                  additional_resources=additional_resources,
                                  source=source)
    stage2 = _shortlist_and_score(candidates, index, page_by_id, notion_cache) if candidates else {}

    merged: Dict[str, float] = {}
    for pid, s in stage1.items():
        merged[pid] = merged.get(pid, 0.0) + s
    for pid, s in stage2.items():
        merged[pid] = merged.get(pid, 0.0) + s

    if not merged:
        return []

    # Title bonus: page names mentioned anywhere in any field get a score lift
    clean_card = re.sub(r'<[^>]+>', ' ',
        re.sub(r'\{\{c\d+::([^}:]+)(?:::[^}]*)?\}\}', r'\1', card_text))
    for field_text in (extra, additional_resources):
        if field_text:
            clean_card += ' ' + re.sub(r'<[^>]+>', ' ', field_text)
    if source:
        clean_card += ' ' + _extract_source_topics(source)
    merged = _apply_title_bonus(merged, page_by_id, clean_card)

    ranked = sorted(
        [(score, pid) for pid, score in merged.items() if score >= MIN_FINAL_SCORE],
        reverse=True,
    )

    subtag = suggest_subtag(card_text, extra=extra)

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
