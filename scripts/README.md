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

Copy shaders and textures from an arbitrary source directory into the
sandbox, then validate each imported shader's dependencies.

```bash

./import-shaders.py ~/Downloads/my-shader-pack
./import-shaders.py ~/Downloads/my-shader-pack --dry-run
./import-shaders.py ~/Downloads/my-shader-pack --force
```
The source directory should look like:
```text

my-shader-pack/
├── FancyShader.fx
├── FancyShader.fxh
├── ReShade.fxh           (optional; framework headers)
└── Textures/
    ├── dirt.png
    └── noise.png
```

---

## What it does:

1 Copies `*.fx`and `*.fxh` from the source directory (top level only) to
  the sandbox `Shaders/`

2. Copies `Textures/*` to the sandbox `Textures/`

3. Parses each imported `.fx` and checks its includes and textures against
   the sandbox contents

4. Prints a report: which shaders are fully resolved, which will gray out
   in the GUI (with the specific missing filenames)

`--dry-run` previews without touching the sandbox. `--force` overwrites
existing files; default is copy-if-newer, so re-importing the same pack is
a no-op.

Run `Fetch` at least once first. Framework headers like ReShade.fxh
are needed for most shaders to compile, and Fetch is what puts them in the
sandbox. Without them, every imported shader reports a missing
ReShade.fxh include.

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


