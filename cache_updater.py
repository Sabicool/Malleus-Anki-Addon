"""
Cache Updater
Handles the cache update process with a themed Malleus progress dialog.

Progress is split into two equal phases, both computed dynamically from
len(DATABASES) so adding or removing databases never breaks the bar:

  Phase 1 — GitHub download : steps 0 … N-1
  Phase 2 — Notion sync     : steps N … 2N-1
  Done                      : step 2N  →  dialog closes

During the async Notion phase the bar briefly goes indeterminate (pulse)
while waiting for the network response, then snaps to the next integer
step when the callback fires.
"""

import time
import threading
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, Qt, QTimer
)
from aqt.utils import tooltip
from .config import DATABASES

try:
    from .ui.styles import apply_malleus_style, make_header, COLORS
except Exception:
    try:
        from .styles import apply_malleus_style, make_header, COLORS
    except Exception:
        def apply_malleus_style(w): pass
        def make_header(*a, **kw):
            from aqt.qt import QWidget
            return QWidget()
        COLORS = {"accent": "#4a82cc"}


class _MalleusProgressDialog(QDialog):
    """
    Themed progress dialog for cache updates.

    Public interface mirrors QProgressDialog (setValue / setLabelText / close)
    so perform_cache_update can call it without caring about the implementation.

    Extra methods
    -------------
    pulse()   – switch bar to indeterminate (busy) mode
    unset_pulse(value) – snap out of pulse and set a concrete value
    """

    def __init__(self, parent, maximum: int):
        super().__init__(parent)
        self.setWindowTitle("Malleus — Update Database Cache")
        self.setMinimumWidth(460)
        self.setFixedHeight(175)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
        )

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(16, 12, 16, 16)

        # Branded header
        header = make_header(
            title="Updating Database Cache",
            subtitle="Downloading and syncing Malleus databases…"
        )
        layout.addWidget(header)

        # Status label + percentage on one line
        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self._label = QLabel("Initialising…")
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "font-size: 12px; background: transparent;"
        )
        status_row.addWidget(self._label, stretch=1)

        self._pct = QLabel("0 %")
        self._pct.setStyleSheet(
            f"font-size: 11px; font-weight: 700; "
            f"color: {COLORS.get('accent','#4a82cc')}; "
            f"background: transparent; min-width: 36px;"
        )
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self._pct)

        layout.addLayout(status_row)

        # Progress bar
        self._bar = QProgressBar()
        self._bar.setMinimum(0)
        self._bar.setMaximum(maximum)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(10)
        layout.addWidget(self._bar)

        self._maximum = maximum

        self.setLayout(layout)
        apply_malleus_style(self)

    # ── Public interface ──────────────────────────────────────────────────────

    def setValue(self, value: int):
        """Set a concrete progress value and update the percentage label."""
        if self._bar.maximum() == 0:          # snap out of indeterminate mode
            self._bar.setMaximum(self._maximum)
        self._bar.setValue(value)
        pct = int(round(value / self._maximum * 100)) if self._maximum else 0
        self._pct.setText(f"{pct} %")

    def setLabelText(self, text: str):
        self._label.setText(text)

    def pulse(self, pct_text: str = ""):
        """
        Switch to indeterminate (animated busy) mode.

        pct_text — short string shown in the percentage column while busy,
                   e.g. "9/16 · 56 %".  Pass empty to clear it.
        """
        self._bar.setMaximum(0)
        self._pct.setText(pct_text)

    def unset_pulse(self, value: int):
        """Leave indeterminate mode and show a concrete value."""
        self._bar.setMaximum(self._maximum)
        self.setValue(value)

    def close(self):
        super().close()


def perform_cache_update(notion_cache, main_window):
    """Download cache from GitHub, then sync new pages from Notion."""

    n = len(DATABASES)          # number of databases
    total_steps = n * 2         # phase 1: n GitHub DLs  + phase 2: n Notion syncs

    current_notion_update = [0]
    progress = [None]

    # ── Create dialog on main thread ──────────────────────────────────────────

    def create_progress():
        progress[0] = _MalleusProgressDialog(main_window, maximum=total_steps)
        progress[0].show()

    main_window.taskman.run_on_main(create_progress)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def update_progress(step: int, message: str):
        def _update():
            if progress[0] is None:
                return
            progress[0].setValue(step)
            progress[0].setLabelText(message)
        main_window.taskman.run_on_main(_update)

    def start_pulse(notion_idx: int, message: str):
        """Go indeterminate while an async Notion request is in-flight.
        Shows 'DB x/n · y%' in the percentage label so the user can see progress."""
        step     = n + notion_idx          # absolute step at start of this DB sync
        pct      = int(round(step / total_steps * 100))
        pct_text = f"DB {notion_idx + 1}/{n} · {pct} %"
        def _pulse():
            if progress[0] is None:
                return
            progress[0].pulse(pct_text)
            progress[0].setLabelText(message)
        main_window.taskman.run_on_main(_pulse)

    def stop_pulse(step: int, message: str):
        """Snap out of indeterminate mode once the callback fires."""
        def _stop():
            if progress[0] is None:
                return
            progress[0].unset_pulse(step)
            progress[0].setLabelText(message)
        main_window.taskman.run_on_main(_stop)

    # ── Phase 2: Notion sync chain ────────────────────────────────────────────

    def process_next_notion_update():
        idx = current_notion_update[0]
        if idx < n:
            db_id, name = DATABASES[idx]
            # Phase-2 step = n + idx  (picks up right after all GitHub downloads)
            notion_step = n + idx
            if db_id:
                start_pulse(idx, f"Syncing new {name} pages from Notion…")
                notion_cache.update_cache_async(
                    db_id, force=True, callback=on_notion_update_complete
                )
            else:
                on_notion_update_complete()
        else:
            def complete():
                if progress[0] is None:
                    return
                progress[0].unset_pulse(total_steps)
                progress[0].setLabelText("Done!")
                # Brief pause so the user sees 100 % before the window closes
                QTimer.singleShot(600, progress[0].close)
                QTimer.singleShot(700, lambda: tooltip(
                    "Cache successfully downloaded and updated"
                ))
            main_window.taskman.run_on_main(complete)

    def on_notion_update_complete():
        idx = current_notion_update[0]
        notion_step = n + idx + 1   # +1 because this callback fires after completion
        db_name = DATABASES[idx][1] if idx < n else ""
        stop_pulse(notion_step, f"{db_name} synced ✓" if db_name else "Synced ✓")
        current_notion_update[0] += 1
        # Small breathing room so the step is visible before the next starts
        time.sleep(0.15)
        process_next_notion_update()

    # ── Phase 1: GitHub downloads (blocking, runs in background thread) ───────

    def on_error(msg="Error downloading cache from GitHub. Check the console for details."):
        def _err():
            if progress[0] is None:
                return
            progress[0].close()
            tooltip(msg)
        main_window.taskman.run_on_main(_err)

    def download_thread():
        for idx, (db_id, name) in enumerate(DATABASES):
            update_progress(idx, f"Downloading {name} from GitHub…")
            success = notion_cache.download_cache_from_github(db_id)
            if not success:
                on_error(f"Failed to download {name} from GitHub")
                return
            time.sleep(0.2)   # brief pause makes each step visible

        # Hand off to Phase 2
        process_next_notion_update()

    thread = threading.Thread(target=download_thread, daemon=True)
    thread.start()
