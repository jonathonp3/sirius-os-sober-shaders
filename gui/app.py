"""gui/app.py — Adw.Application entry point."""

from gui import _gi_setup  # noqa: F401  (must run before gi.repository imports)

from gi.repository import Adw, Gio

from gui.window import MainWindow


class SoberShaderApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.siriusos.SoberShaderManager",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_activate(self):
        win = self.props.active_window or MainWindow(application=self)
        win.present()


if __name__ == "__main__":
    try:
        SoberShaderApp().run(None)
    except KeyboardInterrupt:
        pass
