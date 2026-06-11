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
from .utils import malleus_tooltip
from .config import DATABASES, GENERATED_DATABASES

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


def perform_cache_update(notion_cache, main_window, full=False, on_complete=None):
    """Refresh the database caches.

    Normal (full=False): download every cache from GitHub — including the
    generated DBs, whose seed the daily CI build keeps fresh — then sync new
    pages from Notion.

    Full (full=True, Shift+click): generated DBs are regenerated directly from
    the full Notion graph (slower, but guaranteed current).  Ordinary DBs never
    do a full Notion re-fetch (too expensive) — they always use the GitHub seed
    plus an incremental edits sync, full or not.

    ``on_complete`` is invoked on the main thread once the whole update chain
    finishes (success or error)."""

    n = len(DATABASES)          # number of databases
    total_steps = n * 2         # phase 1: n GitHub DLs  + phase 2: n Notion syncs

    current_notion_update = [0]
    progress = [None]
    failed_downloads = set()    # DBs whose GitHub seed was missing → rebuild from Notion

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
            # Full Notion rebuilds apply to GENERATED databases only — when
            # explicitly asked (Shift+click) or when their GitHub seed failed in
            # Phase 1.  Ordinary DBs always sync incrementally on top of the seed.
            needs_rebuild = (db_id in GENERATED_DATABASES
                             and (full or db_id in failed_downloads))
            if not db_id:
                on_notion_update_complete()
            else:
                if needs_rebuild:
                    msg = f"Rebuilding {name} from Notion…"
                elif db_id in GENERATED_DATABASES:
                    # Smart refresh: regenerate only if Notion changed since the seed.
                    msg = f"Checking {name} for changes…"
                else:
                    msg = f"Syncing new {name} pages from Notion…"
                start_pulse(idx, msg)
                notion_cache.update_cache_async(
                    db_id, force=True, full=needs_rebuild,
                    callback=on_notion_update_complete
                )
        else:
            def complete():
                if progress[0] is not None:
                    progress[0].unset_pulse(total_steps)
                    progress[0].setLabelText("Done!")
                    # Brief pause so the user sees 100 % before the window closes
                    QTimer.singleShot(600, progress[0].close)
                    QTimer.singleShot(700, lambda: malleus_tooltip(
                        "Cache successfully downloaded and updated"
                    ))
                if on_complete:
                    on_complete()
            main_window.taskman.run_on_main(complete)

    def on_notion_update_complete():
        idx = current_notion_update[0]
        notion_step = n + idx + 1   # +1 because this callback fires after completion
        db_name = DATABASES[idx][1] if idx < n else ""
        stop_pulse(notion_step, f"{db_name} synced ✓" if db_name else "Synced ✓")
        current_notion_update[0] += 1
        # Small breathing room so the step is visible before the next starts —
        # scheduled via QTimer (this callback runs on the main thread; sleeping
        # here would freeze the UI).
        def _next():
            main_window.taskman.run_on_main(process_next_notion_update)
        QTimer.singleShot(150, _next)

    # ── Phase 1: GitHub downloads (blocking, runs in background thread) ───────

    def on_error(msg="Error downloading cache from GitHub. Check the console for details."):
        def _err():
            if progress[0] is None:
                return
            progress[0].close()
            malleus_tooltip(msg)
            if on_complete:
                on_complete()
        main_window.taskman.run_on_main(_err)

    def download_thread():
        for idx, (db_id, name) in enumerate(DATABASES):
            # Full (Shift+click) rebuilds from Notion apply to generated DBs only;
            # ordinary DBs still seed from GitHub below (a full Notion re-fetch
            # of them is too expensive — seed + incremental sync is the model).
            if full and db_id in GENERATED_DATABASES:
                update_progress(idx, f"{name}: will rebuild from Notion…")
                time.sleep(0.1)
                continue
            # Generated DBs included: their seed download is ETag-conditional
            # (304 = no transfer) and never touches the local raw graph, and on a
            # first run after an add-on update it's what makes Phase 2 cheap —
            # without it the missing raw graph would force a slow Notion rebuild.
            update_progress(idx, f"Downloading {name} from GitHub…")
            success = notion_cache.download_cache_from_github(db_id)
            if not success:
                # Missing seed (e.g. not yet committed) — don't abort the whole
                # update; generated DBs get rebuilt from Notion in Phase 2.
                print(f"No GitHub seed for {name}.")
                failed_downloads.add(db_id)
            time.sleep(0.2)   # brief pause makes each step visible

        # Hand off to Phase 2
        process_next_notion_update()

    thread = threading.Thread(target=download_thread, daemon=True)
    thread.start()
