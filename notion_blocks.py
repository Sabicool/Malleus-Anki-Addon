"""
Notion block → HTML conversion for Synced Extra / Synced Additional Resources
page bodies.  Shared by the CI cache build (update_cache.py) and the add-on's
incremental sync (notion_cache.py) so both embed identical `_block_html`.

Dependency-free apart from `requests`; safe to import in CI and inside Anki.
"""
import requests

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


def fetch_page_html(page_id: str, headers: dict, timeout: float) -> str:
    """Fetch a page's block tree (children nested as `_children`) and render it
    with blocks_to_html.  Raises on network/HTTP errors."""
    def children(block_id):
        blocks, cursor = [], None
        while True:
            url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
            if cursor:
                url += f"&start_cursor={cursor}"
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            blocks.extend(data.get("results", []))
            cursor = data.get("next_cursor") if data.get("has_more") else None
            if not cursor:
                break
        for block in blocks:
            if block.get("has_children"):
                block["_children"] = children(block["id"])
        return blocks

    return blocks_to_html(children(page_id)).strip()
