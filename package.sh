#!/bin/bash
# Builds malleus-anki-addon.zip for AnkiWeb upload.
set -e

ADDON_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$ADDON_DIR/malleus-anki-addon.zip"

rm -f "$OUTPUT"
cd "$ADDON_DIR"

zip -r "$OUTPUT" . \
  -x "test*.py" \
  -x "*/__pycache__/*" \
  -x "__pycache__/*" \
  -x "recent_tags.json" \
  -x ".git/*" \
  -x ".github/*" \
  -x ".claude/*" \
  -x ".env" \
  -x ".env~" \
  -x ".gitignore" \
  -x ".gitignore~" \
  -x ".DS_Store" \
  -x "*/.DS_Store" \
  -x "readme.md" \
  -x "readme for ankiweb.md" \
  -x "readme for ankiweb.org" \
  -x "meta.json" \
  -x "*.zip" \
  -x "*~"

echo "Created: $OUTPUT"
echo "Size: $(du -sh "$OUTPUT" | cut -f1)"
