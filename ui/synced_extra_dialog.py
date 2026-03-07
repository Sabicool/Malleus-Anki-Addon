"""
Synced Extra Selection Dialog
Lets the user choose which Synced Extra entries to include in Extra (Synced).
Styled with the Malleus design system; adapts to Anki's light and dark themes.
"""
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QScrollArea, QWidget, QPushButton, QDialogButtonBox, QFrame, Qt
)
from aqt.utils import showInfo
from typing import List, Dict, Set

try:
    from .styles import apply_malleus_style, make_header, COLORS
except Exception:
    def apply_malleus_style(w): pass
    def make_header(*a, **kw):
        from aqt.qt import QWidget
        return QWidget()
    COLORS = {"accent": "#4a82cc", "border": "rgba(74,130,204,0.28)"}


class SyncedExtraSelectionDialog(QDialog):
    """
    Shows all SE entries matching the current note's tags as checkboxes.

    - Entries already present in the field (identified by <!-- se:N --> markers)
      are pre-checked.
    - New entries are unchecked by default.
    - Unchecking a pre-checked entry removes its content and tag from the note.
    - The context frame uses palette() roles so it respects Anki's dark mode.

    Args:
        parent:          Parent QWidget (browser, AddCards dialog, etc.)
        entries:         List of {title, content, se_id, tag} dicts
        existing_se_ids: Set of se_id strings already present in the field
        note_context:    Optional snippet from the note's Text field
    """

    def __init__(
        self,
        parent,
        entries: List[Dict],
        existing_se_ids: Set[str],
        note_context: str = None,
    ):
        super().__init__(parent)
        self.entries         = entries
        self.existing_se_ids = existing_se_ids
        self.note_context    = note_context
        self._selected       = []
        self._cancelled      = False
        self._checkboxes     = []
        self._setup_ui()
        apply_malleus_style(self)

    def _setup_ui(self):
        self.setWindowTitle("Synced Extra Content Available")
        self.setMinimumWidth(540)
        self.setMinimumHeight(380)

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(16, 12, 16, 14)

        # Branded header
        header = make_header(
            title="Synced Extra Content",
            subtitle="Select entries to include in Extra (Synced)"
        )
        layout.addWidget(header)

        # Note context — palette-safe, no hardcoded background
        if self.note_context:
            ctx_frame = QFrame()
            ctx_frame.setFrameShape(QFrame.Shape.StyledPanel)
            ctx_frame.setStyleSheet(
                "QFrame { border: 1px solid " + COLORS['border'] + "; "
                "border-radius: 6px; background: transparent; }"
            )
            ctx_layout = QVBoxLayout()
            ctx_layout.setContentsMargins(8, 6, 8, 6)
            ctx_layout.setSpacing(3)

            ctx_title = QLabel("Card context")
            ctx_title.setStyleSheet(
                "font-weight: 700; font-size: 11px; color: " + COLORS['accent'] + "; "
                "background: transparent; border: none;"
            )
            ctx_layout.addWidget(ctx_title)

            snippet = self.note_context[:250] + ("…" if len(self.note_context) > 250 else "")
            ctx_text = QLabel(snippet)
            ctx_text.setWordWrap(True)
            # palette(text) adapts to Anki's light/dark theme automatically
            ctx_text.setStyleSheet(
                "font-size: 10px; color: palette(text); "
                "background: transparent; border: none;"
            )
            ctx_layout.addWidget(ctx_text)

            ctx_frame.setLayout(ctx_layout)
            layout.addWidget(ctx_frame)

        # Scrollable checklist
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        cb_layout = QVBoxLayout()
        cb_layout.setSpacing(2)
        cb_layout.setContentsMargins(6, 6, 6, 6)

        for entry in self.entries:
            se_id    = entry.get("se_id", "")
            title    = entry.get("title", "Untitled")
            in_field = bool(se_id and se_id in self.existing_se_ids)

            cb = QCheckBox()
            cb.setChecked(in_field)
            cb.setText("")

            row_widget = QWidget()
            row_widget.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(4, 2, 4, 2)
            row_layout.setSpacing(8)
            row_layout.addWidget(cb)

            lbl_html = title
            if in_field:
                lbl_html += (
                    "  <span style='color:" + COLORS['accent'] + "; "
                    "font-size:10px; font-weight:600;'>✓ already in field</span>"
                )
            lbl = QLabel(lbl_html)
            lbl.setWordWrap(False)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet("font-size: 13px; background: transparent; border: none;")
            lbl.mousePressEvent = lambda _evt, c=cb: c.setChecked(not c.isChecked())
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            row_layout.addWidget(lbl, stretch=1)

            row_widget.setLayout(row_layout)
            cb_layout.addWidget(row_widget)
            self._checkboxes.append((cb, entry))

        cb_layout.addStretch()
        scroll_widget.setLayout(cb_layout)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # Select All / Deselect All
        sel_layout = QHBoxLayout()
        sel_layout.setSpacing(8)

        select_all_btn = QPushButton("Select All")
        select_all_btn.setObjectName("secondary")
        select_all_btn.clicked.connect(self._select_all)
        sel_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.setObjectName("secondary")
        deselect_all_btn.clicked.connect(self._deselect_all)
        sel_layout.addWidget(deselect_all_btn)

        sel_layout.addStretch()
        layout.addLayout(sel_layout)

        # OK / Cancel
        buttons = QDialogButtonBox()
        ok_btn = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setDefault(True)
        cancel_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel_btn.setObjectName("secondary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def _select_all(self):
        for cb, _ in self._checkboxes:
            cb.setChecked(True)

    def _deselect_all(self):
        for cb, _ in self._checkboxes:
            cb.setChecked(False)

    def _on_cancel(self):
        self._cancelled = True
        self.reject()

    def accept(self):
        self._selected = [entry for cb, entry in self._checkboxes if cb.isChecked()]
        super().accept()

    def get_selected_entries(self) -> List[Dict]:
        return self._selected

    def was_cancelled(self) -> bool:
        return self._cancelled
