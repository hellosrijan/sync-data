#!/usr/bin/env bash
# Build the GitHub wiki into OUT_DIR. The repo is the source of truth:
#   - wiki/*.md            hand-written wiki pages, copied as-is
#   - README.md            -> Getting-Started.md
#   - docs/design.md       -> Design.md
# Repo-relative links in generated pages are rewritten to BLOB_URL so they work from the wiki.
#
# usage: scripts/build-wiki.sh OUT_DIR [BLOB_URL]
#   BLOB_URL e.g. https://github.com/<owner>/<repo>/blob/main (CI passes this)
set -euo pipefail

out=${1:?usage: build-wiki.sh OUT_DIR [BLOB_URL]}
blob=${2:-../blob/main}
root=$(cd "$(dirname "$0")/.." && pwd)

mkdir -p "$out"
cp "$root"/wiki/*.md "$out"/

# gen SRC DEST: copy SRC to DEST with a "generated" banner and repo-relative links made absolute.
gen() {
  local src=$1 dest=$2 dir
  dir=$(dirname "$src")
  [ "$dir" = "." ] && dir="" || dir="$dir/"
  {
    echo "> Generated from [\`$src\`]($blob/$src). Edit that file, not this page."
    echo
    # Links that aren't absolute (no ':'), anchors (#...) or mailto: get the blob prefix.
    sed -E "s#\]\(([^)#:][^):]*)\)#](${blob}/${dir}\1)#g" "$root/$src"
  } > "$out/$dest"
}

gen README.md Getting-Started.md
gen docs/design.md Design.md

echo "Built $(ls "$out" | wc -l | tr -d ' ') pages into $out"
