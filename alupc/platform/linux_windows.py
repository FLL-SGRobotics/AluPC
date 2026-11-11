"""Programmfenster unter Linux auf Monitor 2 verschieben.

* KDE (X11 und Wayland): kleines KWin-Skript, das das aktive Fenster verschiebt.
* X11: zusätzlich Fensterliste und Verschieben über `wmctrl`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid

from . import dbus_util
from .base import WindowBackend, WindowInfo
from .linux_display import is_wayland

KWIN_SCRIPT = r"""
(function () {
    var targetName = %(name)s;
    var rect = %(rect)s;
    var fullscreen = %(fullscreen)s;
    var w = %(select)s;
    if (!w) { return; }
    if (workspace.screens !== undefined) {
        // Plasma 6: Monitore sind Objekte mit Namen
        for (var i = 0; i < workspace.screens.length; i++) {
            if (workspace.screens[i].name === targetName) {
                workspace.sendClientToScreen(w, workspace.screens[i]);
                break;
            }
        }
    } else {
        // Plasma 5: Monitore sind Nummern, über die Position finden
        for (var j = 0; j < workspace.numScreens; j++) {
            var a = workspace.clientArea(KWin.ScreenArea, j, workspace.currentDesktop);
            if (a.x === rect[0] && a.y === rect[1]) {
                workspace.sendClientToScreen(w, j);
                break;
            }
        }
    }
    if (fullscreen) { w.fullScreen = true; } else { w.setMaximize(true, true); }
})();
"""


# Bleibt aktiv: legt jedes (auch später erscheinende) Fenster mit passendem Titel auf den Monitor –
# z. B. UxPlay, dessen Fenster erst aufgeht, wenn sich ein iPhone verbindet.
KWIN_FOLLOW_SCRIPT = r"""
(function () {
    var titles = %(titles)s;
    var targetName = %(name)s;
    var rect = %(rect)s;
    function matches(w) {
        // nur UxPlays eigenes Fenster (Programmklasse) oder GENAU dieser Titel – kein Browser-Tab mit „AluPC“
        if (!w) { return false; }
        var cls = String(w.resourceClass || "").toLowerCase();
        if (cls.indexOf("uxplay") >= 0) { return true; }
        if (!w.caption) { return false; }
        var cap = String(w.caption).replace(/\u00a0/g, " ").trim();
        for (var t = 0; t < titles.length; t++) {
            if (cap === String(titles[t]).replace(/\u00a0/g, " ").trim()) { return true; }
        }
        return false;
    }
    function place(w) {
        if (!matches(w)) { return; }
        if (workspace.screens !== undefined) {
            for (var i = 0; i < workspace.screens.length; i++) {
                if (workspace.screens[i].name === targetName) { workspace.sendClientToScreen(w, workspace.screens[i]); break; }
            }
        } else {
            for (var j = 0; j < workspace.numScreens; j++) {
                var a = workspace.clientArea(KWin.ScreenArea, j, workspace.currentDesktop);
                if (a.x === rect[0] && a.y === rect[1]) { workspace.sendClientToScreen(w, j); break; }
            }
        }
        if (%(fullscreen)s) { w.fullScreen = true; } else { w.setMaximize(true, true); }
        w.keepAbove = true;  // über dem Warte-Bildschirm von AluPC
        // nach vorne holen – sonst legt KWin ein neues Fenster eines Hintergrund-Programms ggf. unter das aktive
        if (workspace.activeWindow !== undefined) { workspace.activeWindow = w; } else { workspace.activeClient = w; }
    }
    function watch(w) {
        if (!w) { return; }
        place(w);
        if (w.captionChanged !== undefined) { w.captionChanged.connect(function () { place(w); }); }
    }
    var list = (workspace.windowList !== undefined) ? workspace.windowList() : workspace.clientList();
    for (var k = 0; k < list.length; k++) { watch(list[k]); }
    if (workspace.windowAdded !== undefined) { workspace.windowAdded.connect(watch); }
    else { workspace.clientAdded.connect(watch); }
})();
"""


def build_follow_script(titles: list[str], output_name: str, rect: tuple[int, int, int, int],
                        fullscreen: bool = True) -> str:
    return KWIN_FOLLOW_SCRIPT % {"titles": json.dumps([t for t in titles if t]), "name": json.dumps(output_name),
                                 "rect": json.dumps(list(rect)), "fullscreen": "true" if fullscreen else "false"}


ACTIVE_WINDOW = "(workspace.activeWindow !== undefined) ? workspace.activeWindow : workspace.activeClient"
WINDOW_BY_CAPTION = r"""(function (part) {
        var list = (workspace.windowList !== undefined) ? workspace.windowList() : workspace.clientList();
        for (var k = 0; k < list.length; k++) {
            if (list[k].caption && list[k].caption.indexOf(part) >= 0) { return list[k]; }
        }
        return null;
    })(%s)"""


def build_kwin_script(output_name: str, rect: tuple[int, int, int, int], fullscreen: bool,
                      caption: str | None = None) -> str:
    """KWin-Skript: aktives Fenster (oder das mit `caption` im Titel) auf den Monitor schieben."""
    return KWIN_SCRIPT % {
        "select": WINDOW_BY_CAPTION % json.dumps(caption) if caption else ACTIVE_WINDOW,
        "name": json.dumps(output_name),
        "rect": json.dumps(list(rect)),
        "fullscreen": "true" if fullscreen else "false",
    }


def start_kwin_script(source: str) -> str:
    """KWin-Skript laden und starten; es läuft weiter, bis `stop_kwin_script(name)` es entfernt."""
    plugin = f"alupc_{uuid.uuid4().hex[:8]}"
    fd, path = tempfile.mkstemp(prefix="alupc-", suffix=".js")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(source)
    iface = "org.kde.kwin.Scripting"
    try:
        with dbus_util.connect("SESSION") as conn:
            (script_id,) = dbus_util.call(conn, "org.kde.KWin", "/Scripting", iface,
                                          "loadScript", "ss", (path, plugin))
            if script_id < 0:
                raise RuntimeError("KWin konnte das Skript nicht laden")
            last_error = None
            # Plasma 6 und Plasma 5 benutzen unterschiedliche Objektpfade
            for obj in (f"/Scripting/Script{script_id}", f"/{script_id}"):
                try:
                    dbus_util.call(conn, "org.kde.KWin", obj, "org.kde.kwin.Script", "run")
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
            else:
                stop_kwin_script(plugin)
                raise RuntimeError(f"KWin-Skript konnte nicht gestartet werden: {last_error}")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return plugin


def stop_kwin_script(plugin: str) -> None:
    try:
        with dbus_util.connect("SESSION") as conn:
            dbus_util.call(conn, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s",
                           (plugin,))
    except Exception:  # noqa: BLE001
        pass


def run_kwin_script(source: str) -> None:
    """KWin-Skript einmal ausführen (und gleich wieder entfernen)."""
    stop_kwin_script(start_kwin_script(source))


def parse_wmctrl(text: str) -> list[WindowInfo]:
    windows = []
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        win_id, desktop, wm_class, _host, title = parts
        if desktop == "-1":  # Leisten, Desktop usw.
            continue
        app = wm_class.split(".")[-1]
        windows.append(WindowInfo(id=win_id, title=title, app=app))
    return windows


class LinuxWindowBackend(WindowBackend):
    def __init__(self):
        self.kde = "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper() \
            or shutil.which("kwin_wayland") is not None or shutil.which("kwin_x11") is not None
        self.wmctrl = shutil.which("wmctrl") is not None and not is_wayland()
        self.can_list = self.wmctrl
        self.can_move_active = self.kde and dbus_util.HAVE_JEEPNEY

    def list_windows(self) -> list[WindowInfo]:
        if not self.wmctrl:
            return []
        out = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, timeout=10).stdout
        return parse_wmctrl(out)

    def move_window(self, window_id, output_name, rect, fullscreen=False):
        x, y, w, h = rect
        base = ["wmctrl", "-i", "-r", window_id]
        subprocess.run(base + ["-b", "remove,maximized_vert,maximized_horz,fullscreen"], timeout=10)
        subprocess.run(base + ["-e", f"0,{x + 20},{y + 20},{max(200, w // 2)},{max(150, h // 2)}"], timeout=10)
        state = "fullscreen" if fullscreen else "maximized_vert,maximized_horz"
        subprocess.run(base + ["-b", f"add,{state}"], timeout=10)
        subprocess.run(["wmctrl", "-i", "-a", window_id], timeout=10)

    def move_active_window(self, output_name, rect, fullscreen=False):
        if not self.can_move_active:
            raise RuntimeError("Nur unter KDE Plasma möglich (KWin)")
        run_kwin_script(build_kwin_script(output_name, rect, fullscreen))

    def follow_windows(self, titles, output_name, rect, fullscreen=True):
        """KDE: KWin-Skript bleibt aktiv und legt passende Fenster sofort beim Erscheinen auf den Monitor."""
        if self.kde and dbus_util.HAVE_JEEPNEY:
            try:
                return start_kwin_script(build_follow_script(list(titles), output_name, rect, fullscreen))
            except Exception:  # noqa: BLE001
                return None
        return None

    def stop_follow(self, token) -> None:
        if token:
            stop_kwin_script(token)

    def move_by_title(self, title_part, output_name, rect, fullscreen=True) -> bool:
        for w in self.list_windows():
            if title_part in w.title:
                self.move_window(w.id, output_name, rect, fullscreen)
                return True
        return False


PLACE_SCRIPT = r"""
(function () {
    var list = (workspace.windowList !== undefined) ? workspace.windowList() : workspace.clientList();
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        if (w && String(w.caption) === %(caption)s) {
            w.frameGeometry = {x: %(x)d, y: %(y)d, width: %(w)d, height: %(h)d};
            w.keepAbove = true;
        }
    }
})();
"""


def build_place_script(caption: str, x: int, y: int, w: int, h: int) -> str:
    return PLACE_SCRIPT % {"caption": json.dumps(caption), "x": x, "y": y, "w": w, "h": h}


def kwin_place_window(caption: str, x: int, y: int, w: int, h: int) -> bool:
    """KDE/Wayland: ein eigenes Fenster an eine feste Stelle legen (Wayland erlaubt das Programmen nicht selbst)."""
    if not dbus_util.HAVE_JEEPNEY:
        return False
    try:
        run_kwin_script(build_place_script(caption, x, y, w, h))
        return True
    except Exception:  # noqa: BLE001
        return False


PROPS_SCRIPT = r"""
(function () {
    var list = (workspace.windowList !== undefined) ? workspace.windowList() : workspace.clientList();
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        if (w && String(w.caption) === %(caption)s) {
            w.keepBelow = %(below)s;
            w.keepAbove = %(above)s;
            w.skipTaskbar = true;
            w.skipSwitcher = true;
            w.skipPager = true;
            if (%(geo)s) { w.frameGeometry = %(geo)s; }
        }
    }
})();
"""

ACTIVATE_SCRIPT = r"""
(function () {
    var list = (workspace.windowList !== undefined) ? workspace.windowList() : workspace.clientList();
    for (var i = 0; i < list.length; i++) {
        var w = list[i];
        if (w && String(w.internalId) === %(id)s) {
            w.minimized = false;
            if (workspace.activeWindow !== undefined) { workspace.activeWindow = w; } else { workspace.activeClient = w; }
        }
    }
})();
"""


def build_props_script(caption: str, below: bool, above: bool, geo: tuple[int, int, int, int] | None) -> str:
    g = "null" if geo is None else json.dumps({"x": geo[0], "y": geo[1], "width": geo[2], "height": geo[3]})
    return PROPS_SCRIPT % {"caption": json.dumps(caption), "below": json.dumps(below), "above": json.dumps(above),
                           "geo": g}


def kwin_window_props(caption: str, below: bool = False, above: bool = False,
                      geo: tuple[int, int, int, int] | None = None) -> bool:
    """KDE: eigenes Fenster ganz unten/oben halten, aus Taskleiste und Alt+Tab nehmen, an eine Stelle legen
    (unter Wayland dürfen Programme das nicht selbst – AluPC-Oberfläche als Desktop braucht es)."""
    if not dbus_util.HAVE_JEEPNEY:
        return False
    try:
        run_kwin_script(build_props_script(caption, below, above, geo))
        return True
    except Exception:  # noqa: BLE001
        return False


def _x_ignore(display, event) -> int:
    return 0


try:
    import ctypes as _ct

    _X_IGNORE = _ct.CFUNCTYPE(_ct.c_int, _ct.c_void_p, _ct.c_void_p)(_x_ignore)  # bleibt am Leben (Rückruf aus C)
except (ImportError, AttributeError):  # pragma: no cover
    _X_IGNORE = None


