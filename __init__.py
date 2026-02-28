"""
Malleus Anki Addon
Main entry point for the addon
"""
import os
from aqt import mw, dialogs
from aqt.qt import QAction, QShortcut, QKeySequence, Qt, QMenu
from aqt.utils import showInfo
from anki.hooks import addHook
from aqt.gui_hooks import browser_menus_did_init, browser_will_show, add_cards_did_init, editor_did_load_note
import weakref

# Import sip for Qt object checking
try:
    import sip
except ImportError:
    # Qt6 uses different import
    from PyQt6 import sip

# Load environment variables
addon_dir = os.path.dirname(os.path.realpath(__file__))

# Import submodules
from .config import load_config, DATABASES
from .notion_cache import NotionCache
from .ui.page_selector import NotionPageSelector
from .ui.randomization_dialog import show_randomization_dialog, setup_editor_buttons
from .utils import open_browser_with_search
from .ui.update_subject_tags import update_subject_tags_for_browser

# Initialize config first
config = load_config()

# Initialize cache instance
notion_cache = NotionCache(addon_dir, config)

def show_page_selector(parent=None):
    """Show the page selector dialog with the appropriate parent window"""
    from aqt.qt import QWidget
    
    # Ensure we have a proper QWidget parent
    if parent is None or not isinstance(parent, QWidget):
        parent = mw

    # Use a dictionary to track dialogs per parent
    if not hasattr(parent, '_malleus_dialogs'):
        parent._malleus_dialogs = []

    # Clean up any deleted dialogs
    parent._malleus_dialogs = [d for d in parent._malleus_dialogs if not sip.isdeleted(d)]

    # Create new dialog with proper parent
    dialog = NotionPageSelector(parent, notion_cache, config)

    # Set up browser note selection change handler
    from aqt.browser import Browser
    if isinstance(parent, Browser):
        # Store original currentRowChanged handler
        original_handler = parent.onRowChanged

        # Create a wrapper function that updates the dialog
        def row_changed_wrapper(current, previous):
            # Call the original handler first
            original_handler(current, previous)

            # Update the dialog's current_note reference
            if dialog and not sip.isdeleted(dialog):
                if hasattr(parent, 'editor') and hasattr(parent.editor, 'note'):
                    dialog.current_note = parent.editor.note

        # Replace the browser's row change handler
        parent.onRowChanged = row_changed_wrapper

        # Restore original handler when dialog closes
        def on_dialog_finished():
            if hasattr(parent, 'onRowChanged') and parent.onRowChanged == row_changed_wrapper:
                parent.onRowChanged = original_handler

        dialog.finished.connect(on_dialog_finished)

    parent._malleus_dialogs.append(dialog)
    dialog.show()
    return dialog

def download_github_cache(browser=None):
    """Download cache from GitHub repository and update cache from Notion"""
    from .cache_updater import perform_cache_update
    perform_cache_update(notion_cache, mw)

# Register shortcut in a window
def register_shortcut(window):
    shortcut_key = config.get('shortcut', 'Ctrl+Alt+M')
    shortcut = QShortcut(QKeySequence(shortcut_key), window)

    # Handle Qt version differences for shortcut context
    if hasattr(Qt, 'ShortcutContext'):
        # Qt6 style
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
    else:
        # Qt5 style
        shortcut.setContext(Qt.WindowShortcut)

    weak_window = weakref.ref(window)

    def trigger():
        target_window = weak_window()
        if target_window and not sip.isdeleted(target_window):
            show_page_selector(target_window)

    shortcut.activated.connect(trigger)

    if not hasattr(window, '_malleus_shortcuts'):
        window._malleus_shortcuts = []
    window._malleus_shortcuts.append(shortcut)

def register_shortcuts():
    """Register shortcut for main window - others via hooks"""
    register_shortcut(mw)

# Hooks to register shortcut when windows are created
def on_browser_setup(browser):
    register_shortcut(browser)

def on_addcards_setup(add_cards_dialog):
    register_shortcut(add_cards_dialog)

