The change

In gui/window.py, replace:
python

SHORTLIST = ["Deband", "Bloom", "Clarity", "Curves", "Vibrance"]

with:
```python

SHORTLIST = [
    "Deband",       # shipped with the app
    "Clarity2",     # GShade's Clarity (note the 2)
    "CA",           # chromatic aberration
    "Censor",       # GShade censor effect
    "Bloom",        # present in the full backup, not the reduced clone
    "Clarity",      # present in the full backup, not the reduced clone
    "Curves",       # present in the full backup, not the reduced clone
    "Vibrance",     # present in the full backup, not the reduced clone
]
```

### What each entry does

| Entry | In reduced clone? | In full backup? | Effect |
|---|---|---|---|
| Deband | ✅ (shipped) | ✅ | Always visible and selectable |
| Clarity2 | ✅ | ✅ | Always visible and selectable |
| CA | ✅ | ✅ | Always visible and selectable |
| Censor | ✅ | ✅ | Always visible and selectable |
| Bloom | ❌ | ✅ | Visible only when the backup is restored |
| Clarity | ❌ | ✅ | Visible only when the backup is restored |
| Curves | ❌ | ✅ | Visible only when the backup is restored |
| Vibrance | ❌ | ✅ | Visible only when the backup is restored |


_populate_list only adds a shortlist entry if its name is in by_name, so the last four are silently skipped on the reduced clone. On the full backup they appear. No gray-out, no error — they just aren't shown when the file isn't there.
Why CA and Censor belong in the shortlist

Right now they're only visible after you apply them (Fix 2 in _populate_list includes applied shaders). That means:

    First time a kid wants to try Censor, they have to toggle "Show all", find it in ~64 entries, and check it.

    After applying, it's visible in the default view.

    If they uncheck and apply, it disappears again.

Adding them to the shortlist makes them always visible, which matches how you're actually using the app (Deband + Clarity2 + Censor + CA is a sensible default chain). The shortlist is the "quick-access set"; the full list is under Show all.
Why keep Bloom/Clarity/Curves/Vibrance

They're not in the reduced clone, but they're in the full backup. If you restore the backup, they should appear — and you probably want them visible then. Keeping them in the shortlist costs nothing on the reduced clone (they're skipped) and gives you the full shortlist on the backup.

If you'd rather the shortlist be strictly "what's in the reduced clone", drop the last four:
python
```bash
SHORTLIST = ["Deband", "Clarity2", "CA", "Censor"]
```
That's cleaner in one sense — every shortlist entry is always present — but you lose the "restore backup and everything appears" behavior.
SHADER_ALIASES adjustment

Clarity2, CA, and Censor are now in the shortlist, which means they're likely to be applied. Worth adding explicit aliases for CA and Censor so the config keys are stable and intentional, rather than fallback-derived:
```python

SHADER_ALIASES = {
    "Deband": "dbd",
    "Clarity": "clr",
    "Clarity2": "clr2",
    "Bloom": "blm",
    "Curves": "crv",
    "Vibrance": "vib",
    "CA": "ca",
    "Censor": "cens",
    "CAS": "cas",
}
```
ca and cens match what the fallback produces today (name.lower()[:4]), so this doesn't change existing configs — it just makes the mapping explicit. If you ever rename a shader or add a colliding name (Camera would also fall back to ca?), the explicit entry is where you'd fix it.

Note: CA → ca is intentional, not a collision with cas (CAS is the built-in). Both are three/four letters and distinct.
What to verify after the change

Rebuild and open the GUI with the current config (effects = dbd:clr2:cas:cens:ca):

- Default view (Show all off): Deband, Clarity2, CA, Censor are all visible and checked. The old shortlist names (Bloom, Clarity, Curves, Vibrance) are absent because the reduced clone doesn't have them.

- Toggle Show all on: the GShade set appears below a separator, with the four shortlist shaders still at the top (not duplicated). The active chain stays checked.

- Toggle Show all off: the extra set disappears; the four shortlist entries remain checked.

- Apply: config unchanged (all five effects still there, including CAS which isn't in the shortlist but is a built-in checkbox).

- Close and reopen: same state.

If a shortlist name isn't present in the reduced clone and isn't applied, its row isn't built — no gray-out, no error, no placeholder. That's the intended behavior: the shortlist is a list of candidates, not a guarantee.
One caveat

If you later add a shader to the shortlist that is present but has missing dependencies (e.g. if Bloom were somehow in the reduced clone and needed LensDBA.png), it would show grayed out (because _add_row calls check_dependencies and disables the checkbox for missing deps). That's correct — the shader is visible, honest about why it can't run, and the tooltip names the missing file. The shortlist change doesn't affect this; it's just worth knowing that "in the shortlist" and "selectable" aren't the same thing.

None of the four new entries (Clarity2, CA, Censor) have missing deps in the reduced clone — the log showed all of them loading with no err lines — so this caveat doesn't apply to them.
Where to put the change

In gui/window.py, near the top:

```python
SHORTLIST = ["Deband", "Clarity2", "CA", "Censor", "Bloom", "Clarity", "Curves", "Vibrance"]
```
One line if you don't want comments; the multi-line version with the "why" comments is worth keeping since it documents the "present only in full backup" distinction.

Rebuild, test, then update the docs/Applied Shaders.md section on SHORTLIST (currently it doesn't mention what's in the shortlist explicitly, but if you want to document the change, add a line under "Why _populate_list reads the active config" noting that "shortlisted but absent shaders are skipped silently — visible only after the backup is restored").


