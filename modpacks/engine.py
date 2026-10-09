import ctypes
import hashlib
import json
import os
import re
import shutil
import socket
import zipfile
from ctypes import wintypes

from . import melon

PACKS_DIR = "Modpacks"
REMOVED_DIR = "_removed"
PACK_FILE = "pack.json"
LEDGER_FILE = "modpacks_ledger.json"
TEMP_SUFFIX = ".modpacks-tmp"
OLD_SUFFIX = ".modpacks-old"
GAME_EXE = "A Township Tale.exe"
DEFAULT_PORT = 1757
MODES = ("off", "all", "servers")
MAX_ENTRY = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}


class ModpacksError(Exception):
    pass


class ConflictError(ModpacksError):
    def __init__(self, conflicts):
        super().__init__("; ".join(conflicts))
        self.conflicts = conflicts


def data_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "TheModdingTavern")


def ledger_file():
    return os.path.join(data_dir(), LEDGER_FILE)


def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_dll(name):
    return name.lower().endswith(".dll")


def is_launcher_mod(name):
    low = name.lower()
    return low == "tavernessentials.dll" or (low.startswith("circuitsvoicechat") and low.endswith(".dll"))


def looks_server_only(name):
    return name.lower().endswith(".server.dll")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


_info_cache = {}


