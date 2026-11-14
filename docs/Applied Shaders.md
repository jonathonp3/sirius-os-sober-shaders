# Applied Shaders

How the app tracks which shaders are applied, how the GUI reconstructs
that state on reopen, and why the matching is done by shader path rather
than by effect alias.

## The user-facing contract

- **Apply** writes the current selection to `vkBasalt.conf`, backs up the
  previous config, launches Sober, and verifies vkBasalt loaded the
  config without errors.
- **Close and reopen the GUI** shows the shaders that are currently in
  the active config, checked, regardless of whether they're in
  `SHORTLIST` or were imported.
- **Toggle "Show all"** is a view filter, not a state change. It must
  preserve whatever the user had selected.
- **Reopen after a reboot** behaves the same as reopen in the same
  session: applied shaders are checked.

The invariant: *the GUI's checkbox state always reflects the active
config, and clicking Apply writes that state back to the config
unchanged* (except for new aliases, see below).

## Why matching is by shader path, not by alias

The config file is flat `key = value`:

effects = dbd:clr2:cas:cens:ca

dbd = /var/home/jonathon/.var/app/org.vinegarhq.Sober/config/vkBasalt/Shaders/Deband.fx
clr2 = /var/home/jonathon/.var/app/org.vinegarhq.Sober/config/vkBasalt/Shaders/Clarity2.fx
cens = /var/home/jonathon/.var/app/org.vinegarhq.Sober/config/vkBasalt/Shaders/Censor.fx
ca = /var/home/jonathon/.var/app/org.vinegarhq.Sober/config/vkBasalt/Shaders/CA.fx
Range = 128.0000

```text

`effects` is a colon-separated list of **effect aliases**. Each alias that
is a shader is also a key with a `path/to/Shader.fx` value. Parameters
use their uniform names (`Range`, `Iterations`, `casSharpness`) as keys.

To reconstruct the GUI's checkbox state from this file, `_load_active_config`
needs to map each alias in `effects` back to a shader **name** (the file
stem, e.g. `Clarity2`), because that's what the rows are titled.

The naive approach — use `SHADER_ALIASES` as a reverse map — only works
for shaders that have an entry in the map. It works for the shortlist
(`Deband` → `dbd`), but fails for any shader not in the map (Clarity2,
CA, Censor, and any of the ~200 GShade shaders). The reverse map returns
the alias itself (`clr2` → `clr2`), which isn't a row title, so the row
isn't checked. A subsequent Apply then sees only the checked shaders in
`self._selected` and silently drops the unrecognized ones — turning a
working multi-shader config into a single-shader one.

The path is unambiguous: `Path(cfg.shader_paths[alias]).stem` gives the
shader's name regardless of whether the alias is in `SHADER_ALIASES`:
```

```python
alias_to_name = {alias: Path(path).stem for alias, path in cfg.shader_paths.items()}
```

So _load_active_config prefers path-based matching, and falls back to
the static SHADER_ALIASES reverse map only for aliases that have no
shader path — notably cas, the built-in CAS effect, which has no
.fx file

Why _populate_list reads the active config

_populate_list builds the rows in the shader list. Without special
handling, it would only show:

    Shaders in SHORTLIST (the curated defaults)

    Shaders in _imported_names (explicitly imported via the Import button)

That's fine for unapplied shaders, but it breaks the reopen invariant for
applied non-shortlist shaders: Censor and CA aren't in the shortlist and
weren't imported, so their rows wouldn't be built at all. _apply_selection
would iterate rows looking for Censor and CA, find none, and leave the
checked state as empty. The GUI would then look like only the shortlist
shaders are applied — even though the config has all of them.

So _populate_list also reads the active config and includes any shader
whose file is in cfg.shader_paths:


active_names: set[str] = set()
if active.exists():
    try:
        cfg = config_mod.parse(active)
        for path_str in cfg.shader_paths.values():
            active_names.add(Path(path_str).stem)
    except config_mod.ConfigError:
        pass

