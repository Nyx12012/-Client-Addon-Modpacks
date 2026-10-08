import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox

import tavern_shared.theme as _theme

from . import engine, window

MODS_BUTTON = "\U0001F9EA Mods"
AFTER_TEXTS = ("\U0001F4C1 Mods Folder", "☑ Toggle Mods")
BUTTON_TEXT = "\U0001F4E6 Modpacks"


def _app():
    return getattr(tk, "_default_root", None)


def _log(message, tag=""):
    app = _app()
    try:
        app._print(message, tag)
    except Exception:
        pass


def _alert(title, text):
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        messagebox.showerror(title, text, parent=_app())
    except Exception:
        pass


def _is_game(args):
    if isinstance(args, (str, bytes)) or not args:
        return False
    try:
        first = os.fspath(args[0])
    except TypeError:
        return False
    if isinstance(first, bytes):
        return False
    if os.path.basename(first).lower() == engine.GAME_EXE.lower():
        return True
    try:
        exe = _app().v_exe.get().strip()
    except Exception:
        return False
    return bool(exe) and engine.same_path(first, exe)


def before_launch(args):
    if not _is_game(args):
        return
    game_dir = os.path.dirname(os.path.abspath(os.fspath(args[0])))
    target = engine.target_from_args(list(args))
    try:
        result = engine.apply(game_dir, target)
    except engine.ConflictError as error:
        _alert("Modpacks", "The game can't start with these mods together:\n\n"
               + "\n".join("• " + c for c in error.conflicts)
               + "\n\nOpen Modpacks to fix it, then join again.")
        raise engine.ModpacksError("Modpacks found a conflict, so nothing was changed. "
                                   + "; ".join(error.conflicts)) from None
    except engine.ModpacksError as error:
        _alert("Modpacks", str(error))
        raise engine.ModpacksError("Modpacks: " + str(error)) from None
    except Exception as error:
        message = "Modpacks hit an error and stopped the launch: %r. Untick Modpacks in Addons to launch without it." % (error,)
        _alert("Modpacks", message)
        raise engine.ModpacksError(message) from None
    if result.skipped == "running":
        _log("Modpacks: the game is already running, so modpacks are unchanged", "warn")
        return
    if result.skipped == "no-mods":
        _log("Modpacks: there's no Mods folder yet, so modpacks are unchanged", "warn")
        return
    for note in result.plan.notes:
        _log("Modpacks: " + note, "warn")
    for path in result.kept_aside:
        _log("Modpacks: no pack holds %s anymore, so it was kept in %s"
             % (os.path.basename(path), os.path.join(engine.PACKS_DIR, engine.REMOVED_DIR)), "warn")
    for warning in result.warnings:
        _log("Modpacks: " + warning, "warn")
    _log(result.plan.summary(), "info")
    window_open = getattr(_app(), "_modpacks_window", None)
    try:
        if window_open is not None and window_open.winfo_exists():
            window_open.after_idle(window_open.refresh)
    except Exception:
        pass


class _Popen(subprocess.Popen):
    _modpacks = True

    def __init__(self, args, *rest, **kwargs):
        before_launch(args)
        super().__init__(args, *rest, **kwargs)


if not getattr(subprocess.Popen, "_modpacks", False):
    subprocess.Popen = _Popen


def _game_dir(root):
    try:
        exe = root.v_exe.get().strip()
    except Exception:
        exe = ""
    if not exe or not os.path.isfile(exe):
        messagebox.showerror("Game not found",
                             "Please set the path to 'A Township Tale.exe' above first.", parent=root)
        return None
    game_dir = os.path.dirname(exe)
    if not os.path.isdir(os.path.join(game_dir, "Mods")):
        messagebox.showinfo("No Mods folder",
                            "There is no Mods folder next to the game yet. Install MelonLoader with the "
                            "Mods button, then start the game once.", parent=root)
        return None
    return game_dir


def _open(root):
    game_dir = _game_dir(root)
    if game_dir:
        window.open_window(root, game_dir)


def _add_button(anchor):
    try:
        root = anchor.winfo_toplevel()
        if not hasattr(root, "v_exe"):
            return
        after = anchor
        for child in anchor.master.pack_slaves():
            try:
                text = child.cget("text")
            except Exception:
                continue
            if text == BUTTON_TEXT:
                return
            if text in AFTER_TEXTS:
                after = child
        for child in anchor.master.winfo_children():
            try:
                if child.cget("text") == BUTTON_TEXT:
                    return
            except Exception:
                continue
        button = _theme._btn(anchor.master, BUTTON_TEXT, lambda: _open(root),
                             font=("Segoe UI", 9), pady=5, padx=10)
        button.pack(side="left", padx=(6, 0), after=after)
    except Exception:
        pass


def _wrap(orig):
    def _btn(parent, text, cmd, style="normal", **kw):
        button = orig(parent, text, cmd, style, **kw)
        if text == MODS_BUTTON:
            button.after_idle(lambda: button.after_idle(lambda: _add_button(button)))
        return button
    return _btn


_theme._btn = _wrap(_theme._btn)

for _name in ("client.core.launcher_window",):
    _module = sys.modules.get(_name)
    if _module is not None and callable(getattr(_module, "_btn", None)):
        _module._btn = _wrap(_module._btn)
