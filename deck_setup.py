"""
Malleus deck setup — AnkiHub → AnkiCollab
Everything the setup wizard (ui/deck_setup_wizard.py) needs to know about the
user's situation, and every action it takes on their behalf.

The Malleus deck is moving from AnkiHub to AnkiCollab.  Most users aren't
technical, so instead of a list of instructions the add-on works out where
each user is and does the steps for them:

  new      no Malleus cards at all            → install AnkiCollab, subscribe
  migrate  Malleus cards, not on AnkiCollab   → leave AnkiHub, set the old deck
                                                aside, subscribe
  dual     on AnkiCollab *and* still on       → leave AnkiHub (the failsafe:
           AnkiHub                              both add-ons syncing one deck
                                                collide)
  done     on AnkiCollab only                 → nothing, or sign in if they
                                                aren't (AnkiCollab requires an
                                                account)

Talking to the other two add-ons means importing their internals — neither
has a public API — so every call here is wrapped: if a call fails, the
wizard falls back to sending the user to that add-on's own window instead of
crashing.

The deck key and the Malleus AnkiHub deck ids live in ankicollab.json in the
GitHub repo, not in code, so the switch can be turned on (and the key
changed) without shipping an add-on update.
"""
import importlib
import json
import os
import re
import threading
import time
from typing import Optional

from aqt import mw

ANKICOLLAB_ADDON_ID = "1957538407"
ANKIHUB_ADDON_ID = "1322529746"
ANKICOLLAB_WEBSITE = "https://ankicollab.com"

REMOTE_SETTINGS_URL = (
    "https://raw.githubusercontent.com/Sabicool/Malleus-Anki-Addon/main/ankicollab.json"
)

# Used until the first download of ankicollab.json succeeds, so someone
# whose network blocks GitHub still gets the setup.
DEFAULT_SETTINGS = {
    "enabled": True,
    "deck_key": "malleus-au-nz",
    # Keys the deck had before a rename.  A subscription saved under one of
    # these is moved to deck_key (same deck, so AnkiCollab updates in place).
    "previous_deck_keys": ["vermont-spaghetti-georgia-black-football-autumn"],
    "deck_name": "Malleus Clinical Medicine",
    "ankihub_deck_ids": ["5a1160ef-6f41-4d8c-948b-f72275ef0ff7"],
    "old_deck_names": [
        "Malleus Clinical Medicine (AU/NZ)",
        "Malleus Clinical Medicine",
    ],
    "prompt_new_users": True,
}

# Add-on config key that overrides the published deck key (and switches the
# wizard on) — for testing against a test deck before the real one exists.
KEY_OVERRIDE_CONFIG = "ankicollab_deck_key_override"

# Set to True only in the maintainers' test build (package.sh TESTER=1), so
# the wizard is on for them before it's switched on for everyone.  It lives
# in code rather than the add-on config because the config is saved to disk
# and would outlive the test build; code is replaced by the next update.
TESTER_BUILD = False

# Per-profile wizard state, kept in Anki's profile prefs (local, never synced).
STATE_KEY = "malleus_deck_setup"

_addon_dir = os.path.dirname(os.path.realpath(__file__))
_settings_cache_path = os.path.join(_addon_dir, "user_files", "ankicollab.json")


# ── Remote settings ──────────────────────────────────────────────────────────

def load_settings() -> dict:
    """Published settings (last downloaded copy), with the local key override
    applied on top."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(_settings_cache_path, "r", encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, ValueError):
        pass

    if TESTER_BUILD:
        settings["enabled"] = True

    override = (mw.addonManager.getConfig(__name__) or {}).get(KEY_OVERRIDE_CONFIG)
    if override:
        settings["deck_key"] = override.strip()
        settings["enabled"] = True
    return settings


def refresh_settings(on_done=None):
    """Download ankicollab.json in the background, then call on_done() on the
    main thread whether or not the download worked."""
    def run():
        try:
            import requests
            resp = requests.get(REMOTE_SETTINGS_URL, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    os.makedirs(os.path.dirname(_settings_cache_path), exist_ok=True)
                    tmp = _settings_cache_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    os.replace(tmp, _settings_cache_path)
        except Exception as e:
            print(f"Malleus: couldn't refresh AnkiCollab settings: {e}")
        if on_done:
            mw.taskman.run_on_main(on_done)

    threading.Thread(target=run, daemon=True).start()


def is_available(settings: Optional[dict] = None) -> bool:
    """True once there's a deck to point people at."""
    settings = settings or load_settings()
    return bool(settings.get("enabled") and settings.get("deck_key"))


