from aqt import mw
from aqt.qt import *
from aqt.utils import showInfo
import os
import requests
from dotenv import load_dotenv
from aqt import dialogs
from aqt.browser import Browser
from aqt.addcards import AddCards
from anki.hooks import addHook
import anki.notes
from typing import List, Dict
import json

# Load environment variables
addon_dir = os.path.dirname(os.path.realpath(__file__))
env_path = os.path.join(addon_dir, '.env')
load_dotenv(env_path)

NOTION_TOKEN = os.getenv('NOTION_TOKEN')
SUBJECT_DATABASE_ID = os.getenv('DATABASE_ID')  
PHARMACOLOGY_DATABASE_ID = os.getenv('PHARMACOLOGY_DATABASE_ID') 

def open_browser_with_search(search_query):
    """Open the browser with a search query"""
    browser = dialogs.open('Browser', mw)
    browser.activateWindow()

    if search_query:
        browser.form.searchEdit.lineEdit().setText(search_query)
        if hasattr(browser, 'onSearch'):
            browser.onSearch()
        else:
            browser.onSearchActivated()
    return

class ConfigManager:
    """
    Handles configuration management for the Anki addon
    """
    DEFAULT_CONFIG = {
        "deck_name": "Malleus Clinical Medicine (AU/NZ)"
    }

    def __init__(self):
        # Get the addon directory path
        addon_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(addon_dir, "config.json")
        self.config = self.load_config()

    def load_config(self):
        """Load configuration from file or create default if not exists"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Merge with defaults to ensure all required fields exist
                    return {**self.DEFAULT_CONFIG, **config}
            else:
                self.save_config(self.DEFAULT_CONFIG)
                return self.DEFAULT_CONFIG
        except Exception as e:
            print(f"Error loading config: {e}")
            return self.DEFAULT_CONFIG

    def save_config(self, config):
        """Save configuration to file"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get_deck_name(self):
        """Get configured deck name"""
        return self.config.get("deck_name", self.DEFAULT_CONFIG["deck_name"])
    
