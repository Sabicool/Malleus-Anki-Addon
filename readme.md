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

This is an Anki addon that integrates with the Malleus notion database to seamlessly manage Malleus clinical medicine cards. The addon allows you to *search the Malleus Notion database, find existing cards with matching tags, and create new cards with proper tagging*.

<br />

• [Malleus Clinical Medicine Website](https://malleuscm.notion.site) •
[Ankiweb Link](https://ankiweb.net/shared/info/620451841) •
[Showcase](#Showcase) •
[Usage](#Usage) •

</div>


<a id="org724359b"></a>
## Showcase
- [ ] I'm scripting a youtube video for the Malleus youtube channel

<a id="org48ca793"></a>

## Usage


<a id="org4bd0b74"></a>

### Finding Existing Cards

1.  Click the &ldquo;Malleus&rdquo; button in the browser toolbar or editor
2.  Select your database (Subjects or Pharmacology)
3.  Enter a search term
4.  Select the subtag you want to search for (leave as Tag to ignore subtag)
5.  Choose the relevant pages from the search results
6.  Click &ldquo;Find Cards&rdquo; to search for existing cards with matching tags


<a id="org3f69585"></a>

### Creating New Cards

1.  Follow steps 1-5 above
2.  Click &ldquo;Create Cards&rdquo; to open the Add Cards window
3.  The cards will be pre-filled with the appropriate tags

Note that you want to select a subject tag for the cards you are creating on. If you are unsure of what particular subtag to use, you can select Main Tag. Additionally you can access the menu from the add card dialogue.


<a id="org335319f"></a>

## Installation


<a id="orgc3dfd8f"></a>

### Through ankiweb

Now on ankiweb: <https://ankiweb.net/shared/info/620451841>. To download this add-on, please copy and paste the following code into Anki 2.1:

    620451841


<a id="org4e7a286"></a>

### Manual Installation

1.  Download the extension files
2.  Run the `update_notion_cache.py` python script (takes a little bit of time)

``` sh
python3 ./update_notion_cache.py
```

3.  Place them in your Anki addons folder:

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
<a id="orgb485ee1"></a>

## Configuration

If you have renamed or moved the Malleus deck it is worthwhile to change `deck_name` in the add on configuration manager to the correct name. This way the create card button will open to the correct deck.

The `cache_expiry` is the number of days after which the local database copy of the notion database will expire and any pages updated since the local database copy will be updated.


<a id="org3262658"></a>

## Directions for the Future

-   [X] Add button in editor page
-   [X] Publish to Ankiweb addons
-   [X] Keep local cache and update only newly updated
-   [ ] Add configuration for:
    -   [ ] Keybindings
    -   [X] Default deck location
    -   [X] Cache expiry duration
-   [X] Add eTG database
-   [ ] Add rotation database
-   [ ] Integrate full database syncs using python script
-   [ ] Integrate with local LLM or neural network to suggest tags


<a id="org164e890"></a>

## Troubleshooting

This extension requires access to the internet to use the notion API. Please ensure you are using it while connected to the internet.

There are some issues when trying to use the add-on while the database is being updated. Try re-installing the addon if issues occur.

<a id="org389a9f6"></a>

## Limitations

I have not yet implemented full database syncs into the add on. Full database syncs can take a bit of time unfortunately and I will update the cache and re-upload the Anki addon when this occurs. I could consider writing a script in python to do this on a publicly hosted github repository and then the cache can be downloaded the cache from there.


<a id="orgd2879ee"></a>

## Licence

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0).

This license grants you the freedom to use, modify, and distribute this software, provided that any derivative work or distribution is also licensed under the AGPL-3.0. Additionally, if you deploy this software on a network, users interacting with it over that network must also be granted access to the source code.

For more details, please refer to the full license text in the [LICENSE](./LICENSE) file or visit [GNU AGPL-3.0 License](https://www.gnu.org/licenses/agpl-3.0.en.html).

