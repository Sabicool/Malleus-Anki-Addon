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
**Default:** `1` (days)

How many days before the local database cache is considered stale and the add-on re-checks
GitHub on startup. The check is conditional (it only downloads a database if it actually
changed since last time), so a short value keeps you in sync with the daily rebuild at little
network cost. Higher values reduce how often the add-on checks.

```
"cache_expiry": 1
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

## `show_card_counts`
**Default:** `false`

When `true`, each search result shows how many notes in your collection are already
tagged with that page. Computing the counts scans every note's tags, which can slow
down search results appearing on large collections — hence off by default.

```
"show_card_counts": false
```

---

## `card_count_threshold`
**Default:** `10`

Only applies when `show_card_counts` is `true`. Maximum number of search results for
which per-result note counts are shown. When results exceed this number the counts
are hidden to keep the UI fast. Set to a high number (e.g. `50`) to always show them.

```
"card_count_threshold": 10
```

---

## `remember_yield_selection`
**Default:** `false`

When `true`, the Page Selector re-selects the yield level you last chose whenever it
opens (remembered for the rest of the Anki session). Useful when making many cards
at the same yield in a row.

```
"remember_yield_selection": false
```

---

## `remember_subtag_selection`
**Default:** `false`

When `true`, new search results have their subtag chip pre-set to the subtag you last
picked (remembered for the rest of the Anki session). Useful when tagging many cards
under the same subtag (e.g. a Management run).

```
"remember_subtag_selection": false
```