def on_editor_did_load_note(editor):
    # Check if we're in an EditCurrent window
    from aqt.editcurrent import EditCurrent
    if isinstance(editor.parentWindow, EditCurrent):
        register_shortcut(editor.parentWindow)

def setup_browser_menu(browser):
    """Setup browser menu items"""
    from aqt.qt import QToolBar
    
    # Get or create Malleus menu
    def getMenu(parent, menu_name):
        menubar = parent.form.menubar
        for action in menubar.actions():
            if menu_name == action.text():
                return action.menu()
        return menubar.addMenu(menu_name)

    notion_menu = getMenu(browser, "&Malleus")

    # Add action for showing page selector
    page_selector_action = QAction(browser)
    page_selector_action.setText("Find/Create Malleus Cards")
    notion_menu.addAction(page_selector_action)
    page_selector_action.triggered.connect(lambda _, b=browser: show_page_selector(b))

    # Add action for updating Notion cache
    update_cache_action = QAction(browser)
    update_cache_action.setText("Update Malleus Database Cache")
    notion_menu.addAction(update_cache_action)
    update_cache_action.triggered.connect(lambda _, b=browser: download_github_cache(b))

    # Add to browser toolbar
    try:
        toolbar = browser.findChild(QToolBar)
        if toolbar:
            page_selector_button = QAction(browser)
            page_selector_button.setText("Malleus")
            page_selector_button.setToolTip("Find/Create Malleus Cards")
            page_selector_button.triggered.connect(lambda _, b=browser: show_page_selector(b))
            toolbar.addAction(page_selector_button)
    except:
        pass

def setup_browser_context_menu(browser, menu):
    """Setup browser context menu (right-click menu)"""
    # Only show if cards are selected
    selected_cards = browser.selectedCards()
    if not selected_cards:
        return
    
    # Add separator before our menu item
    menu.addSeparator()
    
    # Add "Update Malleus Subject Tags" action
    update_tags_action = QAction("Update Malleus Subject Tags", browser)
    update_tags_action.triggered.connect(
        lambda: update_subject_tags_for_browser(browser, notion_cache, config)
    )
    menu.addAction(update_tags_action)

def init_notion_cache():
    """Initialize the cache check asynchronously on startup"""
    import threading
    
    def check_caches():
        try:
            print("Starting background cache check...")
            
            for db_id, name in DATABASES:
                if not db_id:
                    print(f"Skipping {name} - no database ID")
                    continue

                print(f"Checking {name} cache status...")
                if notion_cache.is_cache_expired(db_id):
                    print(f"{name} cache is expired, attempting GitHub download...")
                    if notion_cache.download_cache_from_github(db_id):
                        print(f"Successfully updated {name} cache from GitHub")
                        continue
                    print(f"GitHub download failed for {name}, falling back to Notion update...")
                    notion_cache.update_cache_async(db_id, force=True)
                else:
                    print(f"{name} cache is up to date")

            print("Background cache check completed")

        except Exception as e:
            print(f"Error in background cache check: {e}")

    thread = threading.Thread(target=check_caches, daemon=True)
    thread.start()

# Setup menu action
malleus_add_card_action = QAction("Malleus Find/Add Cards", mw)
malleus_add_card_action.triggered.connect(show_page_selector)
mw.form.menuTools.addAction(malleus_add_card_action)

# Register all hooks
browser_menus_did_init.append(setup_browser_menu)
browser_will_show.append(on_browser_setup)
add_cards_did_init.append(on_addcards_setup)
editor_did_load_note.append(on_editor_did_load_note)

# Register browser context menu hook
try:
    # Try to import the context menu hook (Anki 2.1.55+)
    from aqt.gui_hooks import browser_will_show_context_menu
    browser_will_show_context_menu.append(setup_browser_context_menu)
except ImportError:
    # Fallback for older Anki versions
    print("Browser context menu hook not available in this Anki version")

# Setup editor buttons with callback
addHook("setupEditorButtons", lambda buttons, editor: setup_editor_buttons(buttons, editor, show_page_selector))

# Initial registration for main window
register_shortcuts()

# Initialize cache when addon is loaded
init_notion_cache()
