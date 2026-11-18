# Architecture Overview

How the app is structured, what each file does, and how the pieces fit
together. This is the map for anyone reading the source for the first time.

## The three layers

The app is a layered design with one rule: **each layer only talks to the
layer below it**.


---

```text
┌─────────────────────────────────────────────────────────────┐
│ GUI (GTK4/Adw) — gui/ │
│ shader list, sliders, Apply/Launch/Rollback buttons │
└──────────────────────┬──────────────────────────────────────┘
│ cli_bridge (subprocess, --json)
┌──────────────────────▼──────────────────────────────────────┐
│ CLI (argparse) — cli/ │
│ check, status, apply, launch, rollback, fetch-shaders, │
│ list-shaders, validate-config │
└──────────────────────┬──────────────────────────────────────┘
│ function calls (no subprocess)
┌──────────────────────▼──────────────────────────────────────┐
│ Core (pure Python) — core/ │
│ runtime, config, launch, fetch, importing, shader │
└─────────────────────────────────────────────────────────────┘
```

---

## Why this shape:

- **Core has no GUI or subprocess imports.** `runtime`, `config`, `launch`,
  `fetch`, `shader` are pure functions and dataclasses; `importing` reads a
  source tree and writes only into the sandbox directories it is given.
  Everything is testable without a display, without Sober installed, without
  network. Subprocess calls are wrapped in injectable `Runner` functions, so
  tests pass a fake and never shell out.
- **The CLI is a thin wrapper.** Every command is a parse + one or two
  core calls + formatting. `apply` is the only command with real
  orchestration logic (backup → write → verify-launch → restore-on-failure).
- **The GUI never touches Core directly** for anything that changes state.
  It runs the CLI as a subprocess via `gui/cli_bridge.py`, parses the
  JSON, and works with the dataclasses. This means the CLI is the single
  source of truth for "what does apply do" — the GUI can't drift.

The GUI also imports `core.shader` (shader list and dependency check),
`core.config` (parsing the active config on load), and `core.importing`
(the folder-import flow, which writes only to the sandbox). None of them
spawn a subprocess, so they're safe to call in the GTK main thread.

## Directory layout

---

```text
sirius-os-sober-shaders/
├── core/ pure Python: no GUI, no subprocess
│ ├── runtime.py detect Sober's runtime + vkBasalt extension
│ ├── config.py parse/validate/write vkBasalt.conf
│ ├── launch.py launch Sober (normal, detached, verify, verify-then-detach)
│ ├── fetch.py git clone/pull GShade and ReShade repos
│ ├── importing.py import decisions, path rewrite, copy
│ └── shader.py parse .fx files, check include/texture deps
├── cli/ argparse wrappers around core
│ ├── main.py subcommand dispatch, one function per command
│ ├── format.py human-readable and JSON output
│ └── main.py python -m cli entry point
├── gui/ GTK4/Adw frontend
│ ├── app.py Adw.Application entry point
│ ├── window.py the main window (list, sliders, buttons)
│ ├── cli_bridge.py runs the CLI as a subprocess, parses --json
│ ├── dialogs.py error dialog helper
│ └── gi_setup.py gi.require_version before any gi.repository import
├── shaders/ shipped with the Flatpak
│ ├── Deband.fx
│ ├── ReShade.fxh
│ └── ReShadeUI.fxh
├── presets/ ready-to-use configs
│ └── clear.conf
├── docs/ this directory
├── tests/ pytest suite, fixture-based
│ ├── fixtures/ captured command output
│ └── test*.py one per core module plus CLI
├── scripts/ recovery and import tools
│ ├── seed-shaders.sh
│ ├── import-shaders.py
│ └── README.md
├── org.siriusos.SoberShaderManager.json Flatpak manifest
├── sober-shader-wrapper.sh CLI wrapper inside the Flatpak
├── sober-shader-manager-wrapper.sh GUI wrapper
└── README.md
```

---


## The core modules

Each module has a `Runner` type and a `_default_runner` so tests can inject
a fake. This is the pattern that makes the suite run without a display or
Sober install.

### `core/runtime.py` — compatibility detection

Answers one question: **is the installed vkBasalt extension compatible
with Sober's runtime?**

Three functions, each shelling out to `flatpak`:

