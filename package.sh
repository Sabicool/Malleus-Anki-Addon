#!/bin/bash
# Builds malleus-anki-addon.zip for AnkiWeb upload.
#
# Uses an ALLOWLIST: only the files the add-on actually needs at runtime are
# copied into a staging dir and zipped.  This means new dev/test/CI files are
# excluded by default (you have to opt a file in here), which is safer than the
# old "zip everything then -x exclude" approach.
#
# The read-only Notion token is injected into the packaged config.py at build
# time from the environment or .env — it is never committed to the repo.
#
# Options (env vars):
#   NOTION_TOKEN=...   the read token to embed (else read from .env)
#   INCLUDE_CACHE=1    also ship the cache/ seeds (off by default; the add-on
#                      downloads fresh caches from GitHub on first run anyway)
set -euo pipefail

ADDON_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$ADDON_DIR/malleus-anki-addon.zip"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

# ── Notion read token (embedded in the shipped add-on) ───────────────────────
TOKEN="${NOTION_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$ADDON_DIR/.env" ]; then
  TOKEN="$(grep -E '^NOTION_TOKEN=' "$ADDON_DIR/.env" | head -1 | cut -d= -f2- | tr -d "\"' \r")"
fi
if [ -z "$TOKEN" ]; then
  echo "ERROR: NOTION_TOKEN not set (export it or add it to .env)." >&2
  echo "       Refusing to build a token-less add-on." >&2
  exit 1
fi

# ── Files the add-on needs at runtime ────────────────────────────────────────
PY_MODULES=(
  __init__.py
  config.py
  notion_cache.py
  cache_generation.py
  cache_updater.py
  extra_sync.py
  guidelines_tags.py
  hierarchy_tags.py
  pharmacology_tags.py
  subjects_tags.py
  suggest_tags.py
  tag_utils.py
  utils.py
)
DATA_FILES=(
  config.json          # Anki add-on default config
  config.md            # config description shown in Anki
  logo.png             # used by the UI
  LICENSE
)
DIRS=(
  ui                   # the UI package
  images               # eTG.jpg etc.
)

# ── Copy into staging ────────────────────────────────────────────────────────
for f in "${PY_MODULES[@]}" "${DATA_FILES[@]}"; do
  cp "$ADDON_DIR/$f" "$STAGING/$f"
done
for d in "${DIRS[@]}"; do
  cp -R "$ADDON_DIR/$d" "$STAGING/$d"
done
if [ "${INCLUDE_CACHE:-0}" = "1" ]; then
  cp -R "$ADDON_DIR/cache" "$STAGING/cache"
  echo "Including cache/ seeds."
fi

# Strip junk that may have come along with the directory copies.
find "$STAGING" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGING" \( -name '*.pyc' -o -name '.DS_Store' -o -name '*~' \) -delete

# ── Inject the Notion token into the packaged config.py only ─────────────────
python3 - "$STAGING/config.py" "$TOKEN" <<'PY'
import sys, re
path, token = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
out, inserted = [], False
for line in lines:
    if re.match(r"\s*NOTION_TOKEN\s*=", line):
        continue  # drop any existing (placeholder or real) token line
    out.append(line)
    if not inserted and line.startswith("from aqt import mw"):
        out.append(f"NOTION_TOKEN = '{token}'\n")
        inserted = True
if not inserted:                       # fallback: prepend if anchor not found
    out.insert(0, f"NOTION_TOKEN = '{token}'\n")
open(path, "w", encoding="utf-8").write("".join(out))
print("Injected NOTION_TOKEN into packaged config.py")
PY

# ── Zip (files at the archive root, as AnkiWeb expects) ───────────────────────
rm -f "$OUTPUT"
( cd "$STAGING" && zip -rq "$OUTPUT" . )

echo "Created: $OUTPUT"
echo "Size: $(du -sh "$OUTPUT" | cut -f1)"
echo "Contents:"
unzip -l "$OUTPUT"
