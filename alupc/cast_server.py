"""AluCast: Handy → Monitor 2 über den Browser – selbst gebaut, ohne App und ohne Zusatzprogramm.

AluPC startet einen kleinen Webserver im eigenen WLAN. Das Handy scannt den QR-Code (oder tippt die
Adresse und den 6-stelligen Code ein) und kann dann Fotos/Videos senden, Links (z. B. YouTube) und
Text zeigen sowie Monitor 2 fernsteuern. Funktioniert mit iPhone und Android gleich.

Was ein Browser nicht kann: den Handy-Bildschirm übertragen (dafür gibt es AirPlay).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, Signal

from .config import config_dir

MAX_UPLOAD = 2 * 1024 ** 3  # 2 GB
KEEP_FILES = 100
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".3gp", ".avi"}
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
            "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm", "video/3gpp": ".3gp"}
# Befehle, die das Handy auslösen darf (dazu „szene:Name“ und „lautstaerke:Zahl“)
ALLOWED_COMMANDS = {"standbild", "schwarz", "spiegeln", "erweitern", "bildschirmschoner", "naechste_szene",
                    "vorherige_szene", "zeichnungen_loeschen", "timer_start_pause", "timer_plus", "timer_minus",
                    "timer_neustart", "timer_zeigen", "video_pause", "video_vor", "video_zurueck",
                    "rgb_farbe", "rgb_monitor2", "rgb_aus", "zeichnung_zurueck", "kamera", "airplay", "qr",
                    "timer_stopp", "ablauf_weiter", "ablauf_zurueck",
                    "musik_zeigen", "musik_pause", "musik_weiter", "musik_zurueck", "overlays", "gluecksrad", "gluecksrad_drehen", "wetter", "system",
                    "umfrage_zeigen", "umfrage_ende", "spiele", "whiteboard",
                    "tafel_gaeste_aus", "tafel_gaeste_alle", "tafel_gaeste_frage",
                    "video_weiterschauen", "video_von_vorn", "video_vor30", "video_zurueck30", "video_neu",
                    "video_wiederholen",
                    # PC: Systemlautstärke, Desktop, Fenster, Bildschirmfoto (Ein/Aus nur am PC – mit Rückfrage)
                    "pc_lauter", "pc_leiser", "pc_stumm_an", "pc_stumm_aus", "pc_desktop", "pc_fenster_wechseln",
                    "pc_screenshot", "pc_sperren", "pc_minimieren", "pc_maximieren", "pc_fenster_zu"}  # Minispiele starten nur am PC
MAX_FAILS = 10
BLOCK_SECONDS = 60


def route_ip() -> str:
    """IP-Adresse, über die dieser PC ins Netz geht (ohne etwas zu senden)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.254.254.254", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


# Adapter, die Handys im WLAN nie erreichen: virtuelle Netze von WSL/Hyper-V, VMs, Docker, VPNs
VIRTUAL_HINTS = ("vethernet", "wsl", "hyper-v", "virtualbox", "vmware", "vmnet", "docker", "vbox", "virbr",
                 "br-", "tailscale", "zerotier", "wireguard", "tun", "tap", "vpn", "loopback", "bluetooth")


def score_address(ip: str, name: str, wifi: bool, route: str) -> int:
    """Wie wahrscheinlich erreicht ein Handy im WLAN diesen PC über diese Adresse? (höher = besser)"""
    low = name.lower()
    score = 0
    if ip == route:
        score += 3
    if wifi or any(w in low for w in ("wlan", "wi-fi", "wifi", "wireless", "wlp")):
        score += 3
    if any(v in low for v in VIRTUAL_HINTS):
        score -= 10
    if ip.startswith(("192.168.", "10.")):
        score += 2
    elif ip.startswith("172."):
        score -= 1  # oft virtuelle Netze (WSL, Docker)
    if ip.startswith(("127.", "169.254.")):
        score -= 20
    return score


def network_addresses() -> list[tuple[str, str, int]]:
    """Alle IPv4-Adressen des PCs: [(Adresse, Adaptername, Bewertung)], beste zuerst."""
    route = route_ip()
    found: dict[str, tuple[str, str, int]] = {}
    try:
        from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface

        for iface in QNetworkInterface.allInterfaces():
            flags = iface.flags()
            if not (flags & QNetworkInterface.IsUp and flags & QNetworkInterface.IsRunning) or \
                    flags & QNetworkInterface.IsLoopBack:
                continue
            wifi = iface.type() == QNetworkInterface.Wifi
            name = iface.humanReadableName() or iface.name()
            for entry in iface.addressEntries():
                addr = entry.ip()
                if addr.protocol() != QAbstractSocket.IPv4Protocol:
                    continue
                ip = addr.toString()
                found[ip] = (ip, name, score_address(ip, name, wifi, route))
    except Exception:  # noqa: BLE001 – ohne QtNetwork: nur die Route
        pass
    if route not in found and route != "127.0.0.1":
        found[route] = (route, "", score_address(route, "", False, route))
    return sorted(found.values(), key=lambda a: -a[2])


def local_ip(preferred: str = "") -> str:
    """IP-Adresse dieses PCs im WLAN/LAN: gewählte Adresse (falls noch vorhanden), sonst die beste."""
    addresses = network_addresses()
    if preferred and any(a[0] == preferred for a in addresses):
        return preferred
    return addresses[0][0] if addresses else route_ip()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def youtube_embed(url: str) -> str:
    """YouTube-Link → Vollbild-Player (ohne Seite drumherum). Andere Links bleiben unverändert."""
    u = urlparse(url)
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    vid = ""
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com"):
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [""])[0]
        elif u.path.startswith(("/shorts/", "/live/", "/embed/")):
            vid = u.path.split("/")[2] if len(u.path.split("/")) > 2 else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid or ""):
        return url
    start = parse_qs(u.query).get("t", [""])[0].rstrip("s")
    extra = f"&start={start}" if start.isdigit() else ""
    return f"https://www.youtube-nocookie.com/embed/{vid}?autoplay=1&rel=0{extra}"


