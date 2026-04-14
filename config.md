# Malleus Addon — Configuration Reference

All settings are edited via **Tools → Add-ons → Malleus Clinical Medicine → Config**.

---

## `deck_name`
**Default:** `"Default"`

The name of the deck where new cards are created. Use `::` to specify a subdeck.

```
"deck_name": "Pre-Made Decks::Malleus Medicine"
```

---

## `shortcut`
**Default:** `"Ctrl+Alt+M"`

Keyboard shortcut to open the Malleus Page Selector from anywhere in Anki.
Use standard Anki key notation, e.g. `"Ctrl+Shift+M"`, `"Alt+M"`.

```
"shortcut": "Ctrl+Alt+M"
```

---

## `cache_expiry`
**Default:** `7` (days)

How many days before the local database cache is considered stale and a refresh is prompted.
Lower values keep you more in sync with the Notion database; higher values reduce network activity.

```
"cache_expiry": 7
```

---

## `autosearch`
**Default:** `true`

When `true`, the Page Selector searches automatically as you type (after `search_delay` ms).
Set to `false` to require pressing Enter to trigger a search.

```
"autosearch": true
```

---

## `search_delay`
**Default:** `300` (milliseconds)

How long to wait after you stop typing before the auto-search fires. Only applies when `autosearch` is `true`.
Increase this if searches feel laggy; decrease it if you want faster results.

```
"search_delay": 300
```

---

## `request_timeout`
**Default:** `30` (seconds)

Timeout for network requests when downloading database caches from GitHub.
Increase this if you are on a slow connection and cache updates are timing out.

```
"request_timeout": 30
```

---

## `card_count_threshold`
**Default:** `10`

Maximum number of search results for which per-result note counts are shown.
When results exceed this number the counts are hidden to keep the UI fast.
Set to `0` to always hide counts, or a high number (e.g. `50`) to always show them.

```
"card_count_threshold": 10
```
