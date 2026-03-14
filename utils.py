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


def malleus_tooltip(msg: str, period: int = 3000, parent=None) -> None:
    """
    Replacement for aqt.utils.tooltip that always renders with a solid
    background on macOS + Qt6.

    Anki's built-in tooltip() uses Qt.WindowType.ToolTip as the window flag.
    On macOS with Qt6 that causes the OS compositor to treat the window as a
    native tooltip and apply its own (often transparent) background.

    Fix: use FramelessWindowHint + Tool + WindowStaysOnTopHint, and set the
    background via an explicit hex stylesheet rather than a palette lookup
    (palette colours can also resolve to transparent on some builds).
    """
    from aqt import mw
    from aqt.qt import (
        QLabel, QTimer, Qt, QCursor, QApplication,
    )

    if parent is None:
        try:
            parent = mw.app.activeWindow() or mw
        except Exception:
            parent = None

    label = QLabel(msg, parent)
    label.setWordWrap(True)
    label.setContentsMargins(10, 8, 10, 8)

    # Resolve colours from the application palette at call-time so the tooltip
    # matches the current Anki theme (light or dark) without any hardcoding.
    try:
        from aqt.qt import QApplication, QPalette, QColor
        palette  = QApplication.instance().palette()
        bg_color = palette.color(QPalette.ColorRole.ToolTipBase)
        fg_color = palette.color(QPalette.ColorRole.ToolTipText)
        # If the palette colours are transparent (the macOS Qt6 bug), fall back
        # to explicit neutrals that look reasonable on both light and dark themes.
        if bg_color.alpha() < 200:
            bg_color = QColor("#2b2b2b")
            fg_color = QColor("#f0f0f0")
        bg_hex = bg_color.name()
        fg_hex = fg_color.name()
    except Exception:
        bg_hex = "#2b2b2b"
        fg_hex = "#f0f0f0"

    label.setStyleSheet(f"""
        QLabel {{
            background-color: {bg_hex};
            color: {fg_hex};
            border: 1px solid rgba(128,128,128,0.5);
            border-radius: 5px;
            font-size: 12px;
            padding: 2px 4px;
        }}
    """)

    # Window flags: frameless + stays-on-top, but NOT Qt.WindowType.ToolTip.
    # The ToolTip type triggers macOS native compositing → transparent bg.
    label.setWindowFlags(
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
        | Qt.WindowType.BypassWindowManagerHint
    )
    label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    label.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    label.adjustSize()

    # Position just below-right of the cursor; clamp to screen bounds.
    try:
        pos    = QCursor.pos()
        screen = QApplication.screenAt(pos)
        if screen:
            geom = screen.availableGeometry()
            x = min(pos.x() + 16, geom.right()  - label.width()  - 4)
            y = min(pos.y() + 24, geom.bottom() - label.height() - 4)
            label.move(x, y)
    except Exception:
        pass

    label.show()
    QTimer.singleShot(period, label.deleteLater)
