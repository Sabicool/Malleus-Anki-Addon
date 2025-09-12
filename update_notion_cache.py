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
TEXTBOOKS_DATABASE_ID = os.getenv('TEXTBOOKS_DATABASE_ID')
GUIDELINES_DATABASE_ID = os.getenv('GUIDELINES_DATABASE_ID')

class NotionCache:
    def __init__(self, addon_dir: str):
        self.cache_dir = Path(addon_dir) / "cache"
        self.cache_dir.mkdir(exist_ok=True)

    def get_data_source_ids(self, database_id: str):
        """Get data source IDs for a database using the 2025-09-03 API"""
        headers = {
            "Authorization": f"Bearer {os.getenv('NOTION_TOKEN')}",
            "Notion-Version": "2025-09-03",
            "Content-Type": "application/json"
        }

        try:
            response = requests.get(
                f"https://api.notion.com/v1/databases/{database_id}",
                headers=headers
            )
            response.raise_for_status()
            data = response.json()

            # Extract data source IDs
            data_sources = data.get('data_sources', [])
            if not data_sources:
                print(f"No data sources found for database {database_id}")
                return []

            return [(ds['id'], ds['name']) for ds in data_sources]

        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                error_data = e.response.json()
                if error_data.get('code') == 'validation_error' and 'multiple_data_sources_for_database' in error_data.get('additional_data', {}).get('error_type', ''):
                    # Handle multiple data sources error for older API versions
                    child_data_source_ids = error_data.get('additional_data', {}).get('child_data_source_ids', [])
                    print(f"Database {database_id} has multiple data sources. Using provided IDs: {child_data_source_ids}")
                    return [(ds_id, f"Data Source {i+1}") for i, ds_id in enumerate(child_data_source_ids)]
            print(f"Error getting data sources for database {database_id}: {e}")
            return []

    def update_cache(self, database_id: str):
        """Fetch and save all pages from a Notion database to the cache using 2025-09-03 API"""
        self.clear_cache(database_id)

        # First, get the data source IDs for this database
        data_sources = self.get_data_source_ids(database_id)

        if not data_sources:
            print(f"No data sources found for database {database_id}, skipping...")
            return

        headers = {
            "Authorization": f"Bearer {os.getenv('NOTION_TOKEN')}",
            "Notion-Version": "2025-09-03",
            "Content-Type": "application/json"
        }

        all_pages = []

        # Query each data source
        for data_source_id, data_source_name in data_sources:
            print(f"Querying data source: {data_source_name} ({data_source_id})")

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
                    # Use the new data sources endpoint
                    response = requests.post(
                        f"https://api.notion.com/v1/data_sources/{data_source_id}/query",
                        headers=headers,
                        json=payload
                    )
                    response.raise_for_status()
                    data = response.json()

                    # Add data source information to each page for reference
                    for page in data['results']:
                        page['_data_source_id'] = data_source_id
                        page['_data_source_name'] = data_source_name

                    pages.extend(data['results'])
                    has_more = data.get('has_more', False)
                    start_cursor = data.get('next_cursor')

                except requests.exceptions.RequestException as e:
                    print(f"Error fetching from data source {data_source_id}: {e}")
                    break

            print(f"Found {len(pages)} pages in data source {data_source_name}")
            all_pages.extend(pages)

        self.save_to_cache(database_id, all_pages)
        print(f"Saved {len(all_pages)} total pages to the cache for database {database_id}")

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
                    'version': 2,  # Updated version to indicate 2025-09-03 API
                    'api_version': '2025-09-03',
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

    # Uncomment the databases you want to update
#    if SUBJECT_DATABASE_ID:
#        notion_cache.update_cache(SUBJECT_DATABASE_ID)
#    if PHARMACOLOGY_DATABASE_ID:
#        notion_cache.update_cache(PHARMACOLOGY_DATABASE_ID)
#    if ETG_DATABASE_ID:
#        notion_cache.update_cache(ETG_DATABASE_ID)
#    if ROTATION_DATABASE_ID:
#        notion_cache.update_cache(ROTATION_DATABASE_ID)
#    if TEXTBOOKS_DATABASE_ID:
#        notion_cache.update_cache(TEXTBOOKS_DATABASE_ID)
    if GUIDELINES_DATABASE_ID:
        notion_cache.update_cache(GUIDELINES_DATABASE_ID)

if __name__ == "__main__":
    update_notion_cache()
