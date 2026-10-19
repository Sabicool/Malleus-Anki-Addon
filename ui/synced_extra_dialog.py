"""
Synced Extra Selection Dialog
Lets the user choose which Synced Extra entries to include in Extra (Synced).
Styled with the Malleus design system; adapts to Anki's light and dark themes.
"""
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QScrollArea, QWidget, QPushButton, QFrame, Qt
)
from aqt.utils import showInfo
from typing import List, Dict, Set

from .bulk_choices import STOP_LABEL, STOP_TOOLTIP, SKIP_LABEL, SKIP_TOOLTIP

try:
    from .styles import apply_malleus_style, make_header, make_apply_all_row, COLORS
except Exception:
    def apply_malleus_style(w): pass
    def make_header(*a, **kw):
        from aqt.qt import QWidget
        return QWidget()
    def make_apply_all_row(label="Apply to all remaining cards", remaining=0,
                           checked=False, tooltip=None):
        from aqt.qt import QWidget, QHBoxLayout, QCheckBox
        row = QWidget(); lay = QHBoxLayout(row)
        cb = QCheckBox(label); cb.setChecked(checked)
        lay.addWidget(cb); lay.addStretch()
        return row, cb
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
        remaining:       Cards left in a bulk run after this one.  When > 0 the
                         "remember these choices" row is shown and Cancel is
                         relabelled Skip card; leave at 0 for single-note
                         callers and both revert to the plain form.
        allow_stop:      Offer a Stop button that ends the whole run.  Only
                         pass this from a loop that actually honours it.
    """

    def __init__(
        self,
        parent,
        entries: List[Dict],
        existing_se_ids: Set[str],
        note_context: str = None,
        remaining: int = 0,
        allow_stop: bool = False,
    ):
        super().__init__(parent)
        self.entries         = entries
        self.existing_se_ids = existing_se_ids
        self.note_context    = note_context
        self.remaining       = remaining
        self.allow_stop      = allow_stop
        self._selected       = []
        self._cancelled      = False
        self._stopped        = False
        self._checkboxes     = []
        self._apply_all_cb   = None
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
            subtitle="There is pre-filled content that can be inserted into the Extra (Synced) field. Please select the entries you think are relevant to the card to be included."
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

        # ── Single-row button bar ─────────────────────────────────────────────
        # All four buttons share the same fixed height and sit on one line.
        # [Select All] [Deselect All]  ────stretch────  [Cancel] [OK]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.setContentsMargins(0, 4, 0, 0)

        BTN_H = 32   # fixed height keeps all four buttons vertically aligned

        def _make_btn(label, obj_name=None, default=False):
            b = QPushButton(label)
            b.setFixedHeight(BTN_H)
            if obj_name:
                b.setObjectName(obj_name)
            if default:
                b.setDefault(True)
            return b

        select_all_btn   = _make_btn("Select All",   "secondary")
        deselect_all_btn = _make_btn("Deselect All", "secondary")
        # In a bulk run Cancel already meant "leave this card's Extra alone
        # and carry on", so it is relabelled to say that.
        cancel_btn       = _make_btn(SKIP_LABEL if self.remaining > 0 else "Cancel",
                                     "secondary")
        if self.remaining > 0:
            cancel_btn.setToolTip(SKIP_TOOLTIP)
        ok_btn           = _make_btn("OK",            default=True)

        select_all_btn.clicked.connect(self._select_all)
        deselect_all_btn.clicked.connect(self._deselect_all)
        cancel_btn.clicked.connect(self._on_cancel)
        ok_btn.clicked.connect(self.accept)

        btn_row.addWidget(select_all_btn)
        btn_row.addWidget(deselect_all_btn)
        btn_row.addStretch()
        if self.allow_stop:
            stop_btn = _make_btn(STOP_LABEL, "danger")
            stop_btn.setToolTip(STOP_TOOLTIP)
            stop_btn.clicked.connect(self._on_stop)
            btn_row.addWidget(stop_btn)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)

        # ── "Remember these choices" (bulk runs only) ─────────────────────────
        # Decisions are recorded per entry, so a later card only reopens this
        # dialog when it matches an entry that has no recorded decision yet.
        if self.remaining > 0:
            apply_all_row, self._apply_all_cb = make_apply_all_row(
                label="Remember these choices for the remaining cards",
                remaining=self.remaining,
                checked=False,
                tooltip=(
                    "Each entry's include/exclude choice is reused for every\n"
                    "remaining card in this run.  The dialog only reappears\n"
                    "for a card that matches an entry you haven't chosen yet."
                ),
            )
            layout.addWidget(apply_all_row)

        layout.addLayout(btn_row)

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

    def _on_stop(self):
        self._stopped = True
        self._cancelled = True
        self.reject()

    def accept(self):
        self._selected = [entry for cb, entry in self._checkboxes if cb.isChecked()]
        super().accept()

    def get_selected_entries(self) -> List[Dict]:
        return self._selected

    def was_cancelled(self) -> bool:
        return self._cancelled

    def was_stopped(self) -> bool:
        """True when the user asked to end the whole run, not just skip this
        card.  Implies was_cancelled()."""
        return self._stopped

    def apply_to_all(self) -> bool:
        """True when the user asked for these per-entry choices to be reused
        for the rest of a bulk run."""
        return bool(self._apply_all_cb and self._apply_all_cb.isChecked())

    def get_entry_decisions(self) -> Dict[str, bool]:
        """se_id → included?  One entry per row shown, so an unchecked row
        records an explicit 'exclude' rather than just an absence."""
        decisions = {}
        for cb, entry in self._checkboxes:
            se_id = entry.get("se_id")
            if se_id:
                decisions[se_id] = cb.isChecked()
        return decisions
