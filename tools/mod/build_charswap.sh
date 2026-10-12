#!/usr/bin/env bash
# Build the character-swap mod: Brock's VS/select portrait becomes "Moss".
# Needs a matching build first (gmake), so build/usa/puzzleleague64.usa.z64 exists.
set -euo pipefail
cd "$(dirname "$0")/../.."

base=build/usa/puzzleleague64.usa.z64
out=build/mod/puzzleleague64.charswap.z64
art=tools/mod/art
swap="CBROCK.BIF=$art/moss_00.png,$art/moss_01.png"

[ -f "$base" ] || { echo "missing $base: run gmake first" >&2; exit 1; }
uv run tools/mod/art/make_moss.py
uv run tools/mod/pplmod.py replace "$base" "$out" "$swap"
uv run tools/mod/pplmod.py verify "$base" "$out" "$swap"
