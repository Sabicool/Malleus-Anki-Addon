"""
Cache Updater
Handles the cache update process with progress dialog
"""
import time
import threading
from aqt.qt import QProgressDialog
from aqt.utils import tooltip
from .config import DATABASES

def perform_cache_update(notion_cache, main_window):
    """Download cache from GitHub repository and update cache from Notion"""
    current_notion_update = [0]  # Use list to allow modification in nested functions
    progress = [None]  # Use list to store progress dialog reference

    # Create progress dialog on main thread
    def create_progress():
        progress[0] = QProgressDialog("Initializing...", None, 0, 10, main_window)
        progress[0].setWindowTitle("Cache Update")
        progress[0].show()

    main_window.taskman.run_on_main(create_progress)

    def update_progress(step, message):
        """Update progress dialog with step number and message"""
        def update():
            if progress[0] is None:
                return
            progress[0].setValue(step)
            progress[0].setLabelText(message)
        main_window.taskman.run_on_main(update)

    def process_next_notion_update():
        """Process the next Notion database update"""
        if current_notion_update[0] < len(DATABASES):
            db_id, name = DATABASES[current_notion_update[0]]
            if db_id:
                update_progress(current_notion_update[0] + 4, f"Updating new {name} pages from Notion...")
                notion_cache.update_cache_async(db_id, force=True, callback=on_notion_update_complete)
            else:
                on_notion_update_complete()
        else:
            def complete():
                if progress[0] is None:
                    return
                progress[0].setValue(10)
                progress[0].close()
                tooltip("Cache successfully downloaded and updated")
            main_window.taskman.run_on_main(complete)

    def on_notion_update_complete():
        """Handle completion of a Notion update"""
        current_notion_update[0] += 1
        process_next_notion_update()

    def update_all_databases():
        """Start the chain of Notion database updates"""
        process_next_notion_update()

    def on_error(error_msg="Error downloading cache from GitHub. Check the console for details."):
        def error():
            if progress[0] is None:
                return
            progress[0].close()
            tooltip(error_msg)
        main_window.taskman.run_on_main(error)

    def download_thread():
        # GitHub downloads (steps 0-3)
        for idx, (db_id, name) in enumerate(DATABASES):
            update_progress(idx, f"Downloading {name} database from GitHub...")
            success = notion_cache.download_cache_from_github(db_id)
            if not success:
                on_error(f"Failed to download {name} database from GitHub")
                return
            time.sleep(0.5)  # Small delay to make progress visible

        # Start Notion updates (steps 4-9)
        update_all_databases()

    thread = threading.Thread(target=download_thread, daemon=True)
    thread.start()
