#!/usr/bin/env bash
# scripts/seed-shaders.sh
#
# Seed the local GShade shader/texture set from a backup. The upstream
# Mortalitas/GShade-Shaders repo has shrunk and no longer ships ~80% of
# the shaders and ~95% of the textures the app used to rely on. This
# script copies the full set from a local backup into the user's
# ~/GShade-Shaders/ and into the Sober Flatpak's config directories, so
# vkBasalt can see them without the GUI needing to Fetch from a repo that
# doesn't have them anymore.
#
# Usage:
#   scripts/seed-shaders.sh [--backup PATH] [--dry-run] [--force]
#
# Defaults:
#   --backup ~/GShade-Shaders-backup
#
# Idempotent: only copies files that are missing or older in the
# destination. Pass --force to overwrite unconditionally.

set -euo pipefail

BACKUP="${HOME}/GShade-Shaders-backup"
DRY_RUN=0
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup)  BACKUP="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --force)   FORCE=1; shift ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

say()  { printf '%s\n' "$*"; }
err()  { printf 'error: %s\n' "$*" >&2; }

[[ -d "$BACKUP" ]] || { err "backup dir not found: $BACKUP"; exit 1; }
[[ -d "$BACKUP/Shaders"  ]] || { err "missing $BACKUP/Shaders";  exit 1; }
[[ -d "$BACKUP/Textures" ]] || { err "missing $BACKUP/Textures"; exit 1; }

GSHADE="${HOME}/GShade-Shaders"
RESHADE="${HOME}/reshade-shaders"
SANDBOX_CFG="${HOME}/.var/app/org.vinegarhq.Sober/config/vkBasalt"
SANDBOX_SHADERS="${SANDBOX_CFG}/Shaders"
SANDBOX_TEXTURES="${SANDBOX_CFG}/Textures"

mkdir -p "$GSHADE/Shaders" "$GSHADE/Textures"
mkdir -p "$SANDBOX_SHADERS" "$SANDBOX_TEXTURES"

rsync_args=(-a --info=stats1,progress2)
[[ $DRY_RUN -eq 1 ]] && rsync_args+=(--dry-run)
[[ $FORCE  -eq 1 ]] && rsync_args+=(--ignore-times) || rsync_args+=(--update)

say "Seeding GShade shaders from $BACKUP"
rsync "${rsync_args[@]}" "$BACKUP/Shaders/"  "$GSHADE/Shaders/"
rsync "${rsync_args[@]}" "$BACKUP/Textures/" "$GSHADE/Textures/"

# Framework headers: prefer reshade-shaders (runtime-cloned), fall back
# to the backup (which may contain ReShade.fxh / ReShadeUI.fxh / Blending.fxh).
headers=(ReShade.fxh ReShadeUI.fxh Blending.fxh Macros.fxh TriDither.fxh DrawText.fxh)
for h in "${headers[@]}"; do
    src=""
    [[ -f "$RESHADE/Shaders/$h"  ]] && src="$RESHADE/Shaders/$h"
    [[ -z "$src" && -f "$BACKUP/Shaders/$h" ]] && src="$BACKUP/Shaders/$h"
    [[ -z "$src" ]] && continue
    for dst in "$GSHADE/Shaders/$h" "$SANDBOX_SHADERS/$h"; do
        if [[ ! -f "$dst" ]] || [[ "$src" -nt "$dst" ]] || [[ $FORCE -eq 1 ]]; then
            [[ $DRY_RUN -eq 1 ]] && say "would copy $src -> $dst" \
                                 || cp -p "$src" "$dst"
        fi
    done
done

say "Seeding sandbox Shaders and Textures"
rsync "${rsync_args[@]}" "$GSHADE/Shaders/"  "$SANDBOX_SHADERS/"
rsync "${rsync_args[@]}" "$GSHADE/Textures/" "$SANDBOX_TEXTURES/"

n_fx=$(find "$SANDBOX_SHADERS" -maxdepth 1 -name '*.fx'  | wc -l)
n_fxh=$(find "$SANDBOX_SHADERS" -maxdepth 1 -name '*.fxh' | wc -l)
n_tex=$(find "$SANDBOX_TEXTURES" -maxdepth 1 -type f     | wc -l)
say ""
say "Done."
say "  sandbox Shaders:  ${n_fx} .fx, ${n_fxh} .fxh"
say "  sandbox Textures: ${n_tex} files"

# Snapshot the full seeded set as a tarball for disaster recovery.
TAR_DIR="${HOME}"
TAR_NAME="GShade-Shaders-full-$(date +%Y%m%d).tar.gz"
TAR_PATH="${TAR_DIR}/${TAR_NAME}"
say "Writing backup tarball to ${TAR_PATH}"
tar -czf "$TAR_PATH" \
    -C "$HOME" \
    GShade-Shaders/Shaders \
    GShade-Shaders/Textures \
    reshade-shaders/Shaders 2>/dev/null || true
if [[ -f "$TAR_PATH" ]]; then
    say "  $(du -h "$TAR_PATH" | cut -f1)  ${TAR_PATH}"
else
    say "  (tar skipped — one of the source dirs is missing)"
fi
