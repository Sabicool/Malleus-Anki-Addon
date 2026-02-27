"""
Utility functions for the Malleus addon
"""
import json
from aqt import dialogs

def get_anki_version():
    """Get the current Anki version"""
    try:
        # 2.1.50+ because of bdd5b27715bb11e4169becee661af2cb3d91a443, https://github.com/ankitects/anki/pull/1451
        from anki.utils import point_version
    except:
        try:
            # introduced with 66714260a3c91c9d955affdc86f10910d330b9dd in 2020-01-19, should be in 2.1.20+
            from anki.utils import pointVersion
        except:
            # <= 2.1.19
            from anki import version as anki_version
            out = int(anki_version.split(".")[-1])
        else:
            out = pointVersion()
    else:
        out = point_version()
    return out

anki_point_version = get_anki_version()

def insert_at_cursor(editor, html):
    """Insert HTML at the cursor position in the editor"""
    if anki_point_version <= 49:
        # For older Anki versions
        js = "document.execCommand('insertHTML', false, %s);" % json.dumps(html)
    else:
        # For newer Anki versions, we need to work directly with the selection
        js = """
(function() {
    // Get the active element
    var activeElement = document.activeElement;

    // Ensure we have focus on a field
    if (!activeElement || !activeElement.classList.contains('field')) {
        // If no field is active, find and focus the first field
        var fields = document.querySelectorAll('.field');
        if (fields.length > 0) {
            activeElement = fields[0];
            activeElement.focus();
        }
    }

    // Insert the content at cursor position
    if (activeElement) {
        // Ensure field is focused before inserting
        activeElement.focus();

        // Small delay to ensure focus is complete and selection is restored
        setTimeout(function() {
            document.execCommand('insertHTML', false, %s);
        }, 50);
    }
})();
""" % json.dumps(html)

    # Ensure editor is focused before running JavaScript
    editor.web.setFocus()
    editor.web.eval(js)

def open_browser_with_search(search_query):
    """Open the browser with a search query"""
    from aqt import mw
    browser = dialogs.open('Browser', mw)
    browser.activateWindow()

    if search_query:
        browser.form.searchEdit.lineEdit().setText(search_query)
        if hasattr(browser, 'onSearch'):
            browser.onSearch()
        else:
            browser.onSearchActivated()
    return
