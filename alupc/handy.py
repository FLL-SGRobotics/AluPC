"""iPhone/iPad → Monitor 2 per AirPlay (über das freie Programm UxPlay).

AirPlay: Das freie Programm UxPlay (GPL) empfängt. Ab UxPlay 1.73 gibt es die Option `-vrtp`: UxPlay
leitet das Bild dann als Videostrom an AluPC weiter und AluPC zeigt es als ganz normale Quelle an
(auch in eigenen Szenen, mit Standbild usw.). Ältere Versionen (z. B. aus Kubuntu 24.04: 1.68)
zeigen das Bild in einem eigenen Fenster – das legt AluPC im Vollbild auf Monitor 2.


UxPlay wird nicht mitgeliefert: Kubuntu installiert es per apt; unter Windows installiert AluPC per winget
„uxplay-windows“ (Community-Paket mit eingebautem UxPlay) und steuert es über dessen arguments.txt.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import socket
import subprocess
import time
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .config import config_dir

IS_WINDOWS = sys.platform.startswith("win")
def bundled_uxplay_dir() -> Path | None:
    """Windows-Installer: UxPlay liegt mit seinen GStreamer-Bibliotheken im Programmordner (uxplay/)."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    folder = base / "uxplay"
    return folder if (folder / "bin" / "uxplay.exe").is_file() else None


def uxplay_environment(uxplay: str) -> dict[str, str]:
    """Umgebung für ein UxPlay, das seine DLLs und GStreamer-Plugins selbst mitbringt (…/bin/uxplay.exe mit
    …/lib/gstreamer-1.0 daneben): mitgeliefert von AluPC oder aus uxplay-windows 1.x."""
    exe = Path(uxplay)
    folder = exe.parent.parent
    if exe.parent.name.lower() != "bin" or not (folder / "lib" / "gstreamer-1.0").is_dir():
        return {}
    plugins = str(folder / "lib" / "gstreamer-1.0")
    env = {"PATH": str(folder / "bin") + os.pathsep + os.environ.get("PATH", ""),
           "GST_PLUGIN_PATH": plugins, "GST_PLUGIN_SYSTEM_PATH_1_0": plugins, "GST_PLUGIN_SYSTEM_PATH": plugins,
           "GST_REGISTRY": str(config_dir() / "gst-registry.bin")}
    scanner = folder / "libexec" / "gstreamer-1.0" / "gst-plugin-scanner.exe"
    if scanner.is_file():
        env["GST_PLUGIN_SCANNER"] = str(scanner)
    return env


# „uxplay-windows“ (Community-Paket von leapbtw, winget „leapbtw.uxplay“): Tray-Programm mit eingebautem UxPlay.
# Es liest seine UxPlay-Optionen aus arguments.txt – darüber steuert AluPC Name, Code und Ports.
UXPLAY_WINDOWS_EXE = "uxplay-windows.exe"


def uxplay_windows_candidates() -> list[str]:
    env = os.environ
    bases = [env.get("ProgramFiles", r"C:\Program Files"), env.get("ProgramW6432", ""),
             env.get("ProgramFiles(x86)", ""), os.path.join(env.get("LOCALAPPDATA", ""), "Programs")]
    return [os.path.join(b, "uxplay-windows", UXPLAY_WINDOWS_EXE) for b in bases if b]


def find_uxplay_windows(configured: str = "") -> str | None:
    if configured and configured.lower().endswith(UXPLAY_WINDOWS_EXE) and Path(configured).is_file():
        return configured
    if not IS_WINDOWS:
        return None
    for candidate in uxplay_windows_candidates():
        if Path(candidate).is_file():
            return candidate
    return None


def is_uxplay_windows(path: str | None) -> bool:
    return bool(path) and re.split(r"[\\/]", path)[-1].lower() == UXPLAY_WINDOWS_EXE


def uxplay_windows_arguments_file() -> Path:
    """Die Datei, aus der uxplay-windows seine Optionen liest (Benutzer-Datei unter %APPDATA%)."""
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "leapbtw" / "uxplay-windows" / "arguments.txt"


def uxplay_windows_machine_file() -> Path:
    """Liegt diese Datei vor, hat sie bei uxplay-windows Vorrang (nur mit Adminrechten änderbar)."""
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "uxplay-windows" / "arguments.txt"


def uxplay_windows_command_line(args: list[str]) -> str:
    """Optionen als eine Zeile für arguments.txt.

    uxplay-windows 2.0.0.1736 (winget) trennt die Zeile einfach an Leerzeichen – Anführungszeichen helfen
    nicht. Leerzeichen im Namen werden deshalb zu geschützten Leerzeichen (U+00A0): Das iPhone zeigt sie wie
    normale Leerzeichen an. „%…%“ ersetzen neuere Versionen durch Umgebungsvariablen – deshalb weg damit."""
    return " ".join(a.replace('"', "").replace("%", "").replace(" ", "\u00a0") for a in args)


def uxplay_windows_log_tail(lines: int = 15) -> list[str]:
    """Letzte Zeilen aus dem neuesten Protokoll von uxplay-windows (für verständliche Fehlermeldungen)."""
    folder = Path(os.environ.get("LOCALAPPDATA", "")) / "uxplay-windows" / "logs"
    try:
        logs = sorted(folder.glob("uxplay-*.log"), key=lambda f: f.stat().st_mtime)
        if not logs:
            return []
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()][-lines:]


def old_uxplay_windows_binary() -> str | None:
    """uxplay-windows 1.x (Python-Tray) startet ein mitgeliefertes bin/uxplay.exe OHNE Einstellungsdatei –
    AluPC startet dieses uxplay.exe deshalb direkt selbst (dann gelten Name, Code usw.)."""
    for candidate in uxplay_windows_candidates():
        exe = Path(candidate).parent / "bin" / "uxplay.exe"
        if exe.is_file():
            return str(exe)
    return None


