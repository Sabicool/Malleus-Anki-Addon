import os
import json
import time
import threading
import concurrent.futures
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path

NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
if not NOTION_TOKEN:
    raise ValueError("NOTION_TOKEN environment variable is required")

SUBJECT_DATABASE_ID      = '2674b67cbdf84a11a057a29cc24c524f'
PHARMACOLOGY_DATABASE_ID = '9ff96451736d43909d49e3b9d60971f8'
ETG_DATABASE_ID          = '22282971487f4f559dce199476709b03'
ROTATION_DATABASE_ID     = '69b3e7fdce1548438b26849466d7c18e'
TEXTBOOKS_DATABASE_ID    = '13d5964e68a480bfb07cf7e2f1786075'
GUIDELINES_DATABASE_ID   = '13d5964e68a48056b40de8148dd91a06'
SYNCED_EXTRA_DATABASE_ID = '2dc5964e68a480909c4ac1dc169b16fb'
SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID = '31b5964e68a48023b1c1c7b23fbdec64'

USES_FOR_SEARCH = {
    SUBJECT_DATABASE_ID,
    PHARMACOLOGY_DATABASE_ID,
    ETG_DATABASE_ID,
    ROTATION_DATABASE_ID,
    TEXTBOOKS_DATABASE_ID,
    GUIDELINES_DATABASE_ID,
}

# Large databases time out at page_size=100 even with server-side formula filtering.
# eTG has heavy rollups that make each row expensive to return — use page_size=5 so
# Notion only computes rollups for 5 rows per request instead of 25/100.
PAGE_SIZE_OVERRIDE = {
    SUBJECT_DATABASE_ID:      25,
    PHARMACOLOGY_DATABASE_ID: 25,
    ETG_DATABASE_ID:          100,
    GUIDELINES_DATABASE_ID:   25,
}
DEFAULT_PAGE_SIZE = 100

# No databases currently need client-side-only filtering, but this set is kept
# for future use if a database's formula scan is too expensive even at page_size=5.
SERVER_SIDE_FILTER_SKIP: set = set()

TIMEOUT = 120  # seconds — large databases need time

# ── Global rate limiter (3 req/s across all threads) ──────────────────

_rate_lock = threading.Lock()
_last_request_time = 0.0
_MIN_INTERVAL = 1.0 / 3  # enforce max 3 requests/second

