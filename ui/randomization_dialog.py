"""
Randomization Dialog
Allows users to add randomization elements to cards
"""
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                    QComboBox, QLineEdit, QSpinBox, QPushButton,
                    QLabel, QGroupBox, QStackedWidget, QWidget,
                    QDialogButtonBox, QScrollArea, QButtonGroup,
                    QRadioButton, QFrame, QPalette, Qt)
from aqt.utils import showInfo
from aqt.addcards import AddCards
import json
from ..utils import insert_at_cursor


class RandomizationDialog(QDialog):
    def __init__(self, parent, editor):
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Add Randomization Elements")
        self.setMinimumWidth(500)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        # Element type selection
        type_group = QGroupBox("Element Type")
        type_layout = QVBoxLayout()

        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "Random Number", "Random List", "Scored List",
            "Scored Number", "Show Score", "Answer by Score"
        ])
        self.type_combo.currentIndexChanged.connect(self.update_form)
        type_layout.addWidget(self.type_combo)

        type_group.setLayout(type_layout)
        layout.addWidget(type_group)

        # Stacked widget setup
        self.stack = QStackedWidget()

        # Random Number form
        random_number_widget = QWidget()
        random_number_layout = QFormLayout()

        self.min_value = QLineEdit()
        self.min_value.setPlaceholderText("e.g., 1")
        random_number_layout.addRow("Minimum Value:", self.min_value)

        self.max_value = QLineEdit()
        self.max_value.setPlaceholderText("e.g., 100")
        random_number_layout.addRow("Maximum Value:", self.max_value)

        self.decimals = QSpinBox()
        self.decimals.setMinimum(0)
        self.decimals.setMaximum(10)
        random_number_layout.addRow("Decimal Places:", self.decimals)

        random_number_widget.setLayout(random_number_layout)
        self.stack.addWidget(random_number_widget)

        # Random List form with dynamic fields
        random_list_widget = QWidget()
        random_list_layout = QVBoxLayout()

        random_list_layout.addWidget(QLabel("Enter options:"))

        self.random_list_items_layout = QVBoxLayout()

        # Add first item field
        self.random_list_items = []
        self.add_random_list_item()

        random_list_layout.addLayout(self.random_list_items_layout)

        # Add and Remove buttons
        buttons_layout = QHBoxLayout()

        add_button = QPushButton("Add Option")
        add_button.clicked.connect(self.add_random_list_item)
        buttons_layout.addWidget(add_button)

        remove_button = QPushButton("Remove Option")
        remove_button.clicked.connect(self.remove_random_list_item)
        buttons_layout.addWidget(remove_button)

        random_list_layout.addLayout(buttons_layout)

        random_list_widget.setLayout(random_list_layout)
        self.stack.addWidget(random_list_widget)

        # Scored List form with dynamic fields
        scored_list_widget = QWidget()
        scored_list_layout = QVBoxLayout()

        scored_list_layout.addWidget(QLabel("Enter options with scores:"))

        self.scored_list_items_layout = QVBoxLayout()

        # Add first scored item
        self.scored_list_items = []
        self.add_scored_list_item()

        scored_list_layout.addLayout(self.scored_list_items_layout)

        # Add and Remove buttons for scored list
        sl_buttons_layout = QHBoxLayout()

        sl_add_button = QPushButton("Add Option")
        sl_add_button.clicked.connect(self.add_scored_list_item)
        sl_buttons_layout.addWidget(sl_add_button)

        sl_remove_button = QPushButton("Remove Option")
        sl_remove_button.clicked.connect(self.remove_scored_list_item)
        sl_buttons_layout.addWidget(sl_remove_button)

        scored_list_layout.addLayout(sl_buttons_layout)

        scored_list_widget.setLayout(scored_list_layout)
        self.stack.addWidget(scored_list_widget)

        # Modified Scored Number form
        scored_number_widget = QWidget()
        scored_number_layout = QVBoxLayout()

        # Visual range display
        self.range_display = QWidget()
        self.range_display.setMinimumHeight(50)
        self.range_display.setStyleSheet("background: #f0f0f0;")
        scored_number_layout.addWidget(self.range_display)

        # Threshold controls
        self.thresholds_container = QWidget()
        self.thresholds_layout = QVBoxLayout(self.thresholds_container)

        self.threshold_items = []
        self.add_threshold_item(initial=True)

        # Range controls
        range_controls = QHBoxLayout()
        add_range_btn = QPushButton("Add Range")
        add_range_btn.clicked.connect(lambda: self.add_threshold_item())
        range_controls.addWidget(add_range_btn)

        remove_range_btn = QPushButton("Remove Range")
        remove_range_btn.clicked.connect(self.remove_threshold_item)
        range_controls.addWidget(remove_range_btn)

        scored_number_layout.addWidget(self.thresholds_container)
        scored_number_layout.addLayout(range_controls)

        # Decimal places
        decimals_container = QHBoxLayout()
        decimals_container.addWidget(QLabel("Decimal Places:"))
        self.sn_decimals = QSpinBox()
        self.sn_decimals.setRange(0, 10)
        decimals_container.addWidget(self.sn_decimals)
        scored_number_layout.addLayout(decimals_container)

        scored_number_widget.setLayout(scored_number_layout)
        self.stack.addWidget(scored_number_widget)

        # Show Score form - simplified to just a button
        show_score_widget = QWidget()
        show_score_layout = QVBoxLayout()

        info_label = QLabel("This will insert [showscore] which displays the total calculated score from all scored elements.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-style: italic; margin: 10px;")
        show_score_layout.addWidget(info_label)

        # Add some vertical spacing to center the content
        show_score_layout.addStretch()
        show_score_layout.addStretch()

        show_score_widget.setLayout(show_score_layout)
        self.stack.addWidget(show_score_widget)

        # Answer by Score form
        answer_score_widget = QWidget()
        answer_score_layout = QVBoxLayout()

        answer_score_layout.addWidget(QLabel("Define score ranges and answers:"))

        # Container for ranges
        self.answer_ranges_container = QWidget()
        self.answer_ranges_layout = QVBoxLayout(self.answer_ranges_container)

        # Dynamic range entries
        self.answer_range_items = []
        self.init_answer_by_score()

        # Add/Remove buttons
        abs_buttons = QHBoxLayout()
        add_abs_btn = QPushButton("Add Range")
        add_abs_btn.clicked.connect(self.add_answer_range_entry)
        abs_buttons.addWidget(add_abs_btn)

        remove_abs_btn = QPushButton("Remove Range")
        remove_abs_btn.clicked.connect(self.remove_answer_range_entry)
        abs_buttons.addWidget(remove_abs_btn)

        answer_score_layout.addWidget(self.answer_ranges_container)
        answer_score_layout.addLayout(abs_buttons)

        answer_score_widget.setLayout(answer_score_layout)
        self.stack.addWidget(answer_score_widget)

        layout.addWidget(self.stack)

        # Buttons - PyQt6 compatible approach
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def add_randomization_tag(self):
        """Add the randomization tag to the current note"""
        try:
            if isinstance(self.parent(), AddCards):
                # We're in an AddCards dialog, modify the existing note directly
                randomization_tag = "#Malleus_CM::#Card_Feature::Randomisation"

                try:
                    # Get the AddCards dialog
                    add_cards_dialog = self.parent()
                    current_note = None

                    # Try to get the current note from the dialog
                    if hasattr(add_cards_dialog, 'editor') and hasattr(add_cards_dialog.editor, 'note'):
                        current_note = add_cards_dialog.editor.note
                    elif hasattr(add_cards_dialog, 'note'):
                        current_note = add_cards_dialog.note

                    if current_note:
                        # Get current tags
                        current_tags = list(current_note.tags) if current_note.tags else []

                        # Add randomization tag if not already present
                        if randomization_tag not in current_tags:
                            current_tags.append(randomization_tag)
                            current_note.tags = current_tags

                            # Try to refresh the tags display in the dialog
                            if hasattr(add_cards_dialog, 'editor') and hasattr(add_cards_dialog.editor, 'loadNote'):
                                add_cards_dialog.editor.loadNote()

                            print(f"Added randomization tag: {randomization_tag}")
                        else:
                            print("Randomization tag already present")
                    else:
                        print("Could not find note in AddCards dialog")

                except Exception as e:
                    print(f"Error modifying note in AddCards dialog: {e}")
                return

            # The tag to add
            randomization_tag = "#Malleus_CM::#Card_Feature::Randomisation"
            # Get current tags
            current_tags = []
            if note.tags:
                current_tags = list(note.tags)
            # Add the randomization tag if it's not already present
            if randomization_tag not in current_tags:
                current_tags.append(randomization_tag)
                # Update the note with new tags
                note.tags = current_tags
                # Save the note
                note.flush()
                # Try to refresh the editor if possible
                try:
                    if hasattr(self.editor, 'loadNote'):
                        self.editor.loadNote()
                    elif hasattr(self.editor.parentWindow, 'editor') and hasattr(self.editor.parentWindow.editor, 'loadNote'):
                        self.editor.parentWindow.editor.loadNote()
                except:
                    pass  # If refresh fails, it's not critical
                print(f"Added randomization tag: {randomization_tag}")
            else:
                print("Randomization tag already present")
        except Exception as e:
            print(f"Error adding randomization tag: {e}")
            # Don't fail the entire operation if tagging fails

    def add_random_list_item(self):
        """Add a new option field to the random list"""
        item_layout = QHBoxLayout()

        item_field = QLineEdit()
        item_field.setPlaceholderText(f"Option {len(self.random_list_items) + 1}")

        item_layout.addWidget(item_field)

        self.random_list_items_layout.addLayout(item_layout)
        self.random_list_items.append(item_field)

    def remove_random_list_item(self):
        """Remove the last option field from the random list"""
        if len(self.random_list_items) <= 1:
            return  # Keep at least one field

        # Get the last item and its layout
        last_item = self.random_list_items.pop()
        last_layout = self.random_list_items_layout.itemAt(len(self.random_list_items))

        # Remove and delete the widget and layout
        last_item.deleteLater()

        # Remove all items from the layout
        while last_layout.count():
            item = last_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Remove the layout
        self.random_list_items_layout.removeItem(last_layout)

    def add_scored_list_item(self):
        """Add a new option with score field to the scored list"""
        item_layout = QHBoxLayout()

        option_field = QLineEdit()
        option_field.setPlaceholderText(f"Option {len(self.scored_list_items) + 1}")

        score_field = QSpinBox()
        score_field.setMinimum(0)
        score_field.setMaximum(100)
        score_field.setValue(len(self.scored_list_items) + 1)  # Default score is the item number

        item_layout.addWidget(option_field, 3)  # Give more space to option text
        item_layout.addWidget(QLabel("Score:"), 0)
        item_layout.addWidget(score_field, 1)   # Less space for score

        self.scored_list_items_layout.addLayout(item_layout)
        self.scored_list_items.append((option_field, score_field))

    def remove_scored_list_item(self):
        """Remove the last option with score field from the scored list"""
        if len(self.scored_list_items) <= 1:
            return  # Keep at least one field

        # Get the last item pair and its layout
        last_item_pair = self.scored_list_items.pop()
        last_layout = self.scored_list_items_layout.itemAt(len(self.scored_list_items))

        # Remove and delete the widgets
        last_item_pair[0].deleteLater()
        last_item_pair[1].deleteLater()

        # Remove all items from the layout
        while last_layout.count():
            item = last_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Remove the layout
        self.scored_list_items_layout.removeItem(last_layout)

    def add_threshold_item(self, initial=False):
        """Add a new threshold range with score"""
        if len(self.threshold_items) >= 5:
            showInfo("Maximum of 5 ranges allowed")
            return

        prev_max = "0"
        if self.threshold_items:
            prev_max = self.threshold_items[-1]["end"].text()

        # Create widgets
        threshold_item = QWidget()
        layout = QHBoxLayout(threshold_item)

        # Start input (only editable for first item)
        start_edit = QLineEdit(prev_max if not initial else "0")
        start_edit.setFixedWidth(80)
        start_edit.setReadOnly(not initial)  # Only first range is editable

        # End input (always editable)
        end_edit = QLineEdit("100" if initial else "")
        end_edit.setFixedWidth(80)

        score_spin = QSpinBox()
        score_spin.setRange(0, 100)
        score_spin.setValue(len(self.threshold_items) + 1)

        # Connect end edits to update next range's start
        if self.threshold_items:
            prev_end = self.threshold_items[-1]["end"]
            prev_end.textChanged.connect(lambda text: start_edit.setText(text))

        # Connect validation
        end_edit.textChanged.connect(self.update_visual_ranges)
        score_spin.valueChanged.connect(self.update_visual_ranges)

        # Add to layout
        layout.addWidget(QLabel("Range:"))
        layout.addWidget(start_edit)
        layout.addWidget(QLabel("to"))
        layout.addWidget(end_edit)
        layout.addWidget(QLabel("Score:"))
        layout.addWidget(score_spin)

        self.thresholds_layout.insertWidget(len(self.threshold_items), threshold_item)

        self.threshold_items.append({
            "start": start_edit,
            "end": end_edit,
            "score": score_spin,
            "widget": threshold_item
        })
        self.update_visual_ranges()

    def remove_threshold_item(self):
        """Remove the last threshold range"""
        if len(self.threshold_items) <= 1:
            return

        last_item = self.threshold_items.pop()
        last_item["widget"].deleteLater()
        self.update_visual_ranges()

    def update_visual_ranges(self):
        """Update the visual range display with theme-aware colors"""
        try:
            # Get theme background color
            palette = self.palette()
            bg_color = palette.color(QPalette.ColorRole.Window)

            # Clear existing display
            if self.range_display.layout():
                QWidget().setLayout(self.range_display.layout())

            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)

            # Add colored segments
            for idx, item in enumerate(self.threshold_items):
                try:
                    start = float(item["start"].text() or 0)
                    end = float(item["end"].text() or 0)
                    if start >= end:
                        continue

                    # Format numbers without decimal points
                    start_text = f"{int(start)}" if start.is_integer() else f"{start:.0f}"
                    end_text = f"{int(end)}" if end.is_integer() else f"{end:.0f}"

                    # Create segment with corrected gradient syntax
                    segment = QLabel()
                    segment.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    segment.setStyleSheet(f"""
                        background: qlineargradient(x1:0, y1:0, x1:1, y1:0,
                            stop:0 hsl({240 - idx*40}, 50%, 40%),
                            stop:1 hsl({200 - idx*40}, 50%, 40%));
                        color: white;
                        border-radius: 3px;
                        margin: 1px;
                        font-weight: bold;
                    """)
                    segment.setText(f"{start_text} → {end_text}\nScore: {item['score'].value()}")

                    layout.addWidget(segment, int(end - start))

                except ValueError:
                    continue

            self.range_display.setLayout(layout)
            self.range_display.setStyleSheet(f"""
                background: {bg_color.name()};
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                border-radius: 4px;
            """)

        except Exception as e:
            print("Visual update error:", e)

    # Function to add a new answer range entry with connected ranges
    def add_answer_range_entry(self):
        """Add a new answer range entry with connected ranges"""
        entry_widget = QWidget()
        entry_layout = QHBoxLayout(entry_widget)
        entry_layout.setContentsMargins(0, 0, 0, 0)

        # Determine the start value based on previous ranges
        start_value = 0
        if self.answer_range_items:
            # Get the 'to' value from the last entry
            prev_to_spin = self.answer_range_items[-1][1]  # Second item is the 'to' spinner
            start_value = prev_to_spin.value()

        # Create the 'from' spinner (read-only if not the first entry)
        from_spin = QSpinBox()
        from_spin.setMinimum(0)
        from_spin.setMaximum(9999)
        from_spin.setValue(start_value)
        from_spin.setReadOnly(len(self.answer_range_items) > 0)  # Only first one is editable

        # Create the 'to' spinner
        to_spin = QSpinBox()
        to_spin.setMinimum(start_value)
        to_spin.setMaximum(9999)
        to_spin.setValue(start_value + 1)

        # Answer text field
        text_edit = QLineEdit()
        text_edit.setPlaceholderText("Answer text")

        # Add widgets to layout
        entry_layout.addWidget(QLabel("From:"))
        entry_layout.addWidget(from_spin)
        entry_layout.addWidget(QLabel("To:"))
        entry_layout.addWidget(to_spin)
        entry_layout.addWidget(QLabel("Text:"))
        entry_layout.addWidget(text_edit)

        # Connect the previous 'to' spinner to this 'from' spinner if this isn't the first entry
        if self.answer_range_items:
            prev_to_spin = self.answer_range_items[-1][1]
            # Store the connection function so we can disconnect it later if needed
            update_func = lambda val: self._update_from_value(from_spin, to_spin, val)
            from_spin.prev_connection = update_func
            prev_to_spin.valueChanged.connect(update_func)

        # Store the items and widget for later reference
        self.answer_ranges_layout.addWidget(entry_widget)
        self.answer_range_items.append((from_spin, to_spin, text_edit, entry_widget))

    # Helper function to update a 'from' value when the previous 'to' value changes
    def _update_from_value(self, from_spin, to_spin, value):
        """Update a 'from' spinner value when the previous 'to' spinner changes"""
        # Block signals to prevent cycles
        from_spin.blockSignals(True)

        # Update value and minimum
        from_spin.setMinimum(value)
        from_spin.setValue(value)

        # Update the 'to' spinner's minimum
        to_spin.setMinimum(value)

        # Unblock signals
        from_spin.blockSignals(False)

    # Updated remove function that properly maintains connections
    def remove_answer_range_entry(self):
        """Remove the last answer range entry"""
        if not self.answer_range_items:
            return

        # Remove the last entry
        last_entry = self.answer_range_items.pop()
        from_spin, to_spin, text_edit, entry_widget = last_entry

        # Delete the widget which contains the layout and all controls
        entry_widget.deleteLater()

    # Initialize or reset the answer by score section
    def init_answer_by_score(self):
        """Initialize or reset the answer by score section"""
        # Clear existing items first
        while self.answer_range_items:
            self.remove_answer_range_entry()

        # Add the first entry
        self.add_answer_range_entry()

    def update_form(self, index):
        self.stack.setCurrentIndex(index)

    def accept(self):
        # Generate the appropriate tag based on selection
        index = self.type_combo.currentIndex()
        tag_added = False  # Track if we successfully added a randomization element

        if index == 0:  # Random Number
            try:
                min_val = float(self.min_value.text() or "0")
                max_val = float(self.max_value.text() or "100")
                decimals = self.decimals.value()
                tag = f"[random:{min_val},{max_val},{decimals}]"
                insert_at_cursor(self.editor, tag)
                tag_added = True
            except ValueError:
                showInfo("Invalid number format. Please enter valid numbers.")
                return

        elif index == 1:  # Random List
            options = []
            for field in self.random_list_items:
                option = field.text().strip()
                if option:
                    options.append(option)

            if not options:
                showInfo("Please enter at least one option.")
                return

            tag = f"[randomlist:{','.join(options)}]"
            insert_at_cursor(self.editor, tag)
            tag_added = True

        elif index == 2:  # Scored List
            scored_options = []
            for option_field, score_field in self.scored_list_items:
                text = option_field.text().strip()
                score = score_field.value()

                if text:
                    scored_options.append(f"{text}:{score}")

            if not scored_options:
                showInfo("Please enter at least one valid option with score.")
                return

            tag = f"[scoredlist:{','.join(scored_options)}]"
            insert_at_cursor(self.editor, tag)
            tag_added = True

        elif index == 3:  # Scored Number
            try:
                thresholds = []
                scores = []

                # Collect all threshold points
                for item in self.threshold_items:
                    start = float(item["start"].text() or 0)
                    end = float(item["end"].text() or 0)
                    thresholds.extend([start, end])
                    scores.append(item["score"].value())

                # Remove duplicates and sort
                thresholds = sorted(list(set(thresholds)))
                decimals = self.sn_decimals.value()

                if len(thresholds) < 2:
                    showInfo("Please define at least one valid range")
                    return

                tag = f"[scorednumber:{','.join(map(str, thresholds))}:{decimals}:{','.join(map(str, scores))}]"
                insert_at_cursor(self.editor, tag)
                tag_added = True
            except Exception as e:
                showInfo(f"Invalid input: {str(e)}")
                return

        elif index == 4:  # Show Score
            tag = "[showscore]"
            insert_at_cursor(self.editor, tag)
            tag_added = True

        elif index == 5:  # Answer by Score
            segments = []
            for item in self.answer_range_items:
                # Extract the first 3 elements from each item
                from_spin, to_spin, text_edit = item[0:3]

                from_val = from_spin.value()
                to_val = to_spin.value()
                text = text_edit.text().strip()

                if not text:
                    continue

                if from_val > to_val:
                    showInfo("'From' value must be ≤ 'To' value.")
                    return

                if from_val == to_val:
                    range_part = f"{from_val}"
                else:
                    range_part = f"{from_val},{to_val}"

                segments.append(range_part)
                segments.append(text)

            if not segments:
                showInfo("Please add at least one valid answer range.")
                return

            tag = f"[answerbyscore:{':'.join(segments)}]"
            insert_at_cursor(self.editor, tag)
            tag_added = True

        # If we successfully added a randomization element, add the randomization tag
        print(f"Need to add randomisation tag: {tag_added}")

        if tag_added:
            self.add_randomization_tag()

        super().accept()


# Show the dialog
def show_randomization_dialog(editor):
    dialog = RandomizationDialog(editor.parentWindow, editor)
    dialog.exec()  # Note: In PyQt6, exec_() is deprecated, use exec() instead


def setup_editor_buttons(buttons, editor, show_page_selector_func):
    """Setup editor buttons with proper callback"""
    # Malleus button
    malleus_btn = editor.addButton(
        icon=None,
        cmd="malleus",
        func=lambda e: show_page_selector_func(editor.parentWindow),
        tip="Find/Add Malleus Tags",
        label="Add Malleus Tags"
    )
    buttons.append(malleus_btn)

    # Randomization button
    random_btn = editor.addButton(
        icon=None,
        cmd="randomization",
        func=lambda e: show_randomization_dialog(e),
        tip="Add Randomization Elements",
        label="Add Random"
    )
    buttons.append(random_btn)
    return buttons