# ── Per-profile state ────────────────────────────────────────────────────────

def get_state() -> dict:
    state = mw.pm.profile.get(STATE_KEY)
    return dict(state) if isinstance(state, dict) else {}


def update_state(**changes):
    state = get_state()
    state.update(changes)
    mw.pm.profile[STATE_KEY] = state
    try:
        mw.pm.save()
    except Exception:
        pass  # saved again when the profile closes


# ── Other add-ons: generic helpers ───────────────────────────────────────────

def addon_status(addon_id: str) -> str:
    """'missing' | 'disabled' | 'restart' (installed but not loaded yet) |
    'loaded'."""
    mgr = mw.addonManager
    if addon_id not in mgr.allAddons():
        return "missing"
    if not mgr.isEnabled(addon_id):
        return "disabled"
    import sys
    if addon_id not in sys.modules:
        return "restart"
    return "loaded"


def _module(addon_id: str, name: str):
    return importlib.import_module(f"{addon_id}.{name}")


# ── AnkiCollab ──────────────────────────────────────────────────────────────

_RESERVED_COLLAB_KEYS = ("settings", "auth")


def _collab_read_config() -> dict:
    # Newer AnkiCollab keeps a per-profile config; older versions use Anki's
    # add-on config.
    try:
        return _module(ANKICOLLAB_ADDON_ID, "config_manager").get_stored_config() or {}
    except Exception:
        return mw.addonManager.getConfig(ANKICOLLAB_ADDON_ID) or {}


def _collab_write_config(data: dict):
    try:
        _module(ANKICOLLAB_ADDON_ID, "config_manager").write_config(data)
    except Exception:
        mw.addonManager.writeConfig(ANKICOLLAB_ADDON_ID, data)


def collab_subscription(deck_key: str) -> Optional[dict]:
    """AnkiCollab's local record for this deck, or None if not subscribed."""
    if addon_status(ANKICOLLAB_ADDON_ID) != "loaded" or not deck_key:
        return None
    details = _collab_read_config().get(deck_key)
    return details if isinstance(details, dict) else None


def collab_carry_over_renamed_key(settings: dict) -> bool:
    """Move a subscription saved under one of the deck's previous keys to the
    current key.  The deck itself is the same, so AnkiCollab keeps updating
    it in place; under the old key it would get nothing (the old key stops
    working once the deck is renamed).  True if anything was moved."""
    key = settings.get("deck_key", "")
    if addon_status(ANKICOLLAB_ADDON_ID) != "loaded" or not key:
        return False
    data = _collab_read_config()
    if isinstance(data.get(key), dict):
        old_entries = [k for k in settings.get("previous_deck_keys", []) if k in data]
        if not old_entries:
            return False
        for old in old_entries:     # already on the new key: just drop leftovers
            del data[old]
    else:
        old = next((k for k in settings.get("previous_deck_keys", [])
                    if isinstance(data.get(k), dict)), None)
        if not old:
            return False
        data[key] = data.pop(old)
    _collab_write_config(data)
    return True


def collab_installed_deck_id(deck_key: str) -> int:
    """Anki deck id AnkiCollab installed the deck into, or 0 while it is
    still downloading (or if the download failed)."""
    details = collab_subscription(deck_key)
    if not details:
        return 0
    did = details.get("deckId") or 0
    if did and mw.col and mw.col.decks.get(did, default=False):
        return did
    return 0


def collab_is_logged_in() -> bool:
    try:
        return bool(_module(ANKICOLLAB_ADDON_ID, "auth_manager").auth_manager.is_logged_in())
    except Exception:
        return False


def collab_open_login(parent):
    """Open AnkiCollab's own sign-in window (it signs in by approving a code
    in the browser — no password typing needed)."""
    dialogs = _module(ANKICOLLAB_ADDON_ID, "dialogs")
    dialogs.LoginDialog(parent).exec()
    try:
        _module(ANKICOLLAB_ADDON_ID, "menu").update_ui_for_login_state()
    except Exception:
        pass


def collab_signup_url() -> str:
    try:
        return _module(ANKICOLLAB_ADDON_ID, "var_defs").WEBSITE_BASE_URL + "/signup"
    except Exception:
        return ANKICOLLAB_WEBSITE + "/signup"