def kill_uxplay_windows() -> bool:
    """Andere laufende AirPlay-Empfänger beenden (Windows: uxplay-windows mit eigenem Autostart, dessen
    uxplay.exe; Linux: ein übrig gebliebenes uxplay, z. B. nach einem Absturz). Es darf nur einen geben –
    sonst sind die Ports belegt, das iPhone sieht weiter den alten Namen und AluPCs Einstellungen gelten nicht.
    Rückgabe: False, wenn danach noch einer läuft (z. B. mit Adminrechten gestartet)."""
    if not IS_WINDOWS:
        try:
            if subprocess.run(["pkill", "-x", "uxplay"], capture_output=True, timeout=5).returncode != 0:
                return True  # lief keins
            for _ in range(15):  # bis zu 3 s warten, bis es wirklich weg ist
                time.sleep(0.2)
                if not _living_uxplay():
                    return True
            subprocess.run(["pkill", "-9", "-x", "uxplay"], capture_output=True, timeout=5)
            time.sleep(0.3)
            return not _living_uxplay()
        except (OSError, subprocess.SubprocessError):
            return True
    for exe in (UXPLAY_WINDOWS_EXE, "uxplay-bluetooth-beacon.exe", "uxplay.exe"):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/IM", exe], capture_output=True, timeout=10,
                           creationflags=0x08000000)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=10, text=True,
                             creationflags=0x08000000).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return True
    return not any(f'"{exe}"' in out for exe in (UXPLAY_WINDOWS_EXE, "uxplay.exe"))