def _rate_limited_wait():
    global _last_request_time
    with _rate_lock:
        now = time.time()
        wait = _MIN_INTERVAL - (now - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()

# ── Session factory ───────────────────────────────────────────────────

def make_session():
    """Create a requests session with built-in retry/backoff at the transport level."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2025-09-03",
        "Content-Type": "application/json",
    })
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST", "GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session

# ── Block → HTML converter ────────────────────────────────────────────

_NOTION_COLORS = {
    "gray": "color:#9b9a97", "brown": "color:#9f6b53",
    "orange": "color:#d9730d", "yellow": "color:#cb912f",
    "green": "color:#448361", "blue": "color:#337ea9",
    "purple": "color:#9065b0", "pink": "color:#c14c8a", "red": "color:#d44c47",
    "gray_background": "background-color:#f1f1ef",
    "brown_background": "background-color:#f4eeee",
    "orange_background": "background-color:#fbecdd",
    "yellow_background": "background-color:#fbf3db",
    "green_background": "background-color:#edf3ec",
    "blue_background": "background-color:#e7f3f8",
    "purple_background": "background-color:#f4f0f9",
    "pink_background": "background-color:#fbe8f1",
    "red_background": "background-color:#fde8e8",
}

def rich_text_to_html(rich_texts: list) -> str:
    """Convert a Notion rich_text array to an HTML string."""
    html = ""
    for rt in rich_texts:
        rt_type = rt.get("type", "text")
        if rt_type == "equation":
            expr = rt.get("equation", {}).get("expression", rt.get("plain_text", ""))
            html += f"<anki-mathjax>{expr}</anki-mathjax>"
            continue
        text = rt.get("plain_text", "")
        if not text:
            continue
        ann = rt.get("annotations", {})
        if ann.get("bold"):        text = f"<strong>{text}</strong>"
        if ann.get("italic"):      text = f"<em>{text}</em>"
        if ann.get("underline"):   text = f"<u>{text}</u>"
        if ann.get("strikethrough"): text = f"<s>{text}</s>"
        if ann.get("code"):        text = f"<code>{text}</code>"
        color = ann.get("color", "default")
        if color and color != "default":
            style = _NOTION_COLORS.get(color, "")
            if style:
                text = f'<span style="{style}">{text}</span>'
        link = (rt.get("href") or (rt.get("text") or {}).get("link") or {})
        if isinstance(link, dict) and link.get("url"):
            text = f'<a href="{link["url"]}">{text}</a>'
        html += text
    return html

def blocks_to_html(blocks: list) -> str:
    """Convert a nested block tree (with _children keys) to HTML."""

    def render_blocks(blocks):
        parts = []
        i = 0
        while i < len(blocks):
            block = blocks[i]
            btype = block.get("type", "")
            data  = block.get(btype, {})
            rt    = data.get("rich_text", [])
            children = block.get("_children", [])

            if btype in ("bulleted_list_item", "numbered_list_item"):
                tag = "ul" if btype == "bulleted_list_item" else "ol"
                items = []
                while i < len(blocks) and blocks[i].get("type") == btype:
                    b = blocks[i]
                    bd = b.get(btype, {})
                    brt = bd.get("rich_text", [])
                    bchildren = b.get("_children", [])
                    item_html = rich_text_to_html(brt)
                    if bchildren:
                        item_html += render_blocks(bchildren)
                    items.append(f"<li>{item_html}</li>")
                    i += 1
                parts.append(f"<{tag}>{''.join(items)}</{tag}>")
                continue

            elif btype == "paragraph":
                inner = rich_text_to_html(rt)
                if inner.strip():
                    color = data.get("color", "default")
                    style = f' style="{_NOTION_COLORS[color]}"' if color != "default" and color in _NOTION_COLORS else ""
                    parts.append(f"<p{style}>{inner}</p>")

            elif btype in ("heading_1", "heading_2", "heading_3"):
                level = btype[-1]
                color = data.get("color", "default")
                style = f' style="{_NOTION_COLORS[color]}"' if color != "default" and color in _NOTION_COLORS else ""
                parts.append(f"<h{level}{style}>{rich_text_to_html(rt)}</h{level}>")

            elif btype in ("callout", "quote"):
                inner = rich_text_to_html(rt)
                if children:
                    inner += render_blocks(children)
                color = data.get("color", "default")
                style = f' style="{_NOTION_COLORS[color]}"' if color != "default" and color in _NOTION_COLORS else ""
                parts.append(f"<blockquote{style}>{inner}</blockquote>")

            elif btype == "code":
                lang = data.get("language", "")
                if lang.lower() == "html":
                    raw = "".join(seg.get("plain_text", "") for seg in rt)
                    parts.append(raw)
                else:
                    parts.append(f'<pre><code class="{lang}">{rich_text_to_html(rt)}</code></pre>')

            elif btype == "equation":
                expr = data.get("expression", "")
                if expr:
                    parts.append(f'<div><anki-mathjax block="true">{expr}</anki-mathjax><br></div>')

            elif btype == "divider":
                parts.append("<hr>")

            elif btype == "image":
                url = (data.get("file") or data.get("external") or {}).get("url", "")
                caption = rich_text_to_html(data.get("caption", []))
                if url:
                    parts.append(f'<figure><img src="{url}"><figcaption>{caption}</figcaption></figure>')

            elif btype == "table":
                rows = render_blocks(children)
                parts.append(f"<table><tbody>{rows}</tbody></table>")

            elif btype == "table_row":
                cells = data.get("cells", [])
                row_html = "".join(f"<td>{rich_text_to_html(cell)}</td>" for cell in cells)
                parts.append(f"<tr>{row_html}</tr>")

            i += 1
        return "\n".join(parts)

    return render_blocks(blocks)

# ── NotionCache ───────────────────────────────────────────────────────

class NotionCache:
    def __init__(self):
        self.cache_dir = Path("cache")
        self.cache_dir.mkdir(exist_ok=True)
        self.session = make_session()  # each instance gets its own session (thread-safe)
        self._data_source_id_cache = {}

    def _get_data_source_id(self, database_id: str) -> str:
        if database_id in self._data_source_id_cache:
            return self._data_source_id_cache[database_id]
        data = self.get(f"https://api.notion.com/v1/databases/{database_id}")
        sources = data.get("data_sources", [])
        ds_id = sources[0].get("id", database_id) if sources else database_id
        self._data_source_id_cache[database_id] = ds_id
        print(f"  data_source_id for {database_id}: {ds_id}")
        return ds_id

    def get(self, url: str) -> dict:
        for attempt in range(1, 6):
            try:
                _rate_limited_wait()
                r = self.session.get(url, timeout=TIMEOUT)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                print(f"  GET attempt {attempt}/5 failed: {type(e).__name__}: {e}")
                if attempt < 5:
                    time.sleep(2 ** attempt)
                else:
                    raise

    def _fetch_children_flat(self, block_id: str) -> list:
        """Fetch direct children of a block, handling pagination."""
        blocks = []
        url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
        while url:
            data = self.get(url)
            blocks.extend(data.get("results", []))
            if data.get("has_more") and data.get("next_cursor"):
                cursor = data["next_cursor"]
                url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100&start_cursor={cursor}"
            else:
                url = None
        return blocks

    def fetch_blocks(self, page_id: str) -> list:
        """Fetch all blocks for a page recursively, embedding children as '_children' key."""
        blocks = self._fetch_children_flat(page_id)
        for block in blocks:
            if block.get("has_children"):
                block["_children"] = self.fetch_blocks(block["id"])
        return blocks

    def fetch_pages_batch(self, database_id: str, start_cursor: str = None, use_for_search: bool = False) -> dict:
        page_size = PAGE_SIZE_OVERRIDE.get(database_id, DEFAULT_PAGE_SIZE)
        if use_for_search:
            payload = {
                "filter": {
                    "property": "For Search",
                    "formula": {"checkbox": {"equals": True}}
                },
                "page_size": page_size,
            }
        else:
            payload = {"page_size": page_size}

        if start_cursor:
            payload["start_cursor"] = start_cursor

        ds_id = self._get_data_source_id(database_id)
        url = f"https://api.notion.com/v1/data_sources/{ds_id}/query"

        for attempt in range(1, 9):
            try:
                _rate_limited_wait()
                response = self.session.post(url, json=payload, timeout=TIMEOUT)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                print(f"  Attempt {attempt}/8 failed: {type(e).__name__}: {e}")
                if attempt < 8:
                    wait = 2 ** attempt
                    print(f"  Waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    raise

    def update_cache(self, database_id: str, name: str):
        use_for_search = database_id in USES_FOR_SEARCH
        server_side_filter = use_for_search and database_id not in SERVER_SIDE_FILTER_SKIP
        is_synced_extra = database_id in (SYNCED_EXTRA_DATABASE_ID, SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID)
        pages = []
        has_more = True
        start_cursor = None
        batch = 0

        while has_more:
            batch += 1
            print(f"  [{name}] Fetching batch {batch}...")
            data = self.fetch_pages_batch(database_id, start_cursor, use_for_search=server_side_filter)
            results = data['results']
            if use_for_search:
                # Always filter client-side — for server_side_filter DBs this is a no-op
                # (server already filtered), for SERVER_SIDE_FILTER_SKIP DBs this does the work
                results = [
                    p for p in results
                    if p.get("properties", {}).get("For Search", {}).get("formula", {}).get("boolean", False)
                ]
            pages.extend(results)
            has_more = data.get('has_more', False)
            start_cursor = data.get('next_cursor')
            print(f"  [{name}] Batch {batch}: {len(results)} pages (total: {len(pages)})")

        # For Synced Extra, fetch block content and embed as HTML
        if is_synced_extra:
            print(f"  [{name}] Fetching block content for {len(pages)} synced pages...")
            for page in pages:
                page_id = page["id"]
                try:
                    blocks = self.fetch_blocks(page_id)
                    html = blocks_to_html(blocks).strip()
                    if html:
                        page["_block_html"] = html
                        print(f"    [{name}] {page_id}: {len(blocks)} blocks → {len(html)} chars HTML")
                except Exception as e:
                    print(f"    [{name}] Warning: could not fetch blocks for {page_id}: {e}")

        cache_path = self.cache_dir / f"{database_id}.json"
        with cache_path.open('w', encoding='utf-8') as f:
            json.dump({'version': 1, 'timestamp': time.time(), 'pages': pages}, f)
        print(f"  [{name}] Saved {len(pages)} pages → {cache_path}")

def _run_one(db_id: str, name: str):
    """Worker function — each database gets its own NotionCache (and session).
    Retries the full database update up to 3 times on failure with increasing waits."""
    for db_attempt in range(1, 4):
        try:
            print(f"\n--- Starting {name} (attempt {db_attempt}/3) ---")
            cache = NotionCache()
            cache.update_cache(db_id, name)
            print(f"--- Done: {name} ---")
            return
        except Exception as e:
            print(f"\n  [{name}] DB-level attempt {db_attempt}/3 failed: {e}")
            if db_attempt < 3:
                wait = 60 * db_attempt  # 60s, then 120s
                print(f"  [{name}] Waiting {wait}s before retrying entire database...")
                time.sleep(wait)
            else:
                raise

def update_notion_cache():
    databases = [
        (SUBJECT_DATABASE_ID,      "Subjects"),
        (PHARMACOLOGY_DATABASE_ID, "Pharmacology"),
        (ETG_DATABASE_ID,          "eTG"),
        (ROTATION_DATABASE_ID,     "Rotation"),
        (TEXTBOOKS_DATABASE_ID,    "Textbooks"),
        (GUIDELINES_DATABASE_ID,   "Guidelines"),
        (SYNCED_EXTRA_DATABASE_ID, "Synced Extra"),
        (SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID, "Synced Additional Resources"),
    ]

    print(f"Running {len(databases)} database updates in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(databases)) as executor:
        futures = {executor.submit(_run_one, db_id, name): name for db_id, name in databases}
        failed = []
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"\nERROR updating {name}: {e}")
                failed.append(name)

    if failed:
        raise RuntimeError(f"The following databases failed to update: {', '.join(failed)}")
    print("\nAll databases updated successfully.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target = sys.argv[1].lower()
        databases = [
            (SUBJECT_DATABASE_ID,      "Subjects"),
            (PHARMACOLOGY_DATABASE_ID, "Pharmacology"),
            (ETG_DATABASE_ID,          "eTG"),
            (ROTATION_DATABASE_ID,     "Rotation"),
            (TEXTBOOKS_DATABASE_ID,    "Textbooks"),
            (GUIDELINES_DATABASE_ID,   "Guidelines"),
            (SYNCED_EXTRA_DATABASE_ID, "Synced Extra"),
            (SYNCED_ADDITIONAL_RESOURCES_DATABASE_ID, "Synced Additional Resources"),
        ]
        matched = [(db_id, name) for db_id, name in databases if target in name.lower()]
        if not matched:
            raise SystemExit(f"No database matching '{sys.argv[1]}'. Options: {[n for _, n in databases]}")
        for db_id, name in matched:
            _run_one(db_id, name)
    else:
        update_notion_cache()
