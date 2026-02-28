"""
Tag Selection Dialog
Allows users to select which tags to replace with checkboxes
"""
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, 
                    QPushButton, QLabel, QScrollArea, QWidget,
                    QDialogButtonBox, QFrame)
from aqt.utils import showInfo
from typing import List, Tuple, Dict


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
    
    def setup_ui(self):
        self.setWindowTitle("Select Tags to Replace")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)
        
        layout = QVBoxLayout()
        
        # Info label
        info_label = QLabel("Multiple tags detected. Select which tags you want to replace:")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(info_label)
        
        # Show note context if available
        if self.note_context:
            context_frame = QFrame()
            context_frame.setFrameShape(QFrame.Shape.StyledPanel)
            context_frame.setStyleSheet("background-color: #f0f0f0; padding: 10px; border-radius: 5px;")
            context_layout = QVBoxLayout()
            
            context_title = QLabel("Card Context (Text field):")
            context_title.setStyleSheet("font-weight: bold; font-size: 11px;")
            context_layout.addWidget(context_title)
            
            context_text = QLabel(self.note_context[:200] + ("..." if len(self.note_context) > 200 else ""))
            context_text.setWordWrap(True)
            context_text.setStyleSheet("font-size: 10px; color: #666;")
            context_layout.addWidget(context_text)
            
            context_frame.setLayout(context_layout)
            layout.addWidget(context_frame)
        
        # Scrollable area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.checkbox_layout = QVBoxLayout()
        
        # Store checkboxes
        self.checkboxes = []
        
        for tag_info in self.simplified_tags:
            checkbox = QCheckBox(tag_info['display_name'])
            checkbox.tag_info = tag_info  # Store the full info
            checkbox.setStyleSheet("padding: 5px; font-size: 12px;")
            self.checkboxes.append(checkbox)
            self.checkbox_layout.addWidget(checkbox)
        
        scroll_widget.setLayout(self.checkbox_layout)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)
        
        # Select all / Deselect all buttons
        select_buttons_layout = QHBoxLayout()
        
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self.select_all)
        select_buttons_layout.addWidget(select_all_btn)
        
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(self.deselect_all)
        select_buttons_layout.addWidget(deselect_all_btn)
        
        select_buttons_layout.addStretch()
        layout.addLayout(select_buttons_layout)
        
        # OK and Cancel buttons
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
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