def mod_info(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = os.path.normcase(os.path.abspath(path))
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _info_cache.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    info = melon.read_info(path)
    _info_cache[key] = (stamp, info)
    return info


def check_name(name):
    name = name.strip()
    if not name:
        return "Give the pack a name."
    if len(name) > 60:
        return "Pack names can be at most 60 characters."
    if BAD_NAME.search(name):
        return 'Pack names can\'t use any of these: < > : " / \\ | ? *'
    if name.startswith(("_", ".")):
        return "Pack names can't start with _ or a dot."
    if name.endswith((".", " ")):
        return "Pack names can't end with a dot."
    if name.split(".")[0].strip().lower() in RESERVED:
        return "Windows doesn't allow that name. Pick another one."
    return None


def _list(value):
    return value if isinstance(value, list) else []


def parse_port(value):
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def resolve(host):
    host = str(host).strip()
    try:
        return socket.gethostbyname(host)
    except (OSError, UnicodeError, ValueError):
        return host.lower()


def target_from_args(args):
    args = [str(a) for a in args]
    lowered = [a.lower() for a in args]

    def value(flag):
        if flag in lowered:
            index = lowered.index(flag)
            if index + 1 < len(args):
                return args[index + 1]
        return None

    ip = value("/dev_server_ip")
    if ip is None and "/join_local_server" in lowered:
        ip = "127.0.0.1"
    if ip is None:
        return None
    return ip.strip(), parse_port(value("/dev_server_port"))


def server_key(host, port):
    return str(host).strip().lower(), parse_port(port)


class Pack:
    def __init__(self, folder):
        self.path = folder
        self.name = os.path.basename(folder)
        data = {}
        try:
            with open(os.path.join(folder, PACK_FILE), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        mode = data.get("mode")
        self.mode = mode if mode in MODES else "off"
        self.servers = []
        seen = set()
        for entry in _list(data.get("servers")):
            if not isinstance(entry, dict) or not str(entry.get("host", "")).strip():
                continue
            key = server_key(entry["host"], entry.get("port"))
            if key in seen:
                continue
            seen.add(key)
            self.servers.append({"name": str(entry.get("name") or entry["host"]).strip(),
                                 "host": str(entry["host"]).strip(), "port": str(key[1])})
        self.off = {n.lower() for n in _list(data.get("off")) if isinstance(n, str)}
        self.on = {n.lower() for n in _list(data.get("on")) if isinstance(n, str)}
        self.source = data.get("source") if isinstance(data.get("source"), dict) else {}

    def files(self):
        try:
            names = os.listdir(self.path)
        except OSError:
            return []
        return sorted((n for n in names if is_dll(n) and os.path.isfile(os.path.join(self.path, n))),
                      key=str.lower)

    def ticked(self, name):
        if looks_server_only(name):
            return name.lower() in self.on
        return name.lower() not in self.off

    def set_ticked(self, name, value):
        low = name.lower()
        if looks_server_only(name):
            self.on.add(low) if value else self.on.discard(low)
        else:
            self.off.discard(low) if value else self.off.add(low)

    def links(self, target, lookup):
        if self.mode == "all":
            return True
        if self.mode != "servers" or target is None:
            return False
        ip, port = target
        for server in self.servers:
            if parse_port(server["port"]) != port:
                continue
            if server["host"].lower() == ip.lower() or lookup(server["host"]) == ip:
                return True
        return False

    def describe(self):
        text = self._mode_text()
        host = self.link_host()
        return text + ("  ·  updates from " + host if host else "")

    def _mode_text(self):
        if self.mode == "all":
            return "On everywhere"
        if self.mode == "servers":
            if not self.servers:
                return "Only on chosen servers (none chosen yet)"
            return "Only on " + ", ".join(s["name"] for s in self.servers)
        return "Off"

    def state(self):
        state = {"format": 1, "mode": self.mode, "servers": self.servers,
                 "off": sorted(self.off), "on": sorted(self.on)}
        if self.source:
            state["source"] = self.source
        return state

    def link_host(self):
        url = self.source.get("url") if isinstance(self.source.get("url"), str) else ""
        return url.split("/")[2].split(":")[0].lower() if url.count("/") >= 2 else ""

    def save(self):
        write_json(os.path.join(self.path, PACK_FILE), self.state())


def write_json(path, data):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def packs_dir(game_dir):
    return os.path.join(game_dir, PACKS_DIR)


def list_packs(game_dir):
    root = packs_dir(game_dir)
    try:
        names = os.listdir(root)
    except OSError:
        return []
    packs = [Pack(os.path.join(root, n)) for n in names
             if not n.startswith(("_", ".")) and os.path.isdir(os.path.join(root, n))]
    return sorted(packs, key=lambda p: p.name.lower())


def find_pack(game_dir, name):
    for pack in list_packs(game_dir):
        if pack.name.lower() == name.lower():
            return pack
    return None


def read_ledger(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ledger_key(mods_dir):
    return os.path.normcase(os.path.abspath(mods_dir))


def loose_files(mods_dir):
    try:
        names = os.listdir(mods_dir)
    except OSError:
        return []
    return sorted((n for n in names if is_dll(n) and os.path.isfile(os.path.join(mods_dir, n))),
                  key=str.lower)


class Plan:
    def __init__(self, game_dir, target, ledger_path=None, lookup=None):
        self.game_dir = game_dir
        self.target = target
        self.mods_dir = os.path.join(game_dir, "Mods")
        self.ledger_path = ledger_path or ledger_file()
        lookup_cache = {}

        def cached(host):
            key = host.strip().lower()
            if key not in lookup_cache:
                lookup_cache[key] = (lookup or resolve)(host)
            return lookup_cache[key]

        self.lookup = cached
        self.conflicts = []
        self.notes = []
        self.packs = list_packs(game_dir)
        self.active = [p for p in self.packs if p.links(target, cached)]
        self._read_mods()
        self._want()
        self._check_names()
        self._changes()

    def _read_mods(self):
        ledger = read_ledger(self.ledger_path)
        self.ledger = ledger
        self.recorded = [e for e in ledger.get(ledger_key(self.mods_dir)) or []
                         if isinstance(e, dict) and isinstance(e.get("file"), str)
                         and isinstance(e.get("sha256"), str)]
        on_disk = {n.lower(): n for n in loose_files(self.mods_dir)}
        self.managed = {}
        self.released = []
        for entry in self.recorded:
            low = entry["file"].lower()
            if low not in on_disk or low in self.managed:
                continue
            try:
                current = sha256(os.path.join(self.mods_dir, on_disk[low]))
            except OSError:
                current = None
            if current == entry["sha256"].upper():
                self.managed[low] = dict(entry, file=on_disk[low])
            else:
                self.released.append(on_disk[low])
        self.loose = {}
        for low, name in on_disk.items():
            if low not in self.managed:
                self.loose[low] = name

    def _want(self):
        self.wanted = {}
        for pack in self.active:
            for name in pack.files():
                if not pack.ticked(name):
                    continue
                if is_launcher_mod(name):
                    self.notes.append("%s in pack %s is the launcher's own mod, so it was left out."
                                      % (name, pack.name))
                    continue
                path = os.path.join(pack.path, name)
                try:
                    digest = sha256(path)
                except OSError as error:
                    self.conflicts.append("%s in pack %s can't be read (%s)" % (name, pack.name, _why(error)))
                    continue
                low = name.lower()
                have = self.wanted.get(low)
                if have is None:
                    self.wanted[low] = {"file": name, "source": path, "sha256": digest,
                                        "pack": pack.name, "packs": [pack.name]}
                elif have["sha256"] != digest:
                    self.conflicts.append("%s is in packs %s and %s, and the two files are different"
                                          % (name, have["pack"], pack.name))
                else:
                    have["packs"].append(pack.name)
        self.identical = []
        for low, item in sorted(self.wanted.items()):
            if low not in self.loose:
                continue
            try:
                digest = sha256(os.path.join(self.mods_dir, self.loose[low]))
            except OSError:
                digest = None
            if digest == item["sha256"]:
                self.identical.append(low)
            else:
                self.conflicts.append("%s from pack %s is also loose in Mods as a different file"
                                      % (item["file"], item["pack"]))

    def _check_names(self):
        loading = {}
        for low, name in self.loose.items():
            loading[low] = (name, "loose in Mods", os.path.join(self.mods_dir, name))
        for low, item in self.wanted.items():
            if low not in loading:
                loading[low] = (item["file"], "pack " + item["pack"], item["source"])
        by_name = {}
        for low, (name, where, path) in sorted(loading.items()):
            info = mod_info(path)
            if info and info["name"]:
                by_name.setdefault(info["name"].lower(), (info["name"], []))[1].append((name, where))
        for title, places in by_name.values():
            if len(places) > 1 and any(where.startswith("pack ") for _, where in places):
                self.conflicts.append("%s would load twice: %s" % (
                    title, ", ".join("%s (%s)" % place for place in places)))

    def _changes(self):
        self.remove = []
        self.add = []
        self.keep = []
        for low, entry in sorted(self.managed.items()):
            item = self.wanted.get(low)
            if item is not None and item["sha256"] == entry["sha256"].upper():
                self.keep.append(dict(entry, pack=item["pack"]))
            else:
                self.remove.append(entry)
        for low, item in sorted(self.wanted.items()):
            if low in self.identical:
                continue
            if low in self.managed and self.managed[low]["sha256"].upper() == item["sha256"]:
                continue
            if low in self.loose:
                continue
            self.add.append(item)

    def summary(self):
        if not self.active:
            return "Modpacks: none"
        count = len(self.wanted)
        return "Modpacks: %s (%d mod%s)" % (", ".join(p.name for p in self.active), count,
                                           "" if count == 1 else "s")


def _why(error):
    return error.strerror or str(error)


def unique_path(folder, name):
    path = os.path.join(folder, name)
    stem, ext = os.path.splitext(name)
    number = 2
    while os.path.exists(path):
        path = os.path.join(folder, "%s (%d)%s" % (stem, number, ext))
        number += 1
    return path


def pack_hashes(game_dir):
    found = set()
    for pack in list_packs(game_dir):
        for name in pack.files():
            try:
                found.add(sha256(os.path.join(pack.path, name)))
            except OSError:
                pass
        found.update(str(s).upper() for s in _list(pack.source.get("seen")))
    return found


def _put_away(game_dir, path, hashes, original_name):
    try:
        digest = sha256(path)
    except OSError:
        digest = None
    if digest is not None and digest in hashes:
        os.remove(path)
        return None
    folder = os.path.join(packs_dir(game_dir), REMOVED_DIR)
    os.makedirs(folder, exist_ok=True)
    destination = unique_path(folder, original_name)
    shutil.move(path, destination)
    return destination


def clean_leftovers(game_dir, mods_dir, hashes):
    moved = []
    try:
        names = os.listdir(mods_dir)
    except OSError:
        return moved
    for name in names:
        path = os.path.join(mods_dir, name)
        try:
            if name.lower().endswith(TEMP_SUFFIX):
                os.remove(path)
            elif name.lower().endswith(OLD_SUFFIX):
                kept = _put_away(game_dir, path, hashes, name[:-len(OLD_SUFFIX)])
                if kept:
                    moved.append(kept)
        except OSError:
            pass
    return moved


class Result:
    def __init__(self, plan):
        self.plan = plan
        self.added = []
        self.removed = []
        self.kept_aside = []
        self.warnings = []
        self.skipped = None


def _entry(item):
    return {"file": item["file"], "sha256": item["sha256"], "pack": item["pack"]}


def apply(game_dir, target, ledger_path=None, lookup=None, running=None):
    mods_dir = os.path.join(game_dir, "Mods")
    if (running or game_running)(game_dir):
        result = Result(None)
        result.skipped = "running"
        return result
    if not os.path.isdir(mods_dir):
        result = Result(None)
        result.skipped = "no-mods"
        return result
    path = ledger_path or ledger_file()
    hashes = pack_hashes(game_dir)
    leftovers = clean_leftovers(game_dir, mods_dir, hashes)
    plan = Plan(game_dir, target, path, lookup)
    if plan.conflicts:
        raise ConflictError(plan.conflicts)
    result = Result(plan)
    result.kept_aside.extend(leftovers)
    key = ledger_key(mods_dir)
    ledger = dict(plan.ledger)
    before = [dict(e) for e in plan.managed.values()]
    leaving = {e["file"].lower() for e in plan.remove}
    packs = {e["file"].lower(): e["pack"] for e in plan.keep}
    intent = [dict(e, pack=packs.get(e["file"].lower(), e.get("pack", ""))) for e in before
              if e["file"].lower() not in leaving] + [_entry(item) for item in plan.add]
    if not plan.add and not plan.remove:
        if ledger.get(key) != intent:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ledger[key] = intent
            write_json(path, ledger)
        return result
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ledger[key] = before + [_entry(item) for item in plan.add]
    write_json(path, ledger)
    staged = []
    placed = []
    temp = None
    try:
        for entry in plan.remove:
            live = os.path.join(mods_dir, entry["file"])
            os.replace(live, live + OLD_SUFFIX)
            staged.append((live, entry))
        for item in plan.add:
            live = os.path.join(mods_dir, item["file"])
            if os.path.lexists(live + TEMP_SUFFIX):
                raise OSError(17, "an old temporary file is in the way", live + TEMP_SUFFIX)
            temp = live + TEMP_SUFFIX
            shutil.copyfile(item["source"], temp)
            if sha256(temp) != item["sha256"]:
                raise OSError(5, "the file changed while it was being copied", item["source"])
            if os.path.exists(live):
                raise OSError(17, "a file with that name appeared in Mods", live)
            os.replace(temp, live)
            temp = None
            placed.append(live)
    except OSError as error:
        problems = _rollback(staged, placed, temp)
        if not problems:
            ledger[key] = before
            write_json(path, ledger)
        name = os.path.basename(error.filename or "") or "A mod file"
        for suffix in (TEMP_SUFFIX, OLD_SUFFIX):
            if name.lower().endswith(suffix):
                name = name[:-len(suffix)]
        message = "%s couldn't be changed (%s). Nothing was changed. If the game is open, close it and try again." % (
            name, _why(error))
        if problems:
            message = "%s couldn't be changed (%s), and these couldn't be put back: %s" % (
                name, _why(error), ", ".join(problems))
        raise ModpacksError(message)
    ledger[key] = intent
    write_json(path, ledger)
    result.added = [item["file"] for item in plan.add]
    for live, entry in staged:
        result.removed.append(entry["file"])
        try:
            kept = _put_away(game_dir, live + OLD_SUFFIX, hashes, entry["file"])
            if kept:
                result.kept_aside.append(kept)
        except OSError as error:
            result.warnings.append("%s couldn't be cleaned up (%s)" % (entry["file"], _why(error)))
    return result


def _rollback(staged, placed, temp):
    problems = []
    for path in ([temp] if temp else []) + list(reversed(placed)):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            problems.append(os.path.basename(path))
    for live, entry in reversed(staged):
        try:
            os.replace(live + OLD_SUFFIX, live)
        except OSError:
            problems.append(entry["file"])
    return problems


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = (ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    return kernel32


def _process_path(kernel32, pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def game_processes(exe_name=GAME_EXE):
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError("process list unavailable")
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == exe_name.lower():
                found.append((entry.th32ProcessID, _process_path(kernel32, entry.th32ProcessID)))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def game_running(game_dir, exe_name=GAME_EXE):
    try:
        processes = game_processes(exe_name)
    except (OSError, AttributeError, ValueError):
        return False
    for pid, path in processes:
        if path is None or same_path(os.path.dirname(path), game_dir):
            return True
    return False


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


def recycle(path):
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHFileOperationW.argtypes = (ctypes.POINTER(SHFILEOPSTRUCTW),)
    shell32.SHFileOperationW.restype = ctypes.c_int
    operation = SHFILEOPSTRUCTW()
    operation.wFunc = 3
    operation.pFrom = os.path.abspath(path) + "\0"
    operation.fFlags = 0x40 | 0x10 | 0x4 | 0x400
    code = shell32.SHFileOperationW(ctypes.byref(operation))
    if code != 0 or operation.fAnyOperationsAborted or os.path.exists(path):
        raise OSError("Windows couldn't move it to the Recycle Bin (code %d)" % code)


def create_pack(game_dir, name, mode="all"):
    name = name.strip()
    problem = check_name(name)
    if problem:
        raise ModpacksError(problem)
    if find_pack(game_dir, name):
        raise ModpacksError("There's already a pack called %s." % name)
    folder = os.path.join(packs_dir(game_dir), name)
    os.makedirs(folder)
    pack = Pack(folder)
    pack.mode = mode
    pack.save()
    return pack


def rename_pack(game_dir, pack, new_name):
    new_name = new_name.strip()
    problem = check_name(new_name)
    if problem:
        raise ModpacksError(problem)
    other = find_pack(game_dir, new_name)
    if other is not None and not same_path(other.path, pack.path):
        raise ModpacksError("There's already a pack called %s." % new_name)
    target = os.path.join(packs_dir(game_dir), new_name)
    os.rename(pack.path, target)
    return Pack(target)


def add_files(pack, paths):
    added, refused = [], []
    for source in paths:
        name = os.path.basename(source)
        if not is_dll(name):
            refused.append("%s (not a .dll)" % name)
            continue
        if is_launcher_mod(name):
            refused.append("%s (the launcher manages it)" % name)
            continue
        target = os.path.join(pack.path, name)
        if same_path(source, target):
            continue
        temp = target + TEMP_SUFFIX
        try:
            shutil.copyfile(source, temp)
            os.replace(temp, target)
        except OSError as error:
            refused.append("%s (%s)" % (name, _why(error)))
            try:
                os.remove(temp)
            except OSError:
                pass
            continue
        added.append(name)
        pack.on.discard(name.lower())
    pack.save()
    return added, refused


def move_loose(game_dir, names, pack):
    mods_dir = os.path.join(game_dir, "Mods")
    moved = []
    try:
        for name in names:
            if is_launcher_mod(name) or not is_dll(name):
                raise ModpacksError("%s can't go in a pack." % name)
            source = os.path.join(mods_dir, name)
            target = os.path.join(pack.path, name)
            if os.path.exists(target):
                if sha256(target) != sha256(source):
                    raise ModpacksError("Pack %s already has a different %s." % (pack.name, name))
                os.remove(source)
                moved.append((source, None))
                continue
            os.rename(source, target)
            moved.append((source, target))
    except (OSError, ModpacksError) as error:
        for source, target in reversed(moved):
            if target is not None:
                try:
                    os.rename(target, source)
                except OSError:
                    pass
            else:
                try:
                    shutil.copyfile(os.path.join(pack.path, os.path.basename(source)), source)
                except OSError:
                    pass
        if isinstance(error, ModpacksError):
            raise
        raise ModpacksError("%s couldn't be moved (%s). If the game is open, close it and try again."
                            % (os.path.basename(error.filename or "") or "A mod", _why(error)))
    return [os.path.basename(source) for source, _ in moved]


def unpack(game_dir, pack, ledger_path=None):
    if game_running(game_dir):
        raise ModpacksError("Close the game first. It's holding the mods open.")
    path = ledger_path or ledger_file()
    plan = Plan(game_dir, None, path)
    mods_dir = plan.mods_dir
    steps, refused, release = [], [], set()
    for name in pack.files():
        low = name.lower()
        source = os.path.join(pack.path, name)
        if is_launcher_mod(name):
            refused.append("%s (the launcher manages it)" % name)
            continue
        digest = sha256(source)
        if low in plan.managed:
            if plan.managed[low]["sha256"].upper() == digest:
                release.add(low)
                steps.append((name, source, None))
            else:
                refused.append("%s (start the game once first, then try again)" % name)
            continue
        if low in plan.loose:
            if sha256(os.path.join(mods_dir, plan.loose[low])) == digest:
                steps.append((name, source, None))
            else:
                refused.append("%s (Mods already has a different %s)" % (name, name))
            continue
        steps.append((name, source, os.path.join(mods_dir, name)))
    if release:
        key = ledger_key(mods_dir)
        ledger = dict(plan.ledger)
        ledger[key] = [dict(e) for low, e in plan.managed.items() if low not in release]
        write_json(path, ledger)
    moved = []
    for name, source, target in steps:
        try:
            if target is None:
                os.remove(source)
            else:
                os.rename(source, target)
            moved.append(name)
        except OSError as error:
            refused.append("%s (%s)" % (name, _why(error)))
    if not pack.files():
        try:
            os.remove(os.path.join(pack.path, PACK_FILE))
            os.rmdir(pack.path)
        except OSError:
            pass
    return moved, refused


def export_pack(pack, zip_path):
    state = pack.state()
    shared = {"format": 1, "off": state["off"]}
    temp = zip_path + TEMP_SUFFIX
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(PACK_FILE, json.dumps(shared, indent=2))
            for name in pack.files():
                archive.write(os.path.join(pack.path, name), name)
        os.replace(temp, zip_path)
    except OSError:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise
    return len(pack.files())


def _unsafe(name):
    clean = name.replace("\\", "/")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    return (clean.startswith("/") or re.match(r"^[A-Za-z]:", clean) is not None
            or ".." in parts or ":" in clean)


def read_zip(zip_path):
    try:
        archive = zipfile.ZipFile(zip_path)
    except Exception as error:
        raise ModpacksError("That isn't a zip file Modpacks can read (%s)." % error)
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        unsafe = [i.filename for i in archive.infolist() if _unsafe(i.filename)]
        if unsafe:
            raise ModpacksError("The zip was refused because it has unsafe paths: " + ", ".join(unsafe[:5]))
        tops = {i.filename.replace("\\", "/").split("/")[0] for i in infos}
        wrapper = None
        if len(tops) == 1 and all("/" in i.filename.replace("\\", "/") for i in infos):
            wrapper = tops.pop()
        entries, skipped = [], []
        total = 0
        seen = set()
        for info in infos:
            name = info.filename.replace("\\", "/")
            if wrapper:
                name = name[len(wrapper) + 1:]
            if "/" in name:
                skipped.append("%s (inside a folder)" % name)
                continue
            if name.lower() != PACK_FILE and not is_dll(name):
                skipped.append("%s (not a .dll)" % name)
                continue
            if check_name(name.rsplit(".", 1)[0] or "x") and name.lower() != PACK_FILE:
                skipped.append("%s (not a file name Windows allows)" % name)
                continue
            if name.lower() in seen:
                raise ModpacksError("The zip was refused because it has %s twice." % name)
            seen.add(name.lower())
            if info.file_size > MAX_ENTRY:
                raise ModpacksError("The zip was refused because %s is too big." % name)
            total += info.file_size
            if total > MAX_TOTAL:
                raise ModpacksError("The zip was refused because it's too big.")
            entries.append((info, name))
    return entries, skipped


def import_pack(game_dir, zip_path, name):
    name = name.strip()
    problem = check_name(name)
    if problem:
        raise ModpacksError(problem)
    if find_pack(game_dir, name):
        raise ModpacksError("There's already a pack called %s." % name)
    entries, skipped = read_zip(zip_path)
    root = packs_dir(game_dir)
    os.makedirs(root, exist_ok=True)
    work = unique_path(root, "_importing")
    os.makedirs(work)
    added = []
    try:
        shared = {}
        with zipfile.ZipFile(zip_path) as archive:
            for info, entry_name in entries:
                if entry_name.lower() == PACK_FILE:
                    try:
                        shared = json.loads(archive.read(info).decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        shared = {}
                    continue
                if is_launcher_mod(entry_name):
                    skipped.append("%s (the launcher manages it)" % entry_name)
                    continue
                with archive.open(info) as source, open(os.path.join(work, entry_name), "wb") as target:
                    copied = 0
                    for chunk in iter(lambda: source.read(1 << 20), b""):
                        copied += len(chunk)
                        if copied > MAX_ENTRY:
                            raise ModpacksError("The zip was refused because %s is too big." % entry_name)
                        target.write(chunk)
                added.append(entry_name)
        names = {n.lower() for n in added}
        shared_off = _list(shared.get("off")) if isinstance(shared, dict) else []
        pack = Pack(work)
        pack.mode = "off"
        pack.servers = []
        pack.on = set()
        pack.off = {n.lower() for n in shared_off if isinstance(n, str) and n.lower() in names}
        pack.save()
        final = os.path.join(root, name)
        os.rename(work, final)
    except Exception as error:
        shutil.rmtree(work, ignore_errors=True)
        if isinstance(error, ModpacksError):
            raise
        raise ModpacksError("The import failed (%s)." % (getattr(error, "strerror", None) or error))
    return Pack(final), added, skipped
