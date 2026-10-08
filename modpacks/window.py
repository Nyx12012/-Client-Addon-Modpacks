import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import tavern_shared.theme as _theme
import tavern_shared.window_chrome as _chrome

from . import engine

TITLE = "Modpacks"
MODE_LABELS = (("off", "Off"), ("all", "On everywhere"), ("servers", "Only on these servers"))
LIST_HEIGHT = 150
WIDE = 880


def _launcher_servers():
    try:
        with open(os.path.join(engine.data_dir(), "tavern_launcher.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return []
    servers = []
    for key in ("saved_servers", "recent_servers"):
        for entry in cfg.get(key) or []:
            if isinstance(entry, dict) and str(entry.get("ip", "")).strip():
                servers.append({"name": str(entry.get("name") or entry["ip"]).strip(),
                                "host": str(entry["ip"]).strip(),
                                "port": str(engine.parse_port(entry.get("port")))})
    return servers


def _ip_box(root):
    try:
        host = root.v_ip.get().strip()
    except Exception:
        host = ""
    try:
        port = engine.parse_port(root.v_port.get())
    except Exception:
        port = engine.DEFAULT_PORT
    return host or "127.0.0.1", port


def _mod_line(path, name):
    info = engine.mod_info(path)
    if not info:
        return name[:-4] if name.lower().endswith(".dll") else name, ""
    detail = []
    if info["version"]:
        detail.append("v" + info["version"])
    if info["author"]:
        detail.append("by " + info["author"])
    return info["name"], "  ".join(detail)


class ScrollList(tk.Frame):
    def __init__(self, parent, height):
        t = _theme
        super().__init__(parent, bg=t.SURF, highlightbackground=t.BORDER, highlightthickness=1)
        self.height = height
        self.canvas = tk.Canvas(self, bg=t.SURF, highlightthickness=0, bd=0, height=height)
        self.scroll = t._mk_scrollbar(self, self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=t.SURF)
        self.window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)

    def wheel(self, event):
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def contains(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False


class ModpacksWindow(tk.Toplevel):
    def __init__(self, root, game_dir):
        super().__init__(root)
        _chrome._start_hidden(self)
        self.title(TITLE)
        self.configure(bg=_theme.BG)
        self.root = root
        self.game_dir = game_dir
        self.selected = None
        self.packs = []
        self.extra_servers = []
        self.keep = []
        self.mode_var = tk.StringVar(value="off")
        self._pending = None
        self._trace = None
        self._build()
        self.refresh()
        self.update_idletasks()
        width = max(WIDE, self.winfo_reqwidth())
        height = self.winfo_reqheight()
        self.geometry("%dx%d" % (width, height))
        self.minsize(760, min(height, 560))
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Destroy>", self._on_destroy)
        try:
            self._trace = root.v_ip.trace_add("write", self._ip_changed)
            self._trace_port = root.v_port.trace_add("write", self._ip_changed)
        except Exception:
            self._trace = None
        _chrome._finish_dark_window(self)

    def _label(self, parent, text, **kw):
        t = _theme
        options = dict(bg=t.SURF, fg=t.PARCH, font=("Segoe UI", 9), anchor="w", justify="left")
        options.update(kw)
        return tk.Label(parent, text=text, **options)

    def _small(self, parent, text, cmd, style="normal"):
        return _theme._btn(parent, text, cmd, style, font=("Segoe UI", 8), pady=3, padx=8)

    def _build(self):
        t = _theme
        header = tk.Frame(self, bg=t.SURF, height=44)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="\U0001F4E6  Modpacks", bg=t.SURF, fg=t.AMBER,
                 font=("Georgia", 12, "bold")).pack(side="left", padx=16, pady=8)
        self._small(header, "⟳ Refresh", self.refresh, "dim").pack(side="right", padx=12)
        tk.Frame(self, bg=t.BORDER, height=1).pack(fill="x")
        tk.Label(self, text=(
            "A pack is a folder of mods. Turn each pack Off, On everywhere, or on only the servers you "
            "pick. Several packs can be on at once. Loose mods in the Mods folder always load and are never "
            "touched. Changes apply when the game starts."),
            bg=t.BG, fg=t.MUTED, font=("Segoe UI", 9), wraplength=WIDE - 40, justify="left"
        ).pack(anchor="w", padx=20, pady=(10, 2))
        tk.Label(self, text=os.path.join(self.game_dir, engine.PACKS_DIR), bg=t.BG, fg=t.MUTED,
                 font=("Consolas", 8)).pack(anchor="w", padx=20, pady=(0, 4))

        top = tk.Frame(self, bg=t.BG)
        top.pack(fill="both", expand=True, padx=16)
        left = tk.Frame(top, bg=t.BG, width=270)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(top, bg=t.BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        tk.Label(left, text="PACKS", bg=t.BG, fg=t.AMBER, font=("Georgia", 9, "bold")).pack(anchor="w", pady=(6, 4))
        self.pack_list = ScrollList(left, 250)
        self.pack_list.pack(fill="both", expand=True)
        row = tk.Frame(left, bg=t.BG)
        row.pack(fill="x", pady=(6, 0))
        self._small(row, "+ New", self.new_pack, "primary").pack(side="left")
        self.rename_btn = self._small(row, "Rename", self.rename_pack)
        self.rename_btn.pack(side="left", padx=(4, 0))
        self.delete_btn = self._small(row, "Delete", self.delete_pack, "danger")
        self.delete_btn.pack(side="left", padx=(4, 0))
        self._small(row, "Import zip...", self.import_pack).pack(side="right")

        head = tk.Frame(right, bg=t.BG)
        head.pack(fill="x", pady=(6, 4))
        self.pack_title = tk.Label(head, text="", bg=t.BG, fg=t.AMBER, font=("Georgia", 11, "bold"))
        self.pack_title.pack(side="left")
        self.export_btn = self._small(head, "Export zip...", self.export_pack)
        self.export_btn.pack(side="right")
        self.unpack_btn = self._small(head, "Unpack to loose", self.unpack_pack)
        self.unpack_btn.pack(side="right", padx=(0, 4))
        self.add_btn = self._small(head, "Add files...", self.add_files)
        self.add_btn.pack(side="right", padx=(0, 4))
        self.folder_btn = self._small(head, "\U0001F4C1 Open folder", self.open_folder)
        self.folder_btn.pack(side="right", padx=(0, 4))

        modes = tk.Frame(right, bg=t.SURF, highlightbackground=t.BORDER, highlightthickness=1)
        modes.pack(fill="x")
        self.mode_buttons = []
        for value, text in MODE_LABELS:
            button = tk.Radiobutton(modes, text=text, value=value, variable=self.mode_var,
                                    command=self.mode_changed, bg=t.SURF, fg=t.PARCH,
                                    activebackground=t.SURF, activeforeground=t.AMBER,
                                    selectcolor=t.AMBERDIM, font=("Segoe UI", 9),
                                    highlightthickness=0, bd=0)
            button.pack(side="left", padx=(10, 6), pady=6)
            self.mode_buttons.append(button)
        self.server_list = ScrollList(right, 72)
        self.server_list.pack(fill="x", pady=(4, 0))
        server_row = tk.Frame(right, bg=t.BG)
        server_row.pack(fill="x", pady=(4, 0))
        self.ip_btn = self._small(server_row, "+ Add the server in the IP box", self.add_ip_server)
        self.ip_btn.pack(side="left")
        self.ip_hint = tk.Label(server_row, text="", bg=t.BG, fg=t.MUTED, font=("Consolas", 8))
        self.ip_hint.pack(side="left", padx=(6, 0))

        tk.Label(right, text="MODS IN THIS PACK", bg=t.BG, fg=t.AMBER,
                 font=("Georgia", 9, "bold")).pack(anchor="w", pady=(8, 4))
        self.mod_list = ScrollList(right, 130)
        self.mod_list.pack(fill="both", expand=True)

        bottom = tk.Frame(self, bg=t.BG)
        bottom.pack(fill="both", padx=16, pady=(10, 0))
        launch = tk.Frame(bottom, bg=t.BG)
        launch.pack(side="left", fill="both", expand=True)
        loose = tk.Frame(bottom, bg=t.BG, width=330)
        loose.pack(side="left", fill="y", padx=(12, 0))
        loose.pack_propagate(False)
        self.launch_title = tk.Label(launch, text="NEXT LAUNCH", bg=t.BG, fg=t.AMBER,
                                     font=("Georgia", 9, "bold"))
        self.launch_title.pack(anchor="w", pady=(0, 4))
        self.launch_list = ScrollList(launch, LIST_HEIGHT)
        self.launch_list.pack(fill="both", expand=True)
        tk.Label(loose, text="LOOSE MODS (ALWAYS ON)", bg=t.BG, fg=t.AMBER,
                 font=("Georgia", 9, "bold")).pack(anchor="w", pady=(0, 4))
        self.loose_list = ScrollList(loose, LIST_HEIGHT - 28)
        self.loose_list.pack(fill="both", expand=True)
        self.make_btn = self._small(loose, "Make a pack from these...", self.make_from_loose, "primary")
        self.make_btn.pack(anchor="w", pady=(4, 0))

        self.status = tk.Label(self, text="", bg=t.BG, fg=t.CYAN, font=("Segoe UI", 9),
                               wraplength=WIDE - 40, justify="left", anchor="w")
        self.status.pack(fill="x", padx=20, pady=(6, 12))

    def say(self, text, error=False):
        self.status.configure(text=text, fg=_theme.RED if error else _theme.CYAN)

    def _wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        for scroll in (self.pack_list, self.server_list, self.mod_list, self.launch_list, self.loose_list):
            if scroll.contains(widget):
                scroll.wheel(event)
                return

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        try:
            if self._trace:
                self.root.v_ip.trace_remove("write", self._trace)
                self.root.v_port.trace_remove("write", self._trace_port)
        except Exception:
            pass

    def _ip_changed(self, *args):
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except Exception:
                pass
        self._pending = self.after(800, self._ip_settled)

    def _ip_settled(self):
        self._pending = None
        self.fill_servers()
        self.fill_launch()

    def current(self):
        if self.selected is None:
            return None
        for pack in self.packs:
            if pack.name.lower() == self.selected.lower():
                return pack
        return None

    def refresh(self, select=None):
        self.packs = engine.list_packs(self.game_dir)
        if select is not None:
            self.selected = select
        if self.current() is None:
            self.selected = self.packs[0].name if self.packs else None
        self.keep = []
        self.fill_packs()
        self.fill_detail()
        self.fill_launch()
        self.fill_loose()

    def fill_packs(self):
        t = _theme
        self.pack_list.clear()
        if not self.packs:
            self._label(self.pack_list.inner, "No packs yet. Click + New, Import zip, or make one from "
                        "your loose mods below.", fg=t.MUTED, wraplength=230).pack(anchor="w", padx=10, pady=12)
        for pack in self.packs:
            chosen = self.selected is not None and pack.name.lower() == self.selected.lower()
            bg = t.SURF2 if chosen else t.SURF
            row = tk.Frame(self.pack_list.inner, bg=bg, highlightbackground=t.AMBERDIM if chosen else bg,
                           highlightthickness=1, cursor="hand2")
            row.pack(fill="x", padx=4, pady=2)
            count = len(pack.files())
            on = pack.mode == "all" or (pack.mode == "servers" and pack.servers)
            name = self._label(row, pack.name, bg=bg, fg=t.AMBER if chosen else t.PARCH,
                               font=("Segoe UI", 10, "bold"))
            name.pack(anchor="w", padx=8, pady=(4, 0))
            detail = self._label(row, "%s  ·  %d mod%s" % (pack.describe(), count, "" if count == 1 else "s"),
                                 bg=bg, fg=t.GREEN if on else t.MUTED, font=("Segoe UI", 8), wraplength=230)
            detail.pack(anchor="w", padx=8, pady=(0, 4))
            for widget in (row, name, detail):
                widget.bind("<Button-1>", lambda e, n=pack.name: self.select(n))

    def select(self, name):
        self.selected = name
        self.fill_packs()
        self.fill_detail()

    def fill_detail(self):
        pack = self.current()
        state = "normal" if pack else "disabled"
        for button in (self.rename_btn, self.delete_btn, self.export_btn, self.add_btn, self.folder_btn,
                       self.unpack_btn, self.ip_btn) + tuple(self.mode_buttons):
            button.configure(state=state)
        self.pack_title.configure(text=pack.name if pack else "No pack selected")
        self.mode_var.set(pack.mode if pack else "off")
        self.fill_servers()
        self.fill_mods()

    def known_servers(self, pack):
        servers = []
        seen = set()
        for entry in (pack.servers if pack else []) + self.extra_servers + _launcher_servers():
            key = engine.server_key(entry["host"], entry["port"])
            if key not in seen:
                seen.add(key)
                servers.append(entry)
        return servers

    def fill_servers(self):
        t = _theme
        pack = self.current()
        self.server_list.clear()
        host, port = _ip_box(self.root)
        self.ip_hint.configure(text="%s:%d" % (host, port))
        if pack is None:
            return
        linked = {engine.server_key(s["host"], s["port"]) for s in pack.servers}
        servers = self.known_servers(pack)
        if not servers:
            self._label(self.server_list.inner, "No saved or recent servers yet. Join one, or add the server "
                        "in the IP box.", fg=t.MUTED).pack(anchor="w", padx=10, pady=8)
        grid = tk.Frame(self.server_list.inner, bg=t.SURF)
        grid.pack(fill="x", padx=6, pady=2)
        for index, server in enumerate(servers):
            key = engine.server_key(server["host"], server["port"])
            var = tk.BooleanVar(value=key in linked)
            self.keep.append(var)
            text = server["name"]
            if server["name"].lower() != server["host"].lower():
                text += "  (%s:%s)" % (server["host"], server["port"])
            elif str(server["port"]) != str(engine.DEFAULT_PORT):
                text += ":%s" % server["port"]
            box = tk.Checkbutton(grid, text=text, variable=var, bg=t.SURF, fg=t.PARCH,
                                 activebackground=t.SURF, activeforeground=t.AMBER, selectcolor=t.AMBERDIM,
                                 font=("Segoe UI", 9), highlightthickness=0, bd=0, anchor="w",
                                 command=lambda s=server, v=var: self.toggle_server(s, v.get()))
            box.grid(row=index // 2, column=index % 2, sticky="w", padx=(4, 12), pady=1)

    def fill_mods(self):
        t = _theme
        pack = self.current()
        self.mod_list.clear()
        if pack is None:
            return
        files = pack.files()
        if not files:
            self._label(self.mod_list.inner, "This pack is empty. Click Add files..., or drop .dll files into "
                        "its folder and click Refresh.", fg=t.MUTED).pack(anchor="w", padx=10, pady=12)
        for name in files:
            path = os.path.join(pack.path, name)
            title, detail = _mod_line(path, name)
            row = tk.Frame(self.mod_list.inner, bg=t.SURF)
            row.pack(fill="x", padx=8, pady=3)
            var = tk.BooleanVar(value=pack.ticked(name))
            self.keep.append(var)
            launcher = engine.is_launcher_mod(name)
            box = tk.Checkbutton(row, variable=var, bg=t.SURF, activebackground=t.SURF, selectcolor=t.AMBERDIM,
                                 highlightthickness=0, bd=0,
                                 command=lambda n=name, v=var: self.toggle_mod(n, v.get()))
            box.pack(side="left", anchor="n", pady=(2, 0))
            if launcher:
                box.configure(state="disabled")
            col = tk.Frame(row, bg=t.SURF)
            col.pack(side="left", fill="x", expand=True)
            line = tk.Frame(col, bg=t.SURF)
            line.pack(anchor="w")
            self._label(line, title, fg=t.PARCH if var.get() else t.MUTED,
                        font=("Segoe UI", 10, "bold")).pack(side="left")
            if detail:
                self._label(line, detail, fg=t.MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))
            note = name
            colour = t.MUTED
            if launcher:
                note += "  ·  the launcher's own mod, never loaded from a pack"
                colour = t.RED
            elif engine.looks_server_only(name):
                note += "  ·  looks like a server mod"
                colour = t.AMBER
            self._label(col, note, fg=colour, font=("Segoe UI", 8)).pack(anchor="w")

    def fill_launch(self):
        t = _theme
        host, port = _ip_box(self.root)
        self.launch_title.configure(text="NEXT LAUNCH FOR %s:%d" % (host, port))
        self.launch_list.clear()
        inner = self.launch_list.inner
        try:
            plan = engine.Plan(self.game_dir, (engine.resolve(host), port))
        except Exception as error:
            self._label(inner, "Couldn't work it out: %s" % error, fg=t.RED).pack(anchor="w", padx=10, pady=8)
            return
        for conflict in plan.conflicts:
            self._label(inner, "✖ " + conflict, fg=t.RED, wraplength=440).pack(anchor="w", padx=10, pady=(4, 0))
        if plan.conflicts:
            self._label(inner, "The game won't start until this is fixed.", fg=t.RED,
                        font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(0, 6))
        if not plan.active:
            self._label(inner, "No packs. Only your loose mods load.", fg=t.MUTED).pack(anchor="w", padx=10, pady=8)
        elif not plan.wanted:
            self._label(inner, "Packs on: %s, with no ticked mods." % ", ".join(p.name for p in plan.active),
                        fg=t.MUTED).pack(anchor="w", padx=10, pady=8)
        for low, item in sorted(plan.wanted.items(), key=lambda kv: kv[1]["file"].lower()):
            title, detail = _mod_line(item["source"], item["file"])
            text = "%s  %s" % (title, detail) if detail else title
            where = "pack " + ", ".join(item["packs"])
            if low in plan.identical:
                where += ", already loose in Mods"
            row = tk.Frame(inner, bg=t.SURF)
            row.pack(fill="x", padx=10, pady=1)
            self._label(row, "+ " + text, fg=t.GREEN).pack(side="left")
            self._label(row, "(%s)" % where, fg=t.MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))
        for entry in plan.remove:
            self._label(inner, "- %s leaves Mods" % entry["file"], fg=t.MUTED,
                        font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=1)
        for note in plan.notes:
            self._label(inner, note, fg=t.AMBER, font=("Segoe UI", 8), wraplength=440).pack(anchor="w", padx=10, pady=1)
        self._label(inner, "A Main Menu launch loads only the packs that are On everywhere.", fg=t.MUTED,
                    font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(6, 6))

    def fill_loose(self):
        t = _theme
        self.loose_list.clear()
        try:
            plan = engine.Plan(self.game_dir, None)
        except Exception:
            return
        names = sorted(plan.loose.values(), key=str.lower)
        if not names:
            self._label(self.loose_list.inner, "No loose mods.", fg=t.MUTED).pack(anchor="w", padx=10, pady=8)
        for name in names:
            title, detail = _mod_line(os.path.join(plan.mods_dir, name), name)
            launcher = engine.is_launcher_mod(name)
            text = title + "  (the launcher's)" if launcher else title + ("  " + detail if detail else "")
            self._label(self.loose_list.inner, text, fg=t.MUTED if launcher else t.PARCH,
                        font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=1)
        self.make_btn.configure(state="normal" if any(not engine.is_launcher_mod(n) for n in names) else "disabled")

    def save(self, pack, message=None):
        try:
            pack.save()
        except OSError as error:
            self.say("Couldn't save %s: %s" % (pack.name, error.strerror or error), True)
            return False
        self.refresh()
        if message:
            self.say(message)
        return True

    def mode_changed(self):
        pack = self.current()
        if pack is None:
            return
        pack.mode = self.mode_var.get()
        message = {"off": "%s is off.", "all": "%s loads on every server.",
                   "servers": "%s loads only on the ticked servers."}[pack.mode] % pack.name
        if pack.mode == "servers" and not pack.servers:
            message += " Tick one below."
        self.save(pack, message + " It applies the next time the game starts.")

    def toggle_server(self, server, on):
        pack = self.current()
        if pack is None:
            return
        key = engine.server_key(server["host"], server["port"])
        pack.servers = [s for s in pack.servers if engine.server_key(s["host"], s["port"]) != key]
        if on:
            pack.servers.append({"name": server["name"], "host": server["host"], "port": str(key[1])})
            pack.mode = "servers"
        self.save(pack, "%s %s %s." % (pack.name, "now loads on" if on else "no longer loads on", server["name"]))

    def add_ip_server(self):
        host, port = _ip_box(self.root)
        server = {"name": host, "host": host, "port": str(port)}
        for known in _launcher_servers():
            if engine.server_key(known["host"], known["port"]) == engine.server_key(host, port):
                server = known
        if all(engine.server_key(s["host"], s["port"]) != engine.server_key(host, port) for s in self.extra_servers):
            self.extra_servers.append(server)
        self.toggle_server(server, True)

    def toggle_mod(self, name, on):
        pack = self.current()
        if pack is None:
            return
        pack.set_ticked(name, on)
        self.save(pack, "%s is %s in %s." % (name, "ticked" if on else "left out", pack.name))

    def ask_name(self, title, prompt, initial=""):
        return simpledialog.askstring(title, prompt, initialvalue=initial, parent=self)

    def new_pack(self):
        name = self.ask_name("New pack", "Name for the new pack:")
        if not name:
            return
        try:
            pack = engine.create_pack(self.game_dir, name)
        except (engine.ModpacksError, OSError) as error:
            self.say(str(error), True)
            return
        self.refresh(select=pack.name)
        self.say("Made %s. It's On everywhere. Add mods with Add files... or Open folder." % pack.name)

    def rename_pack(self):
        pack = self.current()
        if pack is None:
            return
        name = self.ask_name("Rename pack", "New name for %s:" % pack.name, pack.name)
        if not name or name.strip() == pack.name:
            return
        try:
            renamed = engine.rename_pack(self.game_dir, pack, name)
        except (engine.ModpacksError, OSError) as error:
            self.say(getattr(error, "strerror", None) or str(error), True)
            return
        self.refresh(select=renamed.name)
        self.say("Renamed to %s." % renamed.name)

    def delete_pack(self):
        pack = self.current()
        if pack is None:
            return
        if not messagebox.askyesno("Delete pack", "Move the pack %s and its %d mods to the Recycle Bin?\n\n"
                                   "Its mods leave the Mods folder the next time the game starts."
                                   % (pack.name, len(pack.files())), parent=self):
            return
        try:
            engine.recycle(pack.path)
        except OSError as error:
            self.say("Couldn't delete %s: %s" % (pack.name, error.strerror or error), True)
            return
        self.selected = None
        self.refresh()
        self.say("%s is in the Recycle Bin." % pack.name)

    def unpack_pack(self):
        pack = self.current()
        if pack is None:
            return
        if not messagebox.askyesno("Unpack to loose", "Move the %d mods in %s back into the Mods folder as "
                                   "loose mods?\n\nLoose mods always load. The empty pack is removed."
                                   % (len(pack.files()), pack.name), parent=self):
            return
        try:
            moved, refused = engine.unpack(self.game_dir, pack)
        except (engine.ModpacksError, OSError) as error:
            self.say(getattr(error, "strerror", None) or str(error), True)
            return
        self.refresh()
        text = "Moved %d mod%s from %s back to Mods." % (len(moved), "" if len(moved) == 1 else "s", pack.name)
        if refused:
            text += " Still in the pack: " + ", ".join(refused) + "."
        self.say(text, bool(refused) and not moved)

    def open_folder(self):
        pack = self.current()
        if pack is None:
            return
        try:
            os.startfile(pack.path)
        except OSError as error:
            self.say("Couldn't open %s: %s" % (pack.path, error.strerror or error), True)

    def add_files(self):
        pack = self.current()
        if pack is None:
            return
        paths = filedialog.askopenfilenames(parent=self, title="Add mods to %s" % pack.name,
                                            filetypes=[("Mods", "*.dll"), ("All files", "*.*")])
        if not paths:
            return
        try:
            added, refused = engine.add_files(pack, paths)
        except OSError as error:
            self.say("Couldn't add files: %s" % (error.strerror or error), True)
            return
        self.refresh()
        text = "Added %d to %s." % (len(added), pack.name)
        servers = [n for n in added if engine.looks_server_only(n)]
        if servers:
            text += " Left unticked because they look like server mods: " + ", ".join(servers) + "."
        if refused:
            text += " Not added: " + ", ".join(refused) + "."
        self.say(text, bool(refused) and not added)

    def export_pack(self):
        pack = self.current()
        if pack is None:
            return
        path = filedialog.asksaveasfilename(parent=self, title="Export %s" % pack.name,
                                            initialfile=pack.name + ".zip", defaultextension=".zip",
                                            filetypes=[("Zip files", "*.zip")])
        if not path:
            return
        try:
            count = engine.export_pack(pack, path)
        except OSError as error:
            self.say("Couldn't export: %s" % (error.strerror or error), True)
            return
        self.say("Saved %s with %d mods. Your own mode and servers stay with you." % (path, count))

    def import_pack(self):
        path = filedialog.askopenfilename(parent=self, title="Import a pack",
                                          filetypes=[("Zip files", "*.zip"), ("All files", "*.*")])
        if not path:
            return
        try:
            engine.read_zip(path)
        except engine.ModpacksError as error:
            self.say(str(error), True)
            return
        name = self.ask_name("Import pack", "Name for the imported pack:",
                             os.path.splitext(os.path.basename(path))[0])
        if not name:
            return
        try:
            pack, added, skipped = engine.import_pack(self.game_dir, path, name)
        except engine.ModpacksError as error:
            self.say(str(error), True)
            return
        self.refresh(select=pack.name)
        text = "Imported %s with %d mods. It's Off until you turn it on." % (pack.name, len(added))
        servers = [n for n in added if engine.looks_server_only(n)]
        if servers:
            text += " Left unticked because they look like server mods: " + ", ".join(servers) + "."
        if skipped:
            text += " Left out: " + ", ".join(skipped) + "."
        self.say(text)

    def make_from_loose(self):
        dialog = LooseDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            pack, moved, created = dialog.result
            self.refresh(select=pack.name)
            text = "Moved %d mod%s into %s." % (len(moved), "" if len(moved) == 1 else "s", pack.name)
            if pack.mode == "all":
                text += " The pack is On everywhere, so they keep loading until you change that."
            else:
                text += " The pack is %s, so they follow that." % pack.describe()
            self.say(text)


class LooseDialog(tk.Toplevel):
    def __init__(self, owner):
        super().__init__(owner)
        _chrome._start_hidden(self)
        t = _theme
        self.owner = owner
        self.result = None
        self.keep = []
        self.title("Make a pack from loose mods")
        self.configure(bg=t.BG)
        self.transient(owner)
        plan = engine.Plan(owner.game_dir, None)
        self.names = [n for n in sorted(plan.loose.values(), key=str.lower) if not engine.is_launcher_mod(n)]
        tk.Label(self, text="Pick the loose mods to move into a pack. They leave the Mods folder and live in "
                 "the pack from now on. Nothing else moves.", bg=t.BG, fg=t.MUTED, font=("Segoe UI", 9),
                 wraplength=440, justify="left").pack(anchor="w", padx=16, pady=(12, 6))
        self.list = ScrollList(self, min(260, max(60, 24 * len(self.names) + 8)))
        self.list.pack(fill="both", expand=True, padx=16)
        self.vars = {}
        for name in self.names:
            title, detail = _mod_line(os.path.join(plan.mods_dir, name), name)
            var = tk.BooleanVar(value=False)
            self.keep.append(var)
            self.vars[name] = var
            tk.Checkbutton(self.list.inner, text="%s   %s" % (title, name), variable=var, bg=t.SURF, fg=t.PARCH,
                           activebackground=t.SURF, activeforeground=t.AMBER, selectcolor=t.AMBERDIM,
                           font=("Segoe UI", 9), highlightthickness=0, bd=0, anchor="w"
                           ).pack(anchor="w", padx=8, pady=1)
        target = tk.Frame(self, bg=t.BG)
        target.pack(fill="x", padx=16, pady=(8, 0))
        self.choice = tk.StringVar(value="new")
        self.keep.append(self.choice)
        tk.Radiobutton(target, text="New pack named", value="new", variable=self.choice, bg=t.BG, fg=t.PARCH,
                       activebackground=t.BG, selectcolor=t.AMBERDIM, font=("Segoe UI", 9),
                       highlightthickness=0).grid(row=0, column=0, sticky="w")
        self.new_name = tk.StringVar(value="My mods")
        tk.Entry(target, textvariable=self.new_name, bg=t.SURF, fg=t.PARCH, insertbackground=t.AMBER,
                 relief="flat", font=("Segoe UI", 9), bd=4).grid(row=0, column=1, sticky="we", padx=(6, 0))
        packs = [p.name for p in owner.packs]
        self.existing = tk.StringVar(value=packs[0] if packs else "")
        if packs:
            tk.Radiobutton(target, text="Existing pack", value="old", variable=self.choice, bg=t.BG, fg=t.PARCH,
                           activebackground=t.BG, selectcolor=t.AMBERDIM, font=("Segoe UI", 9),
                           highlightthickness=0).grid(row=1, column=0, sticky="w", pady=(4, 0))
            frame = tk.Frame(target, bg=t.SURF)
            frame.grid(row=1, column=1, sticky="we", padx=(6, 0), pady=(4, 0))
            _theme._mk_combobox(frame, self.existing, packs)
        target.columnconfigure(1, weight=1)
        self.error = tk.Label(self, text="", bg=t.BG, fg=t.RED, font=("Segoe UI", 9), wraplength=440,
                              justify="left")
        self.error.pack(anchor="w", padx=16, pady=(6, 0))
        row = tk.Frame(self, bg=t.BG)
        row.pack(fill="x", padx=16, pady=(6, 14))
        _theme._btn(row, "Move into pack", self.move, "primary", font=("Georgia", 10, "bold"),
                    pady=6, padx=14).pack(side="left")
        _theme._btn(row, "Cancel", self.destroy, font=("Segoe UI", 9), pady=6, padx=10).pack(side="left", padx=6)
        self.bind("<MouseWheel>", lambda e: self.list.wheel(e))
        self.update_idletasks()
        self.geometry("480x%d" % self.winfo_reqheight())
        _chrome._finish_dark_window(self)
        self.grab_set()

    def move(self):
        picked = [n for n in self.names if self.vars[n].get()]
        if not picked:
            self.error.configure(text="Tick at least one mod.")
            return
        game_dir = self.owner.game_dir
        created = False
        try:
            if self.choice.get() == "new":
                pack = engine.create_pack(game_dir, self.new_name.get())
                created = True
            else:
                pack = engine.find_pack(game_dir, self.existing.get())
                if pack is None:
                    raise engine.ModpacksError("Pick a pack.")
            if engine.game_running(game_dir):
                raise engine.ModpacksError("Close the game first. It's holding the mods open.")
            moved = engine.move_loose(game_dir, picked, pack)
        except (engine.ModpacksError, OSError) as error:
            if created:
                try:
                    if not pack.files():
                        os.remove(os.path.join(pack.path, engine.PACK_FILE))
                        os.rmdir(pack.path)
                except OSError:
                    pass
            self.error.configure(text=getattr(error, "strerror", None) or str(error))
            return
        self.result = (engine.Pack(pack.path), moved, created)
        self.destroy()


def open_window(root, game_dir):
    window = getattr(root, "_modpacks_window", None)
    if window is not None and window.winfo_exists():
        if engine.same_path(window.game_dir, game_dir):
            window.refresh()
            window.deiconify()
            window.lift()
            window.focus_force()
            return window
        window.destroy()
    window = ModpacksWindow(root, game_dir)
    root._modpacks_window = window
    return window
