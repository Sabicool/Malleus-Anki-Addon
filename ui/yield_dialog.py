"""
Yield Selection Dialog
Asks for a yield level for a note that has none.

Used by both bulk tagging flows — Browser → Update Malleus Subject Tags and the
Page Selector's Replace Tags run — which each used to carry their own
near-identical copy of this dialog.

In a bulk run the dialog carries an "apply to all" row, so a batch of cards
missing a yield can be answered once; the caller holds the decision in a
BulkChoices object (see bulk_choices.py) and stops calling here.
"""
from typing import Optional, Tuple

try:
    from .styles import apply_malleus_style, make_apply_all_row
except Exception:
    def apply_malleus_style(w): pass
    def make_apply_all_row(label="Apply to all remaining cards", remaining=0,
                           checked=False, tooltip=None):
        from aqt.qt import QWidget, QHBoxLayout, QCheckBox
        row = QWidget(); lay = QHBoxLayout(row)
        cb = QCheckBox(label); cb.setChecked(checked)
        lay.addWidget(cb); lay.addStretch()
        return row, cb

YIELD_OPTIONS = {
    "High Yield":                   "#Malleus_CM::#Yield::High",
    "Medium Yield":                 "#Malleus_CM::#Yield::Medium",
    "Low Yield":                    "#Malleus_CM::#Yield::Low",
    "Beyond Medical Student Level": "#Malleus_CM::#Yield::Beyond_medical_student_level",
}

DEFAULT_YIELD = "High Yield"


def prompt_for_yield_selection(parent, note_context: str = None,
                               remaining: int = 0) -> Tuple[Optional[str], bool]:
    """
    Prompt the user to select a yield level for a note that has none.

    Args:
        parent:       Parent widget (browser, Page Selector, AddCards dialog…).
        note_context: Optional snippet from the note's Text field, shown so the
                      user can tell which card they are answering for.
        remaining:    Cards left in a bulk run after this one.  When > 0 the
                      "apply to all" row is shown; 0 hides it, which is what a
                      single-note caller wants.

    Returns:
        (yield_tag, apply_to_all) — yield_tag is None if cancelled.
    """
    from aqt.qt import (QDialog, QVBoxLayout, QLabel, QRadioButton,
                        QButtonGroup, QDialogButtonBox, QFrame)

    dialog = QDialog(parent)
    dialog.setWindowTitle("Select Yield Level")
    dialog.setMinimumWidth(400)
    apply_malleus_style(dialog)

    layout = QVBoxLayout()

    info_label = QLabel("This card has no yield level. Please select one:")
    info_label.setWordWrap(True)
    info_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
    layout.addWidget(info_label)

    # ── Card context ─────────────────────────────────────────────────────────
    if note_context:
        ctx_frame = QFrame()
        ctx_frame.setFrameShape(QFrame.Shape.StyledPanel)
        ctx_frame.setStyleSheet(
            "background-color: palette(alternateBase); padding: 10px; "
            "border-radius: 7px; border: 1px solid rgba(74,130,204,0.30);"
        )
        ctx_layout = QVBoxLayout()

        ctx_title = QLabel("Card context:")
        ctx_title.setStyleSheet("font-weight: bold; font-size: 11px;")
        ctx_layout.addWidget(ctx_title)

        ctx_text = QLabel(note_context[:200] + ("..." if len(note_context) > 200 else ""))
        ctx_text.setWordWrap(True)
        ctx_text.setStyleSheet("font-size: 10px;")
        ctx_layout.addWidget(ctx_text)

        ctx_frame.setLayout(ctx_layout)
        layout.addWidget(ctx_frame)

    # ── Yield levels ─────────────────────────────────────────────────────────
    button_group  = QButtonGroup(dialog)
    radio_buttons = {}

    for display_text, tag_value in YIELD_OPTIONS.items():
        radio = QRadioButton(display_text)
        radio_buttons[display_text] = (radio, tag_value)
        button_group.addButton(radio)
        layout.addWidget(radio)

    radio_buttons[DEFAULT_YIELD][0].setChecked(True)

    # ── "Apply to all" (bulk runs only) ──────────────────────────────────────
    apply_all_cb = None
    if remaining > 0:
        apply_all_row, apply_all_cb = make_apply_all_row(
            label="Apply this yield to all remaining cards",
            remaining=remaining,
            checked=False,
            tooltip=(
                "Give every remaining card that has no yield level this same\n"
                "yield, without asking again.  Cards that already have a yield\n"
                "are left alone."
            ),
        )
        layout.addWidget(apply_all_row)

    buttons = QDialogButtonBox()
    buttons.addButton(QDialogButtonBox.StandardButton.Ok)
    buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    dialog.setLayout(layout)

    if dialog.exec() == QDialog.DialogCode.Accepted:
        apply_all = bool(apply_all_cb and apply_all_cb.isChecked())
        for display_text, (radio, tag_value) in radio_buttons.items():
            if radio.isChecked():
                return (tag_value, apply_all)

    return (None, False)
