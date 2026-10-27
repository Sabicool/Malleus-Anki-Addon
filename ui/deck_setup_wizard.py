"""
Malleus Deck Setup wizard
Walks a user onto the AnkiCollab copy of the Malleus deck, doing each step
for them.  deck_setup.py holds the detection and the actions; this file is
the window and the startup prompts.

The wizard works out a plan from where the user is (see deck_setup.detect):

  new      Install AnkiCollab → Account → Download → Sync → Done
  migrate  Install AnkiCollab → Account → Sync & back up → Leave AnkiHub →
           Set aside old deck → Download → Save to AnkiWeb → Done
  dual     Sync & back up → Leave AnkiHub → Account → Save to AnkiWeb → Done
  done     Account → Done (only when not signed in)

Installing AnkiCollab needs a restart, so it comes first, before anything
is changed; the wizard reopens itself after the restart.  Steps the user
has already done are left out of the plan, so reopening it simply picks up
where they left off.

AnkiCollab requires an account to download decks, so the account step
can't be skipped: nothing is downloaded until the user is signed in.  For
someone on both add-ons it comes after leaving AnkiHub, so stopping the
clash never waits on a sign-in.
"""
import os
import time
import webbrowser

from aqt import mw
from aqt.qt import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                    QWidget, QFrame, QCheckBox, QTimer, Qt, QApplication,
                    QScrollArea)
from aqt.utils import tooltip

from .. import deck_setup as ds

try:
    from .styles import apply_malleus_style, make_header, COLORS
except Exception:
    COLORS = {"accent": "#4a82cc", "danger": "#c05050"}
    def apply_malleus_style(w): pass
    def make_header(title="", subtitle=None, logo_path=None, **kwargs):
        label = QLabel(f"<b>{title}</b>")
        return label

_addon_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_LOGO = os.path.join(_addon_dir, "logo.png")

ANKICOLLAB_ANKIWEB_PAGE = "https://ankiweb.net/shared/info/1957538407"

STEPS = {
    "welcome":  "Welcome",
    "install":  "Install AnkiCollab",
    "account":  "AnkiCollab account",
    "prepare":  "Sync and back up",
    "ankihub":  "Leave AnkiHub",
    "aside":    "Set aside old deck",
    "download": "Download Malleus",
    "save":     "Save to AnkiWeb",
    "finish":   "All done",
}

# One line each for the welcome page's "here's what will happen" list.
BLURBS = {
    "install":  "Install the free AnkiCollab add-on, then restart Anki once",
    "account":  "Sign in to AnkiCollab. It needs a free account",
    "prepare":  "Sync with AnkiWeb and back up your collection",
    "ankihub":  "Stop AnkiHub updating Malleus (AnkiHub stays for any other decks)",
    "aside":    "Rename your current Malleus deck so a fresh one can be built",
    "download": "Download Malleus. Your existing cards keep all their progress",
    "save":     "Sync to AnkiWeb so your other devices get the changes",
}

STATS_ASK = (
    "<b>When AnkiCollab asks whether to share your review statistics, please "
    "say yes.</b> They show the Malleus team which cards people struggle "
    "with, so we know what to fix first."
)

_wizard = None


def build_plan(info: dict) -> list:
    scenario = info["scenario"]
    signed_in = info["collab_logged_in"]
    if scenario == "done":
        return ["finish"] if signed_in else ["account", "finish"]

    plan = ["welcome"]
    if scenario != "dual":
        if info["collab_status"] != "loaded":
            plan.append("install")
        if not signed_in:
            plan.append("account")
    if info["ankihub_decks"] or info["old_decks"]:
        plan.append("prepare")
    if info["ankihub_decks"]:
        plan.append("ankihub")
    if info["old_decks"]:
        plan.append("aside")
    if scenario == "dual":
        if not signed_in:
            plan.append("account")
    else:
        plan.append("download")
    if info["ankiweb"]:
        plan.append("save")
    plan.append("finish")
    return plan


def _bullets(lines) -> str:
    items = "".join(f"<li style='margin-bottom:4px'>{line}</li>" for line in lines)
    return f"<ul style='margin-left:-20px'>{items}</ul>"