def _living_uxplay() -> bool:
    """Läuft (unter Linux) noch ein uxplay? Beendete, noch nicht abgeholte Prozesse („Zombies“) zählen nicht."""
    try:
        out = subprocess.run(["ps", "-C", "uxplay", "-o", "stat="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.strip() and not line.strip().startswith("Z") for line in out.splitlines())


# Programme, deren Fenster NIE das iPhone-Bild sind (ihr Titel kann „AluPC“ oder „UxPlay“ enthalten)
NOT_IPHONE_APPS = {"firefox", "chrome", "chromium", "chromium-browser", "google-chrome", "msedge", "brave",
                   "opera", "vivaldi", "explorer", "code", "notepad", "notepad++", "kate", "dolphin", "konsole",
                   "alupc", "python", "pythonw", "navigator", "thunderbird", "discord", "teams", "obs64", "obs"}


def is_iphone_window(title: str, app: str, titles) -> bool:
    """Ist das UxPlays Fenster mit dem iPhone-Bild? Nur das Fenster des UxPlay-Programms selbst oder ein Fenster,
    dessen Titel GENAU so heißt – nicht jeder Browser-Tab, in dessen Titel „AluPC“ vorkommt."""
    app = (app or "").lower()
    if app.endswith(".exe"):
        app = app[:-4]
    title = (title or "").replace("\u00a0", " ").strip()
    if app in NOT_IPHONE_APPS:
        return False
    if app in ("uxplay-windows", "uxplay"):
        low = title.lower()
        return title != "uxplay-windows" and "log" not in low and "einstell" not in low and "setting" not in low
    return any(title == (t or "").replace("\u00a0", " ").strip() for t in titles if t)


# Zeilen in UxPlays Ausgabe, wenn sich ein iPhone verbindet (Versionen 1.6x–1.7x, uxplay-windows)
DISCONNECT_HINTS = ("open connections: 0", "connection closed", "client disconnected", "stopped mirroring")
CONNECT_HINTS = ("open connections: 1", "accepted ipv4 client", "accepted ipv6 client", "client connected",
                 "raop_rtp_mirror starting", "begin streaming to gstreamer")


def uxplay_autostarts() -> list[tuple[str, str, str]]:
    """Fremde Autostarts von UxPlay (starten es mit eigenen Einstellungen – Standardname, kein Code):
    (Art, Kennung, Anzeige). Windows: Run-Schlüssel, Autostart-Ordner, Aufgabenplanung; Linux:
    ~/.config/autostart und systemd (Benutzer)."""
    found: list[tuple[str, str, str]] = []
    if IS_WINDOWS:
        try:
            import winreg

            run = r"Software\Microsoft\Windows\CurrentVersion\Run"
            for hive, hive_name, path in ((winreg.HKEY_CURRENT_USER, "HKCU", run),
                                          (winreg.HKEY_LOCAL_MACHINE, "HKLM", run),
                                          (winreg.HKEY_LOCAL_MACHINE, "HKLM",
                                           r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run")):
                try:
                    key = winreg.OpenKey(hive, path)
                except OSError:
                    continue
                with key:
                    i = 0
                    while True:
                        try:
                            name, value, _t = winreg.EnumValue(key, i)
                        except OSError:
                            break
                        i += 1
                        if "uxplay" in f"{name} {value}".lower():
                            found.append(("reg", f"{hive_name}|{path}|{name}", f"Autostart „{name}“ ({hive_name})"))
        except ImportError:
            pass
        for folder in (Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup",
                       Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs/StartUp"):
            try:
                for f in folder.iterdir():
                    if "uxplay" in f.name.lower():
                        found.append(("datei", str(f), f"Autostart-Ordner: {f.name}"))
            except OSError:
                pass
        try:
            out = subprocess.run(["schtasks", "/query", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                 timeout=15, creationflags=0x08000000).stdout
            for line in out.splitlines():
                task = line.split('","')[0].strip('"')
                if "uxplay" in task.lower():
                    found.append(("aufgabe", task, f"Aufgabenplanung: {task}"))
        except (OSError, subprocess.SubprocessError):
            pass
        return found
    autostart = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart"
    try:
        for f in autostart.glob("*.desktop"):
            try:
                if "uxplay" in f.read_text(encoding="utf-8", errors="replace").lower():
                    found.append(("datei", str(f), f"Autostart: {f.name}"))
            except OSError:
                pass
    except OSError:
        pass
    if shutil.which("systemctl"):
        for unit in ("uxplay.service",):
            try:
                state = subprocess.run(["systemctl", "--user", "is-enabled", unit], capture_output=True, text=True,
                                       timeout=5).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                continue
            if state in ("enabled", "enabled-runtime"):
                found.append(("systemd", unit, f"systemd: {unit}"))
    return found


def disable_uxplay_autostarts() -> tuple[list[str], list[str]]:
    """Fremde UxPlay-Autostarts abschalten (umkehrbar: Dateien bekommen die Endung „.aus-durch-AluPC“).
    Rückgabe: (abgeschaltet, nicht möglich – z. B. für alle Benutzer, nur mit Adminrechten)."""
    done, failed = [], []
    for kind, ident, label in uxplay_autostarts():
        try:
            if kind == "reg":
                import winreg

                hive_name, path, name = ident.split("|", 2)
                hive = winreg.HKEY_CURRENT_USER if hive_name == "HKCU" else winreg.HKEY_LOCAL_MACHINE
                with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, name)
            elif kind == "datei":
                os.replace(ident, ident + ".aus-durch-AluPC")
            elif kind == "aufgabe":
                ok = subprocess.run(["schtasks", "/change", "/tn", ident, "/disable"], capture_output=True,
                                    timeout=15, creationflags=0x08000000).returncode == 0
                if not ok:
                    raise PermissionError(ident)
            elif kind == "systemd":
                subprocess.run(["systemctl", "--user", "disable", "--now", ident], capture_output=True, timeout=15,
                               check=True)
            done.append(label)
        except (OSError, subprocess.SubprocessError, ImportError):
            failed.append(label)
    return done, failed


STUCK_TEXT = ("Ein anderes AirPlay-Programm läuft schon (evtl. mit Adminrechten) und lässt sich nicht beenden – "
              "dann gelten Name und Code von AluPC nicht. Im Task-Manager „uxplay“ beenden oder den PC neu starten.")


WINDOWS_UXPLAY = [r"C:\msys64\ucrt64\bin\uxplay.exe", r"C:\msys64\mingw64\bin\uxplay.exe",
                  r"C:\Program Files\UxPlay\uxplay.exe"]
# winget legt Programme hier als Verknüpfung ab (der PATH von AluPC kennt das nach der Installation noch nicht)
WINGET_LINKS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links")


def find_program(name: str, configured: str = "", extra: list[str] | None = None) -> str | None:
    """Programm suchen: eigener Pfad aus dem Setup, dann PATH, dann bekannte Orte."""
    for candidate in [configured] if configured else []:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which(name)
    if found:
        return found
    if IS_WINDOWS:
        linked = shutil.which(name, path=WINGET_LINKS)
        if linked:
            return linked
    for candidate in extra or []:
        if Path(candidate).is_file():
            return candidate
    return None


_vrtp_cache: dict[str, str] = {}  # UxPlay → Hilfetext


def supports_vrtp(uxplay: str) -> bool:
    """Kann diese UxPlay-Version das Bild an AluPC weiterleiten (-vrtp, ab 1.73)?"""
    if is_uxplay_windows(uxplay):
        return False  # Tray-Programm: „-h“ würde es starten; zeigt das Bild immer im eigenen Fenster
    return "-vrtp" in uxplay_help(uxplay)


def uxplay_help(uxplay: str) -> str:
    """Hilfetext dieser UxPlay-Version (zeigt, welche Optionen sie kennt)."""
    if is_uxplay_windows(uxplay):
        return ""
    if uxplay not in _vrtp_cache:
        try:
            out = subprocess.run([uxplay, "-h"], capture_output=True, text=True, timeout=8)
            _vrtp_cache[uxplay] = out.stdout + out.stderr
        except (OSError, subprocess.SubprocessError):
            _vrtp_cache[uxplay] = ""
    return _vrtp_cache[uxplay]


def vm_options(help_text: str) -> list[str]:
    """Virtuelle Maschine: keine Hardware-Videodecoder (Software-Decoder erzwingen), und ihre Uhr läuft oft
    ungleichmäßig – mit Zeitstempel-Abgleich verwirft UxPlay dann jedes Bild (Ton ja, Bild nein).
    Nur Optionen, die diese UxPlay-Version kennt (ältere brechen bei unbekannten Optionen ab)."""
    opts = []
    if re.search(r"^\s*-avdec\b", help_text, re.M):
        opts += ["-avdec"]
    if re.search(r"^\s*-vsync\b", help_text, re.M):
        opts += ["-vsync", "no"]
    return opts


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sdp_text(port: int) -> str:
    """Beschreibung des Videostroms von UxPlay (H.264 über RTP, Payload 96) für den Videoplayer."""
    return ("v=0\no=- 0 0 IN IP4 127.0.0.1\ns=AluPC AirPlay\nc=IN IP4 127.0.0.1\nt=0 0\n"
            f"m=video {port} RTP/AVP 96\na=rtpmap:96 H264/90000\n")


_VM: bool | None = None


def in_virtual_machine() -> bool:
    """Linux in VirtualBox/VMware/KVM? (einmal ermitteln)"""
    global _VM
    if _VM is None:
        _VM = False
        if sys.platform.startswith("linux"):
            try:
                _VM = subprocess.run(["systemd-detect-virt", "--vm", "-q"], timeout=5).returncode == 0
            except (OSError, subprocess.SubprocessError):
                try:
                    product = open("/sys/class/dmi/id/product_name", encoding="utf-8").read().lower()
                    _VM = any(k in product for k in ("virtualbox", "vmware", "kvm", "qemu"))
                except OSError:
                    pass
    return _VM


RTP_SINK = "rtph264pay config-interval=1 pt=96 ! udpsink host=127.0.0.1 port={port}"
def screen_size_option(help_text: str, size: tuple[int, int] | None) -> list[str]:
    """UxPlay „-s BxH“: dem iPhone die Größe von Monitor 2 melden – es schickt dann nicht mehr Pixel als nötig
    (weniger Rechenarbeit, flüssiger – vor allem in einer VM). Höchstens 1920×1080 (UxPlay-Standard)."""
    if not size or not re.search(r"^\s*-s\s", help_text, re.M):
        return []
    w, h = size
    scale = min(1.0, 1920 / max(1, w), 1080 / max(1, h))
    w, h = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    return ["-s", f"{w}x{h}"] if w >= 320 and h >= 240 else []


def supports_rtp_pipeline(uxplay: str) -> bool:
    """Linux, UxPlay ohne -vrtp (z. B. 1.68 aus Kubuntu): Bild trotzdem an AluPC weiterleiten – über die
    frei wählbaren Pipeline-Teile -vd/-vc/-vs. UxPlay baut daraus
    „appsrc ! queue ! h264parse ! identity ! identity ! rtph264pay … ! udpsink … name=video_sink sync=…“:
    das H.264 vom iPhone geht unverändert per RTP an AluPC (wie -vrtp ab 1.73). Kein eigenes UxPlay-Fenster,
    das auf Monitor 2 geschoben werden müsste (unter Wayland/in VMs unzuverlässig)."""
    if not sys.platform.startswith("linux"):
        return False
    text = uxplay_help(uxplay)
    if not all(re.search(rf"^\s*{opt}\b", text, re.M) for opt in ("-vd", "-vc", "-vs")):
        return False
    if shutil.which("gst-inspect-1.0"):  # RTP-Bausteine da? (gstreamer1.0-plugins-good/-base)
        try:
            for element in ("rtph264pay", "udpsink"):
                if subprocess.run(["gst-inspect-1.0", "--exists", element], timeout=10).returncode != 0:
                    return False
        except (OSError, subprocess.SubprocessError):
            return False
    return True


def uxplay_args(name: str, pin: str, port: int | None, window_title: bool = True,
                extra: list[str] | None = None, rtp_pipeline: bool = False) -> list[str]:
    # -p: feste Ports (TCP 7000, 7001, 7100 / UDP 6000, 6001, 7011) – so lässt sich die Firewall gezielt öffnen
    args = ["-n", name or "AluPC", "-nh", "-p"]
    # Bild geht an AluPC: UxPlay dekodiert nichts → Software-Decoder-Zusatz (-avdec) ist dann sinnlos
    args += [a for a in (extra or []) if not (port is not None and a == "-avdec")]
    if pin:
        args += ["-pin", pin] if pin != "zufall" else ["-pin"]
    if port is not None and rtp_pipeline:  # UxPlay 1.6x: H.264 unverändert per RTP an AluPC
        args += ["-vd", "identity", "-vc", "identity", "-vs", RTP_SINK.format(port=port)]
    elif port is not None:  # Bild an AluPC weiterleiten statt selbst anzeigen (ab 1.73)
        args += ["-vrtp", f"config-interval=1 ! udpsink host=127.0.0.1 port={port}"]
    elif not IS_WINDOWS:  # eigenes Fenster im Vollbild (AluPC schiebt es auf Monitor 2)
        args += ["-fs"]
        if window_title:
            args += ["-vs", "ximagesink"]  # X11-Fenster: Titel = Name, lässt sich überall platzieren
    # Windows: kein -fs – Vollbild landete auf Monitor 1; AluPC legt das Fenster selbst randlos auf Monitor 2
    return args


# --------------------------------------------------------------------------- AirPlay-Empfang
class AirPlayServer(QObject):
    """Startet/stoppt UxPlay. Mehrere Quellen können es gleichzeitig benutzen (Zähler)."""

    status = Signal(str)
    log_line = Signal(str)
    failed = Signal(str)  # UxPlay hat sich unerwartet beendet – verständliche Erklärung
    notice = Signal(str)  # Hinweis für den Nutzer (z. B. fremdes UxPlay lässt sich nicht beenden)
    connected = Signal()  # ein iPhone verbindet sich (aus UxPlays Meldungen)
    disconnected = Signal()  # … und trennt sich wieder
    settings_changed = Signal()  # Name/Code o. Ä. geändert – alle Anzeigen neu einlesen

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.proc: QProcess | None = None
        self.users = 0
        self.mode = ""  # "stream" (an AluPC) oder "fenster" (eigenes Fenster)
        self.port = 0
        self.log: list[str] = []
        self.pin_code = ""
        self._background = False  # „Immer bereit“: hält UxPlay dauerhaft am Laufen
        self.relay = None  # rtp_relay.RtpRelay im Modus „stream“
        self.output_screen = None  # Funktion → QScreen von Monitor 2 (setzt der Controller)
        self.is_connected = False  # laut UxPlays Meldungen ist gerade ein iPhone verbunden
        self._stop_timer = QTimer(self, singleShot=True, interval=1500)
        self._stop_timer.timeout.connect(self._really_stop)

    def settings(self) -> dict:
        return {"airplay_name": "AluPC", "pin": "", "uxplay_path": "", **self.config["handy"]}

    def update_settings(self, **values) -> bool:
        """Nur die übergebenen AirPlay-Einstellungen ändern (nie veraltete Werte anderer Felder mitschreiben),
        bei laufendem Empfang sofort neu starten. Rückgabe: neu gestartet?"""
        if "airplay_name" in values:
            values["airplay_name"] = (values["airplay_name"] or "").strip() or "AluPC"
            values["name_set"] = True  # vom Nutzer gewählt → Einrichtung ersetzt ihn nicht mehr
        current = self.config["handy"]
        if all(current.get(k) == v for k, v in values.items()):
            return False
        self.config["handy"] = {**current, **values}
        restarted = self.restart_if_changed()
        if not restarted:
            self.sync_windows_arguments()
        self.settings_changed.emit()
        return restarted

    def ensure_unique_name(self) -> None:
        """Bei der Einrichtung: aus „AluPC“ einen eindeutigen Namen machen – aber nur, wenn ihn niemand gewählt hat."""
        s = self.config["handy"]
        if s.get("airplay_simple"):  # fester Name „AluPC“ – nichts mehr umbenennen
            return
        if not s.get("name_set") and s.get("airplay_name", "AluPC") == "AluPC":
            self.config["handy"] = {**s, "airplay_name": default_airplay_name()}
            self.restart_if_changed()
            self.settings_changed.emit()

    def binary(self) -> str | None:
        bundled = bundled_uxplay_dir() if IS_WINDOWS else None
        extra = ([str(bundled / "bin" / "uxplay.exe")] if bundled else []) + (WINDOWS_UXPLAY if IS_WINDOWS else [])
        configured = self.settings()["uxplay_path"]
        if IS_WINDOWS and bundled and not configured:
            return extra[0]  # mitgeliefertes UxPlay zuerst
        if is_uxplay_windows(configured):
            return find_uxplay_windows(configured)
        if IS_WINDOWS:
            old = old_uxplay_windows_binary()
            extra += [old] if old else []
        return find_program("uxplay", configured, extra) or find_uxplay_windows()

    def sdp_path(self) -> Path:
        return config_dir() / "airplay.sdp"

    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.NotRunning

    def acquire(self, want_stream: bool = True) -> str:
        """UxPlay starten (falls nötig). Rückgabe: Modus „stream“/„fenster“ oder Fehlertext."""
        self._stop_timer.stop()
        self.users += 1
        if self.running():
            return self.mode
        uxplay = self.binary()
        if not uxplay:
            self.users -= 1
            return "fehlt"
        s = self.settings()
        if is_uxplay_windows(uxplay):
            return self._start_uxplay_windows(uxplay, s)
        vrtp = supports_vrtp(uxplay)
        rtp_pipeline = not vrtp and want_stream and self.settings().get("airplay_stream", True) \
            and supports_rtp_pipeline(uxplay)
        stream = want_stream and (vrtp or rtp_pipeline)
        self.port = free_udp_port() if stream else 0
        self._stop_relay()
        if stream:
            self.sdp_path().parent.mkdir(parents=True, exist_ok=True)
            self.sdp_path().write_text(sdp_text(self.port), encoding="ascii")
            from .rtp_relay import RtpRelay

            # UxPlay → Relais → Player: das Relais merkt sich das Schlüsselbild für einen (neu) startenden Player
            self.relay = RtpRelay(self.port)
        pin = s.get("pin", "")
        self.pin_code = pin if pin and pin != "zufall" else ""
        help_text = uxplay_help(uxplay)
        extra = (vm_options(help_text) if in_virtual_machine() else []) + \
            screen_size_option(help_text, self._output_size())  # auch im Fenster-Modus: UxPlay dekodiert weniger
        args = uxplay_args(s["airplay_name"], pin, self.relay.in_port if stream else None, extra=extra or None,
                           rtp_pipeline=rtp_pipeline)
        if not kill_uxplay_windows():  # fremde Empfänger (z. B. uxplay-windows im Autostart) belegen sonst die Ports
            self._stuck()
        self._started_with = self._wanted()
        self.proc = QProcess(self)
        extra_env = uxplay_environment(uxplay)
        if extra_env:  # UxPlay mit eigenen GStreamer-Plugins (mitgeliefert bzw. uxplay-windows 1.x)
            from PySide6.QtCore import QProcessEnvironment

            env = QProcessEnvironment.systemEnvironment()
            for key, value in extra_env.items():
                env.insert(key, value)
            self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.start(uxplay, args)
        self.mode = "stream" if stream else "fenster"
        self.status.emit("läuft")
        return self.mode

    def _output_size(self) -> tuple[int, int] | None:
        """Pixelgröße von Monitor 2 (dort wird das iPhone-Bild gezeigt)."""
        try:
            screen = self.output_screen() if self.output_screen else None  # vom Controller gesetzt
        except Exception:  # noqa: BLE001
            screen = None
        if screen is None:
            return None
        g, dpr = screen.geometry(), screen.devicePixelRatio()
        return int(g.width() * dpr), int(g.height() * dpr)

    def _wanted(self) -> tuple:
        s = self.settings()
        return (s.get("airplay_name"), s.get("pin"), self.binary())

    def _stuck(self) -> None:
        self.log = (self.log + [STUCK_TEXT])[-60:]
        self.notice.emit(STUCK_TEXT)

    def running_settings(self) -> dict:
        """Womit UxPlay gerade wirklich läuft (Name, Code) – leer, wenn es nicht läuft."""
        if not self.running() or not getattr(self, "_started_with", None):
            return {}
        name, pin, _binary = self._started_with
        return {"airplay_name": name or "AluPC", "pin": self.pin_code or ("zufall" if pin == "zufall" else "")}

    def restart_if_changed(self) -> bool:
        """Name/Code/Programm geändert, während der Empfang läuft → mit den neuen Einstellungen neu starten."""
        if not self.running() or self._wanted() == getattr(self, "_started_with", None):
            return False
        users, mode = self.users, self.mode
        self.users = 0
        self._really_stop()
        self.acquire(want_stream=(mode == "stream"))
        self.users = users  # gleich viele Nutzer wie vorher (sonst läuft es nach dem Schließen ewig weiter)
        if users == 0:
            self._stop_timer.start()
        self.status.emit("neu gestartet")
        return True

    def _write_windows_arguments(self, args: list[str]) -> None:
        """uxplay-windows liest Name/Code aus arguments.txt – auch ein von Windows selbst gestartetes."""
        try:
            target = uxplay_windows_arguments_file()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(uxplay_windows_command_line(args), encoding="utf-8")
        except OSError as exc:
            self.log = (self.log + [f"arguments.txt nicht schreibbar: {exc}"])[-60:]
            self.notice.emit(f"AirPlay-Einstellungen ließen sich nicht speichern ({target})")
        machine = uxplay_windows_machine_file()
        if machine.exists():  # hat bei uxplay-windows Vorrang → mit überschreiben, wenn erlaubt
            try:
                machine.write_text(uxplay_windows_command_line(args), encoding="utf-8")
            except OSError:
                self.log = (self.log + [f"Hinweis: {machine} hat Vorrang – Name/Code von AluPC gelten dann nicht"])[-60:]
                self.notice.emit(f"Name/Code gelten nicht: {machine} hat Vorrang und ist schreibgeschützt – "
                                 "Datei löschen (Adminrechte), dann klappt es.")

    def sync_windows_arguments(self) -> None:
        """Einstellungen sofort in arguments.txt – auch wenn AirPlay gerade nicht läuft (fester Code/aus)."""
        if not IS_WINDOWS or not is_uxplay_windows(self.binary()) or self.running():
            return
        s = self.settings()
        pin = s.get("pin", "")
        pin = pin if pin.isdigit() else ""  # Zufallscode würfelt AluPC erst beim Start
        self._write_windows_arguments(["-n", s["airplay_name"] or "AluPC", "-nh", "-p"] + (["-pin", pin] if pin else []))

    def set_background(self, on: bool) -> str:
        """„Immer bereit“: UxPlay läuft mit AluPCs Name/Code dauerhaft im Hintergrund (eigener Nutzer-Zähler)."""
        if on and not self._background:
            self._background = True
            return self.acquire(want_stream=True)
        if not on and self._background:
            self._background = False
            self.release()
        return self.mode if self.running() else ""

    def _start_uxplay_windows(self, exe: str, s: dict) -> str:
        """Windows: uxplay-windows mit AluPCs Name/Code starten. Es zeigt das Bild in einem eigenen Fenster."""
        pin = s.get("pin", "")
        if pin == "zufall":  # das Protokoll von uxplay-windows liest AluPC nicht live → Code selbst würfeln
            pin = random_pin()
        self.pin_code = pin
        args = ["-n", s["airplay_name"] or "AluPC", "-nh", "-p"] + (["-pin", pin] if pin else [])
        if not kill_uxplay_windows():  # evtl. mit anderen Einstellungen schon laufend (eigener Autostart)
            self._stuck()
        self._started_with = self._wanted()
        self._write_windows_arguments(args)
        self.proc = QProcess(self)
        from PySide6.QtCore import QProcessEnvironment

        env = QProcessEnvironment.systemEnvironment()
        env.insert("QT_FORCE_STDERR_LOGGING", "1")  # Meldungen auf stderr statt ins Windows-Debug-Protokoll
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.setWorkingDirectory(str(Path(exe).parent))
        self.proc.start(exe, [])
        self.port = 0
        self.mode = "fenster"
        self.status.emit("läuft")
        return self.mode

    def _finished(self, code=None, *_):
        self.status.emit("beendet")
        if code is not None:
            self.log = (self.log + [f"(Programm beendet, Exit-Code {code})"])[-60:]
        if self.users > 0:  # sollte laufen, ist aber weg → Grund aus den Meldungen ableiten
            log = self.log + (uxplay_windows_log_tail() if is_uxplay_windows(self.binary()) else [])
            self.failed.emit(explain_uxplay_error(log))
            self.proc = None

    def release(self) -> None:
        self.users = max(0, self.users - 1)
        if self.users == 0:
            # kurz warten: beim Szenenwechsel mit AirPlay in beiden Szenen nicht neu starten
            self._stop_timer.start()

    def _really_stop(self):
        if self.users == 0 and self.proc is not None:
            tray_app = IS_WINDOWS and is_uxplay_windows(self.proc.program())
            if not tray_app:
                self.proc.terminate()  # uxplay-windows ist ein Tray-Programm und reagiert darauf nicht
            if tray_app or not self.proc.waitForFinished(2000):
                self.proc.kill()
                self.proc.waitForFinished(2000)
            if tray_app:
                kill_uxplay_windows()  # auch den Bluetooth-Helfer
            self.proc = None
            self._stop_relay()
            self.is_connected = False
            self.status.emit("gestoppt")

    def _stop_relay(self) -> None:
        if self.relay is not None:
            self.relay.stop()
            self.relay = None

    def replay(self) -> None:
        """AluPCs Player wurde (neu) gestartet → aktuelles iPhone-Bild noch einmal schicken (siehe rtp_relay)."""
        if self.relay is not None:
            self.relay.replay()

    def _read(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            self.log = (self.log + [line])[-60:]
            self.log_line.emit(line)
            low = line.lower()
            if any(k in low for k in CONNECT_HINTS):
                self.is_connected = True
                self.connected.emit()
            elif any(k in low for k in DISCONNECT_HINTS):
                self.is_connected = False
                if self.relay is not None:
                    self.relay.reset()  # nächste Verbindung bringt ein neues Schlüsselbild
                self.disconnected.emit()
            if "pin" in low and any(ch.isdigit() for ch in line):  # „-pin“ ohne feste Zahl: Code steht im Log
                digits = "".join(ch for ch in line.split(":")[-1] if ch.isdigit())
                if len(digits) == 4:
                    self.pin_code = digits
                    self.status.emit("pin")

    def shutdown(self):
        self._background = False
        self.users = 0
        self._really_stop()


_server: AirPlayServer | None = None


def airplay_server(config=None) -> AirPlayServer:
    global _server
    if _server is None:
        if config is None:
            raise RuntimeError("AirPlay-Empfang ist noch nicht eingerichtet")
        _server = AirPlayServer(config)
    elif config is not None:
        _server.config = config
    return _server


def explain_uxplay_error(log: list[str]) -> str:
    """Letzte UxPlay-Meldungen → verständlicher Grund mit Abhilfe."""
    text = "\n".join(log[-15:])
    if "DNS-SD" in text or "dns_sd" in text.lower():
        if IS_WINDOWS:
            return "Der Apple-Dienst „Bonjour“ fehlt oder läuft nicht – ohne ihn findet das iPhone den PC nicht."
        return ("Der Dienst „avahi-daemon“ läuft nicht – ohne ihn findet das iPhone den PC nicht. "
                "„Automatisch einrichten“ schaltet ihn ein.")
    if "video renderer" in text or "GStreamer" in text:
        return "GStreamer-Pakete fehlen (Videoausgabe) – „Automatisch einrichten“ installiert sie."
    if "Address already in use" in text or "bind" in text.lower():
        return "Die AirPlay-Ports sind belegt – läuft UxPlay schon (z. B. ein zweites AluPC)?"
    if "unknown option" in text:
        return "Diese UxPlay-Version kennt eine Option nicht: " + text.strip().splitlines()[-1]
    last = [line for line in log[-5:] if line.strip()]
    return "UxPlay hat sich beendet" + (f": {last[-1]}" if last else ".")


def random_pin() -> str:
    return f"{random.randint(0, 9999):04d}"


def install_command(program: str) -> list[str]:
    """Kubuntu: Paket per pkexec installieren (Passwortabfrage)."""
    # Lock::Timeout: laufen gerade Updates (Discover …), warten statt „Sperrung nicht möglich“
    return ["pkexec", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-o", "DPkg::Lock::Timeout=600", "install",
            "-y", *APT_PACKAGES[program]]


APT_PACKAGES = {"uxplay": ["uxplay", "gstreamer1.0-plugins-good", "gstreamer1.0-plugins-bad", "gstreamer1.0-libav",
                           "avahi-daemon"]}
WINGET_IDS = {"bonjour": "Apple.Bonjour", "uxplay": "leapbtw.uxplay"}


def can_install() -> bool:
    return sys.platform.startswith("linux") and bool(shutil.which("apt-get")) and bool(shutil.which("pkexec"))


def can_winget() -> bool:
    return IS_WINDOWS and bool(shutil.which("winget"))


# --------------------------------------------------------------------------- Automatisch einrichten
AIRPLAY_PORTS = ["7000:7001/tcp", "7100/tcp", "6000:6001/udp", "7011/udp", "5353/udp"]  # zu „uxplay -p“


def missing_packages(packages: list[str]) -> list[str]:
    """Kubuntu: welche dieser Pakete sind (noch) nicht installiert?"""
    if not shutil.which("dpkg-query"):
        return []
    missing = []
    for pkg in packages:
        try:
            out = subprocess.run(["dpkg-query", "-W", "-f=${Status}", pkg], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        if "install ok installed" not in out:
            missing.append(pkg)
    return missing


def avahi_running() -> bool:
    """Linux: läuft der Dienst, über den iPhones den PC finden (mDNS/Bonjour)?"""
    if IS_WINDOWS:
        return True
    if shutil.which("systemctl"):
        try:
            if subprocess.run(["systemctl", "is-active", "avahi-daemon"], capture_output=True, text=True,
                              timeout=5).stdout.strip() == "active":
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return bool(shutil.which("pgrep")) and subprocess.run(["pgrep", "-x", "avahi-daemon"],
                                                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def linux_setup_script(packages: list[str], avahi: bool, firewall_port: int | None) -> str:
    """Ein Skript für EINE Passwortabfrage: Pakete, avahi-Dienst, Firewall (nur feste Werte, keine Eingaben)."""
    lines = ["set -e", "export DEBIAN_FRONTEND=noninteractive"]
    if packages:
        from .addons import apt_install_script

        lines.append(apt_install_script(packages))
    if avahi:
        lines.append("systemctl enable --now avahi-daemon")
    if firewall_port:
        rules = " && ".join(f"ufw allow {r}" for r in AIRPLAY_PORTS + [f"{int(firewall_port)}/tcp"])
        lines.append(f'if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then {rules}; fi')
    return "\n".join(lines)


def setup_plan(config) -> list[tuple[str, list[str]]]:
    """Was für AirPlay fehlt und automatisch eingerichtet werden kann: [(Beschreibung, Befehl)]."""
    plan = []
    if can_install():
        packages = missing_packages(APT_PACKAGES["uxplay"])
        avahi = not avahi_running()
        firewall = None if config["handy"].get("firewall_done") else int(config["cast"].get("port", 8765))
        if packages or avahi or firewall:
            parts = []
            if packages:
                parts.append("UxPlay installieren" if "uxplay" in packages else "Video-/Netzwerk-Pakete installieren")
            if avahi:
                parts.append("iPhone-Suche (avahi) einschalten")
            if firewall:
                parts.append("Firewall für AirPlay/Handy öffnen")
            plan.append((", ".join(parts), ["pkexec", "sh", "-c", linux_setup_script(packages, avahi, firewall)]))
    elif can_winget():
        if not bonjour_installed():
            plan.append(("Bonjour (damit das iPhone den PC findet) installieren", _winget("bonjour")))
        if not (find_uxplay_windows() or find_program("uxplay", "", WINDOWS_UXPLAY)):
            plan.append(("AirPlay-Empfänger (UxPlay für Windows) installieren", _winget("uxplay")))
    if IS_WINDOWS and int(config["handy"].get("firewall_version", 0)) < FIREWALL_VERSION:
        plan.append(("Firewall für Handy und AirPlay freigeben (eine Windows-Abfrage)",
                     windows_firewall_command(sys.executable, int(config["cast"].get("port", 8765)))))
    return plan


# 2: AirPlay für alle Netzwerktypen (Windows stuft WLANs oft als „Öffentlich“ ein → iPhone sieht „AluPC“, kann
#    sich aber nicht verbinden) + von Windows angelegte Sperr-Regeln für UxPlay entfernen
FIREWALL_VERSION = 3  # 3: UxPlay-Sperrregeln nach „Ein Teil dieser App wurde blockiert“ ersetzen


def uxplay_programs() -> list[str]:
    """Alle UxPlay-Programmdateien, für die Windows eigene (evtl. sperrende) Firewall-Regeln anlegt."""
    found = []
    for candidate in uxplay_windows_candidates():
        folder = Path(candidate).parent
        for exe in (folder / UXPLAY_WINDOWS_EXE, folder / "bin" / "uxplay.exe", folder / "uxplay.exe"):
            if exe.is_file():
                found.append(str(exe))
    bundled = bundled_uxplay_dir()
    if bundled:
        found.append(str(bundled / "bin" / "uxplay.exe"))
    for path in WINDOWS_UXPLAY:
        if os.path.isfile(path):
            found.append(path)
    return list(dict.fromkeys(found))


def windows_firewall_ok() -> bool:
    """Gibt es die Freigaben schon (z. B. vom Installer eingerichtet)?"""
    try:
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=AluPC Handy"],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000)
        return out.returncode == 0 and "AluPC Handy" in out.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def windows_firewall_command(alupc_exe: str, cast_port: int) -> list[str]:
    """Windows: Firewall-Freigaben für Handy-Steuerung (AluPC) und AirPlay – EINE Admin-Abfrage (UAC).
    Nur private Netzwerke (Zuhause/Schule), nicht öffentliche."""
    rules = [
        'netsh advfirewall firewall delete rule name="AluPC Handy"',
        f'netsh advfirewall firewall add rule name="AluPC Handy" dir=in action=allow protocol=TCP '
        f'localport={int(cast_port)}-{int(cast_port) + 9} profile=private,domain',
        'netsh advfirewall firewall delete rule name="AluPC AirPlay"',
        # alle Netzwerktypen: sonst blockiert Windows bei „öffentlichem“ WLAN die Verbindung (Name sichtbar,
        # Verbinden lädt endlos). Die Ports gehören nur UxPlay, das AluPC selbst startet.
        'netsh advfirewall firewall add rule name="AluPC AirPlay" dir=in action=allow protocol=TCP '
        'localport=7000,7001,7100 profile=any',
        'netsh advfirewall firewall add rule name="AluPC AirPlay" dir=in action=allow protocol=UDP '
        'localport=5353,6000,6001,7011 profile=any',
    ]
    for exe in uxplay_programs():
        # Hat man Windows' Nachfrage beim ersten Start weggeklickt, gibt es Sperr-Regeln für das Programm –
        # die gewinnen immer gegen Freigaben → alle Regeln dieses Programms ersetzen durch eine Freigabe
        rules += [f'netsh advfirewall firewall delete rule name=all program="{exe}"',
                  f'netsh advfirewall firewall add rule name="AluPC AirPlay (UxPlay)" dir=in action=allow '
                  f'program="{exe}" enable=yes profile=any']
    script = " & ".join(rules)
    ps = ("Start-Process -FilePath cmd.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
          f"'/c {script.replace(chr(39), chr(39) * 2)}'")
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]


def _winget(key: str) -> list[str]:
    """Über AluPC installieren (winget) – so merkt sich AluPC, was es neu installiert hat, und kann es beim
    Deinstallieren wieder entfernen (addons.py)."""
    from .addons import self_command

    return self_command("--zusatz", f"installieren={key}")


def bonjour_installed() -> bool:
    """Windows: gibt es den Dienst „Bonjour Service“ (Apple-Bonjour oder der von uxplay-windows)?"""
    if not IS_WINDOWS:
        return True
    if os.path.exists(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Bonjour",
                                   "mDNSResponder.exe")):
        return True
    try:
        return subprocess.run(["sc", "query", "Bonjour Service"], capture_output=True, timeout=10,
                              creationflags=0x08000000).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_plan(plan: list[tuple[str, list[str]]], status=None) -> list[str]:
    """Schritte nacheinander ausführen. Rückgabe: Fehlermeldungen (leer = alles gut)."""
    errors = []
    for i, (label, cmd) in enumerate(plan):
        if status:
            status(f"{label} …", i, len(plan))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                                  creationflags=0x08000000 if IS_WINDOWS else 0)
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        if proc.returncode in (126, 127) and cmd[0] == "pkexec":
            errors.append(f"{label}: abgebrochen (kein Passwort)")
        elif proc.returncode != 0:
            last = (proc.stderr or proc.stdout or "fehlgeschlagen").strip().splitlines()
            errors.append(f"{label}: {last[-1] if last else 'fehlgeschlagen'}")
    _vrtp_cache.clear()
    return errors


def default_airplay_name() -> str:
    """Name, unter dem der PC am iPhone erscheint: „AluPC (Rechnername)“."""
    host = re.sub(r"[^\w .-]", "", socket.gethostname().split(".")[0])[:24]
    return f"AluPC ({host})" if host else "AluPC"
