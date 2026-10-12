#!/usr/bin/env bash
# Build the character-swap mod: Brock becomes "Moss" everywhere he has an image or
# a name in the bin_file archive (see tools/mod/README.md for the list).
# Needs a matching build first (gmake), so build/usa/puzzleleague64.usa.z64 exists.
set -euo pipefail
cd "$(dirname "$0")/../.."

base=build/usa/puzzleleague64.usa.z64
out=build/mod/puzzleleague64.charswap.z64
work=build/mod/work
art=tools/mod/art

[ -f "$base" ] || { echo "missing $base: run gmake first" >&2; exit 1; }
uv run "$art/make_moss.py"
uv run "$art/paint_group.py" "$base" "$work"   # composites: built from the base ROM, never committed

edits=(
    "CBROCK.BIF=$art/moss_00.png,$art/moss_01.png"   # VS / Pokemon pick portrait
    "RECHEADS.BIF=,$art/moss_head.png"                # frame 1 of 16: records screen head
    "PBACKBROCK.BIF=$art/moss_back.png"               # puzzle editor background
    "TRSELECT.BIF=$work/trselect.png"                 # 2P trainer select group picture
    "BROCK.BIF=$work/silhouette.png"                  # its highlight silhouette
    --rename "MENU.SBF:BROCK=MOSS" --rename "MENU.SBF:Brock=Moss"
    --rename "TITLE.SBF:BROCK=MOSS" --rename "RESULT.SBF:BROCK=MOSS"
)
uv run tools/mod/pplmod.py replace "$base" "$out" "${edits[@]}"
uv run tools/mod/pplmod.py verify "$base" "$out" "${edits[@]}"