def collab_subscribe(deck_key: str, on_pulled=None):
    """Subscribe to the deck and start AnkiCollab's download, exactly as its
    Manage Subscriptions → Add button does (minus the "only install decks you
    trust" warning — this is the deck the user came for).

    on_pulled(ok) runs once AnkiCollab has fetched the deck and handed it to
    its installer; installation itself carries on in the background, so
    callers watch collab_installed_deck_id() for the finish line.
    """
    data = _collab_read_config()
    if deck_key not in data:
        try:
            protected = list(_module(ANKICOLLAB_ADDON_ID, "var_defs").DEFAULT_PROTECTED_TAGS)
        except Exception:
            protected = ["leech", "marked", "missing-media"]
        data[deck_key] = {
            "timestamp": "2022-12-31 23:59:59",
            "deckId": 0,
            "optional_tags": {},
            "personal_tags": protected,
        }
        _collab_write_config(data)

    # Bumps the subscriber count on the website (needs sign-in; harmless if
    # it fails).
    def count_subscription():
        try:
            _module(ANKICOLLAB_ADDON_ID, "identifier").subscribe_to_deck(deck_key)
        except Exception:
            pass
    threading.Thread(target=count_subscription, daemon=True).start()

    import_manager = _module(ANKICOLLAB_ADDON_ID, "import_manager")
    from aqt.operations import QueryOp

    def on_success(result):
        webresult = result[0] if isinstance(result, tuple) and result else result
        try:
            import_manager.import_webresult(result)
        finally:
            if on_pulled:
                on_pulled(bool(webresult))

    def on_failure(exc):
        print(f"Malleus: AnkiCollab download failed: {exc}")
        if on_pulled:
            on_pulled(False)

    QueryOp(
        parent=mw,
        op=lambda _: import_manager.async_start_pull(deck_key, False),
        success=on_success,
    ).failure(on_failure).with_progress(
        "Downloading the Malleus deck from AnkiCollab..."
    ).run_in_background()


def collab_forget_subscription(deck_key: str):
    """Drop the local subscription so it can be added again (the "it didn't
    create the deck" retry)."""
    data = _collab_read_config()
    if deck_key in data:
        del data[deck_key]
        _collab_write_config(data)


def collab_set_update_on_startup(enabled: bool):
    data = _collab_read_config()
    data.setdefault("settings", {})["pull_on_startup"] = bool(enabled)
    _collab_write_config(data)


def collab_update_on_startup() -> bool:
    return bool(_collab_read_config().get("settings", {}).get("pull_on_startup"))


def collab_open_subscriptions():
    """Fallback: AnkiCollab's own Manage Subscriptions window."""
    _module(ANKICOLLAB_ADDON_ID, "menu").on_edit_list()


def install_ankicollab(on_done):
    """Download AnkiCollab from AnkiWeb (or re-enable it).  on_done(ok, msg)
    runs on the main thread; Anki must restart before it loads."""
    status = addon_status(ANKICOLLAB_ADDON_ID)
    if status == "disabled":
        mw.addonManager.toggleEnabled(ANKICOLLAB_ADDON_ID, True)
        on_done(True, "")
        return
    if status in ("restart", "loaded"):
        on_done(True, "")
        return

    from aqt.addons import download_addons, download_encountered_problem, download_log_to_html

    def finished(log):
        if download_encountered_problem(log):
            on_done(False, download_log_to_html(log))
        else:
            on_done(True, "")

    download_addons(mw, mw.addonManager, [int(ANKICOLLAB_ADDON_ID)], finished)


# ── AnkiHub ─────────────────────────────────────────────────────────────────

def ankihub_malleus_decks(settings: dict) -> list:
    """Malleus decks the AnkiHub add-on is still syncing in this profile:
    [{'uuid', 'name', 'anki_did'}].  Empty when AnkiHub isn't running — a
    disabled or removed AnkiHub can't collide with AnkiCollab."""
    if addon_status(ANKIHUB_ADDON_ID) != "loaded":
        return []
    try:
        config = _module(ANKIHUB_ADDON_ID, "settings").config
        wanted = {str(u).lower() for u in settings.get("ankihub_deck_ids", [])}
        found = []
        for ah_did in config.deck_ids():
            deck_config = config.deck_config(ah_did)
            name = getattr(deck_config, "name", "") or ""
            if str(ah_did).lower() in wanted or "malleus" in name.lower():
                found.append({
                    "uuid": ah_did,
                    "name": name,
                    "anki_did": int(getattr(deck_config, "anki_id", 0) or 0),
                })
        return found
    except Exception as e:
        print(f"Malleus: couldn't read AnkiHub subscriptions: {e}")
        return []


