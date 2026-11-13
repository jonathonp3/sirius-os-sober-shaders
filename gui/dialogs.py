"""gui/dialogs.py — small reusable dialogs."""

from gi.repository import Adw, Gtk


def show_error_dialog(parent, message: str) -> None:
    dlg = Adw.MessageDialog(
        transient_for=parent,
        heading="Error",
        body=message,
    )
    dlg.add_response("ok", "OK")
    dlg.present()


def show_compatibility_dialog(parent, result) -> None:
    body = f"Installed: {result.installed_branch}\nRequired: {result.expected_branch}\n\n"
    if result.fix_commands:
        body += "Fix with:\n" + "\n".join(result.fix_commands)
    dlg = Adw.MessageDialog(
        transient_for=parent,
        heading="vkBasalt extension mismatch",
        body=body,
    )
    dlg.add_response("ok", "OK")
    dlg.present()