Applied shaders are visible; unapplied non-shortlist shaders appear only
under "Show all". This is self-consistent: to get a non-shortlist shader
into the always-visible set, you apply it once, after which it stays
visible until you uncheck it and apply again (at which point it's neither
shortlist, imported, nor applied, so it's only under Show all).
Why "Show all" preserves the selection

_populate_list destroys and rebuilds all rows, and clears
self._selected in the process. Toggling "Show all" calls _populate_list,
so without intervention the toggle would wipe the user's selection.

"Show all" is a view filter, not a state change: it shouldn't discard
in-progress work. _on_show_all_toggled saves the currently selected
names before the rebuild and re-applies them afterward:
python

previously_selected = set(self._selected.keys())
self._populate_list()
self._apply_selection(previously_selected)

_apply_selection is the shared helper: it walks the listbox rows and
checks the ones whose title is in the given name set. Checking a row's
Gtk.CheckButton fires toggled, which routes through
_on_shader_toggled and populates self._selected — so no separate
bookkeeping is needed. The same helper is used by _load_active_config
(from the config) and by _on_show_all_toggled (from the pre-toggle
selection).
Aliases and the config format

The app writes effect aliases, not shader names. _alias(name) returns:

    SHADER_ALIASES[name] if the shader has an entry (e.g. Deband → dbd,
    Clarity2 → clr2, CAS → cas)

    Otherwise name.lower()[:4] (e.g. Censor → cens, CA → ca,
    ColorInversion → colo)

Aliases must satisfy two constraints:

    Unique within the config's effects list. name.lower()[:4]
    collides for names sharing the first four characters (Clarity and
    Clarity2 both truncate to clar; Deband and Deblur to deba).
    SHADER_ALIASES should be used for any shader that would collide.

    Distinct from any parameter name. The config is a flat
    key = value file: Vibrance as a shader alias and Vibrance as a
    uniform name produce duplicate keys and the config fails to parse.
    The short aliases (vib, clr, ca) are distinct from uniform names
    (Vibrance, Clarity, CA — wait, CA is a uniform in some shaders;
    see below).

Alias collisions to know about

    Clarity vs Clarity2: old Clarity.fx and GShade's Clarity2.fx
    have different aliases (clr and clr2) to avoid the clar fallback
    collision. If both are ever applied together, they get distinct keys.

    CA: the shader CA.fx has alias ca. Some shaders (e.g.
    ChromaticAberration in other packs) have a uniform named CA. If that
    shader were applied alongside CA.fx, the uniform CA = ... and the
    shader path ca = .../CA.fx would not collide (different case), but
    case-insensitive filesystems could be a concern. Not currently an
    issue with the shaders in the clone.

    Censor: alias cens. No known uniform collision.

What "applied" means for vkBasalt

The GUI can persist the selection, but the shaders only render if vkBasalt
loads them. After Apply, check the Sober log:
bash

grep -iE "vkbasalt (info|err)" ~/.var/app/org.vinegarhq.Sober/data/sober/sober_logs/latest.log | head -40

Expected on a clean load:
text

vkBasalt info:  config file: .../vkBasalt.conf
vkBasalt info:  effects = dbd:clr2:cas:cens:ca
vkBasalt info:  dbd = .../Deband.fx
vkBasalt info:  clr2 = .../Clarity2.fx
vkBasalt info:  cens = .../Censor.fx
vkBasalt info:  ca = .../CA.fx
vkBasalt info:  Range = 128.0000
...

No vkBasalt err: lines. If any appear, the named shader failed to
compile — typically a missing include or a vkBasalt version incompatibility.
The GUI may still show it as applied (the config is correct), but it won't
render. That's a vkBasalt/shader issue, not an app issue.
Debugging checklist

If reopen shows fewer shaders checked than the config has:

    Is the shader in by_name?
    bash

    flatpak run --command=sh org.siriusos.SoberShaderManager -c "PYTHONPATH=/app/lib/sober-shader python3 - <<'PY'
    from pathlib import Path
    from core import shader
    for label, d in {
        'shipped': Path('/app/share/sober-shader/shaders'),
        'gshade': Path.home() / 'GShade-Shaders' / 'Shaders',
        'sandbox': Path.home() / '.var/app/org.vinegarhq.Sober/config/vkBasalt/Shaders',
    }.items():
        if d.exists():
            print(label, [s.name for s in shader.discover(d) if 'clar' in s.name.lower()])
    PY"

    If the shader isn't in any source, it won't have a row and won't be
    checked on reopen.

    Does parse populate cfg.shader_paths for the alias?
    bash

    flatpak run --command=sh org.siriusos.SoberShaderManager -c "PYTHONPATH=/app/lib/sober-shader python3 - <<'PY'
    from pathlib import Path
    from core import config
    cfg = config.parse(Path.home() / '.var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf')
    print('effects:', cfg.effects)
    print('shader_paths:', cfg.shader_paths)
    PY"

    shader_paths should contain an entry for every shader alias in
    effects. If it doesn't, parse is classifying the line as a
    parameter instead of a shader path — that's a core/config.py issue.

    Is the row's title what _apply_selection expects?
    If _add_row changes to include the file extension or a subtitle,
    row.get_title() will no longer equal the stem. Keep
    Adw.ActionRow(title=s.name) where s.name is path.stem.

Files involved

    gui/window.py — _load_active_config, _apply_selection,
    _populate_list, _on_show_all_toggled, _collect_config, _alias

    core/config.py — parse (populates shader_paths),
    write (emits the config), validate (rejects duplicate keys)

    cli/main.py — apply command (writes config, launches Sober,
    verifies, rolls back on failure)

History

    2026-11-14: Fixed three related bugs that surfaced when the app
    started using the reduced GShade clone (which ships Clarity2, Censor,
    CA, etc. under names the old SHADER_ALIASES didn't know):

        _fetch_done's framework-header copy used cp src dest1 dest2 (invalid),
        silently failing; switched to two independent flatpak-spawn --host cp
        calls per file.

        _load_active_config matched by alias, failing for any shader not
        in SHADER_ALIASES; switched to path-based matching.

        _populate_list only built rows for shortlist + imported, so applied
        non-shortlist shaders had no row on reopen; it now also reads the
        active config.

        Added _apply_selection as shared row-checking helper; "Show all"
        toggle now preserves the in-progress selection instead of wiping it.

text


## Where to put it

Save as `docs/Applied Shaders.md` (or `docs/applied-shaders.md` if you prefer lowercase filenames — just be consistent with the rest of `docs/`).

If you have a `docs/README.md` or index, add a line linking to it:

```markdown
- [Applied Shaders](Applied%20Shaders.md) — how the GUI tracks and persists
  the applied shader selection across reopen, and why matching is by path.

One thing worth verifying before you commit this doc

The doc says "the app writes effect aliases, not shader names" and lists cens/ca as the fallback aliases. That matches the log you pasted (effects = dbd:clr2:cas:cens:ca). But if you later add Censor and CA to SHADER_ALIASES, the doc needs updating — the aliases would become whatever you set, not the fallback.

Right now SHADER_ALIASES doesn't have entries for Censor or CA, so the fallback name.lower()[:4] is used, giving cens and ca. That's consistent with the doc.

The doc also notes a potential CA uniform collision — if you ever apply a shader that has a CA uniform alongside CA.fx, the config writer would emit ca = .../CA.fx and CA = <value>. Different keys (case-sensitive), so no duplicate-key error. But worth knowing if a shader ever does that. The doc mentions it as a "to know about" not a "currently broken" — accurate as of now.


        
