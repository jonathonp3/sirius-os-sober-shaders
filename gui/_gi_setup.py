"""gui/_gi_setup.py — must be imported before any gi.repository import."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
