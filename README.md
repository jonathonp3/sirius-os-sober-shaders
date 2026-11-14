
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
# the VulkanLayer version declared by Sober's runtime. To find yours:
flatpak info --show-metadata org.gnome.Platform//51 | grep -A2 VulkanLayer

# The app's `check` command reports the required branch too.
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

Updating
```bash
flatpak update --system org.siriusos.SoberShaderManager
```

The project's CI publishes to the stable branch on each push to main,
so flatpak update picks up new builds.

---

## Optional: run the validate-config command against the current config

If you want to confirm the command works as documented before committing, run it:
bash

```bash
flatpak run --command=sober-shader org.siriusos.SoberShaderManager validate-config \
    ~/.var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf
echo "exit: $?"
```

Expected: `Validation: OK`, exit 0. If it fails, the doc's claim ("Exit 0 means the config is valid") is still true, but the current config has a problem worth that is worth checking

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


The GShade upstream has shrunk

This matters for what the app can do on a fresh install.

The current `Mortalitas/GShade-Shaders` repository ships ~65 shaders and 5
textures. When this app was first built, it shipped ~200 shaders and ~90
textures — including Bloom, Clarity, Curves, Vibrance, the qUINT set, and
most of the texture assets. Those are no longer in the upstream repo.

---

### Consequences:

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

    Missing dependencies. The reduced clone's Textures/ doesn't have
    everything the shaders reference. check_dependencies grays out any
    shader whose includes or textures are missing, with a tooltip naming
    what's absent. On a fully seeded profile, 204/207 are selectable;
    CRTGeomMOD, DOF, and MartyMcFlyDOF gray out on genuinely missing
    textures (bezel artwork, mcmask.png).

    Silent black screens. Some shaders load without a vkBasalt error but
    render a black frame. Layer.fx and Layer2–Layer5 are the known
    offenders: they sample an external texture and blend it over the whole
    screen, and in this setup the blend produces black with no log line. The
    app has a VKBASALT_BLOCKLIST in gui/window.py that hides these from
    the GUI. Add to it if a new shader misbehaves.

---

## Configuration

The app writes `~/.var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf`
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

Effect aliases (`dbd`, `blm`, …) are short keys chosen to avoid collisions
with uniform names. The app's `SHADER_ALIASES` map covers the common ones;
anything not in the map falls back to its full lowercase alphanumeric name,
which is unique by construction. The matching logic and the config format
are documented in `docs/Applied Shaders.md`.  

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
licensed under the MIT License. See LICENSE.

The shader files and textures it fetches at runtime are not part of this
project and are not redistributed by it:

- ReShade framework (ReShade.fxh, ReShadeUI.fxh, Blending.fxh,
Macros.fxh, TriDither.fxh, DrawText.fxh) is fetched from
crosire/reshade-shaders
under the BSD 3-Clause License.

- GShade shaders are fetched from
Mortalitas/GShade-Shaders.
Each shader file carries its own license; the GShade repository documents
these separately.

- Sober (the Flatpak this app configures) is a separate project with
its own license.

If you build a distributable bundle (e.g. a Flatpak) from this repository,
do not include fetched shaders or textures in it. The app fetches them at
runtime on the user's machine for exactly this reason.

The Flatpak bundles the shipped `Deband.fx`, `ReShade.fxh`, and
`ReShadeUI.fxh` (installed to /app/share/sober-shader/shaders/). Those
three files fall under their respective upstream licenses; they are
included so the app works offline before Fetch, not redistributed for
their own sake.