class NotionPageSelector(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.database_properties = {
            "Subjects": [
                "Tag",
                "Epidemiology",
                "Aetiology",
                "Risk Factors",
                "Physiology/Anatomy",
                "Pathophysiology",
                "Clinical Features",
                "Pathology",
                "Diagnosis/Investigations",
                "Scoring Criteria",
                "Management",
                "Complications/Prognosis",
                "Screening/Prevention",
                "Main Tag"
            ],
            "Pharmacology": [
                "Tag",
                "Generic Names",
                "Indications",
                "Contraindications/Precautions",
                "Route/Frequency",
                "Adverse Effects",
                "Toxicity & Reversal",
                "Advantages/Disadvantages",
                "Monitoring"
            ]
        }
        self.setup_ui()
        self.pages_data = []  # Store full page data

    def setup_ui(self):
        self.setWindowTitle("Malleus Page Selector")
        self.setMinimumWidth(600)

        layout = QVBoxLayout()

        # Search section
        search_layout = QHBoxLayout()

        # Database selector
        self.database_selector = QComboBox()
        self.database_selector.addItems(["Subjects", "Pharmacology"])
        self.database_selector.currentTextChanged.connect(self.update_property_selector)
        search_layout.addWidget(self.database_selector)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter search term...")
        search_layout.addWidget(self.search_input)

        # Property selector
        self.property_selector = QComboBox()
        search_layout.addWidget(self.property_selector)
        
        # TODO Somehow make this dynamic depending on the database
        self.update_property_selector(self.database_selector.currentText())

        # Search button
        search_button = QPushButton("Search")
        search_button.clicked.connect(self.perform_search)
        search_layout.addWidget(search_button)

        layout.addLayout(search_layout)

        # Results section
        self.results_group = QGroupBox("Search Results")
        results_layout = QVBoxLayout()

        # Scrollable area for checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.checkbox_layout = QVBoxLayout()
        scroll_widget.setLayout(self.checkbox_layout)
        scroll.setWidget(scroll_widget)

        results_layout.addWidget(scroll)
        self.results_group.setLayout(results_layout)
        layout.addWidget(self.results_group)

        # Buttons
        button_layout = QHBoxLayout()
        select_all_button = QPushButton("Select All")
        select_all_button.clicked.connect(self.select_all_pages)

        find_cards_button = QPushButton("Find Cards")
        find_cards_button.clicked.connect(self.search_cards)

        create_cards_button = QPushButton("Create Cards")
        create_cards_button.clicked.connect(self.create_cards)

        button_layout.addWidget(select_all_button)
        button_layout.addWidget(find_cards_button)
        button_layout.addWidget(create_cards_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)
        
    def update_property_selector(self, database_name):
        """Update property selector items based on selected database"""
        self.property_selector.clear()
        properties = self.database_properties.get(database_name, [])
        self.property_selector.addItems(properties)

    def get_selected_database_id(self):
        if self.database_selector.currentText() == "Subjects":
            return SUBJECT_DATABASE_ID
        else:
            return PHARMACOLOGY_DATABASE_ID

    def query_notion_pages(self, filter_text: str, database_id: str) -> List[Dict]:
        """Query Notion database and return page titles and properties"""
        if not NOTION_TOKEN or not database_id:
            showInfo("Environment variables not loaded correctly")
            return []

        database_url = f"https://api.notion.com/v1/databases/{database_id}/query"

        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }

        filter_payload = {
            "filter": {
                "property": "Name",
                "title": {
                    "contains": filter_text
                }
            }
        }

        try:
            response = requests.post(database_url, headers=headers, json=filter_payload)
            response.raise_for_status()
            return response.json()['results']
        except Exception as e:
            showInfo(f"Error accessing Notion: {str(e)}")
            return []

    def perform_search(self):
        search_term = self.search_input.text()
        if not search_term:
            showInfo("Please enter a search term")
            return

        # Clear existing checkboxes
        for i in reversed(range(self.checkbox_layout.count())):
            self.checkbox_layout.itemAt(i).widget().setParent(None)

        database_id = self.get_selected_database_id()
        self.pages_data = self.query_notion_pages(search_term, database_id)

        # Create checkboxes for results
        for page in self.pages_data:
            title = page['properties']['Name']['title'][0]['text']['content'] if page['properties']['Name']['title'] else "Untitled"
            checkbox = QCheckBox(title)
            self.checkbox_layout.addWidget(checkbox)

    def select_all_pages(self):
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            checkbox.setChecked(True)

    def search_cards(self):
        selected_pages = []
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            if checkbox.isChecked():
                selected_pages.append(self.pages_data[i])

        if not selected_pages:
            showInfo("Please select at least one page")
            return

        property_name = self.property_selector.currentText()

        # Get tags from pages
        tags = []
        for page in selected_pages:
            tag_prop = page['properties'].get('Tag')
            if tag_prop and tag_prop['type'] == 'formula':
                formula_value = tag_prop['formula']
                if formula_value['type'] == 'string':
                    tags.extend(formula_value['string'].split())

        database_id = self.get_selected_database_id()

        # Split each tag by spaces and flatten the list
        individual_tags = []
        for tag in tags:
            individual_tags.extend(tag.split())

        if property_name == 'Tag' or property_name == 'Main Tag':
            subtag = ""
        else:
            subtag = f"::*{property_name}".replace(' ', '_')
        
        # Format tags for Anki search
        search_query = " or ".join(f"tag:{tag}{subtag}" for tag in individual_tags)

        if isinstance(self.parent(), Browser):
            # If called from browser, update the current browser
            browser = self.parent()
            browser.form.searchEdit.lineEdit().setText(search_query)
            if hasattr(browser, 'onSearch'):
                browser.onSearch()
            else:
                browser.onSearchActivated()
        else:
            # Otherwise open a new browser window
            open_browser_with_search(search_query)

        self.accept()

    def get_property_content(self, page, property_name):
        """Extract property content from page data"""
        prop = page['properties'].get(property_name)
        if prop and prop['type'] == 'rich_text' and prop['rich_text']:
            return prop['rich_text'][0]['text']['content']
        return ""

    def create_cards(self):
        config_manager=ConfigManager()
        config = mw.addonManager.getConfig(__name__)
        selected_pages = []
        for i in range(self.checkbox_layout.count()):
            checkbox = self.checkbox_layout.itemAt(i).widget()
            if checkbox.isChecked():
                selected_pages.append(self.pages_data[i])

        property_name = self.property_selector.currentText()

        # Get tags from pages
        tags = []
        for page in selected_pages:
            tag_prop = page['properties'].get(property_name)
            if tag_prop and tag_prop['type'] == 'formula':
                formula_value = tag_prop['formula']
                if formula_value['type'] == 'string':
                    tags.extend(formula_value['string'].split())

        if not selected_pages:
            tags = ["#Malleus_CM::#TO_BE_TAGGED"]
            # return

        # Create note data
        note = {
            'deckName': config['deck_name'],  # Make this configurable
            'modelName': 'MalleusCM - Cloze (Malleus Clinical Medicine / Stapedius)',  # Make this configurable
            # Can consider adding something like this, or have the source field populate
            # 'fields': {
            #     'Front': property_name,
            #     'Back': '\n\n'.join(self.get_property_content(page, property_name)
            #                       for page in selected_pages)
            # },
            'tags': tags
        }

        # Open add cards dialog
        self.guiAddCards(note)
        self.accept()

    def guiAddCards(self, note):
        collection = mw.col

        deck = collection.decks.by_name(note['deckName'])
        if deck is None:
            raise Exception('deck was not found: {}'.format(note['deckName']))

        collection.decks.select(deck['id'])
        savedMid = deck.pop('mid', None)

        model = collection.models.by_name(note['modelName'])
        if model is None:
            raise Exception('model was not found: {}'.format(note['modelName']))

        collection.models.set_current(model)
        collection.models.update(model)

        ankiNote = anki.notes.Note(collection, model)

        # Fill note fields
        if 'fields' in note:
            for name, value in note['fields'].items():
                if name in ankiNote:
                    ankiNote[name] = value

        # Set tags
        if 'tags' in note:
            ankiNote.tags = note['tags']

        def openNewWindow():
            nonlocal ankiNote

            addCards = dialogs.open('AddCards', mw)

            if savedMid:
                deck['mid'] = savedMid

            addCards.editor.set_note(ankiNote)
            addCards.activateWindow()

            dialogs.open('AddCards', mw)
            addCards.setAndFocusNote(addCards.editor.note)

        currentWindow = dialogs._dialogs['AddCards'][1]

        if currentWindow is not None:
            current
        else:
            openNewWindow()

