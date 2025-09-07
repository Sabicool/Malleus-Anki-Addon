import os
import json
import time
import requests
from dotenv import load_dotenv
from pathlib import Path

addon_dir = os.path.dirname(os.path.realpath(__file__))
env_path = os.path.join(addon_dir, '.env')
load_dotenv(env_path)

SUBJECT_DATABASE_ID = os.getenv('DATABASE_ID')
PHARMACOLOGY_DATABASE_ID = os.getenv('PHARMACOLOGY_DATABASE_ID')
ETG_DATABASE_ID = os.getenv('ETG_DATABASE_ID')
ROTATION_DATABASE_ID = os.getenv('ROTATION_DATABASE_ID')
GUIDELINES_DATABASE_ID = os.getenv('GUIDELINES_DATABASE_ID')

class NotionCache:
    def __init__(self, addon_dir: str):
        self.cache_dir = Path(addon_dir) / "cache"
        self.cache_dir.mkdir(exist_ok=True)

    def update_cache(self, database_id: str):
        """Fetch and save all pages from a Notion database to the cache"""
        self.clear_cache(database_id)
        headers = {
            "Authorization": f"Bearer {os.getenv('NOTION_TOKEN')}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        pages = []
        has_more = True
        start_cursor = None
        while has_more:
            payload = {
                "filter": {
                    "property": "For Search",
                    "checkbox": {
                        "equals": True
                    }
                },
                "page_size": 100
            }
            if start_cursor:
                payload["start_cursor"] = start_cursor
            try:
                response = requests.post(
                    f"https://api.notion.com/v1/databases/{database_id}/query",
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
                pages.extend(data['results'])
                has_more = data.get('has_more', False)
                start_cursor = data.get('next_cursor')
            except Exception as e:
                print(f"Error fetching from Notion: {e}")
                break
        self.save_to_cache(database_id, pages)
        print(f"Saved {len(pages)} pages to the cache for database {database_id}")

    def clear_cache(self, database_id: str):
        """Clear the cache for a specific database"""
        cache_path = self.get_cache_path(database_id)
        if cache_path.exists():
            os.remove(cache_path)
            print(f"Cleared cache for database {database_id}")

    def save_to_cache(self, database_id: str, pages: list):
        """Save pages to cache file"""
        cache_path = self.get_cache_path(database_id)
        try:
            with cache_path.open('w', encoding='utf-8') as f:
                json.dump({
                    'version': 1,
                    'timestamp': time.time(),
                    'pages': pages
                }, f)
        except Exception as e:
            print(f"Error saving cache: {e}")

    def get_cache_path(self, database_id: str) -> Path:
        """Get the path for a specific database's cache file"""
        return self.cache_dir / f"{database_id}.json"

def update_notion_cache():
    """Update the Notion database cache"""
    notion_cache = NotionCache(addon_dir)
    if GUIDELINES_DATABASE_ID:
        notion_cache.update_cache(GUIDELINES_DATABASE_ID)
##    if SUBJECT_DATABASE_ID:
##        notion_cache.update_cache(SUBJECT_DATABASE_ID)
##    if PHARMACOLOGY_DATABASE_ID:
##        notion_cache.update_cache(PHARMACOLOGY_DATABASE_ID)
##    if ETG_DATABASE_ID:
##        notion_cache.update_cache(ETG_DATABASE_ID)
##    if ROTATION_DATABASE_ID:
##        notion_cache.update_cache(ROTATION_DATABASE_ID)

if __name__ == "__main__":
    update_notion_cache()
