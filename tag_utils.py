"""
Tag Utilities
Helper functions for parsing, simplifying, and managing Malleus tags
"""
import re
from typing import List, Dict, Tuple, Set


def parse_tag(tag: str) -> Dict:
    """
    Parse a Malleus tag into its components
    
    Args:
        tag: Full tag like "#Malleus_CM::#Subjects::Cardiology::01_Coronary_&_Ischaemic_Heart_Disease::STEMI::02_Aetiology"
    
    Returns:
        Dict with keys: 'database', 'path_parts', 'page_name', 'subtag', 'full_tag'
    """
    # Remove the #Malleus_CM:: prefix
    if not tag.startswith("#Malleus_CM::"):
        return None
    
    tag_content = tag.replace("#Malleus_CM::", "")
    parts = tag_content.split("::")
    
    if len(parts) < 1:
        return None
    
    # First part is the database (with # prefix)
    database = parts[0].replace("#", "")
    
    # Last part is either the subtag or page name
    page_name = parts[-1] if len(parts) > 1 else None
    subtag = None
    
    # Check if the last part looks like a subtag (starts with digit or specific patterns)
    if page_name and len(parts) > 2:
        # Common subtag patterns
        subtag_patterns = [
            r'^\d+_',  # Starts with number and underscore
            r'^\*',     # Starts with asterisk (general)
            r'^(Epidemiology|Aetiology|Risk Factors|Physiology|Pathophysiology|Clinical Features)',
            r'^(Management|Complications|Screening|Prevention|Diagnosis|Investigations)',
            r'^(Generic Names|Mechanism of Action|Indications|Contraindications)',
            r'^(Route|Frequency|Adverse Effects|Toxicity|Reversal|Advantages|Disadvantages|Monitoring)'
        ]
        
        if any(re.match(pattern, page_name) for pattern in subtag_patterns):
            subtag = page_name
            page_name = parts[-2] if len(parts) > 2 else parts[-1]
    
    return {
        'database': database,
        'path_parts': parts,
        'page_name': page_name,
        'subtag': subtag,
        'full_tag': tag
    }


def extract_page_and_subtag_from_tag(tag: str) -> Tuple[str, str]:
    """
    Extract the page name and subtag from a tag
    
    Returns:
        Tuple of (page_name, subtag or None)
    """
    parsed = parse_tag(tag)
    if not parsed:
        return None, None
    
    return parsed['page_name'], parsed['subtag']


def simplify_tags_by_page(tags: List[str], database: str) -> List[Dict]:
    """
    Simplify a list of tags by grouping them by page name and subtag
    
    Args:
        tags: List of full tag strings
        database: The database name to filter by (e.g., "Subjects")
    
    Returns:
        List of dicts with:
        - 'display_name': Human-readable name for display
        - 'page_name': Actual page name
        - 'subtag': Subtag if present
        - 'original_tags': List of original tags that map to this simplified entry
    """
    # Filter tags by database
    database_tags = [tag for tag in tags if f"#{database}::" in tag]
    
    if not database_tags:
        return []
    
    # Group by page and subtag
    grouped = {}  # Key: (page_name, subtag), Value: list of original tags
    
    for tag in database_tags:
        parsed = parse_tag(tag)
        if not parsed:
            continue
        
        page_name = parsed['page_name']
        subtag = parsed['subtag']
        
        # Clean up page name for display
        page_name_clean = clean_page_name(page_name)
        
        key = (page_name_clean, subtag)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(tag)
    
    # Convert to list of dicts
    simplified = []
    for (page_name, subtag), original_tags in grouped.items():
        # Create display name
        if subtag:
            subtag_clean = clean_page_name(subtag)
            display_name = f"{page_name} ({subtag_clean})"
        else:
            display_name = page_name
        
        simplified.append({
            'display_name': display_name,
            'page_name': page_name,
            'subtag': subtag,
            'original_tags': original_tags
        })
    
    # Sort by display name
    simplified.sort(key=lambda x: x['display_name'])
    
    return simplified


