"""
Bulk "apply to all" choices
Run-scoped decisions shared by the add-on's two bulk tagging flows.

Both bulk paths — Browser → Update Malleus Subject Tags and the Page Selector's
Replace Tags run — loop over the selected notes and stop at modal dialogs.  Each
of those dialogs carries an "apply to all" tickbox (make_apply_all_row() in
styles.py); what the user ticks is recorded in one of these objects and reused
for the rest of the run.

Nothing is written to config.json on purpose: a yield picked for one batch must
never leak into the next one, so each invocation starts with a fresh instance
and the decisions die with it.

This module imports nothing from the UI modules.  Both page_selector.py and
update_subject_tags.py use it, and update_subject_tags.py already imports from
page_selector.py, so anything shared between them has to live outside that pair.
"""
from typing import Dict, List, Optional, Tuple

# ── Shared wording for the escape hatches ────────────────────────────────────
# Every prompt that can interrupt a bulk run offers the same two ways out, in
# the same place and with the same words, so they do not drift apart again:
#
#   Stop       ends the whole run.  Cards already done keep their changes;
#              Update Subject Tags additionally offers Undo in its summary.
#   Skip card  declines this one card and moves on - what each dialog's
#              Cancel already did, just said plainly.
#
# Outside a bulk run there is nothing to skip or stop, so the dialogs show an
# ordinary Cancel instead and neither label is used.
STOP_LABEL = "Stop"
STOP_TOOLTIP = ("End the run here.\n"
                "Cards already updated keep their changes.")
SKIP_LABEL = "Skip card"
SKIP_TOOLTIP = "Leave this card alone and move on to the next one."


class BulkChoices:
    """The decisions every bulk flow can make: the yield level, and which
    Synced Extra entries to include.

    Both prompts appear in all three flows — Update Subject Tags, Replace Tags
    and Add Tags — so they live on the base rather than in any one subclass.
    Add Tags needs nothing else and uses this class directly.

    Fields:
        yield_tag      Yield applied to every later note that has none.
        se_decisions   se_id → included?  A later note only reopens the Synced
                       Extra dialog if it matches an entry absent from here.
    """

    def __init__(self):
        self.yield_tag: Optional[str] = None
        self.se_decisions: Dict[str, bool] = {}
        self.yield_applied = 0
        self.se_applied = 0

    # ── Yield ────────────────────────────────────────────────────────────────

    def take_yield(self) -> Optional[str]:
        """Return the saved yield and count the note against it, or None if
        the user hasn't asked for one to be reused."""
        if not self.yield_tag:
            return None
        self.yield_applied += 1
        return self.yield_tag

    # ── Synced Extra ─────────────────────────────────────────────────────────

    def se_covers(self, entries: List[Dict]) -> bool:
        """True when every matched entry already has a recorded decision, so
        the dialog can be skipped.  An entry with no se_id is never covered."""
        if not self.se_decisions:
            return False
        return all(
            e.get('se_id') and e['se_id'] in self.se_decisions
            for e in entries
        )

    def se_selection(self, entries: List[Dict]) -> List[Dict]:
        return [e for e in entries if self.se_decisions.get(e.get('se_id'))]

    # ── Bookkeeping ──────────────────────────────────────────────────────────

    def has_saved(self) -> bool:
        """True once anything is being reused without a prompt.  Drives the
        "using saved choices" hint, so a run that has gone quiet doesn't look
        like it simply stopped asking of its own accord."""
        return bool(self.yield_tag or self.se_decisions)

    def summary_lines(self) -> List[str]:
        """Lines for the completion summary, spelling out what was resolved
        without a dialog so a mis-click is visible there rather than only
        after reading the tags back."""
        lines = []
        if self.yield_applied:
            lines.append(f"Yield applied from saved choice: {self.yield_applied}")
        if self.se_applied:
            lines.append(f"Synced Extra applied from saved choices: {self.se_applied}")
        return lines


class SubjectTagChoices(BulkChoices):
    """Decisions for Browser → Update Malleus Subject Tags.

    Fields beyond the base:
        page_cache     page_name → (action, selections) for missing subject
                       tags, so the Tag Not Found dialog is asked once per
                       broken page rather than once per card.
    """

    def __init__(self):
        super().__init__()
        self.page_cache: Dict[str, tuple] = {}
        self.page_applied = 0

    def has_saved(self) -> bool:
        return bool(super().has_saved() or self.page_cache)

    def summary_lines(self) -> List[str]:
        lines = super().summary_lines()
        if self.page_applied:
            lines.append(f"Tag corrections reused: {self.page_applied}")
        return lines


class ReplaceTagChoices(BulkChoices):
    """Decisions for the Page Selector's Replace Tags run.

    Fields beyond the base:
        group_decisions  (page_name, subtag) → replace?  Keyed on the pair
                         simplify_tags_by_page() groups on, which is derived
                         from the tag itself and so is stable across notes —
                         unlike display_name, which is only a rendering of it.
                         A later note reopens the Select Tags dialog only if it
                         offers a group absent from here.
    """

    def __init__(self):
        super().__init__()
        self.group_decisions: Dict[Tuple[str, str], bool] = {}
        self.groups_applied = 0

    def has_saved(self) -> bool:
        return bool(super().has_saved() or self.group_decisions)

    @staticmethod
    def group_key(tag_info: Dict) -> Tuple[str, str]:
        """The stable identity of one row in the Select Tags dialog."""
        return (tag_info.get('page_name') or '', tag_info.get('subtag') or '')

    def groups_cover(self, simplified_tags: List[Dict]) -> bool:
        """True when every group offered has a recorded decision."""
        if not self.group_decisions:
            return False
        return all(
            self.group_key(t) in self.group_decisions
            for t in simplified_tags
        )

    def group_selection(self, simplified_tags: List[Dict]) -> List[Dict]:
        return [
            t for t in simplified_tags
            if self.group_decisions.get(self.group_key(t))
        ]

    def summary_lines(self) -> List[str]:
        lines = super().summary_lines()
        if self.groups_applied:
            lines.append(f"Tag selection applied from saved choices: {self.groups_applied}")
        return lines
