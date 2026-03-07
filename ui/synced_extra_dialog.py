"""
Synced Extra Selection Dialog
Lets the user choose which Synced Extra entries to include in Extra (Synced).
"""
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QScrollArea, QWidget, QPushButton, QDialogButtonBox, QFrame, Qt
)
from aqt.utils import showInfo
from typing import List, Dict, Set


class SyncedExtraSelectionDialog(QDialog):
    """
    Shows all SE entries matching the current note's tags as checkboxes.

    - Entries already present in the field (identified by <!-- se:N --> markers)
      are pre-checked.
    - New entries are unchecked by default.
    - Unchecking a pre-checked entry removes its content and tag from the note.

    Args:
        parent:          Parent QWidget (browser, AddCards dialog, etc.)
        entries:         List of {title, content, se_id, tag} from get_matching_se_entries()
        existing_se_ids: Set of se_id strings already present in the field
        note_context:    Optional snippet from the note's Text field (shown for batch ops)
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
        self._checkboxes     = []   # list of (QCheckBox, entry_dict)
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle("Synced Extra Content Available")
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        layout = QVBoxLayout()

        # ── Intro label ──────────────────────────────────────────────────────
        intro = QLabel(
            "The following Synced Extra content matches this card.\n"
            "Select the entries you want to include in Extra (Synced):"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("margin-bottom: 6px;")
        layout.addWidget(intro)

        # ── Note context (shown for batch / multi-note ops) ──────────────────
        if self.note_context:
            ctx_frame = QFrame()
            ctx_frame.setFrameShape(QFrame.Shape.StyledPanel)
            ctx_frame.setStyleSheet(
                "background-color: #f0f0f0; padding: 8px; border-radius: 4px; margin-bottom: 6px;"
            )
            ctx_layout = QVBoxLayout()

            ctx_title = QLabel("Card context:")
            ctx_title.setStyleSheet("font-weight: bold; font-size: 11px;")
            ctx_layout.addWidget(ctx_title)

            snippet = self.note_context[:250] + ("…" if len(self.note_context) > 250 else "")
            ctx_text = QLabel(snippet)
            ctx_text.setWordWrap(True)
            ctx_text.setStyleSheet("font-size: 10px; color: #444;")
            ctx_layout.addWidget(ctx_text)

            ctx_frame.setLayout(ctx_layout)
            layout.addWidget(ctx_frame)

        # ── Scrollable checklist ─────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        cb_layout = QVBoxLayout()
        cb_layout.setSpacing(4)

        for entry in self.entries:
            se_id     = entry.get("se_id", "")
            title     = entry.get("title", "Untitled")
            in_field  = bool(se_id and se_id in self.existing_se_ids)

            label_text = title
            if in_field:
                label_text += "  <span style='color:#555; font-size:10px;'>(already in field)</span>"

            cb = QCheckBox()
            cb.setChecked(in_field)           # pre-checked only if already present
            cb.setText("")                     # text set via label below

            row_widget = QWidget()
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(2, 0, 2, 0)
            row_layout.setSpacing(6)

            row_layout.addWidget(cb)

            lbl = QLabel(label_text)
            lbl.setWordWrap(False)
            lbl.setStyleSheet("font-size: 12px; padding: 2px 0;")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            # Clicking the label toggles the checkbox
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

        # ── Select All / Deselect All ─────────────────────────────────────────
        sel_layout = QHBoxLayout()

        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        sel_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(self._deselect_all)
        sel_layout.addWidget(deselect_all_btn)

        sel_layout.addStretch()
        layout.addLayout(sel_layout)

        # ── OK / Cancel ───────────────────────────────────────────────────────
        buttons = QDialogButtonBox()
        ok_btn = buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setDefault(True)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

        self.setLayout(layout)

    # ── Slot helpers ──────────────────────────────────────────────────────────

    def _select_all(self):
        for cb, _ in self._checkboxes:
            cb.setChecked(True)

    def _deselect_all(self):
        for cb, _ in self._checkboxes:
            cb.setChecked(False)

    def _on_cancel(self):
        self._cancelled = True
        self.reject()

    # ── Result ────────────────────────────────────────────────────────────────

    def accept(self):
        self._selected = [entry for cb, entry in self._checkboxes if cb.isChecked()]
        super().accept()

    def get_selected_entries(self) -> List[Dict]:
        """Return the list of entry dicts the user checked."""
        return self._selected

    def was_cancelled(self) -> bool:
        return self._cancelled
