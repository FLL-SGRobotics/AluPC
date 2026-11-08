"""Programmfenster unter Windows auflisten und auf Monitor 2 verschieben (Win32)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .base import WindowBackend, WindowInfo
from .windows_display import monitor_rect

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
SW_RESTORE = 9
SW_MAXIMIZE = 3
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_BOTTOM = 1
HWND_TOPMOST = -1
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZE = 0x20000000
WS_MAXIMIZE = 0x01000000
SWP_FRAMECHANGED = 0x0020
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Fenster der Windows-Oberfläche selbst (Desktop „Program Manager“, Taskleisten …)
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow",
                 "ApplicationFrameInputSinkWindow", "Internet Explorer_Hidden"}


class WindowsWindowBackend(WindowBackend):
    can_list = True
    can_move_active = True
    can_restore_background = True

    def __init__(self):
        u = self.user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        HWND = wintypes.HWND
        # Typen festlegen, damit 64-Bit-Fensterhandles nicht abgeschnitten werden
        u.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [HWND, ctypes.c_int, ctypes.c_long]
        u.SetWindowLongW.restype = ctypes.c_long
        u.GetWindow.argtypes = [HWND, wintypes.UINT]
        u.GetWindow.restype = HWND
        u.GetForegroundWindow.restype = HWND
        u.IsWindowVisible.argtypes = [HWND]
        u.GetWindowTextLengthW.argtypes = [HWND]
        u.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
        u.ShowWindow.argtypes = [HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.UINT]
        u.SetForegroundWindow.argtypes = [HWND]
        u.IsIconic.argtypes = [HWND]
        u.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
        k = self.kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
        try:
            self.dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
            self.dwm.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        except OSError:
            self.dwm = None

    def _cloaked(self, hwnd) -> bool:
        if not self.dwm:
            return False
        value = ctypes.c_int(0)
        self.dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
        return value.value != 0

    def _class_name(self, hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _exe_name(self, pid: int) -> str:
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            name = buf.value.replace("\\", "/").rsplit("/", 1)[-1]
            return name[:-4] if name.lower().endswith(".exe") else name
        finally:
            self.kernel32.CloseHandle(handle)

    def list_windows(self) -> list[WindowInfo]:
        result: list[WindowInfo] = []
        own_pid = self.kernel32.GetCurrentProcessId()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _lparam):
            if not self.user32.IsWindowVisible(hwnd):
                return True
            if self.user32.GetWindow(hwnd, GW_OWNER):
                return True
            if self.user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            length = self.user32.GetWindowTextLengthW(hwnd)
            if length == 0 or self._cloaked(hwnd):
                return True
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid or self._class_name(hwnd) in SHELL_CLASSES:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, buf, length + 1)
            result.append(WindowInfo(id=str(int(hwnd)), title=buf.value, app=self._exe_name(pid.value),
                                     minimized=bool(self.user32.IsIconic(hwnd))))
            return True

        self.user32.EnumWindows(callback, 0)
        return result

    def _move(self, hwnd: int, output_name: str, rect, fullscreen: bool) -> None:
        target = monitor_rect(output_name) or rect
        x, y, w, h = target
        self.user32.ShowWindow(hwnd, SW_RESTORE)
        self.user32.SetWindowPos(hwnd, None, x + 20, y + 20, max(300, w // 2), max(200, h // 2),
                                 SWP_NOZORDER | SWP_SHOWWINDOW)
        # Fremde Programme lassen sich nicht zuverlässig in echtes Vollbild zwingen → maximieren
        self.user32.ShowWindow(hwnd, SW_MAXIMIZE)
        self.user32.SetForegroundWindow(hwnd)

    def present_window(self, window_id, output_name, rect) -> None:
        """Fremdes Fenster (z. B. AirPlay-Bild) randlos genau über Monitor 2 legen, immer im Vordergrund –
        auch wenn es später im Hintergrund aufgeht (Windows lässt fremde Fenster sonst nicht nach vorne)."""
        hwnd = int(window_id)
        x, y, w, h = monitor_rect(output_name) or rect
        u = self.user32
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        u.SetWindowLongW(hwnd, GWL_STYLE, style & ~(WS_CAPTION | WS_THICKFRAME | WS_MINIMIZE | WS_MAXIMIZE))
        u.ShowWindow(hwnd, SW_RESTORE)
        u.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), x, y, w, h, SWP_SHOWWINDOW | SWP_FRAMECHANGED)

    def window_fits(self, window_id, output_name, rect) -> bool:
        """Liegt das Fenster noch genau über Monitor 2? UxPlay (GStreamer) setzt sein Fenster auf die Videogröße
        zurück, sobald das iPhone-Bild kommt oder sich dreht – dann muss AluPC es wieder groß ziehen."""
        r = wintypes.RECT()
        if not self.user32.GetWindowRect(int(window_id), ctypes.byref(r)):
            return True  # Fenster weg – nichts zu tun
        x, y, w, h = monitor_rect(output_name) or rect
        return abs(r.left - x) <= 2 and abs(r.top - y) <= 2 and abs(r.right - r.left - w) <= 2 \
            and abs(r.bottom - r.top - h) <= 2

    def move_window(self, window_id, output_name, rect, fullscreen=False):
        self._move(int(window_id), output_name, rect, fullscreen)

    def move_by_title(self, title_part, output_name, rect, fullscreen=True) -> bool:
        for w in self.list_windows():
            if title_part in w.title:
                self._move(int(w.id), output_name, rect, fullscreen)
                return True
        return False

    def move_active_window(self, output_name, rect, fullscreen=False):
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            raise RuntimeError("Kein aktives Fenster gefunden")
        self._move(hwnd, output_name, rect, fullscreen)

    def _find(self, title: str):
        for w in self.list_windows():
            if w.title == title:
                return int(w.id), w
        return None, None

    def is_minimized(self, title: str) -> bool:
        _hwnd, info = self._find(title)
        return bool(info and info.minimized)

    def restore_in_background(self, title: str) -> bool:
        """Minimiertes Fenster wiederherstellen, ohne es zu aktivieren, und ganz nach hinten legen –
        dann zeichnet Windows es wieder und die Aufnahme bekommt Bilder."""
        hwnd, info = self._find(title)
        if not hwnd or not info.minimized:
            return False
        self.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        self.user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        return True