def clean_page_name(name: str) -> str:
    """
    Clean up a page name for display
    - Remove number prefixes like "01_", "02_"
    - Replace underscores with spaces
    - Handle special characters
    """
    if not name:
        return ""
    
    # Remove number prefix (e.g., "01_", "02_")
    cleaned = re.sub(r'^\d+_', '', name)
    
    # Replace underscores with spaces
    cleaned = cleaned.replace('_', ' ')
    
    # Replace ampersand variants
    cleaned = cleaned.replace('&', 'and')
    
    # Handle asterisk for general
    if cleaned.startswith('*'):
        cleaned = cleaned[1:] + " (General)"
    
    return cleaned.strip()


def get_subtag_from_tag(tag: str) -> str:
    """
    Extract just the subtag from a full tag
    
    Args:
        tag: Full tag string
    
    Returns:
        Subtag string or None
    """
    parsed = parse_tag(tag)
    if parsed:
        return parsed['subtag']
    return None


def get_all_subtags_from_tags(tags: List[str]) -> Set[str]:
    """
    Get all unique subtags from a list of tags
    
    Returns:
        Set of subtag strings (excluding None)
    """
    subtags = set()
    for tag in tags:
        subtag = get_subtag_from_tag(tag)
        if subtag:
            subtags.add(subtag)
    return subtags


def normalize_for_comparison(text: str) -> str:
    """
    Normalize text for comparison (handle spaces, slashes, underscores)
    """
    return text.replace(' ', '_').replace('/', '_').replace('&', '_').lower()


def normalize_subtag_for_matching(subtag: str, possible_subtags: list) -> str:
    """
    Normalize a subtag from a tag to match against property selector options
    
    Args:
        subtag: The subtag from the tag (e.g., "10_Management", "*General")
        possible_subtags: List of valid subtags from database properties
    
    Returns:
        Matching subtag from possible_subtags, or original if no match
    
    Examples:
        normalize_subtag_for_matching("10_Management", ["Management", "Aetiology"])
        → "Management"
        
        normalize_subtag_for_matching("02_Aetiology", ["Management", "Aetiology"]) 
        → "Aetiology"
        
        normalize_subtag_for_matching("*General", ["Management"]) 
        → "Main Tag" (for general pages)
    """
    if not subtag:
        return None
    
    # Handle general pages
    if subtag.startswith('*'):
        return "Main Tag"
    
    # Remove number prefix (e.g., "10_" from "10_Management")
    cleaned = re.sub(r'^\d+_', '', subtag)
    
    # Replace underscores with spaces for matching
    cleaned_with_spaces = cleaned.replace('_', ' ')
    
    # Try exact match first
    for possible in possible_subtags:
        if cleaned.lower() == possible.lower():
            return possible
        if cleaned_with_spaces.lower() == possible.lower():
            return possible
    
    # Try partial match (for cases like "Contraindications/Precautions")
    for possible in possible_subtags:
        possible_normalized = possible.replace('/', ' ').replace('_', ' ').lower()
        cleaned_normalized = cleaned.replace('/', ' ').replace('_', ' ').lower()
        
        if cleaned_normalized in possible_normalized or possible_normalized in cleaned_normalized:
            return possible
    
    # No match found - return cleaned version
    return cleaned


def get_subtags_with_normalization(tags: list, possible_subtags: list) -> set:
    """
    Get unique normalized subtags from a list of tags
    
    Args:
        tags: List of full tag strings
        possible_subtags: List of valid subtags from database properties
    
    Returns:
        Set of normalized subtag strings that match property selector options
    """
    normalized_subtags = set()
    for tag in tags:
        subtag = get_subtag_from_tag(tag)
        if subtag:
            normalized = normalize_subtag_for_matching(subtag, possible_subtags)
            if normalized:
                normalized_subtags.add(normalized)
    
    return normalized_subtags
