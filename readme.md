<div align="Center">

# 🌟 Malleus Anki Helper Addon 🌟

<a href="https://malleuscm.notion.site">
    <b>
        The Malleus Clinical Medicine Anki deck is the best continually updated clinical medicine anki deck for Australian and New Zealand medical students.
    </b>
    <div>
        <sup>👉 Vist <u>malleuscm.notion.site</u> to learn more 👈</sup>
    </div>
</a>

<br />

This is an Anki addon that integrates with the Malleus Notion databases to seamlessly manage Malleus clinical medicine cards. The addon allows you to *search across all of the Malleus Notion databases at once, find existing cards with matching tags, and create new cards with proper tagging*.

A single search surfaces the best matches from every database — Subjects, Pharmacology, eTG, Rotation, Textbooks and Guidelines — and you can narrow it down with per-database filter chips. Tags (including subtags and yield levels) are applied automatically, and the local cache stays in sync with the daily Notion rebuild.

<br />

• [Malleus Clinical Medicine Website](https://malleuscm.notion.site) •
[Ankiweb Link](https://ankiweb.net/shared/info/620451841) •
[Usage](#Usage) •

<a id="org724359b"></a>
## Showcase
[📺 Watch the full howto and showcase playlist on YouTube](https://www.youtube.com/playlist?list=PLKoggb5cOb9lP5mCR2-2yqdFRe6aaqEaw)

[![Watch the playlist](https://img.youtube.com/vi/bgRVxccuMho/0.jpg)](https://www.youtube.com/playlist?list=PLKoggb5cOb9lP5mCR2-2yqdFRe6aaqEaw)

</div>

<a id="org48ca793"></a>

## Usage


<a id="org4bd0b74"></a>

### Finding Existing Cards

1.  Click the Malleus button in the browser toolbar or editor (or use the keyboard shortcut, `Ctrl+Alt+M` by default)
2.  Enter a search term — results from every database appear together, ranked by relevance
3.  (Optional) Toggle the database filter chips to narrow results to specific databases
4.  Check the relevant pages from the search results
5.  For pages that support subtags, pick the subtag from the chip that appears on each checked row (leave as Main Tag to tag the whole topic)
6.  Click Find Cards to search for existing cards with matching tags


<a id="org3f69585"></a>

### Creating New Cards

1.  Follow steps 1-5 above
2.  (Optional) Choose a yield level for the new cards
3.  Click Create Cards to open the Add Cards window
4.  The cards will be pre-filled with the appropriate tags

If you are unsure of what particular subtag to use, leave the row set to Main Tag. You can also open the page selector directly from the Add Cards dialogue.


<a id="org335319f"></a>

## Installation


<a id="orgc3dfd8f"></a>

### Through ankiweb

Now on ankiweb: <https://ankiweb.net/shared/info/620451841>. To download this add-on, please copy and paste the following code into Anki 2.1:

    620451841


<a id="org4e7a286"></a>

### Manual Installation

1.  Download the extension files
2.  Place the extensions files in your Anki addons folder:

<details>
<summary>Windows</summary>

> ```
> Windows: %APPDATA%\Anki2\addons21\
> ```

</details>

<details>
<summary>macOS</summary>

> ```
> Mac: ~/Library/Application Support/Anki2/addons21/
> ```

</details>

<details>
<summary>Linux</summary>

> ```
> Linux: ~/.local/share/Anki2/addons21/
> ```

</details>

3. Restart Anki

The add-on keeps a local cache of the databases and refreshes it automatically — on startup it checks GitHub and only downloads a database if it has actually changed since last time (see `cache_expiry` below). You normally never need to update it by hand.

If need be, you can also rebuild the cache directly from Notion yourself using the python script `update_notion_cache.py` (takes a little bit of time):

``` sh
python3 ./update_notion_cache.py
```

<a id="orgb485ee1"></a>

## Configuration

Settings are edited via **Tools → Add-ons → Malleus Clinical Medicine → Config**. The most common ones to change:

-   `deck_name` — if you have renamed or moved the Malleus deck, set this to the correct name (use `::` for subdecks) so the create card button opens to the right deck.
-   `shortcut` — keyboard shortcut to open the Page Selector (default `Ctrl+Alt+M`).
-   `cache_expiry` — number of days before the local cache is considered stale and the add-on re-checks GitHub. The check is conditional, so a short value keeps you in sync cheaply.

Other options control auto-search behaviour, network timeouts, per-result card counts, and whether the yield/subtag selections are remembered between cards. See [config.md](./config.md) for the full reference.


<a id="org3262658"></a>

## Directions for the Future

-   [X] Add button in editor page
-   [X] Publish to Ankiweb addons
-   [X] Keep local cache and update only newly updated
-   [X] Add configuration for:
    -   [X] Keybindings
    -   [X] Default deck location
    -   [X] Cache expiry duration
-   [X] Add eTG database
-   [X] Add rotation database
-   [X] Add guidelines database
-   [X] Integrate full database syncs using python script
-   [X] Add yield tags
-   [X] Unified search across all databases with filter chips
-   [ ] Integrate with local LLM or neural network to suggest tags
-   [ ] Add note type customisation options


<a id="org164e890"></a>

## Troubleshooting

There are some issues when trying to use the add-on while the database is being updated. Please wait for the progress bar to finish before using the page selector.

<a id="org389a9f6"></a>

## Licence

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0).

This license grants you the freedom to use, modify, and distribute this software, provided that any derivative work or distribution is also licensed under the AGPL-3.0. Additionally, if you deploy this software on a network, users interacting with it over that network must also be granted access to the source code.

For more details, please refer to the full license text in the [LICENSE](./LICENSE) file or visit [GNU AGPL-3.0 License](https://www.gnu.org/licenses/agpl-3.0.en.html).

