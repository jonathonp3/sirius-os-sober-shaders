"""
gui/window.py

Main window: compatibility banner, shader list with parameter sliders,
Apply / Launch / Rollback buttons.

Reads shaders via core.shader (fast, pure). Before writing a config, copies
the selected shaders and their dependencies into Sober's sandbox directory,
because vkBasalt runs inside the Flatpak sandbox and cannot see the host's
~/GShade-Shaders/ tree.
"""
from __future__ import annotations

from gui import _gi_setup  # noqa: F401  (must run before gi.repository imports)

import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from gi.repository import Adw, Gtk, GLib

from core import config as config_mod
from core import shader as shader_mod
from gui import cli_bridge
from gui.dialogs import show_error_dialog


GHSHADE_SHADER_DIR = Path.home() / "GShade-Shaders" / "Shaders"
GHSHADE_TEXTURE_DIR = Path.home() / "GShade-Shaders" / "Textures"

# Shipped shaders, installed by the Flatpak manifest at
# /app/share/sober-shader/shaders/. On the host this doesn't exist, so
# the discovery loop skips it.
SHIPPED_SHADER_DIR = Path("/app/share/sober-shader/shaders")

SHORTLIST = ["Deband", "Bloom", "Clarity", "Curves", "Vibrance"]

# Short lowercase aliases for vkBasalt.conf effect keys. Using the shader's
# file basename as the effect key causes a duplicate-key collision when the
# shader has a uniform with the same name (e.g. Vibrance.fx has a uniform
# named Vibrance). The alias keeps the shader path key distinct from any
# uniform name.
SHADER_ALIASES = {
    "Deband": "dbd",
    "Bloom": "blm",
    "Clarity": "clr",
    "Curves": "crv",
    "Vibrance": "vib",
    "CAS": "cas",
}

# Sandbox-visible directories inside Sober's Flatpak.
SANDBOX_CONFIG_DIR = Path.home() / ".var/app/org.vinegarhq.Sober/config/vkBasalt"
SANDBOX_SHADER_DIR = SANDBOX_CONFIG_DIR / "Shaders"
SANDBOX_TEXTURE_DIR = SANDBOX_CONFIG_DIR / "Textures"

