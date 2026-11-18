# Sober Shader Manager

A GTK4 GUI and CLI for managing vkBasalt shader chains for
[Sober](https://sober.vinegarhq.org/) (the Roblox Flatpak). Pick shaders,
adjust parameters, apply with automatic rollback if the config fails to load,
and launch Sober with the chain active.

## What it does

- **Check** — verifies Sober's runtime and the vkBasalt extension are
  compatible, with a clear fix command if they're not.
- **Status** — shows the active shader chain, compatibility, and whether a
  last-known-good backup exists.
- **Apply** — validates the config, backs up the previous one, writes the new
  one, launches Sober in verify mode, and restores the backup on failure.
  Runs detached by default, so the GUI returns once vkBasalt reports the
  config loaded (usually a few seconds) and Sober keeps running.
- **Rollback** — restores the last-known-good config.
- **Fetch** — clones `Mortalitas/GShade-Shaders` and
  `crosire/reshade-shaders` into the user's home, then copies the shaders,
  textures, and framework headers into Sober's sandbox where vkBasalt can
  read them.
- **List shaders** — parses `.fx` files and shows their uniforms.
- **Validate config** — checks a `vkBasalt.conf` for structural and
  parameter-name errors.

## Install

Requires [Sober](https://github.com/VinegarHQ/Sober) to be installed.
If you don't have it yet, install it first:

```bash
flatpak install flathub org.vinegarhq.Sober
```
Then install the shader manager from the project's Flatpak remote:

```bash
# 1. Add Flathub (skip if already added)
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

# 2. Install the vkBasalt extension. The branch (26.08 here) must match
#    the VulkanLayer version declared by Sober's runtime. To find yours:
#      flatpak info --show-metadata org.gnome.Platform//51 | grep -A2 VulkanLayer
#    The app's `check` command reports the required branch too.
flatpak install -y flathub org.freedesktop.Platform.VulkanLayer.vkBasalt//26.08

# 3. Fetch the remote's signing key
wget2 -q -O /tmp/sirius-os-sober-shaders.gpg \
    https://raw.githubusercontent.com/jonathonp3/sirius-os-sober-shaders/main/sirius-os-sober-shaders.gpg

# 4. Add the project's Flatpak remote
flatpak remote-add --system --if-not-exists \
    --gpg-import=/tmp/sirius-os-sober-shaders.gpg \
    sirius-os-sober-shaders https://jonathonp3.github.io/sirius-os-sober-shaders/

# 5. Install the app
flatpak install --system sirius-os-sober-shaders org.siriusos.SoberShaderManager
```

Then run:

```bash
flatpak run org.siriusos.SoberShaderManager
```

Or use the CLI:

```bash
flatpak run --command=sober-shader org.siriusos.SoberShaderManager check
flatpak run --command=sober-shader org.siriusos.SoberShaderManager status
flatpak run --command=sober-shader org.siriusos.SoberShaderManager apply my.conf --detach
```

---

## Updating

```bash
flatpak update --system org.siriusos.SoberShaderManager
```

The project's CI publishes to the `stable` branch on each push to `main`,
so `flatpak update` picks up new builds.

---

## Building from source

```bash
git clone https://github.com/jonathonp3/sirius-os-sober-shaders
cd sirius-os-sober-shaders
flatpak run org.flatpak.Builder --user --force-clean --install \
    build-dir org.siriusos.SoberShaderManager.json
```

---

## The GShade upstream has shrunk

This matters for what the app can do on a fresh install.

The current `Mortalitas/GShade-Shaders` repository ships ~65 shaders and 5
textures. When this app was first built, it shipped ~200 shaders and ~90
textures — including Bloom, Clarity, Curves, Vibrance, the qUINT set, and
most of the texture assets. Those are no longer in the upstream repo.

---

## Consequences:

- Fetch clones the current reduced set. On a clean machine, about
  63/65 shaders are selectable; the rest gray out on missing textures.

- The full set is preserved locally. If you have a
 `~/GShade-Shaders-backup/` directory, `scripts/seed-shaders.sh` copies it
  into `~/GShade-Shaders/` and the Sober sandbox. See Backups.

- Fetch is not the recovery path for the full set. It only rebuilds
  the reduced clone. The seed script and the tar snapshot are the recovery
  path.

If you're installing on a fresh machine and want the full set, you need a
copy of the backup. The repo does not include it (the shader licenses don't
permit redistribution).

---

## Shader compatibility

Two kinds of shaders may not work in vkBasalt even though they load:

- Missing dependencies. The reduced clone's `Textures/` doesn't have
  everything the shaders reference. `check_dependencies` grays out any
  shader whose includes or textures are missing, with a tooltip naming
  what's absent. On a fully seeded profile, 204/207 are selectable;
  CRTGeomMOD, DOF, and MartyMcFlyDOF gray out on genuinely missing
  textures (bezel artwork, `mcmask.png`).

- Silent black screens. Some shaders load without a vkBasalt error but
  render a black frame. `Layer.fx` and `Layer2–Layer5 `are the known
  offenders: they sample an external texture and blend it over the whole
  screen, and in this setup the blend produces black with no log line. The
  app has a `VKBASALT_BLOCKLIST` in `gui/window.py` that hides these from
  the GUI. Add to it if a new shader misbehaves.

---

## Presets

The `presets/` directory ships ready-to-use shader chains:

- `clear.conf` — sharp, neutral image:
  Deband → Bloom → CAS → Clarity → Curves → Vibrance.

Of these, **Deband ships with the app and CAS is built into vkBasalt** —
both work on a fresh install with no Fetch. The other four (Bloom,
Clarity, Curves, Vibrance) come from the full GShade/ReShade set that is
no longer in the upstream repo. They are present if you have a fully
seeded profile (see [Backups](#backups)) or have imported them from
another source (see [Importing shaders from other sources](#importing-shaders-from-other-sources)).

On a fresh install with only the reduced Fetch clone, `clear.conf`
resolves Deband and CAS; the other four are missing from the sandbox,
and the preset's path lines for them point at files that don't exist.

To get the full chain working:

1. Seed from a backup:
   `scripts/seed-shaders.sh --backup /path/to/GShade-Shaders-backup`
2. Or import the individual shaders:
   `python3 scripts/import-shaders.py /path/to/pack`
3. Then validate: `validate-config presets/clear.conf` should exit 0.

### Validating a preset

The preset hardcodes `/var/home/jonathon/` in its path lines. The GUI
rewrites these to your home directory on load, so the preset works for any
user. The CLI's `validate-config` checks the paths as written, so on a
machine where your username isn't `jonathon`, rewrite a copy first:

```bash
sed "s|/var/home/jonathon|$HOME|g" presets/clear.conf > /tmp/clear.conf
flatpak run --command=sober-shader org.siriusos.SoberShaderManager \
    validate-config /tmp/clear.conf
echo "exit: $?"
```

---

## Backups

The full shader set (~207 shaders, ~83 textures) is preserved in three
places:

1. `~/GShade-Shaders-backup/` — the original backup, the seed script's
   source.

2. `~/GShade-Shaders-full-YYYYMMDD.tar.gz` — a snapshot tarball written by
    scripts/seed-shaders.sh after each seed (~43 MB).

3. `~/.var/app/org.vinegarhq.Sober/config/vkBasalt/{Shaders,Textures}/` —
    the sandbox copies vkBasalt reads at runtime.

Restore from a tarball:

```bash
scripts/restore-shaders.sh /path/to/GShade-Shaders-full-YYYYMMDD.tar.gz
```

Seed from a backup directory:

```bash
scripts/seed-shaders.sh --backup /path/to/GShade-Shaders-backup
```

Both are idempotent. `--dry-run` previews; `--force` overwrites
unconditionally. See [scripts/README.md](scripts/README.md) for the
details, including the shared-backup distribution model for multi-user
machines.

The tar should be copied off-machine (USB drive, another host, or a private
Git release) — the on-disk copies don't survive disk failure.

---

## Importing shaders from other sources

`Fetch` clones the GShade and ReShade repos. If you have shaders from
another source — a GitHub repo, a ReShade forum post, a friend's pack — the
import script locates the assets itself. It handles both common layouts:

```text
qUINT/                                reshade-shaders/
├── Shaders/                          ├── Shaders/
│   ├── qUINT_ssr.fx                  │   ├── BaBa_SSR.fx
│   └── qUINT_common.fxh              │   └── Includes/bb_common.fxh
└── Textures/ (optional)              └── Textures/Barbatos/SS_BN2.png
```

In the app, **Import folder…** (next to **Import shader…**) runs this same
validation and shows the imported/rejected breakdown. From a terminal,
point the script at a directory or a git URL:

```bash
# Preview: validate and report, touch nothing
python3 scripts/import-shaders.py ~/qUINT --dry-run

# Import only shaders whose dependencies all resolve
python3 scripts/import-shaders.py ~/qUINT

# Same, from a repo URL
python3 scripts/import-shaders.py https://github.com/martymcmodding/qUINT

# Machine-readable report for the GUI
python3 scripts/import-shaders.py ~/qUINT --json
```

Validation is compiler-accurate: an `#include` resolves only against the
flat sandbox `Shaders/` directory (the way vkBasalt's include path works),
and includes are followed transitively. Textures declared inside included
headers count as dependencies too. Windows-style `.\Includes\foo.fxh`
paths are rewritten to POSIX in the copied `.fx`/`.fxh` files, because
vkBasalt does not convert backslashes when it opens an include.

The report lists every imported shader, every rejected shader with the
missing include/texture, and any name collisions. Re-importing the same
pack is a no-op unless the source files changed.

### Compile verification

Dependency validation cannot know whether vkBasalt's compiler accepts a
shader — newer ReShade intrinsics like `tex2Dfetch` or `tex2DgatherG` may
not be supported. Two optional gates launch Sober once and read its log:

```bash
# One batch launch: fast, attributes errors that name the .fx
python3 scripts/import-shaders.py ~/Reshade-Shaders --verify

# One launch per shader: slower, exact even when the error names a header
python3 scripts/import-shaders.py ~/Reshade-Shaders --verify-each
```

Shaders that fail the compile gate are removed from the sandbox (only the
top-level `.fx`; shared headers/textures stay) and reported as rejected.
`--verify-each` is the reliable mode: vkBasalt reports some errors at the
header path, which the single-pass mode cannot attribute.

Run `Fetch` at least once before importing: framework headers like
`ReShade.fxh` are needed by many shaders, and Fetch puts them in the
sandbox. A shader that still needs a header is now rejected with
`missing include: ReShade.fxh` instead of being silently imported.

See [scripts/README.md](scripts/README.md) for the full workflow.

---

## Configuration

The app writes ~/.var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf
using the standard vkBasalt format:

```ini

effects = dbd:blm:cas:clr:crv:vib

dbd = /path/to/Deband.fx
blm = /path/to/Bloom.fx
clr = /path/to/Clarity.fx
crv = /path/to/Curves.fx
vib = /path/to/Vibrance.fx
Range = 128.0000
...
reshadeTexturePath = /path/to/Textures
reshadeIncludePath = /path/to/Shaders
depthCapture = off
enableOnLaunch = True
```

Effect aliases `(dbd, blm, …) ` are short keys chosen to avoid collisions
with uniform names. The app's SHADER_ALIASES map covers the common ones;
anything not in the map falls back to its full lowercase alphanumeric name,
which is unique by construction. The matching logic and the config format
are documented in [docs/Applied-Shaders.md](docs/Applied-Shaders.md).

---

## Validating a config

`validate-config` checks a vkBasalt.conf for structural and
parameter-name errors without applying it:

```bash
flatpak run --command=sober-shader org.siriusos.SoberShaderManager \
    validate-config ~/.var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf
```

Exit 0 means the config is valid: every effect in `effects` has a
matching `alias = /path/to/Shader.fx` line, no key appears twice, and
the parameter names match uniforms in the referenced shaders. Exit 1
prints the specific errors.

Useful after hand-editing the config, or to diagnose why an `apply`
failed. apply runs the same validation internally before writing, so
`validate-config` is mainly a standalone check for the current config
or a candidate one.

---

## Tests

```bash
source .venv/bin/activate
python3 -m pytest tests/ -v
```

The suite is fixture-based: no Sober install, no display, no vkBasalt
required.

---

## License

This project (the CLI, GUI, and core modules in this repository) is
licensed under the MIT License. See [LICENSE](LICENSE).

The shader files and textures it fetches at runtime are **not** part of this
project and are not redistributed by it:

- **ReShade framework** (`ReShade.fxh`, `ReShadeUI.fxh`, `Blending.fxh`,
  `Macros.fxh`, `TriDither.fxh`, `DrawText.fxh`) is fetched from
  [crosire/reshade-shaders](https://github.com/crosire/reshade-shaders)
  under the BSD 3-Clause License.
- **GShade shaders** are fetched from
  [Mortalitas/GShade-Shaders](https://github.com/Mortalitas/GShade-Shaders).
  Each shader file carries its own license; the GShade repository documents
  these separately.
- **Sober** (the Flatpak this app configures) is a separate project with
  its own license.

If you build a distributable bundle (e.g. a Flatpak) from this repository,
do not include fetched shaders or textures in it. The app fetches them at
runtime on the user's machine for exactly this reason.

The Flatpak bundles the shipped `Deband.fx`, `ReShade.fxh`, and
`ReShadeUI.fxh` (installed to `/app/share/sober-shader/shaders/`). Those
three files fall under their respective upstream licenses; they are
included so the app works offline before Fetch, not redistributed for
their own sake.
