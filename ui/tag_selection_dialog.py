"""
Tag Selection Dialog
Allows users to select which tags to replace with checkboxes
"""
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, 
                    QPushButton, QLabel, QScrollArea, QWidget,
                    QDialogButtonBox, QFrame)
from aqt.utils import showInfo
from typing import List, Tuple, Dict
try:
    from .styles import apply_malleus_style, make_header, COLORS
except Exception:
    def apply_malleus_style(w): pass
    def make_header(title="Malleus Clinical Medicine", subtitle=None, logo_path=None):
        from aqt.qt import QWidget, QHBoxLayout, QLabel
        h = QWidget(); h.setFixedHeight(48 if not subtitle else 62)
        lay = QHBoxLayout(h); lay.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel(title); lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        lay.addWidget(lbl); lay.addStretch(); return h
    COLORS = {}


class TagSelectionDialog(QDialog):
    """Dialog for selecting which tags to replace"""
    
    def __init__(self, parent, simplified_tags: List[Dict], note_context: str = None):
        """
        Args:
            parent: Parent window
            simplified_tags: List of dicts with 'display_name', 'original_tags', 'page_name', 'subtag'
            note_context: Optional context from note's Text field to help user identify the card
        """
        super().__init__(parent)
        self.simplified_tags = simplified_tags
        self.note_context = note_context
        self.selected_tags = []
        self.setup_ui()
        apply_malleus_style(self)
    
    def setup_ui(self):
        self.setWindowTitle("Select Tags to Replace")
        self.setMinimumWidth(600)
        self.setMinimumHeight(420)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = make_header("Select Tags to Replace",
                             "Choose which existing tags to update")
        layout.addWidget(header)

        content_widget = __import__('aqt.qt', fromlist=['QWidget']).QWidget()
        content_layout = __import__('aqt.qt', fromlist=['QVBoxLayout']).QVBoxLayout(content_widget)
        content_layout.setContentsMargins(16, 14, 16, 12)
        content_layout.setSpacing(10)

        # Info label
        info_label = QLabel("Multiple tags detected. Select which tags you want to replace:")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-weight: 600; color: #7a92b5; font-size: 12px; background: transparent;")
        content_layout.addWidget(info_label)
        
        # Show note context if available
        if self.note_context:
            context_frame = QFrame()
            context_frame.setFrameShape(QFrame.Shape.StyledPanel)
            context_frame.setStyleSheet(f"background-color: palette(alternateBase); padding: 10px; border-radius: 7px; border: 1px solid rgba(74,130,204,0.35);")
            context_layout = QVBoxLayout()

            context_title = QLabel("Card Context:")
            context_title.setStyleSheet(f"font-weight: 700; font-size: 11px; color: {COLORS.get('accent','#4a82cc')}; background: transparent; letter-spacing: 0.3px;")
            context_layout.addWidget(context_title)

            context_text = QLabel(self.note_context[:200] + ("..." if len(self.note_context) > 200 else ""))
            context_text.setWordWrap(True)
            context_text.setStyleSheet("font-size: 11px; background: transparent;")
            context_layout.addWidget(context_text)
            
            context_frame.setLayout(context_layout)
            content_layout.addWidget(context_frame)

        # Scrollable area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.checkbox_layout = QVBoxLayout()
        self.checkbox_layout.setSpacing(2)

        # Store checkboxes
        self.checkboxes = []

        for tag_info in self.simplified_tags:
            checkbox = QCheckBox(tag_info['display_name'])
            checkbox.tag_info = tag_info  # Store the full info
            self.checkboxes.append(checkbox)
            self.checkbox_layout.addWidget(checkbox)

        scroll_widget.setLayout(self.checkbox_layout)
        scroll.setWidget(scroll_widget)
        content_layout.addWidget(scroll)

        # Select all / Deselect all buttons
        select_buttons_layout = QHBoxLayout()
        select_buttons_layout.setSpacing(6)

        select_all_btn = QPushButton("Select All")
        select_all_btn.setObjectName("secondary")
        select_all_btn.clicked.connect(self.select_all)
        select_buttons_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.setObjectName("secondary")
        deselect_all_btn.clicked.connect(self.deselect_all)
        select_buttons_layout.addWidget(deselect_all_btn)

        select_buttons_layout.addStretch()
        content_layout.addLayout(select_buttons_layout)

        # OK and Cancel buttons
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        content_layout.addWidget(buttons)

        layout.addWidget(content_widget)
        self.setLayout(layout)
    
    def select_all(self):
        """Select all checkboxes"""
        for checkbox in self.checkboxes:
            checkbox.setChecked(True)
    
    def deselect_all(self):
        """Deselect all checkboxes"""
        for checkbox in self.checkboxes:
            checkbox.setChecked(False)
    
    def accept(self):
        """Get selected tags when OK is clicked"""
        self.selected_tags = []
        for checkbox in self.checkboxes:
            if checkbox.isChecked():
                self.selected_tags.append(checkbox.tag_info)
        
        if not self.selected_tags:
            showInfo("Please select at least one tag to replace.")
            return
        
        super().accept()
    
    def get_selected_tags(self) -> List[Dict]:
        """Return the selected tag information"""
        return self.selected_tags
