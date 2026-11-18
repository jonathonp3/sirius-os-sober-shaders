# Testing

How to run the test suite, what it covers, and how the fixtures work.

## Quick start

```bash
cd ~/sirius-os-sober-shaders
source .venv/bin/activate
python3 -m pytest tests/ -v
```

Expected on a clean checkout: all tests pass, no Sober install, no display,
no vkBasalt required.

If pytest isn't installed in the venv:

```bash
pip install pytest
```

## What the suite covers

The tests are organized by module, one file per source file:

| Test file | Covers | Count |
|---|---|---:|
| `tests/test_runtime.py` | `core/runtime.py` — Sober runtime detection, vkBasalt extension branch detection, and compatibility checks | 8 |
| `tests/test_shader.py` | `core/shader.py` — `.fx` parsing, uniform extraction, reference normalization, and include/texture dependency resolution | 17 |
| `tests/test_importing.py` | `core/importing.py` — asset discovery, flattened namespace, import decisions, path rewriting, and copy layout | 24 |
| `tests/test_config.py` | `core/config.py` — `vkBasalt.conf` parsing, writing, and validation | 9 |
| `tests/test_fetch.py` | `core/fetch.py` — Git clone/pull wrapper | 5 |
| `tests/test_launch.py` | `core/launch.py` — launch modes (normal, detached, verify, verify-then-detach) and running-instance checks | 14 |
| `tests/test_cli.py` | `cli/main.py` — each subcommand’s handler with a mocked runner | 23 |
| `tests/fixtures/` | Captured Flatpak info, `flatpak list` output, `.fx` files, and fake `.fx` shaders | — |


The count adds up to 100 tests. The exact number appears in the pytest output
(`100 passed in 0.08s` or similar).

`tests/test_importing.py` builds small source trees under pytest's
`tmp_path` — a qUINT-style flat `Shaders/` and a Barbatos-style
`Shaders/Includes/` + `Textures/<author>/` tree — and asserts which shaders
are imported, which are rejected, and why. It also covers the Windows-path
rewrite and that copies land at both the flat and preserved paths. No Sober
launch is involved.


## The three design constraints

The suite was built around three constraints, and the test structure
reflects them:

- No display. The tests never import `gi.repository` (GTK). `gui/` is
  untested at the integration level — its behavior is validated manually.
  The CLI and core modules are pure Python and testable without a display.

- No Sober install. Every subprocess call (`flatpak info`, `flatpak list`, `git`, `flatpak-spawn`) 
  is mocked via a Runner injection point.
  The tests never shell out to the real system. This means the suite runs
  on any machine, including CI containers without Flatpak.

- No network. `core/fetch.py`'s tests mock the git runner. No clone
  happens during the suite.

`_launch_verify_then_detach` using a fake Popen object. Still no real
subprocess, but the "process" is a small class that yields predefined stdout
lines. That lets the tests exercise the log-parsing logic without spawning
anything.

## Running subsets

Just one file:

```bash
python3 -m pytest tests/test_runtime.py -v
```

Just one test:

```bash
python3 -m pytest tests/test_runtime.py::test_detect_sober_runtime_ok -v
```

Tests matching a keyword:

```bash
python3 -m pytest tests/ -k "vulkanlayer" -v
```

Show local variables on failure (useful when a fixture mismatch surprises
you):

```bash
python3 -m pytest tests/ -v -l
```

Stop on first failure:

```bash
python3 -m pytest tests/ -x
```

Run in parallel (if you have `pytest-xdist`):

```bash
pip install pytest-xdist
python3 -m pytest tests/ -n auto
```

The suite is small enough that parallelism barely matters — `pytest` finishes
in under a second.

---

## Fixtures

tests/fixtures/ contains captured outputs from the real commands, plus a
handful of fake .fx files:

| Fixture | What it is |
|---|---|
| `gnome-51-metadata.txt` | Captured output of `flatpak info --show-metadata org.gnome.Platform//51`. |
| `sober-metadata.txt` | Captured output of `flatpak info --show-metadata org.vinegarhq.Sober`. |
| `flatpak-list-vkbasalt-installed.txt` | Captured output of `flatpak list --columns=application,branch` with one vkBasalt branch. |
| `flatpak-list-vkbasalt-missing.txt` | Output of the same command, with no vkBasalt branch. |
| `flatpak-list-vkbasalt-multiple.txt` | Output of the same command, with two vkBasalt branches (`25.08`, `26.08`). |
| `*.fx` | Small shader files for `test_shader.py`, including examples with includes, textures, and sliders. |

### Fixture fidelity

The fixtures are captures, not hand-written approximations. This matters
because hand-written fixtures tend to match the parser's assumptions, which
is exactly where bugs hide. The `gnome-51-metadata.txt` case is the canonical

example: the parser originally `expected version=26.08` (no spaces), the
hand-written fixture confirmed that, and the real flatpak info output was
`version = 26.08` (with spaces). The fixture and the parser both had the same
wrong assumption, so the test passed while the CLI failed on real data.

The fix was to correct the fixture to match reality (version = 26.08) and
make the parser tolerant of whitespace around =. If you update a fixture,
capture it from the real command:

```bash
flatpak info --show-metadata org.gnome.Platform//51 > tests/fixtures/gnome-51-metadata.txt
```

Then trim trailing blank lines. Do not reformat to match the parser — the
parser should match the fixture.

---

## Adding a fixture

If you add a new command whose output the app parses, add a fixture:

1. Run the command on your system, capture stdout:

```bash
flatpak list --columns=application,branch > tests/fixtures/flatpak-list.txt
```

2. Trim trailing blank lines.

3. Reference it in a test via the `FIXTURES` path:

```python
from tests.conftest import FIXTURES
output = (FIXTURES / "flatpak-list.txt").read_text()
```

`tests/conftest.py` defines the `FIXTURES` constant and the `_fixture_runner`
helper used throughout the tests. It also sets up the sys.path so
`from core import ...` works.

---

##How the runner injection works

Each core module that shells out has a Runner type and a _default_runner:
```python

# core/runtime.py
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

def _default_runner(args):
    return subprocess.run(args, capture_output=True, text=True, check=False)

def detect_sober_runtime(runner: Runner = _default_runner) -> str | None:
    result = _run(["flatpak", "info", "--show-metadata", SOBER_APP_ID], runner)
    ...
```

Tests pass a fake runner that returns predefined outputs keyed by command:
```python

def test_detect_sober_runtime_ok():
    runner = _fixture_runner({
        ("flatpak", "info", "--show-metadata", "org.vinegarhq.Sober"):
            (0, (FIXTURES / "sober-metadata.txt").read_text()),
    })
    assert runtime.detect_sober_runtime(runner) == "org.gnome.Platform//51"
```
The `_fixture_runner` returns a `CompletedProcess` whose `stdout` is the
fixture text and `returncode` is 0 (or whatever the tuple specifies). If the
test calls a command that isn't in the dict, the runner raises — that catches
"the test forgot to mock a subprocess call" bugs.

`core/launch.py` is slightly different: its runner is typed
`Callable[[list[str]]`, `"subprocess.CompletedProcess[str]"`] for _launch_normal
and `_launch_verify`, but `_launch_verify_then_detach` takes a `Popen`
factory instead (because it reads stdout line by line). The tests for that
function use a _FakePopen class that emulates the parts of the Popen API
the function uses (`stdout.readline`, `poll`, `kill`, `stdout.close`).

---

## What's not tested

- `gui/`. The GTK layer is validated manually. A future test could use
  `xvfb` + `Gtk.Test` to drive the GUI headlessly, but that's out of scope
  for the current suite.

- Real subprocess execution. Everything shells out via mocks. The suite
  doesn't catch "the real `flatpak info` output changed format" bugs — those
  show up as fixture-vs-reality mismatches in manual testing, not as test
  failures. The fixture-fidelity rule (captures, not transcriptions) is the
  mitigation.

- Real git clones. `core/fetch.py's` runner is mocked. The suite doesn't
  catch network errors, auth issues, or repo-URL changes.

- Compile verification. `--verify` / `--verify-each` launch Sober and read
  vkBasalt's log, so they are validated manually against a real install.
  The unit suite covers the dependency decisions that happen before the
  launch, not the compiler's verdict.

- Integration. There's no end-to-end test that runs apply against a
  real Sober install. That's the manual validation flow described in
  `docs/Applied-Shaders.md`.

---

## CI

The suite runs in the project's CI workflow (`.github/workflows/`) on each
push. If CI fails on a test that passes locally, the most likely cause is a
fixture with a hardcoded path that doesn't exist in the CI container. Check
the test output for the failing assertion; the fix is usually to make the
test use `tmp_path` (pytest's built-in temp directory fixture) rather than a
real home path.

To reproduce CI locally:
```bash

# Same command CI runs
python3 -m pytest tests/ -v --tb=short
```

If CI and local disagree, the difference is usually environment variables
(`$HOME`, `$XDG_DATA_HOME`) or a missing file in `tests/fixtures/`.


---


## Adding a test

For a new function in core/:

1. Add the test to the matching `tests/test_*.py` file.

2. Use `_fixture_runner` to mock any subprocess calls.

3. Assert on the Result dataclass (status, fields), not on formatted output.

4. If the function has a code path that depends on a file format, add a
    fixture capture rather than an inline string.

For a new CLI subcommand in `cli/main.py`:

1. Add the test to `tests/test_cli.py`.

2. Invoke via main.main(["subcommand", "arg1", ...]) — the CLI entry point
    is testable without a subprocess.

3. Use the same `_fixture_runner` mechanism for any subprocess the handler
triggers.

For a new GUI behavior: not covered by the suite. Test manually against a
Sober install with `flatpak run org.siriusos.SoberShaderManager`, and note
the validation steps in the commit message or a doc.

---

## Common failures and fixes

`ModuleNotFoundError: No module named 'core'`

You're running pytest from outside the repo root, or the venv doesn't have
the repo's `sys.path`. Fix: `cd ~/sirius-os-sober-shaders` first, and
activate the venv (`source .venv/bin/activate`).

`FileNotFoundError: tests/fixtures/...`

A fixture path is wrong. The `FIXTURES` constant in `tests/conftest.py`
points at `tests/fixtures/`. If the test uses a relative path instead,
it'll fail when run from a different directory. Fix: use `FIXTURES / "name"`
in the test.

`AssertionError` with assert runner.calls == [...]

If a test has an assertion like `assert runner.calls == [...]` (some tests
do), it's checking that only the expected subprocesses were called. If you
change a function to add a new `_run(...)` call, update the expected list.

`_FakePopen hangs`

In `test_launch.py`, if a fake Popen is configured to yield lines and then
never set poll() to a non-None value, the test may wait. Check that the
fake process's `stdout.readline()` returns `b""` when the lines run out, and
that `poll()` returns the exit code (not `None`) after that.

## Reference

- tests/conftest.py — FIXTURES path, _fixture_runner helper, sys.path
   setup

- tests/test_runtime.py — the canonical example of the runner-injection
  pattern; read this first if you're adding tests

- core/runtime.py — the module whose Runner type and _default_runner
  the pattern is built on

- docs/Applied-Shaders.md — the manual validation flow for the GUI and
  config behavior, since those aren't in the suite