- `detect_sober_runtime()` — reads `flatpak info --show-metadata
  org.vinegarhq.Sober`, parses `runtime=org.gnome.Platform/x86_64/51` →
  `"org.gnome.Platform//51"`
- `detect_vulkanlayer_version(runtime_ref)` — reads the runtime's
  `[Extension org.freedesktop.Platform.VulkanLayer]` block →
  `"26.08"`
- `detect_installed_vkbasalt_branches()` — reads `flatpak list
  --columns=application,branch` → `["25.08", "26.08"]` (multiple can coexist)

`check_compatibility()` combines them into a `CompatibilityResult` with a
`status` of `ok`, `mismatch`, `missing`, `no_sober`, or `error`. On
mismatch/missing it includes `fix_commands` — the exact `flatpak install` /
`flatpak uninstall` commands the user needs.

The `Runner` type is `Callable[[list[str]], CompletedProcess]`. Tests pass
a dict-backed fake keyed by argv.

### `core/config.py` — vkBasalt.conf

The config file is flat `key = value`, with `effects = a:b:c` being the one
colon-separated list. The module has five functions:

- `parse(path)` → `VkBasaltConfig` dataclass. Raises `ConfigError` on
  malformed lines or duplicate keys. **Distinguishes shader paths from
  parameters** by checking whether the value ends in `.fx`: `dbd =
  /path/Deband.fx` goes in `shader_paths`, `Range = 128.0` goes in
  `parameters`.
