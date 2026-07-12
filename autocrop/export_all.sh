#!/usr/bin/env bash
# Parallel full-resolution JPEG export of every CR3 (applying its darktable .xmp),
# mirroring darktable's own export but across many darktable-cli processes.
#
# Usage: export_all.sh <photos_dir> [workers] [quality]
set -u
SRC="${1:?photos dir required}"
WORKERS="${2:-8}"
QUALITY="${3:-95}"
# darktable-cli: $DARKTABLE_CLI override -> PATH (e.g. nix) -> macOS app bundle
DT="${DARKTABLE_CLI:-$(command -v darktable-cli || echo /Applications/darktable.app/Contents/MacOS/darktable-cli)}"
if [ ! -x "$DT" ] && ! command -v "$DT" >/dev/null 2>&1; then
  echo "error: darktable-cli not found. Install darktable or set DARKTABLE_CLI." >&2
  exit 1
fi
OUTDIR="$SRC/export"
mkdir -p "$OUTDIR"
# start clean: darktable-cli appends _01/_02 instead of overwriting, so any
# leftover exports would become duplicates on a re-run.
rm -f "$OUTDIR"/*.jpg

PROGRESS="$OUTDIR/.progress"
: > "$PROGRESS"
TOTAL=$(ls "$SRC"/*.CR3 2>/dev/null | wc -l | tr -d ' ')
echo "exporting $TOTAL images with $WORKERS workers, jpeg q$QUALITY -> $OUTDIR"

export DT OUTDIR QUALITY PROGRESS
one() {
  cr3="$1"
  base=$(basename "$cr3" .CR3)
  xmp="$cr3.xmp"
  [ -f "$xmp" ] || { echo "NOXMP $base" >> "$PROGRESS"; return; }
  cfg=$(mktemp -d)
  "$DT" "$cr3" "$xmp" "$OUTDIR/$base.jpg" \
    --width 0 --height 0 --hq true --upscale false \
    --core --library ":memory:" --configdir "$cfg" --cachedir "$cfg" \
    --conf plugins/imageio/format/jpeg/quality="$QUALITY" >/dev/null 2>&1
  rc=$?
  rm -rf "$cfg"
  if [ $rc -eq 0 ] && [ -f "$OUTDIR/$base.jpg" ]; then echo "OK $base" >> "$PROGRESS"
  else echo "FAIL $base" >> "$PROGRESS"; fi
}
export -f one

ls "$SRC"/*.CR3 | xargs -P "$WORKERS" -I{} bash -c 'one "$@"' _ {}

ok=$(grep -c '^OK' "$PROGRESS"); fail=$(grep -c '^FAIL' "$PROGRESS"); nox=$(grep -c '^NOXMP' "$PROGRESS")
echo "DONE: $ok exported, $fail failed, $nox no-xmp, $TOTAL total"