def ankihub_unsubscribe_on_server(ah_did) -> bool:
    """Network call — run off the main thread.  False when signed out or
    offline; the local removal still stops AnkiHub syncing the deck."""
    try:
        client_mod = _module(ANKIHUB_ADDON_ID, "addon_ankihub_client")
        client_mod.AddonAnkiHubClient().unsubscribe_from_deck(ah_did)
        return True
    except Exception as e:
        print(f"Malleus: AnkiHub server unsubscribe failed: {e}")
        return False


def ankihub_remove_locally(ah_did):
    """Stop the AnkiHub add-on managing the deck and undo its note type
    changes.  Cards and review history stay put."""
    _module(ANKIHUB_ADDON_ID, "main.deck_unsubscribtion").uninstall_deck(ah_did)


# ── The collection ──────────────────────────────────────────────────────────

def has_malleus_notes() -> bool:
    """Any notes of a Malleus note type — however they got here."""
    mids = [nt.id for nt in mw.col.models.all_names_and_ids() if "malleus" in nt.name.lower()]
    if not mids:
        return False
    ids = ",".join(str(int(m)) for m in mids)
    return bool(mw.col.db.scalar(f"select 1 from notes where mid in ({ids}) limit 1"))


# Shared content that would otherwise clutter the editor and flood searches.
# AnkiHub's note type shipped these collapsed and excluded from unqualified
# searches; AnkiCollab's doesn't, and replaces local field settings whenever
# the maintainers change the field list, so this is reapplied at startup.
SYNCED_FIELDS = ("Extra (Synced)", "Additional Resources (Synced)")


def fix_synced_field_options() -> int:
    """Collapse the synced fields and exclude them from unqualified searches
    on every Malleus note type.  Returns how many note types changed.

    Field options aren't a schema change, so this syncs normally (no full
    upload)."""
    wanted = {name.lower() for name in SYNCED_FIELDS}
    changed = 0
    for entry in mw.col.models.all_names_and_ids():
        if not entry.name.lower().startswith("malleuscm"):
            continue
        nt = mw.col.models.get(entry.id)
        dirty = False
        for fld in nt["flds"]:
            if fld["name"].lower() not in wanted:
                continue
            for key in ("collapsed", "excludeFromSearch"):
                if not fld.get(key):
                    fld[key] = True
                    dirty = True
        if dirty:
            mw.col.models.update_dict(nt)
            changed += 1
    return changed


def old_malleus_decks(settings: dict, ankihub_decks: list) -> list:
    """Top-level deck ids holding a pre-AnkiCollab copy of Malleus."""
    dids = []
    for d in ankihub_decks:
        did = d.get("anki_did")
        if did and mw.col.decks.get(did, default=False):
            # AnkiHub records the deck it installed into, which may be a subdeck
            # if the user moved it; set aside the whole top-level tree.
            name = mw.col.decks.name(did)
            top = mw.col.decks.by_name(name.split("::")[0])
            dids.append(top["id"] if top else did)
    for name in settings.get("old_deck_names", []):
        deck = mw.col.decks.by_name(name)
        if deck:
            dids.append(deck["id"])
    return list(dict.fromkeys(dids))


def set_deck_aside(did) -> str:
    """Rename a deck so AnkiCollab builds a fresh deck tree next to it.
    Returns the new name."""
    base = mw.col.decks.name(did).split("::")[0] + " (old copy)"
    name, n = base, 2
    while mw.col.decks.by_name(name):
        name = f"{base[:-1]} {n})"
        n += 1
    mw.col.decks.rename(did, name)
    return name


DEFAULT_PRESET_ID = 1


def copy_deck_options(old_did, new_did) -> int:
    """Put the new deck tree on the options presets the old one used, so
    limits, FSRS and the rest carry over.  A subdeck takes the preset of the
    old subdeck at the same path, or else the old top-level deck's.  Only
    decks still on Default change, so a choice the user has made since is
    kept.  The preset is shared rather than duplicated, and AnkiCollab never
    sets a deck's preset, so this survives its updates.  Returns how many
    decks changed."""
    old_top = mw.col.decks.name(old_did)
    new_top = mw.col.decks.name(new_did)
    old_presets = {}
    for did in mw.col.decks.deck_and_child_ids(old_did):
        deck = mw.col.decks.get(did)
        if not deck.get("dyn"):
            old_presets[deck["name"][len(old_top):]] = deck["conf"]
    fallback = old_presets.get("", DEFAULT_PRESET_ID)

    changed = 0
    for did in mw.col.decks.deck_and_child_ids(new_did):
        deck = mw.col.decks.get(did)
        if deck.get("dyn") or deck["conf"] != DEFAULT_PRESET_ID:
            continue
        preset = old_presets.get(deck["name"][len(new_top):], fallback)
        if preset != DEFAULT_PRESET_ID and mw.col.decks.get_config(preset):
            deck["conf"] = preset
            mw.col.decks.save(deck)
            changed += 1
    return changed


