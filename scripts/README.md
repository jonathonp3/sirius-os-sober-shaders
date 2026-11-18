# scripts/

Helper tools for setting up and recovering the shader and texture set.

These are not part of the Flatpak. They run on the host, because they need
access to `~/GShade-Shaders-backup/`, `~/reshade-shaders/`, and the Sober
sandbox — none of which the app's sandbox can see directly.

## The problem these solve

The upstream `Mortalitas/GShade-Shaders` repo has shrunk from ~200 shaders
and ~90 textures to ~65 and 5. The app's `Fetch` command clones the
current upstream, so on a fresh machine it gets the reduced set. The full
set — Bloom, Clarity, Curves, Vibrance, the qUINT shaders, most textures —
is no longer fetchable from GitHub.

These scripts restore the full set from a local backup, and import shaders
from other sources.

## `seed-shaders.sh`

Copy the full shader/texture set from a local backup into
`~/GShade-Shaders/` and the Sober sandbox, then write a tar snapshot.

```bash
./seed-shaders.sh                          # default backup: ~/GShade-Shaders-backup
./seed-shaders.sh --backup /path/to/backup
./seed-shaders.sh --dry-run                # preview, don't copy
./seed-shaders.sh --force                  # overwrite unconditionally
```

---

## What it does:

1. Copies `<backup>/Shaders/` to `~/GShade-Shaders/Shaders/` (rsync
  `--update`, so only missing or newer files are copied)

2. Copies `<backup>/Textures/` to `~/GShade-Shaders/Textures/`  

3. Copies the ReShade framework headers (`ReShade.fxh`, `ReShadeUI.fxh`,
   `Blending.fxh`, `Macros.fxh`, `TriDither.fxh`, `DrawText.fxh`) from
   `~/reshade-shaders/Shaders/` (or the backup, if the clone is missing)
   into both `~/GShade-Shaders/Shaders/` and the sandbox `Shaders/`
  
4. Copies `~/GShade-Shaders/Shaders/` and `Textures/` into the sandbox
   `~/.var/app/org.vinegarhq.Sober/config/vkBasalt/{Shaders,Textures}/`)

5. Writes `~/GShade-Shaders-full-YYYYMMDD.tar.gz` — a snapshot of the
   full set (~43 MB), for off-machine backup

Idempotent. Running it twice copies nothing the second time (unless
`--force`).

**import-shaders.py**

Import shaders (and their headers/textures) from a local directory or a git
URL into the Sober sandbox. Every shader is validated before anything is
copied, so the sandbox only receives shaders whose dependencies resolve.

```bash
./import-shaders.py https://github.com/martymcmodding/qUINT --dry-run
./import-shaders.py https://github.com/martymcmodding/qUINT
./import-shaders.py ~/Reshade-Shaders --verify-each
./import-shaders.py ~/Reshade-Shaders --json
```

The source may be:

- a local directory (a clone of qUINT, reshade-shaders, an extracted pack), or
- an `https://` / `git@` URL, which is shallow-cloned into a temp directory
  and removed afterwards.

Layout is discovered, not prescribed: shaders may sit at the top level or
under `Shaders/`, headers at the top level or under `Includes/`, textures
under `Textures/` or `Textures/<author>/`.

What it does:

1. Finds `.fx` files, `.fxh` headers, and image files under the source.
2. Builds the flattened namespace vkBasalt will see after import, seeded
   with what is already in the sandbox (so `ReShade.fxh` from a prior Fetch
   satisfies an include).
3. Validates each shader: every include is followed transitively, and every
   texture (including textures declared inside headers) must exist at the
   flat path vkBasalt searches.
4. Copies only validated shaders, rewriting Windows `.\Includes\...`
   paths to POSIX (`Includes/...`) because vkBasalt does not convert
   backslashes. Each header/texture lands at both the flat path and its
   preserved subpath, so `#include "foo.fxh"` and
   `#include "Includes/foo.fxh"` both resolve.
5. Prints a report: imported shaders, rejected shaders with the missing
   dependency, and name collisions.

Options:

- `--dry-run` — validate and report without touching the sandbox.
- `--force` — overwrite even when the sandbox copy looks up to date.
- `--json` — machine-readable report for the GUI.
- `--verify` — launch Sober once with all candidates and read vkBasalt's
  log; shaders that fail to compile are removed and reported.
- `--verify-each` — launch Sober once per shader. Slower, but attributes
  errors that vkBasalt reports at a header path.

`--verify-each` is the reliable compile gate. Without a verify flag, a
shader that has all its dependencies but uses an unsupported ReShade
intrinsic is still imported (dependency checking cannot see that); run a
verify pass after importing a new pack.

Run Fetch at least once first: many shaders need `ReShade.fxh`, and a
shader that still needs it is rejected with `missing include: ReShade.fxh`
rather than silently imported.

**restore-shaders.sh**

Extract a tarball written by `seed-shaders.sh` into `~/` and sync the
sandbox.
```bash

./restore-shaders.sh ~/GShade-Shaders-full-20261114.tar.gz
```

For when `~/GShade-Shaders-backup/` is gone but the tarball survived. Same
end state as seed-shaders.sh: full set in `~/GShade-Shaders/` and the
sandbox.

## Distribution to other machines

The scripts assume a `~/GShade-Shaders-backup/` directory exists. For a
multi-user setup, put the backup somewhere all users can read:

```bash
sudo mkdir -p /var/lib/sober-shader-backup
sudo cp -r ~/GShade-Shaders-backup/. /var/lib/sober-shader-backup/
sudo cp ~/GShade-Shaders-full-*.tar.gz /var/lib/sober-shader-backup/
sudo chmod -R a+rX /var/lib/sober-shader-backup

Then each user runs:

```bash
./seed-shaders.sh --backup /var/lib/sober-shader-backup
```

Or, if the shared backup dir is gone but the tarball survived:

```bash
./restore-shaders.sh /var/lib/sober-shader-backup/GShade-Shaders-full-YYYYMMDD.tar.gz
```

To make the scripts available system-wide:

```bash
sudo cp seed-shaders.sh import-shaders.py restore-shaders.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/seed-shaders /usr/local/bin/import-shaders /usr/local/bin/restore-shaders
```

## Why the backup matters

`Fetch` clones the current upstream repo. The upstream is now ~65 shaders
and 5 textures. The backup has ~207 shaders and ~83 textures. If the
backup is lost:

- Fetch can only rebuild the reduced set.

- The shortlist shaders (Bloom, Clarity, Curves, Vibrance) won't be
  available.

- The seeded texture set (83 vs. the clone's 5) won't be available, so
  every shader that references a texture grays out.

So: keep the tarball somewhere off-machine (USB drive, another host, or
a private Git release). The on-disk copies — `~/GShade-Shaders-backup/`,
the tarball, the sandbox — are all one disk failure away from being lost.