class DeckSetupWizard(QDialog):

    def __init__(self, parent=None, resumed=False):
        super().__init__(parent or mw)
        self.setWindowTitle("Malleus Deck Setup")
        self.setMinimumSize(780, 520)
        apply_malleus_style(self)

        self.resumed = resumed
        self.info = ds.detect()
        self.settings = self.info["settings"]
        self.key = self.info["deck_key"]
        self.plan = build_plan(self.info)
        self.current = None
        self.done_steps = set()
        self.skipped = set()
        self.summary = []             # ✓ lines for the finish page
        self.safe_to_upload = False   # synced cleanly earlier this session
        self.save_skipped = False
        self.ankihub_still_on_server = []
        self.aside_dids = []
        self.aside_lines = {}         # did → its "Renamed your old deck…" summary line
        self.busy = False

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_download)
        self._pull_finished_at = None

        self._build_ui()
        self.finished.connect(self._on_closed)
        self.go(self.plan[0])

    # ── Layout ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(make_header(
            "Malleus Deck Setup",
            subtitle="Get the Malleus deck through AnkiCollab",
            logo_path=_LOGO if os.path.exists(_LOGO) else None,
        ))

        body = QHBoxLayout()
        body.setContentsMargins(18, 14, 18, 16)
        body.setSpacing(20)
        outer.addLayout(body, 1)

        # Step list
        self.sidebar = QVBoxLayout()
        self.sidebar.setSpacing(8)
        side = QWidget()
        side.setFixedWidth(200)
        side.setLayout(self.sidebar)
        self.step_labels = {}
        for step in self.plan:
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)   # a long name plus "(skipped)" can outgrow the column
            label.linkActivated.connect(lambda _=None, s=step: self._go_back(s))
            self.sidebar.addWidget(label)
            self.step_labels[step] = label
        self.sidebar.addStretch()
        body.addWidget(side)

        line = QWidget()
        line.setFixedWidth(1)
        line.setStyleSheet(f"background: {COLORS.get('border', 'rgba(74,130,204,0.28)')};")
        body.addWidget(line)

        # Page
        page = QVBoxLayout()
        page.setSpacing(12)
        self.title = QLabel()
        self.title.setStyleSheet(f"font-size: 19px; font-weight: 700; color: {COLORS['accent']};")
        self.title.setWordWrap(True)
        page.addWidget(self.title)

        # Everything between the title and the buttons scrolls, so a long
        # finish page never gets cut off.
        content = QWidget()
        content.setObjectName("wizard_content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The shared stylesheet frames every scroll area; this one should
        # read as plain page text.
        scroll.setObjectName("wizard_scroll")
        scroll.setStyleSheet("#wizard_scroll { border: none; background: transparent; }"
                             " #wizard_content { background: transparent; }")
        page.addWidget(scroll, 1)

        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setOpenExternalLinks(True)
        self.body.setStyleSheet("font-size: 13px; line-height: 140%;")
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_layout.addWidget(self.body)

        self.extra = QVBoxLayout()   # page-specific widgets (checkboxes, …)
        self.extra.setSpacing(10)
        content_layout.addLayout(self.extra)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setOpenExternalLinks(True)
        content_layout.addWidget(self.status)
        content_layout.addStretch()

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(10)
        page.addLayout(self.buttons)
        body.addLayout(page, 1)

    def _step_name(self, step):
        # Without an AnkiWeb account the first step only backs up.
        if step == "prepare" and not self.info["ankiweb"]:
            return "Back up"
        return STEPS[step]

    def _refresh_sidebar(self):
        accent = COLORS["accent"]
        for step, label in self.step_labels.items():
            name = self._step_name(step)
            if step == self.current:
                label.setText(f"<span style='color:{accent}; font-weight:700'>›&nbsp; {name}</span>")
            elif self._can_go_back(step):
                mark = ("<span style='color:gray'>–</span>" if step in self.skipped
                        else "<span style='color:#3a9a5a'>✓</span>")
                suffix = " (skipped)" if step in self.skipped else ""
                label.setText(f"{mark}&nbsp; <a href='#' style='color:{accent}; "
                              f"text-decoration:none'>{name}{suffix}</a>")
            elif step in self.skipped:
                label.setText(f"<span style='color:gray'>–&nbsp; {name} (skipped)</span>")
            elif step in self.done_steps:
                label.setText(f"<span style='color:#3a9a5a'>✓</span>&nbsp; {name}")
            else:
                label.setText(f"<span style='color:gray'>○&nbsp; {name}</span>")

    def _page(self, title, body, primary=None, secondary=None, link=None):
        """primary/secondary/link: (text, callback) or None."""
        self.title.setText(title)
        self.body.setText(body)
        self.status.setText("")
        self._clear_layout(self.extra)
        self._set_buttons(primary, secondary, link)

    def _set_buttons(self, primary=None, secondary=None, link=None):
        self._clear_layout(self.buttons)
        if link:
            text, callback = link
            label = QLabel(f"<a href='#' style='color:gray'>{text}</a>")
            label.linkActivated.connect(lambda _=None, cb=callback: cb())
            self.buttons.addWidget(label)
        self.buttons.addStretch()
        if secondary:
            btn = QPushButton(secondary[0])
            btn.setObjectName("secondary")
            btn.clicked.connect(lambda _=False, cb=secondary[1]: cb())
            self.buttons.addWidget(btn)
        if primary:
            btn = QPushButton(primary[0])
            btn.setDefault(True)
            btn.clicked.connect(lambda _=False, cb=primary[1]: cb())
            self.buttons.addWidget(btn)

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                DeckSetupWizard._clear_layout(item.layout())

    def _working(self, text):
        self.busy = True
        self.status.setText(f"<span style='color:{COLORS['accent']}'>⏳ {text}</span>")
        self._set_buttons()
        QApplication.processEvents()

    def _error(self, text, retry=None, link=None):
        self.busy = False
        self.status.setText(f"<span style='color:{COLORS['danger']}'>{text}</span>")
        self._set_buttons(primary=("Try again", retry) if retry else None,
                          secondary=("Close", self.close), link=link)

    # ── Navigation ──────────────────────────────────────────────────────────

    def go(self, step):
        self.current = step
        self.busy = False
        self._refresh_sidebar()
        getattr(self, f"_page_{step}")()

    # Only the pages that just ask the user something can be revisited, and
    # only until the wizard starts changing things: there's no going back
    # on a backup, an unsubscribe or a download.
    _REVISITABLE = ("welcome", "account")
    _POINT_OF_NO_RETURN = ("prepare", "ankihub", "aside", "download")

    def _can_go_back(self, step):
        return (step in self._REVISITABLE and step != self.current and not self.busy
                and (step in self.done_steps or step in self.skipped)
                and not any(s in self.done_steps or s == self.current
                            for s in self._POINT_OF_NO_RETURN))

    def _go_back(self, step):
        if self._can_go_back(step):
            self.skipped.discard(step)
            self.done_steps.discard(step)
            self.go(step)

    def next(self):
        self.skipped.discard(self.current)
        self.done_steps.add(self.current)
        index = self.plan.index(self.current)
        self.go(self.plan[index + 1])

    def skip(self):
        self.skipped.add(self.current)
        index = self.plan.index(self.current)
        self.go(self.plan[index + 1])

    # ── Pages ───────────────────────────────────────────────────────────────

    def _page_welcome(self):
        scenario = self.info["scenario"]
        blurbs = dict(BLURBS)
        if self.info["collab_status"] == "disabled":
            blurbs["install"] = "Switch the AnkiCollab add-on back on, then restart Anki once"
        if not self.info["ankiweb"]:
            blurbs["prepare"] = "Back up your collection"
        if scenario == "new":
            blurbs["download"] = "Download Malleus"   # no existing cards to keep
        upcoming = [blurbs[s] for s in self.plan if s in blurbs]
        timing = ("<p>It takes about 10–30 minutes, mostly waiting for the download. "
                  "Best done on a reliable internet connection.</p>")

        if scenario == "dual":
            title = "Finish moving to AnkiCollab"
            text = ("<p>You're getting Malleus from <b>both AnkiHub and AnkiCollab</b>. "
                    "When both add-ons try to update the same cards they clash, so AnkiHub "
                    "needs to stop updating Malleus.</p>"
                    "<p><b>Your cards and review history stay exactly as they are.</b></p>"
                    "<p>Here's what will happen:</p>" + _bullets(upcoming))
        elif scenario == "migrate":
            title = "Move Malleus to AnkiCollab"
            text = ("<p>Malleus has moved from AnkiHub to <b>AnkiCollab</b>, a free add-on that "
                    "downloads the deck and keeps it up to date. This will move your deck "
                    "across for you.</p>"
                    "<p><b>Your cards, progress and review history are kept.</b></p>"
                    "<p>Here's what will happen:</p>" + _bullets(upcoming) + timing)
        else:
            title = "Get the Malleus deck"
            text = ("<p>Malleus is shared through <b>AnkiCollab</b>, a free add-on that "
                    "downloads the deck and keeps it up to date. This will set it all up "
                    "for you.</p>"
                    "<p>Here's what will happen:</p>" + _bullets(upcoming) + timing)

        if self.resumed:
            text = "<p><b>Welcome back! Let's carry on where you left off.</b></p>" + text

        self._page(title, text,
                   primary=("Continue" if self.resumed else "Start", self.next),
                   secondary=("Not now", self.close))

    # Install ----------------------------------------------------------------

    def _page_install(self):
        status = ds.addon_status(ds.ANKICOLLAB_ADDON_ID)
        if status == "loaded":
            self.next()
            return
        if status == "restart":
            self._page(
                "Restart Anki",
                "<p>AnkiCollab is installed. Anki needs to restart to switch it on.</p>"
                "<p>Close Anki and open it again. <b>This window will come back by itself</b> "
                "so you can carry on.</p>",
                primary=("Close Anki", self._restart_anki),
                secondary=("I'll restart later", self._restart_later),
            )
            return
        if status == "disabled":
            self._page(
                "Switch on AnkiCollab",
                "<p>AnkiCollab, the free add-on that delivers the Malleus deck, is installed "
                "but switched off. We'll switch it back on for you.</p>",
                primary=("Switch on AnkiCollab", self._install),
                secondary=("Not now", self.close),
            )
            return

        self._page(
            "Install AnkiCollab",
            "<p>AnkiCollab is the free add-on that delivers the Malleus deck and keeps it "
            "up to date. We'll download it from AnkiWeb for you.</p>",
            primary=("Install AnkiCollab", self._install),
            secondary=("Not now", self.close),
        )

    def _install(self):
        switching_on = ds.addon_status(ds.ANKICOLLAB_ADDON_ID) == "disabled"
        self._working("Switching on AnkiCollab…" if switching_on
                      else "Downloading AnkiCollab from AnkiWeb…")

        def done(ok, message):
            if ok:
                self.summary.append("Switched AnkiCollab back on" if switching_on
                                    else "Installed AnkiCollab")
                self._page_install()
            else:
                self._error(
                    "AnkiCollab couldn't be downloaded. Check your internet connection "
                    "and try again.",
                    retry=self._install,
                    link=("Install it yourself from AnkiWeb",
                          lambda: webbrowser.open(ANKICOLLAB_ANKIWEB_PAGE)),
                )
        ds.install_ankicollab(done)

    def _restart_later(self):
        ds.update_state(resume=True)
        tooltip("The setup will carry on next time you open Anki.", parent=mw)
        self.close()

    def _restart_anki(self):
        ds.update_state(resume=True)
        self.close()
        mw.close()

    # Account ----------------------------------------------------------------

    def _page_account(self):
        if ds.collab_is_logged_in():
            self.next()
            return
        self._page(
            "Your AnkiCollab account",
            "<p><b>AnkiCollab needs a free account</b> to download Malleus and keep it up "
            "to date. You only sign in once.</p>"
            "<p><b>New to AnkiCollab?</b> Create an account on the website first, then "
            "come back here and press <i>Sign in</i>.</p>"
            "<p><b>Signing in</b> shows a short code. Approve it in your browser and Anki "
            "signs in by itself, with no password to type.</p>",
            primary=("Sign in", self._sign_in),
            secondary=("Create a free account", self._sign_up),
            link=("Not now", self.close),
        )

    def _sign_up(self):
        webbrowser.open(ds.collab_signup_url())
        self.status.setText("Once you've made your account, come back and press "
                            "<b>Sign in</b>.")

    def _sign_in(self, then=None):
        """then: what to do once signed in (default: the next step)."""
        # Already signed in through AnkiCollab's own menu (the fallback below)?
        if not ds.collab_is_logged_in():
            try:
                ds.collab_open_login(self)
            except Exception as e:
                self.status.setText(f"Couldn't open the AnkiCollab sign-in window ({e}). "
                                    "Use <b>AnkiCollab → Login</b> in Anki's menu bar, then "
                                    "press <b>Sign in</b> here again.")
                return
        if ds.collab_is_logged_in():
            self.summary.append("Signed in to AnkiCollab")
            (then or self.next)()
        else:
            self.status.setText("You're not signed in yet, and AnkiCollab needs an account "
                                "to download Malleus. Press <b>Sign in</b> to try again.")

    # Sync and back up -------------------------------------------------------

    def _page_prepare(self):
        if self.info["ankiweb"]:
            text = ("<p>Before anything changes, we bring this computer up to date with "
                    "AnkiWeb and make a backup you can restore if anything goes wrong.</p>")
        else:
            text = ("<p>Before anything changes, we make a backup you can restore if "
                    "anything goes wrong.</p>")
        self._page(self._step_name("prepare"), text)
        self._prepare()

    def _prepare(self):
        if self.info["ankiweb"]:
            self._working("Syncing with AnkiWeb…")
            ds.sync_ankiweb(self._after_first_sync)
        else:
            self._backup()

    def _after_first_sync(self, result):
        if result == "error":
            self._error("The sync didn't finish, so nothing has been changed. Check your "
                        "internet connection and try again.",
                        retry=self._prepare,
                        link=("Carry on without syncing", self._backup))
            return
        self.safe_to_upload = result == "ok"
        if result == "ok":
            self.summary.append("Synced with AnkiWeb")
        self._backup()

    def _backup(self):
        from aqt.operations import QueryOp
        self._working("Backing up your collection…")

        def failed(exc):
            self._error(f"The backup didn't work, so nothing has been changed.<br>({exc})",
                        retry=self._backup)

        def succeeded(_):
            self.summary.append("Backed up your collection")
            self.next()

        QueryOp(parent=self, op=lambda col: mw.create_backup_now(),
                success=succeeded).failure(failed).with_progress(
            "Backing up your collection…").run_in_background()

    # Leave AnkiHub ----------------------------------------------------------

    def _page_ankihub(self):
        self._page(
            "Leave AnkiHub",
            "<p>Stopping AnkiHub from updating Malleus. Your cards and review history "
            "stay exactly as they are, and AnkiHub stays installed for any other decks "
            "you get from it.</p>",
        )
        self._leave_ankihub()

    def _leave_ankihub(self):
        from aqt.operations import QueryOp
        decks = ds.ankihub_malleus_decks(self.settings)
        if not decks:
            self.next()
            return
        self._working("Unsubscribing from Malleus on AnkiHub…")

        def unsubscribe(_):
            return [(d, ds.ankihub_unsubscribe_on_server(d["uuid"])) for d in decks]

        def remove(results):
            try:
                for deck, on_server_done in results:
                    ds.ankihub_remove_locally(deck["uuid"])
                    if not on_server_done:
                        self.ankihub_still_on_server.append(deck["name"])
            except Exception as e:
                self._error(
                    f"AnkiHub wouldn't let go of Malleus ({e}).<br>Open <b>AnkiHub → Decks</b>, "
                    "choose Malleus and press <b>Unsubscribe</b>, then press Try again.",
                    retry=self._leave_ankihub)
                return
            mw.reset()
            self.summary.append("Stopped AnkiHub updating Malleus")
            self.next()

        QueryOp(parent=self, op=unsubscribe, success=remove).without_collection().failure(
            lambda e: self._error(f"Something went wrong leaving AnkiHub ({e}).",
                                  retry=self._leave_ankihub)
        ).run_in_background()

    # Set aside the old deck -------------------------------------------------

    def _page_aside(self):
        self._page(
            "Set aside the old deck",
            "<p>AnkiCollab builds a fresh Malleus deck and moves your existing cards into "
            "it, with all their progress. To make room, your current deck gets “(old "
            "copy)” added to its name.</p>",
        )
        # Decks found when the wizard opened: AnkiHub's record of which deck
        # is Malleus is gone once we've left it.
        self._set_aside(self.info["old_decks"])
        self.next()

    def _set_aside(self, dids):
        for did in dids:
            if not mw.col.decks.get(did, default=False):
                continue
            new_name = ds.set_deck_aside(did)
            line = f"Renamed your old deck to “{new_name}”"
            self.aside_dids.append(did)
            self.aside_lines[did] = line
            self.summary.append(line)
        mw.reset()

    # Download ---------------------------------------------------------------

    def _page_download(self):
        migrating = self.info["scenario"] == "migrate"
        self._page(
            "Download Malleus",
            "<p>AnkiCollab is downloading the deck: the cards first, then the images. "
            "The first download is big, so it can take 10–30 minutes. "
            "<b>Please keep Anki open.</b></p>"
            + ("<p>Your existing cards are matched up and moved into the new deck with "
               "their progress intact.</p>" if migrating else "")
            + f"<p>{STATS_ASK}</p>",
        )
        if ds.collab_installed_deck_id(self.key):
            self._downloaded()
            return
        # A subscription with no deck yet is a download that never finished.
        if ds.collab_subscription(self.key) is not None:
            ds.collab_forget_subscription(self.key)
        self._start_download()

    def _start_download(self):
        # The account step comes first, but someone can sign out in between,
        # and AnkiCollab only serves decks to signed-in users.
        if not ds.collab_is_logged_in():
            self.busy = False
            self.status.setText("AnkiCollab needs you to be signed in before it can "
                                "download Malleus.")
            self._set_buttons(primary=("Sign in",
                                       lambda: self._sign_in(then=self._start_download)),
                              secondary=("Not now", self.close))
            return
        self._working("Downloading… AnkiCollab will show its own progress too.")
        self._pull_finished_at = None
        try:
            ds.collab_subscribe(self.key, self._pulled)
        except Exception as e:
            print(f"Malleus: subscribing through AnkiCollab failed: {e}")
            self._manual_subscribe_fallback()
            return
        self._poll.start(1500)

    def _pulled(self, ok):
        if not ok:
            self._poll.stop()
            self._error("The download didn't finish. Check your internet connection, "
                        "then try again.",
                        retry=self._retry_download,
                        link=("Use AnkiCollab's own window instead",
                              self._manual_subscribe_fallback))
            return
        self._pull_finished_at = time.time()

    def _poll_download(self):
        if ds.collab_installed_deck_id(self.key):
            self._poll.stop()
            self._downloaded()
            return
        # AnkiCollab has fetched the deck and its installer has gone quiet,
        # but no deck appeared: offer the "unsubscribe and add it again"
        # retry.
        if (self._pull_finished_at and not mw.progress.busy()
                and time.time() - self._pull_finished_at > 20):
            self._set_buttons(
                primary=("Try again", self._retry_download),
                link=("Use AnkiCollab's own window instead", self._manual_subscribe_fallback),
            )
            self.status.setText(
                "This is taking longer than expected. If AnkiCollab showed an error or "
                "nothing seems to be happening, press <b>Try again</b>.")
            self._pull_finished_at = None

    def _retry_download(self):
        self._poll.stop()
        ds.collab_forget_subscription(self.key)
        self._set_aside(ds.old_malleus_decks(self.settings, []))
        self._start_download()

    def _manual_subscribe_fallback(self):
        self._poll.stop()
        QApplication.clipboard().setText(self.key)
        self._page(
            "Add Malleus in AnkiCollab",
            "<p>Let's do this step in AnkiCollab's own window. The Malleus deck key is "
            "already copied, so:</p>"
            + _bullets(["In the window that opens, click in the box at the bottom",
                        "Paste (Ctrl+V, or ⌘V on a Mac) and click <b>Add</b>",
                        "Let it download, then come back here and press <b>Check again</b>"])
            + f"<p>The key is <code>{self.key}</code></p>",
            primary=("Check again", self._page_download_check),
            secondary=("Open AnkiCollab", self._open_collab_subscriptions),
        )

    def _open_collab_subscriptions(self):
        try:
            ds.collab_open_subscriptions()
        except Exception:
            self.status.setText("Open <b>AnkiCollab → Manage Subscriptions</b> from Anki's "
                                "menu bar.")

    def _page_download_check(self):
        if ds.collab_installed_deck_id(self.key):
            self._downloaded()
        else:
            self.status.setText("Malleus isn't there yet. Once AnkiCollab has finished "
                                "downloading, press <b>Check again</b>.")

    def _downloaded(self):
        did = ds.collab_installed_deck_id(self.key)
        # AnkiCollab finds its own decks by an ID stored on the deck, not by
        # name.  A deck it made earlier (an old subscription, or a first
        # attempt before Try again) is taken back and renamed to Malleus
        # rather than left aside, so it isn't an old deck after all.
        if did:
            reclaimed = [d for d in self.aside_dids if mw.col.decks.get(d, default=False)
                         and did in mw.col.decks.deck_and_child_ids(d)]
            for d in reclaimed:
                line = self.aside_lines.pop(d, None)
                if line in self.summary:
                    self.summary.remove(line)
            self.aside_dids = [d for d in self.aside_dids if d not in reclaimed]
        try:
            ds.fix_synced_field_options()
        except Exception as e:
            print(f"Malleus: couldn't set the synced fields' options: {e}")
        name = mw.col.decks.name(did) if did else "Malleus"
        self.summary.append(f"Downloaded Malleus into “{name}”")
        if did:
            try:
                if any(ds.copy_deck_options(d, did) for d in self.aside_dids
                       if mw.col.decks.get(d, default=False)):
                    self.summary.append("Gave the new deck your old deck's options")
                ds.update_state(deck_options_copied=True)
            except Exception as e:
                print(f"Malleus: couldn't copy the old deck's options: {e}")
        self.next()

    # Save to AnkiWeb --------------------------------------------------------

    def _page_save(self):
        changed_note_types = bool(self.info["ankihub_decks"])
        if changed_note_types:
            text = ("<p>Last step. Leaving AnkiHub changed the Malleus card types, so AnkiWeb "
                    "needs a fresh copy of this computer's collection.</p>")
            if self.safe_to_upload:
                text += "<p>Press the button and we'll upload it for you.</p>"
            else:
                text += ("<p>If Anki asks whether to upload or download, choose "
                         "<b>Upload to AnkiWeb</b>.</p>")
            text += ("<p><b>On your phone and other computers:</b> next time you sync, Anki "
                     "will ask the same question there. Choose <b>Download</b> so they get "
                     "this computer's copy.</p>")
        else:
            text = ("<p>Sync with AnkiWeb so Malleus shows up on your phone and other "
                    "devices too.</p>")
        self._page("Save to AnkiWeb", text,
                   primary=("Sync now", self._sync_after),
                   link=("Skip, I'll sync later", self._skip_save))

    def _skip_save(self):
        self.save_skipped = True
        self.skip()

    def _sync_after(self):
        self._working("Syncing with AnkiWeb…")

        def done(result):
            if result == "error":
                self._error("The sync didn't finish. Check your internet connection and "
                            "try again.", retry=self._sync_after,
                            link=("Skip, I'll sync later", self._skip_save))
                return
            # The first sync already put "Synced with AnkiWeb" on the list.
            if self.safe_to_upload and result == "full":
                self.summary.append("Uploaded this computer's collection to AnkiWeb")
            elif self.safe_to_upload:
                self.summary.append("Saved your changes to AnkiWeb")
            else:
                self.summary.append("Synced with AnkiWeb")
            self.next()
        ds.sync_ankiweb(done, upload_if_asked=self.safe_to_upload)

    # Finish -----------------------------------------------------------------

    def _page_finish(self):
        ds.update_state(resume=False, finished=True)
        scenario = self.info["scenario"]

        if scenario == "done" and not self.summary:
            text = "<p>Malleus is set up through AnkiCollab on this computer.</p>"
        else:
            text = "".join(f"<div style='margin-bottom:4px'><span style='color:#3a9a5a'>✓</span>"
                           f"&nbsp; {line}</div>" for line in self.summary)

        notes = ["To get the latest Malleus changes at any time, use "
                 "<b>AnkiCollab → Update Decks</b>."]
        if self.ankihub_still_on_server:
            notes.append("You weren't signed in to AnkiHub, so your subscription is still "
                         "active on the AnkiHub website. It no longer affects this computer, "
                         "but please unsubscribe from Malleus at "
                         "<a href='https://app.ankihub.net'>ankihub.net</a> so it doesn't "
                         "carry on elsewhere.")
        if self.save_skipped and self.info["ankihub_decks"]:
            notes.append("When you next sync, choose <b>Upload to AnkiWeb</b> here, then "
                         "<b>Download</b> on your other devices.")

        # AnkiCollab asks about sharing stats once the deck has installed, by
        # which time the download page (where we first asked) is gone.
        if "download" in self.done_steps:
            text += f"<p>{STATS_ASK}</p>"
        text += "<p></p>" + _bullets(notes)
        self._page("You're all set" if scenario != "done" or self.summary else "Malleus is set up",
                   text, primary=("Done", self._finish))

        if ds.addon_status(ds.ANKICOLLAB_ADDON_ID) == "loaded":
            self.auto_update = QCheckBox("Check for Malleus updates every time Anki starts")
            self.auto_update.setChecked(True if scenario != "done" else ds.collab_update_on_startup())
            self.extra.addWidget(self.auto_update)
        else:
            self.auto_update = None

        self._add_old_deck_rows()

    def _add_old_deck_rows(self):
        """One row per deck we set aside.  Only a deck with no cards left
        can be deleted from here; anything still in it (cards AnkiHub marked
        deleted, the user's own cards never submitted) is theirs to sort."""
        rows = []
        for did in self.aside_dids:
            if not mw.col.decks.get(did, default=False):
                continue
            name = mw.col.decks.name(did)
            count = ds.deck_card_count(did)
            if count:
                text = (f"<b>“{name}”</b> still has {count:,} card{'s' if count != 1 else ''}. "
                        "These didn't move to the new deck. They might be cards AnkiHub "
                        "marked as deleted, or cards you made yourself. They're untouched, "
                        "so have a look and keep or delete them as you like.")
                button = ("Show these cards", lambda _=False, n=name: self._browse_deck(n))
            else:
                text = f"<b>“{name}”</b> is now empty. All its cards moved to the new deck."
                button = ("Delete this empty deck", None)
            rows.append((did, text, button))

        if not rows:
            return
        heading = QLabel("Your old deck" + ("s" if len(rows) > 1 else ""))
        heading.setStyleSheet(f"font-weight: 700; color: {COLORS['accent']}; margin-top: 6px;")
        self.extra.addWidget(heading)
        for did, text, (label, callback) in rows:
            row = QHBoxLayout()
            info = QLabel(text)
            info.setWordWrap(True)
            info.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(info, 1)
            btn = QPushButton(label)
            btn.setObjectName("secondary")
            if callback is None:
                btn.clicked.connect(lambda _=False, d=did, b=btn, i=info: self._delete_empty(d, b, i))
            else:
                btn.clicked.connect(callback)
            row.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
            self.extra.addLayout(row)

    def _browse_deck(self, name):
        from aqt import dialogs
        browser = dialogs.open("Browser", mw)
        escaped = name.replace('"', '\\"')
        browser.search_for(f'"deck:{escaped}"')

    def _delete_empty(self, did, button, info):
        # Re-check: something may have landed in it since the page was drawn.
        if not mw.col.decks.get(did, default=False):
            return
        installed = ds.collab_installed_deck_id(self.key)
        if installed and installed in mw.col.decks.deck_and_child_ids(did):
            info.setText(f"<b>“{mw.col.decks.name(did)}”</b> is where AnkiCollab keeps "
                         "Malleus, so it was left alone.")
            button.setEnabled(False)
            return
        if ds.deck_card_count(did):
            info.setText(f"<b>“{mw.col.decks.name(did)}”</b> isn't empty any more, "
                         "so it was left alone.")
            button.setEnabled(False)
            return
        name = mw.col.decks.name(did)
        mw.col.decks.remove([did])
        mw.reset()
        info.setText(f"Deleted the empty deck “{name}”.")
        button.setEnabled(False)
        button.setText("Deleted")

    def _finish(self):
        if self.auto_update is not None:
            try:
                ds.collab_set_update_on_startup(self.auto_update.isChecked())
            except Exception as e:
                print(f"Malleus: couldn't change AnkiCollab's startup setting: {e}")
        self.accept()

    # ── Closing ─────────────────────────────────────────────────────────────

    def _on_closed(self, _result):
        self._poll.stop()
        if self.current == "download" and self.busy:
            tooltip("AnkiCollab carries on downloading Malleus in the background.",
                    parent=mw, period=5000)
        elif self.current not in ("finish", "welcome"):
            tooltip("You can finish setting up Malleus any time from "
                    "Tools → Malleus Deck Setup.", parent=mw, period=5000)


# ── Opening ─────────────────────────────────────────────────────────────────

def open_wizard(resumed=False):
    global _wizard
    if not ds.is_available():
        from aqt.utils import showInfo
        showInfo("The Malleus deck isn't on AnkiCollab just yet. This setup will be "
                 "ready as soon as it is.", title="Malleus Deck Setup")
        return
    if _wizard is not None:
        try:
            if _wizard.isVisible():
                _wizard.raise_()
                _wizard.activateWindow()
                return
        except RuntimeError:
            pass
    _wizard = DeckSetupWizard(mw, resumed=resumed)
    _wizard.show()


# ── Startup prompts ─────────────────────────────────────────────────────────

DAY = 24 * 60 * 60

PROMPTS = {
    "new": (
        "Get the Malleus deck",
        "<p>Malleus is shared through <b>AnkiCollab</b>, a free add-on. "
        "We can set it all up for you in a few clicks.</p>"
        "<p>You'll need a free AnkiCollab account. We'll help you make one.</p>",
        "Set it up",
    ),
    "migrate": (
        "Malleus has moved to AnkiCollab",
        "<p>Malleus updates now come through <b>AnkiCollab</b> instead of AnkiHub. "
        "We can move your deck across for you. <b>Your cards, progress and review "
        "history are kept.</b></p>"
        "<p>You'll need a free AnkiCollab account. We'll help you make one.</p>",
        "Move my deck",
    ),
    "dual": (
        "Malleus is coming from two places",
        "<p>You're getting Malleus from <b>both AnkiHub and AnkiCollab</b>. When both "
        "add-ons update the same cards they clash, which can cause duplicate or lost "
        "changes.</p><p>We can stop AnkiHub updating Malleus for you. Your cards and "
        "review history stay exactly as they are.</p>",
        "Fix this now",
    ),
}


def _ask(title, text, go_label, later_label="Remind me later", never=True):
    """Small branded prompt.  Returns 'go' | 'later' | 'never'."""
    dialog = QDialog(mw)
    dialog.setWindowTitle("Malleus")
    dialog.setMinimumWidth(460)
    apply_malleus_style(dialog)
    choice = {"value": "later"}

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(0, 0, 0, 14)
    layout.addWidget(make_header("Malleus Clinical Medicine",
                                 logo_path=_LOGO if os.path.exists(_LOGO) else None))

    inner = QVBoxLayout()
    inner.setContentsMargins(20, 10, 20, 0)
    inner.setSpacing(10)
    heading = QLabel(title)
    heading.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {COLORS['accent']};")
    inner.addWidget(heading)
    body = QLabel(text)
    body.setWordWrap(True)
    body.setTextFormat(Qt.TextFormat.RichText)
    inner.addWidget(body)

    row = QHBoxLayout()
    if never:
        never_link = QLabel("<a href='#' style='color:gray'>Don't ask again</a>")
        never_link.linkActivated.connect(lambda _: (choice.update(value="never"), dialog.reject()))
        row.addWidget(never_link)
    row.addStretch()
    later = QPushButton(later_label)
    later.setObjectName("secondary")
    later.clicked.connect(dialog.reject)
    row.addWidget(later)
    go = QPushButton(go_label)
    go.setDefault(True)
    go.clicked.connect(lambda: (choice.update(value="go"), dialog.accept()))
    row.addWidget(go)
    inner.addSpacing(6)
    inner.addLayout(row)
    layout.addLayout(inner)

    dialog.exec()
    return choice["value"]


def startup_check():
    """Run once a profile is open and the published settings are fresh."""
    if not mw.col or not ds.is_available():
        return
    state = ds.get_state()
    if state.get("resume"):
        ds.update_state(resume=False)
        open_wizard(resumed=True)
        return

    info = ds.detect()
    scenario = info["scenario"]
    now = time.time()

    if scenario == "done":
        # AnkiCollab requires an account, so this asks at every startup until
        # they sign in: no "Don't ask again", no snooze.
        if not info["collab_logged_in"]:
            answer = _ask(
                "Sign in to AnkiCollab",
                "<p>You're getting Malleus through AnkiCollab, which needs a free "
                "account to download and update decks. Please sign in so Malleus "
                "keeps updating.</p>"
                "<p>It takes a minute, with no password to type.</p>",
                "Sign in", later_label="Not now", never=False)
            if answer == "go":
                open_wizard()
        return

    if scenario == "new" and not info["settings"].get("prompt_new_users", True):
        return
    # Being on both is the one case that can damage the deck, so it can be
    # snoozed but not silenced.
    if scenario != "dual" and state.get("never_ask"):
        return
    if now < state.get("snooze_until", 0):
        return

    title, text, go_label = PROMPTS[scenario]
    answer = _ask(title, text, go_label, never=(scenario != "dual"))
    if answer == "go":
        open_wizard()
    elif answer == "never":
        ds.update_state(never_ask=True)
    else:
        ds.update_state(snooze_until=now + (1 if scenario == "dual" else 3) * DAY)


def on_profile_open():
    # Straight away, not after the delay below: AnkiCollab's own startup
    # update deletes subscriptions whose key no longer exists on the server,
    # which after a rename includes the old key.  Moving the subscription to
    # the new key first (AnkiCollab re-reads its settings before deleting)
    # keeps it.
    try:
        ds.collab_carry_over_renamed_key(ds.load_settings())
    except Exception as e:
        print(f"Malleus: couldn't move the AnkiCollab subscription to the new key: {e}")

    def check():
        # Whether or not the wizard is switched on: AnkiCollab's startup
        # update may have just reset the synced fields' options.
        try:
            if mw.col:
                ds.fix_synced_field_options()
        except Exception as e:
            print(f"Malleus: couldn't set the synced fields' options: {e}")
        # Once, for anyone who set up AnkiCollab before the wizard copied
        # the old deck's options across.
        try:
            if mw.col and not ds.get_state().get("deck_options_copied"):
                did = ds.collab_installed_deck_id(ds.load_settings().get("deck_key", ""))
                if did:
                    for old in ds.old_copy_decks():
                        ds.copy_deck_options(old, did)
                    ds.update_state(deck_options_copied=True)
        except Exception as e:
            print(f"Malleus: couldn't copy the old deck's options: {e}")
        try:
            startup_check()
        except Exception as e:
            print(f"Malleus: deck setup check failed: {e}")
    # Let Anki (and AnkiCollab's own startup pull) settle first.
    ds.refresh_settings(lambda: QTimer.singleShot(2500, check))