def safe_name(name: str, content_type: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9äöüÄÖÜß._-]+", "_", Path(name or "handy").stem)[:60] or "handy"
    ext = Path(name or "").suffix.lower()
    if ext not in IMAGE_EXT | VIDEO_EXT:
        ext = MIME_EXT.get((content_type or "").split(";")[0].strip().lower(), "")
    return f"{time.strftime('%Y%m%d-%H%M%S')}_{stem}{ext}"


def upload_dir() -> Path:
    return config_dir() / "Vom Handy"


def cleanup(folder: Path, keep: int = KEEP_FILES) -> None:
    files = sorted((f for f in folder.glob("*") if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass



def live_request(path: str, data: dict) -> dict:
    """Laser, Zeichnen, Whiteboard vom Handy/iPad: prüfen und als Anfrage für den Controller bauen (HTTP und WebSocket).
    ValueError/TypeError bei ungültigen Werten."""
    if path == "/api/laser":
        if data.get("up"):
            return {"kind": "laser", "x": None, "y": None}
        x, y = float(data.get("x")), float(data.get("y"))
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("außerhalb")
        return {"kind": "laser", "x": x, "y": y}
    elif path == "/api/draw":  # mit dem Finger auf Monitor 2 zeichnen
        phase = str(data.get("phase", ""))
        if phase not in ("down", "move", "up"):
            raise ValueError("phase")
        req = {"kind": "draw", "phase": phase}
        if phase != "up":
            x, y = float(data.get("x")), float(data.get("y"))
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError("außerhalb")
            tool = str(data.get("tool", "stift"))
            color = str(data.get("color", "#ef4444"))
            if tool not in DRAW_TOOLS or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValueError("Werkzeug")
            req.update(x=x, y=y, tool=tool, color=color,
                       pressure=max(0.0, min(1.0, float(data.get("pressure", 0.5)))))
            if data.get("width") is not None:
                req["width"] = max(0.5, min(200.0, float(data["width"])))
        return req
    elif path == "/api/board":  # Whiteboard: zoomen/verschieben oder Seiten, Rückgängig …
        op = str(data.get("op", ""))
        if op == "view":
            req = {"kind": "board", "op": "view",
                   "zoom": max(0.2, min(5.0, float(data.get("zoom") or 1))) if data.get("zoom") else 0,
                   "dx": max(-2.0, min(2.0, float(data.get("dx", 0)))),
                   "dy": max(-2.0, min(2.0, float(data.get("dy", 0)))),
                   "ax": max(0.0, min(1.0, float(data.get("ax", 0.5)))),
                   "ay": max(0.0, min(1.0, float(data.get("ay", 0.5))))}
        elif op == "text":  # Text auf die Tafel (Stelle 0…1 im sichtbaren Ausschnitt)
            x, y = float(data.get("x")), float(data.get("y"))
            color = str(data.get("color", "#111827"))
            if not (0 <= x <= 1 and 0 <= y <= 1) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValueError("Text")
            req = {"kind": "board", "op": "text", "x": x, "y": y, "color": color,
                   "text": str(data.get("text", ""))[:500]}
        elif op == "einstellung":  # wie „Einstellungen“ im Whiteboard-Fenster am PC
            key = str(data.get("key", ""))
            req = {"kind": "board", "op": "einstellung", "key": key,
                   "value": board_setting(key, data.get("value"))}
        elif op in BOARD_OPS:
            arg = str(data.get("arg", ""))
            if op == "hintergrund" and not re.fullmatch(r"[a-z]{2,20}", arg):
                raise ValueError("Hintergrund")
            if op == "seite" and not re.fullmatch(r"\d{1,3}", arg):
                raise ValueError("Seite")
            if op.startswith("tafel_") and (len(arg) > 60 or "/" in arg or "\\" in arg):
                raise ValueError("Name")
            req = {"kind": "board", "op": op, "arg": arg}
        else:
            raise ValueError("op")
        return req
    raise ValueError("Pfad")


class CastServer(QObject):
    """Webserver in einem eigenen Thread; Anfragen kommen als Signal im Qt-Hauptthread an."""

    request = Signal(dict)
    state_changed = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port = 0
        self.snapshot = {"now": "", "scenes": [], "volume": 100, "video": False}
        self.preview = b""  # JPEG von Monitor 2 (vom Qt-Hauptthread erneuert, solange ein Handy zuschaut)
        self.preview_wanted = 0.0
        self.preview_seq = 0  # Nummer des Live-Bilds
        # Eingaben vom iPad (Striche, Zoom …) werden durchnummeriert; jedes Live-Bild weiß, bis zu welcher Nummer es
        # alles enthält – so ersetzt das iPad seine eigene Vorschau genau im richtigen Moment
        self.live_no = 0  # zuletzt vergebene Nummer (Webserver-Threads)
        self.applied = 0  # zuletzt vom Controller ausgeführte Nummer (Qt-Hauptthread)
        self.preview_applied = 0  # im aktuellen Live-Bild enthalten bis Nummer
        self.preview_fast = 0.0  # zuletzt „schnell“ angefragt (Zeichnen am iPad): öfter und schärfer
        self.board_thumbs: list[bytes] = []  # Whiteboard-Seiten als kleine JPEGs (Controller füllt sie)
        self.thumbs_wanted = 0.0
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self.poll = None  # laufende Abstimmung (polls.Poll) – Handys stimmen ohne Steuer-Code ab
        self.games = None  # Minispiele (games.GameHub) – Handys spielen ohne Steuer-Code mit
        self.chat = None  # Chat (chat.Chat) – Handys schreiben ohne Steuer-Code, Monitor 2 zeigt es sofort
        self.wall = None  # Foto-Wand (photowall.PhotoWall) – Handys schicken Fotos ohne Steuer-Code
        from .board_guests import BoardGuests

        self.board_guests = BoardGuests()  # Whiteboard für Gäste: wer darf schreiben (board_guests.py)
        self.board_info = None  # () → (Modus „aus“/„alle“/„frage“, Tafel gerade auf Monitor 2?) – vom Controller
        self.duel = None  # Schiffe versenken (duel.Battleship) – Spieler 2 am Handy, nur mit dem Stichwort
        self.access: dict[str, dict] = {}  # Freigabe-Anfragen: id → {name, ip, state, key, t}
        self.screen_stream = None  # Bildschirm an Fernseher senden (sender.HlsStream) – nur mit Zufallsschlüssel

    def set_preview(self, data: bytes, applied: int | None = None) -> None:
        self.preview = data
        self.preview_applied = self.applied if applied is None else applied
        self.preview_seq += 1

    def emit_live(self, req: dict) -> int:
        """Eingabe vom iPad weitergeben; Rückgabe: ihre Nummer (ein Live-Bild mit preview_applied ≥ Nummer zeigt sie)."""
        with self._lock:
            self.live_no += 1
            req["_no"] = self.live_no
        self.request.emit(req)
        return req["_no"]

    # ------------------------------------------------------------ Einstellungen
    def settings(self) -> dict:
        return {"port": 8765, "code": "", "autostart": False, **self.config["cast"]}

    def icon_png(self) -> bytes:
        """App-Symbol fürs Handy (Home-Bildschirm, Browser-Tab) – einmal erzeugt."""
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if getattr(self, "_icon", None) is None and app is not None and QThread.currentThread() == app.thread():
            try:
                from PySide6.QtCore import QBuffer, QByteArray, QIODevice

                from .ui import icons

                data = QByteArray()
                buf = QBuffer(data)
                buf.open(QIODevice.WriteOnly)
                icons.app_icon().pixmap(192, 192).toImage().save(buf, "PNG")
                buf.close()
                self._icon = bytes(data)
            except Exception:  # noqa: BLE001 – ohne Symbol geht es auch
                self._icon = b""
        return getattr(self, "_icon", None) or b""

    def allowed(self, what: str) -> bool:
        """Darf ein Handy das? („senden“, „steuern“, „live“, „laser“ – im Setup ausschaltbar)"""
        return bool({"senden": True, "steuern": True, "live": True, "laser": True,
                     **(self.settings().get("allow") or {})}.get(what, False))

    def code(self) -> str:
        code = self.settings()["code"]
        if not re.fullmatch(r"\d{6}", code or ""):
            code = new_code()
            self.config["cast"] = {**self.config["cast"], "code": code}
        return code

    def renew_code(self) -> str:
        self.config["cast"] = {**self.config["cast"], "code": new_code()}
        self.state_changed.emit()
        return self.code()

    def base(self) -> str:
        """http://IP:Port/ – läuft der Hotspot von AluPC, dessen Adresse (Handys sind dann in DIESEM WLAN)."""
        from .hotspot import hotspot

        ip = hotspot.ip if hotspot.running and hotspot.ip else local_ip(self.settings().get("ip", ""))
        # (Hotspot an → Handys sind in DIESEM WLAN, also diese Adresse – auch beim normalen Hotspot)
        return f"http://{ip}:{self.port or self.settings()['port']}/"

    def url(self, with_code: bool = True) -> str:
        base = self.base()
        return base + (f"?k={self.code()}" if with_code else "")

    def running(self) -> bool:
        return self.httpd is not None

    def poll_url(self) -> str:
        base = self.base()
        return base + (f"abstimmung?u={self.poll.token}" if self.poll else "abstimmung")

    def games_url(self) -> str:
        base = self.base()
        return base + (f"spiel?u={self.games.token}" if self.games else "spiel")

    # ------------------------------------------------------------ Start/Stopp
    def start(self) -> bool:
        if self.httpd is not None:
            return True
        self._fails.clear()  # neuer Start → alte Sperren vergessen
        self.code()
        self.icon_png()  # im Qt-Hauptthread erzeugen – der Webserver-Thread liefert es nur aus
        first = int(self.settings()["port"])
        handler = _make_handler(self)
        for port in range(first, first + 10):
            try:
                self.httpd = _QuietServer(("0.0.0.0", port), handler)
                break
            except OSError:
                continue
        if self.httpd is None:
            return False
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="AluCast", daemon=True)
        self.thread.start()
        global _ACTIVE
        _ACTIVE = self
        self.state_changed.emit()
        return True

    def stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        self.port = 0
        self.state_changed.emit()

    # ------------------------------------------------------------ Zugangscode prüfen (mit Sperre)
    # ------------------------------------------------------------ Geräte freigeben (statt Code)
    @staticmethod
    def _key_hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def devices(self) -> list[dict]:
        return list(self.settings().get("devices") or [])

    def device_ok(self, key: str) -> bool:
        if not key.startswith("d-") or len(key) < 20:
            return False
        h = self._key_hash(key)
        return any(hmac.compare_digest(h, d.get("hash", "")) for d in self.devices())

    def request_access(self, name: str, ip: str) -> str | None:
        """Handy bittet um Freigabe → Anfrage-ID (None = zu viele offene Anfragen von dieser Adresse)."""
        name = re.sub(r"[^\w .\-'äöüÄÖÜß]", "", str(name or ""))[:30].strip() or "Handy"
        from .hotspot import note_name

        note_name(ip, name)
        now = time.monotonic()
        with self._lock:
            for rid in [r for r, a in self.access.items() if now - a["t"] > 600]:
                del self.access[rid]
            if sum(1 for a in self.access.values() if a["ip"] == ip and a["state"] == "wait") >= 2:
                return None
            rid = secrets.token_urlsafe(12)
            self.access[rid] = {"name": name, "ip": ip, "state": "wait", "key": "", "t": now}
        # Immer am PC bestätigen – auch wenn diese Adresse schon mal erlaubt war (im WLAN wechseln Adressen)
        self.request.emit({"kind": "freigabe", "id": rid, "name": name, "ip": ip})
        return rid

    def answer_access(self, rid: str, allow: bool) -> bool:
        """Am PC erlaubt/abgelehnt. Erlaubt → Geräteschlüssel (gespeichert nur als Prüfsumme)."""
        with self._lock:
            req = self.access.get(rid)
            if req is None or req["state"] != "wait":
                return False
            if allow:
                key = "d-" + secrets.token_urlsafe(24)
                req.update(state="ok", key=key)
                entry = {"hash": self._key_hash(key), "name": req["name"], "added": time.strftime("%d.%m.%Y")}
                self.config["cast"] = {**self.config["cast"], "devices": [*self.devices(), entry]}
            else:
                req["state"] = "no"
        return True

    def access_state(self, rid: str) -> dict:
        with self._lock:
            req = self.access.get(rid)
            if req is None:
                return {"state": "unbekannt"}
            if req["state"] == "ok" and req["key"]:
                key, req["key"] = req["key"], ""  # Schlüssel nur EINMAL herausgeben
                return {"state": "ok", "key": key}
            return {"state": req["state"]}

    def forget_devices(self) -> None:
        self.config["cast"] = {**self.config["cast"], "devices": []}

    def check(self, ip: str, code: str) -> bool | None:
        """True = ok, False = falsch, None = gesperrt (zu viele Fehlversuche)."""
        if code and code.startswith("d-") and self.device_ok(code):  # freigegebenes Gerät
            return True
        now = time.monotonic()
        with self._lock:
            fails = [t for t in self._fails.get(ip, []) if now - t < BLOCK_SECONDS]
            self._fails[ip] = fails
            if len(fails) >= MAX_FAILS:
                return None
            if not code:  # ohne Code ist es kein Rateversuch
                return False
            if hmac.compare_digest((code or "").encode(), self.code().encode()):
                return True
            fails.append(now)
            return False


# Ohne AluPC-WLAN erreichbar: nur das App-Symbol – alles andere (auch Abstimmen) nur über das AluPC-WLAN
PUBLIC_PATHS = {"/icon.png", "/apple-touch-icon.png", "/favicon.ico", "/manifest.json"}
BLOCKED_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AluPC</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0e0f13;
color:#eceef1;font:16px system-ui,sans-serif;text-align:center;padding:24px;box-sizing:border-box}
b{font-size:22px;display:block;margin-bottom:10px}</style></head><body><div>
<b>📶 Nur über das AluPC-WLAN</b>Scanne zuerst den WLAN-Code auf dem Bildschirm, dann den Code der Seite.</div></body></html>"""


_ACTIVE = None  # laufender CastServer


def active_port() -> int:
    """Port, auf dem AluPCs Webserver gerade wirklich läuft (0 = läuft nicht)."""
    return _ACTIVE.port if _ACTIVE is not None and _ACTIVE.httpd is not None else 0


def wlan_url(path: str = "/") -> str:
    """Adresse einer Handy-Seite im AluPC-WLAN (für die QR-Codes auf Monitor 2), z. B. wlan_url("/chat").
    "" = AluPC-WLAN oder Webserver aus (Handys kommen nur über das AluPC-WLAN herein)."""
    from .hotspot import hotspot

    port = active_port()
    if not (port and hotspot.running and hotspot.ip):
        return ""
    return f"http://{hotspot.ip}:{port}{path if path.startswith('/') else '/' + path}"


class _QuietServer(ThreadingHTTPServer):
    """Handy bricht eine Verbindung ab (Seite gewechselt, WLAN weg) → kein Fehler-Stapel in der Konsole.
    Ist der Port schon belegt, schlägt das Binden fehl (→ AluPC nimmt den nächsten). Unter Windows würde
    SO_REUSEADDR einen belegten Port einfach mitbenutzen – dort deshalb exklusiv."""
    daemon_threads = True
    allow_reuse_address = not sys.platform.startswith("win")

    def server_bind(self):
        if sys.platform.startswith("win") and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        import sys

        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


DRAW_TOOLS = ("stift", "marker", "radierer", "pen", "eraser", "lasso", "line", "arrow", "rect", "ellipse", "laser")
BOARD_OPS = ("zoom_in", "zoom_out", "fit", "seite_neu", "seite_vor", "seite_zurueck", "seite_loeschen",
             "rueckgaengig", "wiederholen", "leeren", "auswahl_loeschen", "hintergrund", "seite", "tafel_neu",
             "tafel_oeffnen", "tafel_umbenennen")


def board_setting(key: str, value):
    """Whiteboard-Einstellung vom iPad/Handy prüfen (dieselben wie im Fenster „Einstellungen“) → sauberer Wert.
    Unbekannt/ungültig → ValueError."""
    from .board import FORMATS
    from .board_guests import MODES
    from .whiteboard import BACKGROUNDS

    ranges = {"width": (1, 20), "marker_width": (6, 60), "eraser": (6, 80), "text_size": (14, 120)}
    if key in ranges:
        lo, hi = ranges[key]
        return float(max(lo, min(hi, float(value))))
    if key in ("pressure", "pencil_only", "autosave", "follow"):
        if not isinstance(value, bool):
            raise ValueError(key)
        return value
    if key == "smooth":
        v = int(value)
        if v not in (0, 1, 2):
            raise ValueError(key)
        return v
    choices = {"format": FORMATS, "background": BACKGROUNDS, "guests": MODES}
    if key in choices:
        if str(value) not in choices[key]:
            raise ValueError(key)
        return str(value)
    if key == "colors":
        if not isinstance(value, list) or len(value) != 8 or \
                not all(re.fullmatch(r"#[0-9a-fA-F]{6}", str(c)) for c in value):
            raise ValueError(key)
        return [str(c) for c in value]
    raise ValueError(key)


def _make_handler(server: CastServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AluCast"
        protocol_version = "HTTP/1.1"
        timeout = 60  # hängende Verbindungen nicht ewig offen halten

        def log_message(self, *_args):  # nichts in die Konsole schreiben
            pass

        def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8",
                  headers: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode())

        def _auth(self) -> bool:
            if not server.running():  # nach „Beenden“ nichts mehr annehmen (auch offene Verbindungen)
                self.close_connection = True
                self._json(503, {"error": "AluCast ist beendet"})
                return False
            ok = server.check(self.client_address[0], self.headers.get("X-AluPC-Code", ""))
            if ok:
                return True
            self.close_connection = True
            if ok is None:
                self._json(429, {"error": "Zu viele falsche Codes – bitte 1 Minute warten"})
            else:
                self._json(403, {"error": "Falscher Code"})
            return False

        def _via_wlan(self) -> bool:
            """Handys kommen NUR über das AluPC-WLAN an AluPC – Steuern, Mitspielen, Chat ….
            Aus einem anderen Netz (Router-WLAN, LAN, Link) geht nichts. Der PC selbst (127.0.0.1) darf."""
            import ipaddress

            from .hotspot import hotspot

            try:
                ip = ipaddress.ip_address(self.client_address[0])
                if getattr(ip, "ipv4_mapped", None):
                    ip = ip.ipv4_mapped
                if ip.is_loopback:
                    return True
                if not (hotspot.running and hotspot.ip):
                    return False
                return ip in ipaddress.ip_network(f"{hotspot.ip}/24", strict=False)
            except ValueError:
                return False

        def _blocked(self, path: str) -> bool:
            """Nicht aus dem AluPC-WLAN → abgelehnt (nur Symbol/Abstimmung sind frei)."""
            if path in PUBLIC_PATHS or self._via_wlan():
                return False
            self.close_connection = True
            if path.startswith("/api/") or path.startswith("/ws/"):
                self._json(403, {"error": "Nur über das AluPC-WLAN: WLAN-Code auf dem Bildschirm scannen"})
            else:
                self._send(403, BLOCKED_PAGE.encode(), "text/html; charset=utf-8")
            return True

        def _body_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 100_000:
                raise ValueError("zu groß")
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("ungültig")
            return data

        def do_HEAD(self):
            if urlparse(self.path).path.startswith("/bildschirm/"):
                self._screen_stream(urlparse(self.path).path)
                return
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _screen_stream(self, path: str) -> None:
            """Bildschirm als HLS für Chromecast/AirPlay: aus jedem Netz, aber nur mit dem Schlüssel des Streams."""
            parts = path.split("/")
            stream = server.screen_stream
            got = None
            if stream is not None and len(parts) == 4 and secrets.compare_digest(parts[2], stream.token):
                got = stream.file(parts[3])
            body, ctype = got or (b"", "text/plain")
            self.send_response(200 if got else 404)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")  # Chromecast holt per XHR
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204 if urlparse(self.path).path.startswith("/bildschirm/") else 405)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/bildschirm/"):
                self._screen_stream(path)
                return
            if self._blocked(path):
                return
            if path in ("/", "/index.html"):
                from .cast_page import themed_page

                page = themed_page(server.config["appearance"])  # Handy sieht aus wie die PC-App
                self._send(200, page.encode(), "text/html; charset=utf-8")
            elif path.startswith("/fonts/") and path.endswith(".ttf"):  # gleiche Schrift wie am PC
                name = path[len("/fonts/"):]
                font = Path(__file__).resolve().parent / "assets" / "fonts" / name
                if "/" not in name and font.is_file():
                    self._send(200, font.read_bytes(), "font/ttf")
                else:
                    self._send(404, b"", "text/plain")
            elif path.startswith("/holz/") and path.endswith(".jpg"):  # Holzflächen für die Handy-Seite
                from .ui import theme

                parts = path[len("/holz/"):-len(".jpg")].split("/")
                if len(parts) == 2 and parts[0] in theme.WOODS and parts[1] in ("bg", "panel", "panel_hi"):
                    self._send(200, theme.wood_jpeg(parts[0], parts[1]), "image/jpeg")
                else:
                    self._send(404, b"", "text/plain")
            elif path == "/abstimmung":
                from .polls import POLL_PAGE

                self._send(200, POLL_PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/umfrage":
                q = parse_qs(urlparse(self.path).query)
                poll = self._poll(q.get("u", [""])[0])
                if poll is not None:
                    self._json(200, poll.public(q.get("v", [""])[0]))
            elif path == "/api/freigabe":  # Handy fragt nach: schon am PC erlaubt?
                self._json(200, server.access_state(parse_qs(urlparse(self.path).query).get("id", [""])[0]))
            elif path == "/anmelden":  # alte Lesezeichen: die Anmeldeseite gibt es nicht mehr → Steuerung
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path == "/chat":
                from .chat import CHAT_PAGE

                self._send(200, CHAT_PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/chat":
                if server.chat is None:
                    self._json(404, {"error": "Der Chat ist aus"})
                else:
                    try:
                        after = int(parse_qs(urlparse(self.path).query).get("after", ["0"])[0])
                    except ValueError:
                        after = 0
                    self._json(200, server.chat.since(after))
            elif path == "/fotos":
                from .photowall import PHOTO_PAGE

                self._send(200, PHOTO_PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/fotowand":
                if server.wall is None:
                    self._json(404, {"error": "Die Foto-Wand ist aus"})
                else:
                    self._json(200, server.wall.public())
            elif path == "/api/fotowand/bild":
                q = parse_qs(urlparse(self.path).query)
                try:
                    photo = server.wall.get(int(q.get("id", ["0"])[0])) if server.wall is not None else None
                except ValueError:
                    photo = None
                key = "thumb" if q.get("klein", [""])[0] else "path"
                try:
                    data = Path(photo.get(key) or photo["path"]).read_bytes() if photo else b""
                except OSError:
                    data = b""
                if data:
                    self._send(200, data, "image/jpeg")
                else:
                    self._json(404, {"error": "Foto nicht gefunden"})
            elif path == "/tafel":
                from .board_guests import GUEST_PAGE

                self._send(200, GUEST_PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/tafel":
                self._json(200, self._board_state(parse_qs(urlparse(self.path).query).get("g", [""])[0]))
            elif path == "/api/tafel/bild":  # Live-Bild der Tafel – nur wer schreiben darf und nur wenn sie zu sehen ist
                st = self._board_state(parse_qs(urlparse(self.path).query).get("g", [""])[0])
                if st["state"] != "frei" or not st["active"]:
                    self._json(403, {"error": "Kein Bild"})
                else:
                    server.preview_wanted = time.monotonic()
                    if server.preview:
                        self._send(200, server.preview, "image/jpeg")
                    else:
                        self._send(204, b"", "image/jpeg")
            elif path == "/schiffe":
                from .duel import PHONE_PAGE

                self._send(200, PHONE_PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/schiffe":
                duel = self._duel(parse_qs(urlparse(self.path).query).get("u", [""])[0])
                if duel is not None:
                    if duel.p2_client and duel.p2_client != self.client_address[0]:
                        self._json(423, {"error": "Spieler 2 spielt schon mit einem anderen Handy"})
                    else:
                        self._json(200, duel.view(2))
            elif path == "/spiel":
                from .game_page import GAME_PAGE

                self._send(200, GAME_PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/ws/live":  # iPad/Handy zeichnet: Live-Bild sofort + Striche ohne neue Anfragen
                self._live_ws(parse_qs(urlparse(self.path).query))
            elif path == "/ws/spiel":  # schnelle Verbindung fürs Spielen
                self._game_ws(parse_qs(urlparse(self.path).query))
            elif path == "/api/spiel":
                q = parse_qs(urlparse(self.path).query)
                hub = self._games(q.get("u", [""])[0])
                if hub is not None:
                    pid = q.get("p", [""])[0]
                    hub.seen(pid)
                    self._json(200, hub.state_for(pid))
            elif path == "/api/status":
                if self._auth():
                    if "heft" in parse_qs(urlparse(self.path).query):  # Heft-Ansicht offen → Seitenbilder bereithalten
                        server.thumbs_wanted = time.monotonic()
                    self._json(200, server.snapshot)
            elif path == "/api/board/seite":  # Vorschaubild einer Whiteboard-Seite (Heft-Ansicht am iPad)
                if self._auth() and self._may("laser"):
                    server.thumbs_wanted = time.monotonic()
                    try:
                        n = int(parse_qs(urlparse(self.path).query).get("n", ["0"])[0])
                    except ValueError:
                        n = -1
                    thumbs = server.board_thumbs
                    if 0 <= n < len(thumbs):
                        self._send(200, thumbs[n], "image/jpeg")
                    else:
                        self._send(204, b"", "image/jpeg")
            elif path == "/api/apps":  # installierte Programme (zum Öffnen vom Handy – nur aus dieser Liste)
                if self._auth() and self._may("steuern"):
                    from .pc_control import app_names

                    self._json(200, {"apps": app_names()[:400]})
            elif path == "/api/preview":
                if self._auth() and self._may("live"):
                    server.preview_wanted = time.monotonic()
                    if parse_qs(urlparse(self.path).query).get("fast"):
                        server.preview_fast = server.preview_wanted
                    if server.preview:
                        self._send(200, server.preview, "image/jpeg", {"X-AluPC-Frame": str(server.preview_applied)})
                    else:
                        self._send(204, b"", "image/jpeg")
            elif path in ("/icon.png", "/apple-touch-icon.png", "/favicon.ico"):
                self._send(200, server.icon_png(), "image/png")
            elif path == "/manifest.json":  # „Zum Home-Bildschirm“: öffnet wie eine App (ohne Browserleiste)
                self._send(200, json.dumps({"name": "AluPC-Fernbedienung", "short_name": "AluPC",
                                            "start_url": "/", "display": "standalone",
                                            "background_color": "#0e0f13", "theme_color": "#0e0f13",
                                            "icons": [{"src": "/icon.png", "sizes": "192x192",
                                                       "type": "image/png"}]}).encode(),
                           "application/manifest+json")
            else:
                self._json(404, {"error": "Nicht gefunden"})

        def _may(self, what: str) -> bool:
            if server.allowed(what):
                return True
            self._json(423, {"error": "In AluPC ausgeschaltet (Setup → Handy & Kamera)"})
            return False

        def _poll(self, token: str):
            """Laufende Abstimmung zu diesem Stichwort – sonst 404 (Stichwort statt Steuer-Code)."""
            poll = server.poll
            if not server.running() or poll is None or not hmac.compare_digest(token.encode(), poll.token.encode()):
                self._json(404, {"error": "Keine laufende Abstimmung"})
                return None
            return poll

        def _games(self, token: str):
            """Laufende Minispiel-Runde zu diesem Stichwort – sonst 404."""
            hub = server.games
            if not server.running() or hub is None or not hmac.compare_digest(token.encode(), hub.token.encode()):
                self._json(404, {"error": "Keine Spielrunde"})
                return None
            return hub

        def _board_mode(self) -> tuple[str, bool]:
            try:
                return server.board_info() if server.board_info else ("aus", False)
            except Exception:  # noqa: BLE001
                return "aus", False

        def _board_state(self, gid: str) -> dict:
            mode, active = self._board_mode()
            return {"state": server.board_guests.state(mode, gid), "active": active, "mode": mode}

        def _board_post(self):
            from .board_guests import BoardGuests, parse_stroke

            guests = server.board_guests
            if guests.throttle(self.client_address[0]):
                self._json(429, {"error": "Nicht so schnell"})
                return
            try:
                data = self._body_json()
            except (ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {"error": "Ungültige Anfrage"})
                return
            gid = str(data.get("g", ""))
            if not BoardGuests.valid_id(gid):
                self._json(400, {"error": "Ungültige Anfrage"})
                return
            mode, active = self._board_mode()
            guests.state(mode, gid)  # Modus gewechselt? → alte Erlaubnisse weg
            action = data.get("action")
            if action == "frage":
                if mode == "frage" and guests.ask(gid, str(data.get("name", "")), self.client_address[0]):
                    server.request.emit({"kind": "tafel_frage", "g": gid, "name": guests.names.get(gid, "Gast")})
                self._json(200, self._board_state(gid))
            elif action == "strich":
                if guests.state(mode, gid) != "frei":
                    self._json(403 if mode != "aus" else 404, {"error": "Das Whiteboard für Gäste ist aus" if mode == "aus"
                                                               else "Erst am PC erlauben lassen"})
                    return
                if not active:
                    self._json(423, {"error": "Das Whiteboard ist gerade nicht auf dem Bildschirm"})
                    return
                try:
                    stroke = parse_stroke(data)
                except (TypeError, ValueError):
                    stroke = None
                if stroke is None:
                    self._json(400, {"error": "Ungültiger Strich"})
                    return
                server.request.emit({"kind": "tafel_strich", **stroke})
                self._json(200, {"ok": True})
            else:
                self._json(400, {"error": "Ungültige Anfrage"})

        def _duel(self, token: str):
            """Laufendes Schiffe-versenken-Spiel zu diesem Stichwort – sonst 404."""
            duel = server.duel
            if not server.running() or duel is None or not hmac.compare_digest(token.encode(), duel.token.encode()):
                self._json(404, {"error": "Kein Schiffe-versenken-Spiel"})
                return None
            return duel

        def _live_ws(self, q):
            from . import ws

            if not server.running() or not server.check(self.client_address[0], q.get("c", [""])[0]) \
                    or not server.allowed("live"):
                self._json(403, {"error": "Falscher Code"})
                return
            key = self.headers.get("Sec-WebSocket-Key", "")
            if "websocket" not in self.headers.get("Upgrade", "").lower() or not key:
                self._json(400, {"error": "WebSocket erwartet"})
                return
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", ws.accept_key(key))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True

            def handle(path: str, data: dict) -> int:
                if not server.allowed("laser"):
                    raise ValueError("nicht erlaubt")
                return server.emit_live(live_request(path, data))

            ws.serve_live(self.connection, self.rfile, server, handle, server.running)

        def _game_ws(self, q):
            from . import ws

            hub = self._games(q.get("u", [""])[0])
            if hub is None:
                return
            key = self.headers.get("Sec-WebSocket-Key", "")
            if "websocket" not in self.headers.get("Upgrade", "").lower() or not key:
                self._json(400, {"error": "WebSocket erwartet"})
                return
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", ws.accept_key(key))
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            ws.serve_game(self.connection, self.rfile, hub, q.get("p", [""])[0],
                          lambda: server.running() and server.games is hub)

        def _game_post(self):
            data = self._body_json()
            hub = self._games(str(data.get("u", "")))
            if hub is None:
                return
            action, pid = data.get("action"), str(data.get("p", ""))
            if action == "join":
                from .hotspot import note_name

                note_name(self.client_address[0], str(data.get("name", "")))
                player = hub.join(str(data.get("name", "")), str(data.get("avatar", "")))
                if player is None:
                    self._json(423, {"error": "Spielrunde ist voll"})
                    return
                server.request.emit({"kind": "spiel"})
                self._json(200, {"p": player.pid, "color": player.color, "name": player.name,
                                 "avatar": player.avatar})
            elif action == "input":  # starten können Handys nicht – nur der PC
                inp = {k: v for k, v in data.items() if k not in ("u", "p", "action")}
                self._json(200 if hub.input(pid, inp) else 404, {})
            else:
                self._json(400, {"error": "Ungültige Anfrage"})

        def _chat_post(self):
            chat = server.chat
            if chat is None:
                self._json(404, {"error": "Der Chat ist aus"})
                return
            try:
                data = self._body_json()
            except (ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {"error": "Ungültige Anfrage"})
                return
            msg, why = chat.post(str(data.get("name", "")), str(data.get("text", "")), self.client_address[0],
                                 str(data.get("avatar", "")))
            if msg is None:
                code = 429 if "schnell" in why else 403 if "gesperrt" in why else 423 if "geschlossen" in why else 400
                self._json(code, {"error": why})
                return
            server.request.emit({"kind": "chat", "id": msg["id"]})
            self._json(200, {"id": msg["id"], "filtered": msg["filtered"]})

        def _wall_post(self, q):
            from .photowall import MAX_BYTES

            wall = server.wall
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = 0
            if wall is None or size <= 0 or size > MAX_BYTES:
                self.close_connection = True
                self._json(404 if wall is None else 413 if size > MAX_BYTES else 400,
                           {"error": "Die Foto-Wand ist aus" if wall is None else
                            "Foto zu groß (höchstens 25 MB)" if size > MAX_BYTES else "Leere Datei"})
                return
            chunks, left = [], size
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    self.close_connection = True
                    return
                chunks.append(chunk)
                left -= len(chunk)
            photo, why = wall.add(b"".join(chunks), q.get("name", [""])[0], self.client_address[0])
            if photo is None:
                code = 429 if "schnell" in why else 403 if "gesperrt" in why else 423 if "geschlossen" in why else 415
                self._json(code, {"error": why})
                return
            server.request.emit({"kind": "fotowand", "id": photo["id"]})
            self._json(200, {"id": photo["id"]})

        def do_POST(self):
            u = urlparse(self.path)
            if self._blocked(u.path):
                return
            if u.path == "/api/spiel":  # Minispiele: ohne Steuer-Code, nur mit dem Stichwort der Runde
                try:
                    self._game_post()
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._json(400, {"error": "Ungültige Anfrage"})
                return
            if u.path == "/api/tafel":  # Whiteboard für Gäste (ohne Steuer-Code, nur übers AluPC-WLAN)
                self._board_post()
                return
            if u.path == "/api/schiffe":  # Schiffe versenken: Spieler 2 am Handy (Stichwort statt Steuer-Code)
                duel = self._duel(parse_qs(u.query).get("u", [""])[0])
                if duel is None:
                    return
                try:
                    data = self._body_json()
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._json(400, {"error": "Ungültige Anfrage"})
                    return
                if data.get("action") == "join":
                    from .hotspot import note_name

                    note_name(self.client_address[0], str(data.get("name", "")))
                result = duel.phone_action(data, self.client_address[0])
                if result is None:
                    self._json(423, {"error": "Spieler 2 spielt schon mit einem anderen Handy"})
                else:
                    self._json(200, result)
                return
            if u.path == "/api/freigabe":  # ohne Code: am PC um Freigabe bitten
                try:
                    rid = server.request_access(str(self._body_json().get("name", "")), self.client_address[0])
                except (ValueError, TypeError, json.JSONDecodeError):
                    rid = server.request_access("", self.client_address[0])
                if rid is None:
                    self._json(429, {"error": "Schon angefragt – bitte am PC freigeben"})
                else:
                    self._json(200, {"id": rid})
                return
            if u.path == "/api/chat":  # Chat: ohne Steuer-Code, nur übers AluPC-WLAN
                self._chat_post()
                return
            if u.path == "/api/fotowand":  # Foto-Wand: ohne Steuer-Code, nur übers AluPC-WLAN
                self._wall_post(parse_qs(u.query))
                return
            if u.path == "/api/umfrage":  # Abstimmen: ohne Steuer-Code, nur mit dem Stichwort der Abstimmung
                try:
                    data = self._body_json()
                    poll = self._poll(str(data.get("u", "")))
                    if poll is None:
                        return
                    voter = str(data.get("v", ""))
                    if not poll.vote(voter, int(data.get("c", -1))):
                        self._json(400, {"error": "Stimme nicht gezählt"})
                        return
                    server.request.emit({"kind": "poll"})
                    self._json(200, poll.public(voter))
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._json(400, {"error": "Ungültige Anfrage"})
                return
            if not self._auth():
                return
            need = {"/api/upload": "senden", "/api/link": "senden", "/api/text": "senden",
                    "/api/laser": "laser", "/api/draw": "laser", "/api/board": "laser", "/api/cmd": "steuern",
                    "/api/ask": "steuern",
                    "/api/mouse": "steuern"}.get(u.path)
            if need and not self._may(need):
                self.close_connection = True
                return
            try:
                if u.path == "/api/upload":
                    self._upload(parse_qs(u.query).get("name", ["handy"])[0])
                    return
                data = self._body_json()
                if u.path == "/api/link":
                    url = str(data.get("url", "")).strip()
                    if not re.match(r"^(https?://)?[\w.-]+\.[a-z]{2,}", url, re.I):
                        self._json(400, {"error": "Das ist kein Link"})
                        return
                    if not url.lower().startswith(("http://", "https://")):
                        url = "https://" + url
                    server.request.emit({"kind": "link", "url": youtube_embed(url), "original": url})
                elif u.path == "/api/text":
                    live = bool(data.get("live"))
                    text = str(data.get("text", ""))[:2000]
                    text = text if live else text.strip()
                    if not text and not live:  # live darf leer sein (alles gelöscht)
                        self._json(400, {"error": "Kein Text"})
                        return
                    server.request.emit({"kind": "text", "text": text, "live": live})
                elif u.path in ("/api/laser", "/api/draw", "/api/board"):
                    self._json(200, {"ok": True, "seq": server.emit_live(live_request(u.path, data))})
                    return
                elif u.path == "/api/mouse":  # Handy als Touchpad
                    if "click" in data:
                        button = str(data["click"])
                        if button not in ("links", "rechts"):
                            raise ValueError("Taste")
                        server.request.emit({"kind": "mouse", "click": button})
                    elif "scroll" in data:
                        server.request.emit({"kind": "mouse", "scroll": max(-10, min(10, int(data["scroll"])))})
                    else:
                        dx, dy = float(data.get("dx", 0)), float(data.get("dy", 0))
                        if abs(dx) > 2000 or abs(dy) > 2000:
                            raise ValueError("zu weit")
                        server.request.emit({"kind": "mouse", "dx": dx, "dy": dy})
                elif u.path == "/api/ask":  # „Frag AluPC“: ganzer Satz, AluPC versteht ihn wie einen Sprachbefehl
                    text = str(data.get("text", "")).strip()[:300]
                    if not text:
                        self._json(400, {"error": "Kein Text"})
                        return
                    server.request.emit({"kind": "ask", "text": text})
                elif u.path == "/api/cmd":
                    cmd = str(data.get("cmd", ""))
                    if not (cmd in ALLOWED_COMMANDS or cmd.startswith("szene:")
                            or re.fullmatch(r"whiteboard:[a-z]{2,20}", cmd)
                            or re.fullmatch(r"lautstaerke:\d{1,3}", cmd) or re.fullmatch(r"timer:\d{1,5}", cmd)
                            or re.fullmatch(r"pc_lautstaerke:\d{1,3}", cmd)
                            or re.fullmatch(r"video_pos:\d{1,6}", cmd) or re.fullmatch(r"video_fortsetzen:\d", cmd)
                            or re.fullmatch(r"video_tempo:(0\.5|0\.75|1|1\.25|1\.5|2)", cmd)
                            or re.fullmatch(r"(ton|mic)_(laut:\d{1,3}|stumm:[01]|geraet:\d{1,2})", cmd)
                            or (cmd.startswith("pc_programm:") and len(cmd) < 120)
                            or re.fullmatch(r"taste:(weiter|zurueck|rechts|links|start|ende|schwarz|leer)", cmd)):
                        self._json(400, {"error": "Unbekannter Befehl"})
                        return
                    server.request.emit({"kind": "cmd", "cmd": cmd})
                else:
                    self._json(404, {"error": "Nicht gefunden"})
                    return
                self._json(200, {"ok": True})
            except (ValueError, TypeError, json.JSONDecodeError):
                self._json(400, {"error": "Ungültige Anfrage"})

        def _upload(self, name: str):
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = 0
            if size <= 0:
                self.close_connection = True
                self._json(400, {"error": "Leere Datei"})
                return
            if size > MAX_UPLOAD:
                self.close_connection = True
                self._json(413, {"error": "Datei zu groß (höchstens 2 GB)"})
                return
            filename = safe_name(name, self.headers.get("Content-Type", ""))
            ext = Path(filename).suffix
            if ext not in IMAGE_EXT | VIDEO_EXT:
                self.close_connection = True
                self._json(415, {"error": "Nur Fotos und Videos"})
                return
            folder = upload_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / filename
            left = size
            try:
                with open(target, "wb") as f:
                    while left > 0:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            raise ConnectionError("abgebrochen")
                        f.write(chunk)
                        left -= len(chunk)
            except (OSError, ConnectionError):
                target.unlink(missing_ok=True)
                self.close_connection = True
                return
            cleanup(folder)
            kind = "video" if ext in VIDEO_EXT else "image"
            server.request.emit({"kind": "file", "type": kind, "path": str(target)})
            self._json(200, {"ok": True, "type": kind})

    return Handler


_server: CastServer | None = None


def cast_server(config=None) -> CastServer:
    global _server
    if _server is None:
        if config is None:
            raise RuntimeError("AluCast ist noch nicht eingerichtet")
        _server = CastServer(config)
    elif config is not None:
        _server.config = config
    return _server
