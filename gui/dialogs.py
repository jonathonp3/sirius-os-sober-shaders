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


def show_import_report(parent, result) -> None:
    """Show which shaders a folder import added and which were rejected."""
    lines = [f"Imported {len(result.imported)} shader(s)."]
    for d in result.imported:
        lines.append(f"  ✓ {d.name}")
    if result.rejected:
        lines.append("")
        lines.append(f"Rejected {len(result.rejected)} shader(s):")
        for d in result.rejected:
            reason = d.reason or "dependencies missing"
            lines.append(f"  ✗ {d.name}: {reason}")
    if result.collisions:
        lines.append("")
        lines.append(
            "Name collisions: " + ", ".join(sorted(set(result.collisions)))
        )
    if not result.imported and not result.rejected:
        lines.append("No shaders found in this folder.")

    label = Gtk.Label(label="\n".join(lines), xalign=0)
    label.set_wrap(True)
    label.set_max_width_chars(72)
    label.set_selectable(True)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_min_content_height(220)
    scrolled.set_child(label)

    dlg = Adw.MessageDialog(
        transient_for=parent,
        heading="Import finished",
        body=f"{len(result.imported)} imported, {len(result.rejected)} rejected.",
    )
    dlg.set_extra_child(scrolled)
    dlg.add_response("ok", "OK")
    dlg.set_default_response("ok")
    dlg.present()