def show_page_selector(browser=None):
    dialog = NotionPageSelector(browser if browser else mw)
    dialog.exec_()

malleus_add_card_action = QAction("Malleus Find/Add Cards", mw)
malleus_add_card_action.triggered.connect(show_page_selector)
mw.form.menuTools.addAction(malleus_add_card_action)

# def setup_editor_buttons(buttons, editor):
#     b = editor.addButton(
#         "",
#         "Add Malleus Tags",
#         show_page_selector,
#         tip="Find Malleus tags for an your card"
#         #os.path.join(addon_path, "icons", "hyperlink.png"),
#         #"hyperlinkbutton",
#         #toggle_hyperlink,
#         #tip="Insert Hyperlink ({})".format(
#         #    keystr(gc("shortcut_insert_link", ""))),
#         #keys=gc('shortcut_insert_link')
#     )
#     buttons.append(b)
# 
#     return buttons
# 
# addHook("setupEditorButtons", setup_editor_buttons)  # noqa

def setup_browser_menu(browser):
    # Get or create Malleus Search menu
    def getMenu(parent, menu_name):
        menubar = parent.form.menubar
        for action in menubar.actions():
            if menu_name == action.text():
                return action.menu()
        return menubar.addMenu(menu_name)

    notion_menu = getMenu(browser, "&Malleus Search")
    
    # Add action for showing page selector
    page_selector_action = QAction(browser)
    page_selector_action.setText("Find/Create Malleus Cards")
    notion_menu.addAction(page_selector_action)
    page_selector_action.triggered.connect(lambda _, b=browser: show_page_selector(b))
    
    # Add to browser toolbar
    try:
        from aqt.qt import QToolBar
        toolbar = browser.findChild(QToolBar)
        if toolbar:
            page_selector_button = QAction(browser)
            page_selector_button.setText("Malleus")
            page_selector_button.setToolTip("Find/Create Malleus Cards")
            page_selector_button.triggered.connect(lambda _, b=browser: show_page_selector(b))
            toolbar.addAction(page_selector_button)
    except:
        pass

# Add hook for browser setup
from aqt.gui_hooks import browser_menus_did_init
browser_menus_did_init.append(setup_browser_menu)