def old_copy_decks() -> list:
    """Top-level decks set_deck_aside renamed and the user hasn't deleted."""
    pattern = re.compile(r" \(old copy(?: \d+)?\)$")
    return [d.id for d in mw.col.decks.all_names_and_ids()
            if "::" not in d.name and pattern.search(d.name)]


def deck_card_count(did) -> int:
    ids = ",".join(str(int(d)) for d in mw.col.decks.deck_and_child_ids(did))
    return mw.col.db.scalar(f"select count() from cards where did in ({ids}) or odid in ({ids})") or 0


# ── Where is this user? ─────────────────────────────────────────────────────

def detect(settings: Optional[dict] = None) -> dict:
    """Snapshot of the user's setup.  scenario is one of
    'new' | 'migrate' | 'dual' | 'done'."""
    settings = settings or load_settings()
    key = settings.get("deck_key", "")
    try:
        collab_carry_over_renamed_key(settings)
    except Exception as e:
        print(f"Malleus: couldn't move the AnkiCollab subscription to the new key: {e}")
    collab = addon_status(ANKICOLLAB_ADDON_ID)
    subscribed = collab_subscription(key) is not None
    ankihub = ankihub_malleus_decks(settings)

    if subscribed and ankihub:
        scenario = "dual"
    elif subscribed:
        scenario = "done"
    elif ankihub or has_malleus_notes():
        scenario = "migrate"
    else:
        scenario = "new"

    return {
        "scenario": scenario,
        "settings": settings,
        "deck_key": key,
        "collab_status": collab,
        "collab_logged_in": collab == "loaded" and collab_is_logged_in(),
        "subscribed": subscribed,
        "ankihub_decks": ankihub,
        "old_decks": old_malleus_decks(settings, ankihub) if not subscribed else [],
        "ankiweb": bool(mw.pm.sync_auth()),
    }


# ── AnkiWeb sync ────────────────────────────────────────────────────────────

def sync_ankiweb(on_done, upload_if_asked=False):
    """Run Anki's normal sync, then on_done(result) with result one of
    'ok' | 'full' (a one-way sync was needed) | 'error' | 'no_account'.

    Leaving AnkiHub removes a field from the Malleus note types, which makes
    AnkiWeb insist on a one-way sync, and Anki then asks "Upload or
    Download?" — the one question here a user can answer catastrophically.
    With upload_if_asked (only after this session already synced cleanly, so
    AnkiWeb holds nothing this computer lacks) the answer is given for them:
    upload.
    """
    import aqt.sync as anki_sync
    from aqt import gui_hooks

    if not mw.pm.sync_auth():
        on_done("no_account")
        return

    original_full_sync = anki_sync.full_sync
    original_handle_error = anki_sync.handle_sync_error
    outcome = {"result": "ok"}

    def full_sync(mw_, out, done):
        outcome["result"] = "full"
        if upload_if_asked and out.required == out.FULL_SYNC:
            server_usn = out.server_media_usn if mw_.pm.media_syncing_enabled() else None
            mw_.closeAllWindows(lambda: anki_sync.full_upload(mw_, server_usn, done))
        else:
            original_full_sync(mw_, out, done)

    def handle_sync_error(mw_, err):
        outcome["result"] = "error"
        original_handle_error(mw_, err)

    def restore():
        anki_sync.full_sync = original_full_sync
        anki_sync.handle_sync_error = original_handle_error
        gui_hooks.sync_did_finish.remove(finished)

    def finished():
        # Unhook on the next tick: removing ourselves while Anki is looping
        # over the hook list would skip the next add-on's callback.
        def later():
            restore()
            on_done(outcome["result"])
        from aqt.qt import QTimer
        QTimer.singleShot(0, later)

    anki_sync.full_sync = full_sync
    anki_sync.handle_sync_error = handle_sync_error
    gui_hooks.sync_did_finish.append(finished)
    try:
        # The sync button only shows the media log while media is syncing;
        # the collection sync underneath it always runs.
        sync = getattr(mw, "_sync_collection_and_media", None)
        if sync:
            sync(getattr(mw, "_refresh_after_sync", lambda: None))
        else:
            mw.on_sync_button_clicked()
    except Exception:
        restore()
        raise