# Persistent record of shader names the user explicitly imported. Used to
# show them in the default shader list across restarts.
IMPORTED_STATE_FILE = (
    Path.home() / ".var/app/org.siriusos.SoberShaderManager/config/imported.json"
)

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application):
        super().__init__(
            application=application,
            title="Sober Shader Manager",
            default_width=800,
            default_height=600,
        )

        self._shaders: list[shader_mod.ShaderInfo] = []
        self._selected: dict[str, shader_mod.ShaderInfo] = {}
        self._sliders: dict[str, Gtk.Scale] = {}
        self._show_all_shaders = False
        self._cas_enabled: bool = False
        self._cas_slider: Gtk.Scale | None = None
        self._active_config_parameters: dict[str, str] = {}
        self._imported_names: set[str] = set()
        try:
            if IMPORTED_STATE_FILE.exists():
                self._imported_names = set(json.loads(IMPORTED_STATE_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass

        self._build_ui()
        self._refresh()

    # ---- construction ----

    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self._banner = Adw.Banner(title="", revealed=False)
        toolbar.add_top_bar(self._banner)

        # Bottom bar: action buttons pinned to the bottom of the window
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom.set_margin_top(6); bottom.set_margin_bottom(6)
        bottom.set_margin_start(12); bottom.set_margin_end(12)

        self._rollback_btn = Gtk.Button(label="Rollback")
        self._rollback_btn.connect("clicked", self._on_rollback)
        bottom.append(self._rollback_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bottom.append(spacer)

        self._apply_btn = Gtk.Button(label="Apply")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.connect("clicked", self._on_apply)
        bottom.append(self._apply_btn)

        self._launch_btn = Gtk.Button(label="Launch Sober")
        self._launch_btn.add_css_class("suggested-action")
        self._launch_btn.connect("clicked", self._on_launch)
        bottom.append(self._launch_btn)

        self._fetch_btn = Gtk.Button(label="Fetch shaders")
        self._fetch_btn.connect("clicked", self._on_fetch_shaders)
        bottom.append(self._fetch_btn)

        self._import_btn = Gtk.Button(label="Import shader…")
        self._import_btn.connect("clicked", self._on_import_shader)
        bottom.append(self._import_btn)

        toolbar.add_bottom_bar(bottom)

        # Scrolled content
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12); content.set_margin_bottom(12)
        content.set_margin_start(12); content.set_margin_end(12)

        # CAS row (built-in effect, no .fx file)
        cas_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._cas_check = Gtk.CheckButton(label="CAS (built-in)")
        self._cas_check.connect("toggled", self._on_cas_toggled)
        cas_row.append(self._cas_check)

        self._cas_slider = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.01,
        )
        self._cas_slider.set_value(0.30)
        self._cas_slider.set_hexpand(True)
        self._cas_slider.set_draw_value(True)
        self._cas_slider.set_sensitive(False)  # disabled until checkbox is on
        cas_row.append(self._cas_slider)
        content.append(cas_row)

        # Shader list header row
        shader_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        list_label = Gtk.Label(label="Active shader chain", xalign=0)
        list_label.add_css_class("heading")
        list_label.set_hexpand(True)
        shader_header.append(list_label)

        self._show_all_switch = Gtk.Switch()
        self._show_all_switch.set_valign(Gtk.Align.CENTER)
        self._show_all_switch.connect("notify::active", self._on_show_all_toggled)
        show_all_label = Gtk.Label(label="Show all")
        shader_header.append(show_all_label)
        shader_header.append(self._show_all_switch)
        content.append(shader_header)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.add_css_class("boxed-list")
        content.append(self._listbox)

        # Parameter area
        param_label = Gtk.Label(label="Parameters", xalign=0)
        param_label.add_css_class("heading")
        param_label.set_margin_top(12)
        content.append(param_label)

        self._param_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.append(self._param_box)

        scroll.set_child(content)
        toolbar.set_content(scroll)
        self.set_content(toolbar)


    # ---- data loading ----

    def _refresh(self) -> None:
        compat = cli_bridge.check()
        if not compat.ok:
            self._banner.set_title(
                f"vkBasalt extension mismatch ({compat.installed_branch} installed, "
                f"{compat.expected_branch} required)"
            )
            self._banner.set_revealed(True)
        else:
            self._banner.set_revealed(False)

        self._shaders = self._discover_all_shaders()
        self._populate_list()
        self._load_active_config()

    def _populate_list(self) -> None:
        while (row := self._listbox.get_first_child()) is not None:
            self._listbox.remove(row)
        self._selected.clear()

        by_name = {s.name: s for s in self._shaders}

        # Default view: shortlist + anything the user explicitly imported.
        visible: list[shader_mod.ShaderInfo] = []
        seen: set[str] = set()
        for name in SHORTLIST:
            if name in by_name:
                visible.append(by_name[name])
                seen.add(name)
        for name in self._imported_names:
            if name in by_name and name not in seen:
                visible.append(by_name[name])
                seen.add(name)

        for s in visible:
            self._add_row(s)

        # "Show all" reveals the rest of the GShade set.
        if self._show_all_shaders:
            sep = Gtk.Separator()
            self._listbox.append(sep)
            for s in self._shaders:
                if s.name not in seen:
                    self._add_row(s)

    def _discover_all_shaders(self) -> list[shader_mod.ShaderInfo]:
        """
        Discover shaders from three locations, in priority order:
        shipped (Flatpak only), the user's GShade clone, and the sandbox
        Shaders directory (imported). The first occurrence of a name wins,
        so the shipped Deband beats a GShade Deband, which beats an
        imported Deband.
        """
        by_name: dict[str, shader_mod.ShaderInfo] = {}

        for directory in (SHIPPED_SHADER_DIR, GHSHADE_SHADER_DIR, SANDBOX_SHADER_DIR):
            if not directory.exists():
                continue
            for info in shader_mod.discover(directory):
                by_name.setdefault(info.name, info)

        # Drop imported names whose files were deleted from the sandbox.
        self._imported_names &= set(by_name.keys())
        self._save_imported_names()
        return sorted(by_name.values(), key=lambda s: s.name)

    def _save_imported_names(self) -> None:
        try:
            IMPORTED_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            IMPORTED_STATE_FILE.write_text(
                json.dumps(sorted(self._imported_names))
            )
        except OSError:
            pass

    def _load_active_config(self) -> None:
        """
        Read the active vkBasalt.conf and check the corresponding boxes,
        so reopening the GUI reflects what's currently applied.
        """
        active = Path.home() / ".var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf"
        if not active.exists():
            return

        try:
            cfg = config_mod.parse(active)
        except config_mod.ConfigError:
            return

        # Stash parameter values so _add_slider can restore them when the
        # toggled signal rebuilds the parameter UI.
        self._active_config_parameters = dict(cfg.parameters)

        alias_to_name = {v: k for k, v in SHADER_ALIASES.items()}
        selected_names = {alias_to_name.get(alias, alias) for alias in cfg.effects}

        row = self._listbox.get_first_child()
        while row is not None:
            next_row = row.get_next_sibling()
            if isinstance(row, Adw.ActionRow):
                name = row.get_title()
                if name in selected_names:
                    check = row.get_activatable_widget()
                    if isinstance(check, Gtk.CheckButton):
                        check.set_active(True)
            row = next_row

        if "cas" in cfg.effects:
            self._cas_check.set_active(True)
        if "casSharpness" in cfg.parameters and self._cas_slider is not None:
            try:
                self._cas_slider.set_value(float(cfg.parameters["casSharpness"]))
            except (TypeError, ValueError):
                pass

    def _add_row(self, s: shader_mod.ShaderInfo) -> None:
        missing = shader_mod.check_dependencies(
            s,
            include_dir=s.path.parent,
            texture_dir=s.path.parent.parent / "Textures",
        )

        row = Adw.ActionRow(title=s.name)
        if missing:
            row.set_subtitle(f"{len(missing)} missing dep(s)")
            row.add_css_class("dim-label")

        check = Gtk.CheckButton()
        if missing:
            check.set_sensitive(False)
            check.set_tooltip_text(
                "Missing dependencies:\n  " + "\n  ".join(missing)
            )
        check.connect("toggled", self._on_shader_toggled, s)
        row.add_prefix(check)
        row.set_activatable_widget(check)

        # Delete button only for shaders that exist in the sandbox dir.
        sandbox_path = SANDBOX_SHADER_DIR / s.path.name
        if sandbox_path.exists():
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text(
                f"Remove {s.path.name} from the app's Shaders directory"
            )
            del_btn.connect("clicked", self._on_delete_shader, s)
            row.add_suffix(del_btn)

        self._listbox.append(row)

    # ---- interactions ----

    def _on_show_all_toggled(self, switch, _pspec) -> None:
        self._show_all_shaders = switch.get_active()
        self._populate_list()

    def _on_cas_toggled(self, check: Gtk.CheckButton) -> None:
        self._cas_enabled = check.get_active()
        if self._cas_slider is not None:
            self._cas_slider.set_sensitive(self._cas_enabled)

    def _on_shader_toggled(self, check: Gtk.CheckButton, s: shader_mod.ShaderInfo) -> None:
        if check.get_active():
            self._selected[s.name] = s
        else:
            self._selected.pop(s.name, None)
        self._rebuild_params()

    def _rebuild_params(self) -> None:
        while (child := self._param_box.get_first_child()) is not None:
            self._param_box.remove(child)
        self._sliders.clear()

        for name, s in self._selected.items():
            header = Gtk.Label(label=name, xalign=0)
            header.add_css_class("heading")
            self._param_box.append(header)
            for u in s.uniforms:
                if u.ui_type == "slider" and u.ui_min is not None and u.ui_max is not None:
                    self._add_slider(name, u)

    def _add_slider(self, shader_name: str, u: shader_mod.Uniform) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=u.ui_label or u.name, xalign=0)
        label.set_size_request(180, -1)
        row.append(label)

        step = u.ui_step or ((u.ui_max - u.ui_min) / 100.0)
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, u.ui_min, u.ui_max, step,
        )
        # Prefer the active config's value if present, else the shader default.
        value = self._active_config_parameters.get(u.name, u.default)
        try:
            scale.set_value(float(value))
        except (TypeError, ValueError):
            pass
        scale.set_hexpand(True)
        scale.set_draw_value(True)
        row.append(scale)

        self._sliders[f"{shader_name}.{u.name}"] = scale
        self._param_box.append(row)

    # ---- sandbox sync ----

    def _sync_shader_to_sandbox(self, s: shader_mod.ShaderInfo) -> Path:
        SANDBOX_SHADER_DIR.mkdir(parents=True, exist_ok=True)
        src_dir = s.path.parent
        for name in [s.path.name, *s.includes]:
            src = src_dir / name
            if not src.exists():
                continue
            dst = SANDBOX_SHADER_DIR / name
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                shutil.copy2(src, dst)
        return SANDBOX_SHADER_DIR / s.path.name

    def _sync_textures(self, shaders: list[shader_mod.ShaderInfo]) -> None:
        SANDBOX_TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
        for s in shaders:
            for tex_name in s.textures:
                src = s.path.parent.parent / "Textures" / tex_name
                if not src.exists():
                    continue
                dst = SANDBOX_TEXTURE_DIR / tex_name
                if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                    shutil.copy2(src, dst)

    # ---- actions ----

    def _alias(self, name: str) -> str:
        """Short lowercase alias for a shader, used as the effect key in vkBasalt.conf."""
        return SHADER_ALIASES.get(name, name.lower()[:4])

    def _collect_config(self) -> config_mod.VkBasaltConfig:
        active = Path.home() / ".var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf"
        if active.exists():
            base = config_mod.parse(active)
        else:
            base = config_mod.VkBasaltConfig()

        base.effects = []
        base.shader_paths = {}
        base.parameters = {}

        selected = list(self._selected.values())
        self._sync_textures(selected)

        base.reshade_texture_path = str(SANDBOX_TEXTURE_DIR)
        base.reshade_include_path = str(SANDBOX_SHADER_DIR)

        # Order effects by SHORTLIST position, not by click order, so the
        # chain always runs dbd -> blm -> cas -> clr -> crv -> vib
        # regardless of the order the user checked the boxes.
        ordered = [self._alias(n) for n in SHORTLIST if n in self._selected]
        ordered += [self._alias(n) for n in self._selected if n not in SHORTLIST]
        if self._cas_enabled:
            insert_at = 2 if len(ordered) >= 2 else len(ordered)
            ordered.insert(insert_at, "cas")
        base.effects = ordered
        base.shader_paths = {
            self._alias(name): str(self._sync_shader_to_sandbox(s))
            for name, s in self._selected.items()
        }
        for key, scale in self._sliders.items():
            _shader_name, param_name = key.split(".", 1)
            base.parameters[param_name] = f"{scale.get_value():.4f}"

        if self._cas_enabled and self._cas_slider is not None:
            base.parameters["casSharpness"] = f"{self._cas_slider.get_value():.4f}"

        return base

    def _on_apply(self, _btn) -> None:
        cfg = self._collect_config()
        errors = config_mod.validate(cfg)
        if errors:
            show_error_dialog(self, "\n".join(errors))
            return

        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            tmp_path = f.name
            config_mod.write(cfg, Path(tmp_path))

        # Disable all action buttons while Apply runs. Apply launches Sober
        # in verify mode and blocks until it exits, so clicking Launch (or
        # Rollback, Fetch, Import) mid-apply would race with the running
        # Sober instance and crash it.
        self._apply_btn.set_sensitive(False)
        self._apply_btn.set_label("Applying…")
        self._launch_btn.set_sensitive(False)
        self._rollback_btn.set_sensitive(False)
        self._fetch_btn.set_sensitive(False)
        self._import_btn.set_sensitive(False)

        def worker():
            result = cli_bridge.apply_config(tmp_path, detach=True)
            GLib.idle_add(self._apply_done, result, tmp_path)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_done(self, result: cli_bridge.ApplyResult, tmp_path: str) -> bool:
        Path(tmp_path).unlink(missing_ok=True)
        self._apply_btn.set_sensitive(True)
        self._apply_btn.set_label("Apply")
        self._launch_btn.set_sensitive(True)
        self._rollback_btn.set_sensitive(True)
        self._fetch_btn.set_sensitive(True)
        self._import_btn.set_sensitive(True)

        if result.ok:
            self._banner.set_title("Shaders applied successfully.")
            self._banner.set_revealed(True)
        else:
            show_error_dialog(
                self,
                "Apply failed.\n\n" + "\n".join(result.errors) +
                ("\n\nPrevious config was restored." if result.restored else ""),
            )
        return False

    def _on_launch(self, _btn) -> None:
        result = cli_bridge.launch(detach=True)
        if not result.ok:
            show_error_dialog(self, "Failed to launch Sober.")

    def _on_rollback(self, _btn) -> None:
        if cli_bridge.rollback():
            self._refresh()
            self._banner.set_title("Rolled back to last-known-good.")
            self._banner.set_revealed(True)
        else:
            show_error_dialog(self, "No backup found or rollback failed.")

    def _on_fetch_shaders(self, _btn) -> None:
        self._fetch_btn.set_sensitive(False)
        self._fetch_btn.set_label("Fetching…")

        def worker():
            result = cli_bridge.fetch_shaders()
            GLib.idle_add(self._fetch_done, result)

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_done(self, result: cli_bridge.FetchResult) -> bool:
        self._fetch_btn.set_sensitive(True)
        self._fetch_btn.set_label("Fetch shaders")

        if result.ok:
            try:
                SANDBOX_SHADER_DIR.mkdir(parents=True, exist_ok=True)
                # The running sandbox's :ro mount of ~/GShade-Shaders doesn't
                # reflect a directory created after launch (git clone ran on
                # the host). Copy via flatpak-spawn --host, which runs on the
                # host where the clone is visible, writing into the sandbox
                # Shaders dir (writable via :create).
                host_gshade = str(Path.home() / "GShade-Shaders" / "Shaders")
                sandbox_dst = str(SANDBOX_SHADER_DIR)
                subprocess.run(
                    ["flatpak-spawn", "--host", "sh", "-c",
                     f'cp -r "{host_gshade}/." "{sandbox_dst}/" 2>/dev/null'],
                    capture_output=True, text=True, check=False,
                )
                # Copy the shipped framework files into the clone (the GShade
                # repo doesn't ship them) and into the sandbox.
                for name in ("ReShade.fxh", "ReShadeUI.fxh"):
                    src = SHIPPED_SHADER_DIR / name
                    if not src.exists():
                        continue
                    dst_sandbox = SANDBOX_SHADER_DIR / name
                    if not dst_sandbox.exists():
                        shutil.copy2(src, dst_sandbox)
                    subprocess.run(
                        ["flatpak-spawn", "--host", "cp",
                         str(src), f"{host_gshade}/{name}"],
                        capture_output=True, text=True, check=False,
                    )
            except OSError:
                pass
            self._refresh()
            self._banner.set_title(
                f"Shaders fetched to {result.dest}. Shader list refreshed."
            )
            self._banner.set_revealed(True)
        else:
            show_error_dialog(self, f"Fetch failed: {result.message}")
        return False

    def _on_import_shader(self, _btn) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Import shader")

        filt = Gtk.FileFilter()
        filt.set_name("ReShade FX shaders (*.fx)")
        filt.add_pattern("*.fx")
        dialog.set_default_filter(filt)

        def done(dlg, result):
            try:
                gfile = dlg.open_finish(result)
            except GLib.Error:
                return  # user cancelled
            if gfile is None:
                return
            self._import_shader_file(Path(gfile.get_path()))

        dialog.open(self, None, done)

    def _import_shader_file(self, src: Path) -> None:
        """
        Copy a .fx file and its sibling .fxh includes into the sandbox
        Shaders directory so vkBasalt can read them.
        """
        if not src.exists() or src.suffix != ".fx":
            show_error_dialog(self, f"Not a .fx file: {src}")
            return

        SANDBOX_SHADER_DIR.mkdir(parents=True, exist_ok=True)

        copied = [src.name]
        shutil.copy2(src, SANDBOX_SHADER_DIR / src.name)
        self._imported_names.add(src.stem)
        self._save_imported_names()

        try:
            info = shader_mod.parse_fx(src)
            for inc in info.includes:
                sib = src.parent / inc
                if sib.exists():
                    shutil.copy2(sib, SANDBOX_SHADER_DIR / inc)
                    copied.append(inc)
            SANDBOX_TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
            for tex in info.textures:
                src_tex = GHSHADE_TEXTURE_DIR / tex
                if src_tex.exists():
                    shutil.copy2(src_tex, SANDBOX_TEXTURE_DIR / tex)
                    copied.append(tex)
        except Exception as e:
            # Include/texture copy is best-effort; the .fx itself is
            # imported above, so a missing dependency shows as a disabled
            # checkbox rather than a silent failure.
            copied.append(f"(partial: {e})")

        self._refresh()
        self._banner.set_title(
            f"Imported {', '.join(copied)} into the app's Shaders directory."
        )
        self._banner.set_revealed(True)
        
        

    def _on_delete_shader(self, _btn, s: shader_mod.ShaderInfo) -> None:
        sandbox_path = SANDBOX_SHADER_DIR / s.path.name
        if not sandbox_path.exists():
            show_error_dialog(self, f"{s.path.name} is not in the sandbox Shaders directory.")
            return

        # Confirm before deleting — this is irreversible.
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Remove {s.path.name}?",
            body=(
                f"This deletes {sandbox_path.name} from the app's Shaders "
                f"directory. If the shader also exists in ~/GShade-Shaders/, "
                f"that copy is untouched and the shader will reappear in the "
                f"list (under 'Show all')."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response != "delete":
                return
            try:
                sandbox_path.unlink()
                self._imported_names.discard(s.name)
                self._save_imported_names()
                # Also drop it from the current selection so Apply doesn't
                # reference a file that's gone.
                self._selected.pop(s.name, None)
                self._refresh()
                self._banner.set_title(f"Removed {sandbox_path.name}.")
                self._banner.set_revealed(True)
            except OSError as e:
                show_error_dialog(self, f"Delete failed: {e}")

        dialog.connect("response", on_response)
        dialog.present()