- `validate(config)` → list of error strings. Checks: `effects` is
  non-empty; every effect has a shader path (or is a built-in like `cas`);
  paths are under `/var/home/` (the sandbox prefix) not `/home/` (the host
  prefix vkBasalt can't see).
- `validate_against_shaders(config, shader_dir)` → list of error strings
  for parameter names that don't match any uniform in the referenced
  shaders. vkBasalt silently ignores unknown parameters; this catches typos.
- `write(config, path)` → atomic write (temp file, then rename).
- `backup(active, backup)` / `restore(backup, active)` — simple
  `shutil.copy2`.

The `VkBasaltConfig` dataclass is the interchange format between `parse`,
`validate`, `write`, and the GUI's `_collect_config`.

### `core/launch.py` — launching Sober

Four launch modes, chosen by keyword:

| Mode | Function | What it does |
|---|---|---|
| `normal` | `_launch_normal` | `flatpak run`, block until Sober exits, return exit code |
| `detached` | `_launch_detached` | `flatpak run` with `start_new_session=True`, return PID immediately |
| `verify` | `_launch_verify` | `flatpak run` with `VKBASALT_LOG_LEVEL=info`, block until exit, parse the log |
| `verify-then-detach` | `_launch_verify_then_detach` | `flatpak run`, read stdout line-by-line, return as soon as vkBasalt reports its config (or errors), leave the process running |

The `launch()` function checks `running_checker()` first — if Sober is
already running, it raises `LaunchError` rather than launching a second
instance (which crashes).

`_build_argv(verify)` constructs the argv with `--env=ENABLE_VKBASALT=1`
and, in verify mode, `--env=VKBASALT_LOG_LEVEL=info`. Inside the Flatpak
(`SOBER_SHADER_HOST_FLATPAK=1`), the command prefix is `flatpak-spawn
--host flatpak` instead of `flatpak`.

`_parse_vkbasalt_log(text)` returns `(loaded, errors)`:
- `loaded` is True if `vkBasalt info: config file:` appears
- `errors` is every `vkBasalt err:` line

The four modes differ mainly in when they return and what they parse. The
`verify-then-detach` mode is what the GUI's Apply uses: it returns as
soon as vkBasalt reports the config, so the GUI unblocks while Sober keeps
running.

### `core/fetch.py` — cloning upstream repos

Two upstreams:

- `Mortalitas/GShade-Shaders` → `~/GShade-Shaders` (the shader collection)
- `crosire/reshade-shaders` → `~/reshade-shaders` (framework headers, BSD 3-Clause)

`fetch(dest, update, repo_url)` runs `_fetch_one` twice (once per repo),
returns the GShade `FetchResult` with `success` ANDed across both. The
`message` field is two lines (one per repo) so the GUI banner can show what
happened to each.

`_git_cmd()` returns `["flatpak-spawn", "--host", "git"]` inside the
Flatpak, `["git"]` on the host.

The fetch is clone-or-pull: if `dest` doesn't exist, `git clone --depth=1`;
if it does and `update=True`, `git pull --ff-only`; if it exists and
`update=False`, return `already-present` without touching it. The
`already-present` path is important: the GUI's Fetch runs `_fetch_done`
even on `already-present`, so the copy-to-sandbox step still happens.

### `core/importing.py` — import decisions and copying

The import pipeline shared by `scripts/import-shaders.py` and the GUI.

- `discover_assets(root)` — finds `.fx` anywhere under the source, and
  anchors headers/textures on a `Shaders/` and `Textures/` directory if
  present (so qUINT's flat layout and reshade-shaders' `Includes/` layout
  both work).
- `build_available()` — builds the flattened namespace vkBasalt will see:
  sandbox contents first, then the source (source wins). Files that are
  identical once path rewriting is applied are not reported as collisions.
- `validate_shader()` — walks includes transitively and collects textures
  declared in headers as well as the top-level shader. Returns a
  `ShaderDecision` with `status`, `reason`, and the resolved dependencies.
- `rewrite_references()` — converts Windows separators and `./` prefixes in
  `#include` and `source = "..."` paths to POSIX. This is required, not
  cosmetic: vkBasalt's preprocessor substitutes the raw include string into
  the including file's path and never converts backslashes.
- `copy_decided()` / `import_root()` — copy each shader and its dependencies
  to both the flat path and the preserved subpath, rewriting shader text on
  the way in. `import_root()` is the one-call entry point (pass `dry=True`
  for a preview).

It has no subprocess calls; the only writes go to the `shader_dir` /
`texture_dir` passed in.

### `core/shader.py` — .fx parsing

Not an HLSL parser — a set of regexes that match the specific syntactic
patterns ReShade shaders use:

- `uniform float name < ui_type = "slider"; ... > = default;` → `Uniform`
- `#include "file.fxh"` → `includes`
- `texture2D tex < source = "path.png"; >` → `textures`

`parse_fx(path)` → `ShaderInfo` with `uniforms`, `includes`, `textures`,
`parse_warnings`. Never raises on unrecognized syntax; a malformed shader
gets whatever was parsed plus warnings.

`discover(shader_dir)` → list of `ShaderInfo` for every `.fx` in the dir.

`check_dependencies(shader, include_dir, texture_dir)` → list of strings
like `"missing include: ReShade.fxh"` or `"missing texture: LensDBA.png"`.
This is what grays out rows in the GUI.

## The CLI

`cli/main.py` builds an argparse parser and dispatches to one function per
command. `cli/format.py` renders the core dataclasses to human-readable or
JSON. `cli/__main__.py` is the `python -m cli` entry point.

### Commands

| Command | Purpose | Exit codes |
|---|---|---|
| `check` | Report whether Sober's runtime and the vkBasalt extension are compatible, with fix commands if not | 0 ok, 1 mismatch, 2 missing, 3 no Sober, 4 error |
| `status` | Show the active config's effects, compatibility status, and whether a last-known-good backup exists | 0 |
| `apply FILE` | Validate `FILE`, back up the current config, write the new one, launch Sober in verify mode, restore the backup on failure. `--detach` uses verify-then-detach so the CLI returns once vkBasalt reports, leaving Sober running | 0 success, 5 invalid config, 6 vkBasalt errors, 7 launch error |
| `launch` | Launch Sober with the active config. Default is detached; `--verify` blocks and parses the log; `--no-detach` blocks without parsing | 0, 5 (no/invalid config), 7 (launch error), 9 (verify failed) |
| `rollback` | Copy `last-known-good.conf` over `vkBasalt.conf` | 0, 10 (no backup) |
| `list-shaders [--shader-dir DIR]` | Parse every `.fx` in `DIR` (default `~/GShade-Shaders/Shaders`), print uniforms, flag missing deps | 0 |
| `fetch-shaders [--dest DIR] [--update]` | Clone or pull GShade and ReShade repos | 0, 11 (FetchError), 12 (fetch failed) |
| `validate-config FILE [--shader-dir DIR]` | Structural check + parameter-name check against the referenced shaders | 0 valid, 5 parse error, 13 validation errors |

Global flags: `--config PATH` (override the active config path),
`--json` (machine-readable output), `--quiet` (suppress progress).

The `--json` flag is what `gui/cli_bridge.py` uses. Every command that the
GUI invokes outputs a JSON object with a stable schema:

- `check` → `{status, sober_runtime, installed_vkbasalt_branch,
  expected_vkbasalt_branch, fix_commands, error}`
- `apply` → `{result: "success"|"failed", effects, errors, restored}`
- `launch` → `{mode, success, exit_code, pid, vkbasalt_loaded, vkbasalt_errors}`
- `rollback` → `{restored, effects}`
- `fetch-shaders` → `{action, dest, success, message}`

## The GUI

### `gui/app.py`

`Adw.Application` subclass with `application_id=org.siriusos.SoberShaderManager`.
`do_activate` creates the `MainWindow` (or reuses the active one) and
presents it.

### `gui/_gi_setup.py`

Two lines: `gi.require_version("Gtk", "4.0")` and
`gi.require_version("Adw", "1")`. **Every GUI module imports this first**
(`from gui import _gi_setup  # noqa`) so the versions are pinned before any
`gi.repository` import. Without it, GTK picks the first available version
and might get 3.0.

### `gui/window.py`

The main window, ~800 lines. The pieces:

- **`__init__`** — sets up state: `_shaders` (all discovered), `_selected`
  (currently checked), `_sliders` (param UI), `_show_all_shaders` (view
  filter), `_cas_enabled`, `_imported_names` (persisted across restarts).
- **`_build_ui`** — the GTK layout: toolbar, banner, CAS row, shader list
  with Show-all switch, parameter area, bottom action bar (Rollback,
  Apply, Launch, Fetch, Import).
- **`_refresh`** — runs `cli_bridge.check()` for the banner, then
  `_discover_all_shaders()` and `_populate_list()`, then
  `_load_active_config()`.
- **`_discover_all_shaders`** — merges shipped + GShade + sandbox shader
  dirs, skips `VKBASALT_BLOCKLIST`, returns sorted by name.
- **`_populate_list`** — rebuilds the listbox rows. Visible set is
  SHORTLIST + imported + shaders in the active config. Under Show-all,
  appends everything else.
- **`_load_active_config`** — parses the active config, maps effect aliases
  to shader stems by path (not by the alias map, which is incomplete), and
  calls `_apply_selection` to check the corresponding rows.
- **`_apply_selection(names)`** — iterates `Adw.ActionRow`s, checks the
  ones whose title is in `names`. Shared by `_load_active_config` and
  `_on_show_all_toggled`.
- **`_add_row(s)`** — builds one row: title, dependency check (grayed if
  missing), checkbox, optional delete button for sandbox-only shaders.
- **`_on_shader_toggled`** — updates `_selected`, rebuilds params.
- **`_add_slider`** — one `Gtk.Scale` per slider-type uniform. Uses the
  active config's value if present, else the shader default.
- **`_sync_shader_to_sandbox`** / **`_sync_textures`** — copy the selected
  shader and its includes/textures into the sandbox before writing the
  config. Necessary because vkBasalt runs inside Sober's Flatpak and can't
  see the host's `~/GShade-Shaders/`.
- **`_collect_config`** — builds a `VkBasaltConfig` from `_selected` +
  `_sliders` + `_cas_enabled`. Uses `_alias()` for effect keys. Clears
  inherited `effects`/`shader_paths`/`parameters` from the base so the
  result is fully regenerated from the GUI state.
- **`_alias(name)`** — returns `SHADER_ALIASES[name]` if present, else
  the full lowercase alphanumeric name. The full-name fallback is what
  makes the family shaders (Spotlight 1–5, StageDepth 1–5, Layer 1–5)
  get distinct keys instead of colliding on `name.lower()[:4]`.
- **`_on_apply`** — `_collect_config` → `validate` → write to temp file →
  disable buttons → `cli_bridge.apply_config(detach=True)` in a worker
  thread → `_apply_done` on the main thread.
- **`_fetch_done`** — after a successful `fetch_shaders()`, copies the
  GShade `.fx`/`.fxh` and Textures into the sandbox via `flatpak-spawn
  --host cp`, then the ReShade framework headers via two separate `cp`
  calls per header (the previous single `cp src dest1 dest2` was invalid).
- **`_on_import_shader`** — file picker for a single `.fx`, calls
  `_import_shader_file` which copies the shader and its sibling
  includes/textures into the sandbox.

### `gui/cli_bridge.py`

The only module that runs the CLI as a subprocess. Each function:
`_run_cli(args, timeout)` → parse JSON → return a dataclass
(`CheckResult`, `ApplyResult`, `LaunchResult`, `FetchResult`).

`_run_cli` prepends `--json` and the current Python:
`[sys.executable, "-m", "cli", "--json", *args]`. The `--json` must come
before the subcommand because argparse's parent-parser flags are only
accepted in that position.

### `gui/dialogs.py`

`show_error_dialog(parent, message)` — one-line helper that shows an
`Adw.MessageDialog` with the message and an OK button. Used everywhere the
GUI needs to report an error.

## The Flatpak wrappers

`sober-shader-wrapper.sh` and `sober-shader-manager-wrapper.sh` are the
entry points inside the Flatpak. They set `SOBER_SHADER_HOST_FLATPAK=1`
(which flips `_flatpak_cmd()` and `_git_cmd()` to use `flatpak-spawn
--host`), then `exec python3 -m cli` or `exec python3 -m gui.app`.

The manifest (`org.siriusos.SoberShaderManager.json`) copies `core/`,
`cli/`, `gui/` to `/app/lib/sober-shader/`, the shipped `shaders/` to
`/app/share/sober-shader/shaders/`, and installs the two wrappers to
`/app/bin/`.

## Data flow for the main use case (Apply)

1. User checks shaders and adjusts sliders in the GUI.
2. Clicks Apply. `_on_apply` runs `_collect_config`, which:
   - Syncs each selected shader + its includes/textures into the sandbox
     (`~/.../config/vkBasalt/{Shaders,Textures}/`).
   - Builds a `VkBasaltConfig` with effect aliases, shader paths, and
     parameter values.
3. `config_mod.validate(cfg)` checks structure.
4. Writes the config to a temp file.
5. `cli_bridge.apply_config(tmp_path, detach=True)` → subprocess
   `python -m cli --json apply <tmp> --detach`.
6. CLI `cmd_apply`:
   - `config_mod.parse` and `validate` the temp file.
   - `config_mod.backup` the active config to `last-known-good.conf`.
   - `config_mod.write` the new config to the active path.
   - `launch_mod.launch(verify_then_detach=True)`:
     - `flatpak run` with `VKBASALT_LOG_LEVEL=info`
     - reads stdout until `vkBasalt info: config file:` appears
     - if a `vkBasalt err:` line appears first, kills the process and
       returns failure (triggers the restore path)
     - if the config block appears, returns success and leaves Sober
       running
   - On success: return 0, print JSON with `result: "success"`.
   - On failure: `config_mod.restore(backup, active)`, return 6/7, print
     JSON with `result: "failed"`.
7. `_apply_done` on the GUI thread re-enables buttons and shows the banner
   or error dialog.

The safety property: if the new config fails to load, the previous config
is restored **by the CLI**, not by the GUI. If the GUI dies mid-apply, the
CLI subprocess completes independently and still rolls back on failure.

## Testing

See `docs/Testing.md` for the suite structure. In short: every module with
a subprocess call has an injectable `Runner`, and tests pass fake runners
backed by fixture files. The suite runs without a display, without Sober,
without network.

## Where to look for X

| If you want to change... | Look at... |
|---|---|
| How Sober's runtime is detected | `core/runtime.py` |
| The config file format | `core/config.py` (`parse` / `write`) |
| How Apply launches Sober and checks the result | `core/launch.py` (`_launch_verify`, `_launch_verify_then_detach`) |
| What shaders are offered in the GUI | `gui/window.py` (`SHORTLIST`, `_discover_all_shaders`, `VKBASALT_BLOCKLIST`) |
| The effect alias for a shader | `gui/window.py` (`SHADER_ALIASES`, `_alias`) |
| Why a shader is grayed out | `core/shader.py` (`check_dependencies`), `gui/window.py` (`_add_row`) |
| How Fetch copies into the sandbox | `gui/window.py` (`_fetch_done`) |
| The CLI's JSON output | `cli/format.py` |
| The Flatpak's permissions and filesystems | `org.siriusos.SoberShaderManager.json` |
| The test fixtures | `tests/fixtures/` |
| How to recover the full shader set | `scripts/seed-shaders.sh`, `docs/Backups` in README |
