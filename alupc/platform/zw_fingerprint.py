"""Fingerabdruckmodule mit eigenem Chip am seriellen Anschluss (z. B. Hi-Link HLK-ZW101/ZW0922,
AS608, R307) – über einen USB-Seriell-Adapter (CH340, CP2102, …) an Windows und Linux.

Solche Module speichern und vergleichen Fingerabdrücke selbst. Sie sprechen das verbreitete
„EF01“-Protokoll (Paketkopf EF 01, Adresse FF FF FF FF). Windows Hello und libfprint kennen sie
nicht – AluPC steuert sie direkt.

Dieses Modul importiert absichtlich kein Qt: Die Anmelde-Prüfung (PAM, siehe `pam_check`) startet
es ohne Oberfläche und soll schnell sein.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from .base import Cancelled, FINGER_NAMES, FingerprintBackend, Sensor

try:
    import serial  # pyserial
    from serial.tools import list_ports

    HAVE_SERIAL = True
except ImportError:  # pragma: no cover - nur ohne pyserial
    HAVE_SERIAL = False

HEADER = b"\xef\x01"
ADDRESS = b"\xff\xff\xff\xff"
PID_COMMAND, PID_DATA, PID_ACK, PID_END = 0x01, 0x02, 0x07, 0x08

CMD_GET_IMAGE = 0x01
CMD_GEN_CHAR = 0x02
CMD_SEARCH = 0x04
CMD_REG_MODEL = 0x05
CMD_STORE = 0x06
CMD_DELETE = 0x0C
CMD_EMPTY = 0x0D
CMD_READ_SYS_PARA = 0x0F
CMD_VERIFY_PASSWORD = 0x13
CMD_TEMPLATE_COUNT = 0x1D
CMD_READ_INDEX_TABLE = 0x1F
CMD_LED = 0x3C  # ZW101 „ControlBLN“: Art (2 = blinken, 4 = aus), Startfarbe, Endfarbe, Wiederholungen
LED_GREEN, LED_RED, LED_BLUE = 0x02, 0x04, 0x01

OK, NO_FINGER, NOT_FOUND = 0x00, 0x02, 0x09
ERRORS = {
    0x01: "Übertragungsfehler",
    0x02: "Kein Finger auf dem Sensor",
    0x03: "Aufnahme fehlgeschlagen",
    0x06: "Bild zu unscharf – Finger fester und ruhig auflegen",
    0x07: "Zu wenig Merkmale – Finger fester auflegen",
    0x08: "Fingerabdruck passt nicht",
    0x09: "Fingerabdruck nicht gefunden",
    0x0A: "Die Aufnahmen passen nicht zusammen – immer denselben Finger nehmen",
    0x0B: "Speicherplatz außerhalb des Bereichs",
    0x10: "Löschen fehlgeschlagen",
    0x11: "Speicher leeren fehlgeschlagen",
    0x13: "Falsches Modul-Passwort",
    0x15: "Kein gültiges Bild im Speicher",
    0x18: "Fehler beim Schreiben in den Speicher des Moduls",
}

# Übliche USB-Seriell-Chips (werden zuerst probiert): CH340/CH341, CP210x, FTDI, PL2303, CH9102
USB_SERIAL_IDS = {(0x1A86, 0x7523), (0x1A86, 0x5523), (0x1A86, 0x55D4), (0x10C4, 0xEA60), (0x0403, 0x6001),
                  (0x0403, 0x6015), (0x067B, 0x2303)}
BAUDS = (57600, 115200, 9600)  # ZW101 ab Werk: 57600
SCANS_PER_ENROLL = 2
LOGIN_FILE = Path("/etc/alupc/fingerprint-login.json")


class SensorError(RuntimeError):
    pass


_PORT_LOCKS: dict[str, threading.RLock] = {}
_LED_UNSUPPORTED: dict[str, bool] = {}  # Anschluss → Modul kennt den LED-Befehl nicht
_PORT_LOCKS_GUARD = threading.Lock()


def _port_lock(port: str) -> threading.RLock:
    with _PORT_LOCKS_GUARD:
        return _PORT_LOCKS.setdefault(port, threading.RLock())


def checksum(data: bytes) -> int:
    return sum(data) & 0xFFFF


def build_packet(pid: int, payload: bytes) -> bytes:
    length = len(payload) + 2
    body = bytes([pid]) + length.to_bytes(2, "big") + payload
    return HEADER + ADDRESS + body + checksum(body).to_bytes(2, "big")


def parse_packet(data: bytes) -> tuple[int, bytes]:
    """(PID, Nutzdaten) aus einem kompletten Paket; wirft SensorError bei Unsinn."""
    if len(data) < 12 or data[:2] != HEADER:
        raise SensorError("Ungültige Antwort vom Modul")
    pid = data[6]
    length = int.from_bytes(data[7:9], "big")
    payload = data[9:9 + length - 2]
    got = int.from_bytes(data[9 + length - 2:9 + length], "big")
    if checksum(data[6:9 + length - 2]) != got:
        raise SensorError("Prüfsumme falsch (Kabel/Baudrate?)")
    return pid, payload


class ZWSensor:
    """Verbindung zu einem Modul (EF01-Protokoll)."""

    def __init__(self, port: str, baud: int = 57600, timeout: float = 1.0):
        self.port, self.baud, self.timeout = port, baud, timeout
        self.ser = None

    def __enter__(self):
        if self.ser is None:  # schon offen (z. B. von _open) → nicht ein zweites Mal öffnen: Windows verbietet das
            self.open()
        return self

    def __exit__(self, *_):
        self.close()

    def open(self, busy_wait: float = 4.0):
        if not HAVE_SERIAL:
            raise SensorError("Python-Paket „pyserial“ fehlt")
        # Innerhalb von AluPC nacheinander: unter Windows darf nur einer einen COM-Anschluss offen haben
        # (z. B. liest die Fingerabdruck-Seite die Finger, während der Assistent anlernen will)
        lock = _port_lock(self.port)
        if not lock.acquire(timeout=15):
            raise SensorError(f"{self.port} ist in AluPC noch beschäftigt – gleich nochmal versuchen")
        deadline = time.monotonic() + busy_wait
        try:
            while True:
                try:
                    extra = {} if sys.platform.startswith("win") else {"exclusive": True}
                    self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout, write_timeout=self.timeout,
                                             **extra)
                    break
                except serial.SerialException as exc:
                    text = str(exc)
                    if "exclusively lock" in text:  # Linux: gerade liest ein anderes Programm (PAM, AluPC)
                        if time.monotonic() < deadline:
                            time.sleep(0.15)
                            continue
                        raise SensorError(f"{self.port} wird gerade von einem anderen Programm gelesen") from exc
                    denied = "ermission" in text or "Zugriff" in text or "Access" in text or "verweigert" in text
                    if denied and sys.platform.startswith("win") and time.monotonic() < deadline:
                        time.sleep(0.3)  # gerade belegt (z. B. kurz von der Anmeldekachel) → kurz warten
                        continue
                    if denied and sys.platform.startswith("win"):
                        raise SensorError(f"{self.port} ist von einem anderen Programm belegt (z. B. Arduino-IDE, "
                                          "serieller Monitor, Cura, ein zweites AluPC) – das schließen und "
                                          "nochmal versuchen") from exc
                    if denied:
                        raise SensorError(f"Kein Zugriff auf {self.port} – siehe „Automatisch einrichten“") from exc
                    raise SensorError(f"{self.port} lässt sich nicht öffnen: {text}") from exc
            self.ser.reset_input_buffer()
        except BaseException:
            self.ser = None
            lock.release()
            raise
        self._locked = lock

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None
        lock = getattr(self, "_locked", None)
        if lock is not None:
            self._locked = None
            lock.release()

    def _read_exact(self, n: int) -> bytes:
        data = b""
        deadline = time.monotonic() + self.timeout
        while len(data) < n and time.monotonic() < deadline:
            chunk = self.ser.read(n - len(data))
            if chunk:
                data += chunk
        if len(data) < n:
            raise SensorError("Keine Antwort vom Modul (falscher Anschluss oder Baudrate?)")
        return data

    def _read_packet(self) -> tuple[int, bytes]:
        # bis zum Paketkopf vorlesen (Reste im Puffer überspringen) – mit Gesamtfrist, damit ein Gerät,
        # das ununterbrochen etwas anderes sendet (Arduino, GPS …), nichts ewig blockiert
        deadline = time.monotonic() + self.timeout * 2
        skipped = 0
        first = self._read_exact(1)
        while True:
            second = self._read_exact(1)
            if first + second == HEADER:
                break
            first = second
            skipped += 1
            if skipped > 256 or time.monotonic() > deadline:
                raise SensorError("Kein Fingerabdruckmodul (unerwartete Daten)")
        rest = self._read_exact(7)  # Adresse (4), PID (1), Länge (2)
        length = int.from_bytes(rest[5:7], "big")
        body = self._read_exact(length)
        return parse_packet(HEADER + rest + body)

    def command(self, code: int, params: bytes = b"") -> tuple[int, bytes]:
        """Befehl senden → (Bestätigungscode, Daten)."""
        self.ser.write(build_packet(PID_COMMAND, bytes([code]) + params))
        pid, payload = self._read_packet()
        if pid != PID_ACK or not payload:
            raise SensorError("Unerwartete Antwort vom Modul")
        return payload[0], payload[1:]

    def check(self, code: int, params: bytes = b"") -> bytes:
        confirm, data = self.command(code, params)
        if confirm != OK:
            raise SensorError(ERRORS.get(confirm, f"Fehlercode {confirm:#04x}"))
        return data

    # ---- Befehle
    def handshake(self) -> bool:
        confirm, _ = self.command(CMD_VERIFY_PASSWORD, (0).to_bytes(4, "big"))
        return confirm == OK

    def sys_params(self) -> dict:
        d = self.check(CMD_READ_SYS_PARA)
        return {"capacity": int.from_bytes(d[4:6], "big") or 100, "security": int.from_bytes(d[6:8], "big"),
                "packet": 32 << int.from_bytes(d[12:14], "big"), "baud": int.from_bytes(d[14:16], "big") * 9600}

    def get_image(self) -> int:
        return self.command(CMD_GET_IMAGE)[0]

    def gen_char(self, buffer: int) -> None:
        self.check(CMD_GEN_CHAR, bytes([buffer]))

    def reg_model(self) -> None:
        self.check(CMD_REG_MODEL)

    def store(self, buffer: int, slot: int) -> None:
        self.check(CMD_STORE, bytes([buffer]) + slot.to_bytes(2, "big"))

    def search(self, buffer: int, capacity: int) -> tuple[int, int] | None:
        confirm, d = self.command(CMD_SEARCH, bytes([buffer]) + (0).to_bytes(2, "big") + capacity.to_bytes(2, "big"))
        if confirm == NOT_FOUND:
            return None
        if confirm != OK:
            raise SensorError(ERRORS.get(confirm, f"Fehlercode {confirm:#04x}"))
        return int.from_bytes(d[0:2], "big"), int.from_bytes(d[2:4], "big")

    def led(self, result: str) -> bool:
        """Rückmeldung am Modul: „ok“ = grün blinken, „fail“ = rot blinken. Module ohne LED-Befehl melden einen
        Fehler – dann eben ohne Licht (nie eine Ausnahme, die Anmeldung darf daran nicht scheitern)."""
        if _LED_UNSUPPORTED.get(self.port):
            return False
        color = LED_GREEN if result == "ok" else LED_RED
        try:
            confirm, _ = self.command(CMD_LED, bytes([2, color, color, 2 if result == "ok" else 3]))
        except Exception:  # noqa: BLE001
            confirm = -1
        if confirm != OK:
            _LED_UNSUPPORTED[self.port] = True
        return confirm == OK

    def identify(self, capacity: int, tries: int = 3, window: float = 1.5) -> tuple[int, int] | None:
        """Finger liegt auf → erkennen. Ist das erste Bild schlecht (Finger halb aufgelegt, verwischt), gleich
        nochmal aufnehmen, solange der Finger liegt – bis zu `tries`-mal in `window` s. So reicht EIN Auflegen."""
        end = time.monotonic() + window
        for attempt in range(tries):
            if attempt:
                time.sleep(0.08)
                if time.monotonic() > end or self.get_image() != OK:
                    return None  # Finger weg oder Zeit um
            try:
                self.gen_char(1)
                hit = self.search(1, capacity)
            except SensorError:
                hit = None
            if hit is not None:
                return hit
        return None

    def delete(self, slot: int, count: int = 1) -> None:
        self.check(CMD_DELETE, slot.to_bytes(2, "big") + count.to_bytes(2, "big"))

    def empty(self) -> None:
        self.check(CMD_EMPTY)

    def used_slots(self, capacity: int) -> set[int]:
        used = set()
        for page in range(max(1, (capacity + 255) // 256)):
            table = self.check(CMD_READ_INDEX_TABLE, bytes([page]))
            for i, byte in enumerate(table[:32]):
                for bit in range(8):
                    if byte & (1 << bit):
                        used.add(page * 256 + i * 8 + bit)
        return {s for s in used if s < capacity}


# --------------------------------------------------------------------------- Suche
def candidate_ports() -> list[str]:
    """Nur USB-Seriell-Adapter mit typischen Chips (CH340, CP210x, FTDI, PL2303) – andere Geräte
    (Arduino mit eigenem USB, Modems …) werden nicht angefasst. Zuletzt gefundener Anschluss zuerst."""
    if not HAVE_SERIAL:
        return []
    ports = [p.device for p in list_ports.comports() if (p.vid, p.pid) in USB_SERIAL_IDS]
    last = _last_port()
    ports.sort(key=lambda d: d != last)
    return ports


def _cache_file() -> Path:
    from ..config import config_dir

    return config_dir() / "fingerprint-module.json"


def _last_port() -> str:
    try:
        return json.loads(_cache_file().read_text(encoding="utf-8")).get("port", "")
    except Exception:  # noqa: BLE001
        return ""


def _remember(port: str, baud: int) -> None:
    try:
        _cache_file().parent.mkdir(parents=True, exist_ok=True)
        _cache_file().write_text(json.dumps({"port": port, "baud": baud}), encoding="utf-8")
    except OSError:
        pass


def probe(port: str) -> tuple[int, dict] | None:
    """Antwortet an `port` ein Modul? → (Baudrate, Parameter)."""
    remembered = None
    try:
        cached = json.loads(_cache_file().read_text(encoding="utf-8"))
        if cached.get("port") == port:
            remembered = int(cached.get("baud", 0))
    except Exception:  # noqa: BLE001
        pass
    for baud in ([remembered] if remembered else []) + [b for b in BAUDS if b != remembered]:
        try:
            with ZWSensor(port, baud, timeout=0.4) as s:
                if s.handshake():
                    return baud, s.sys_params()
        except SensorError as exc:
            if "Kein Zugriff" in str(exc) or "belegt" in str(exc):
                raise
        except Exception:  # noqa: BLE001
            continue
    return None


CHIP_NAMES = {0x1A86: "CH340", 0x10C4: "CP210x", 0x0403: "FTDI", 0x067B: "PL2303"}
DRIVER_PAGES = {"CH340": "wch-ic.com/downloads/CH341SER_EXE.html",
                "CP210x": "silabs.com/developers/usb-to-uart-bridge-vcp-drivers",
                "FTDI": "ftdichip.com/drivers/vcp-drivers", "PL2303": "prolific.com.tw"}


def windows_adapter_without_driver() -> str:
    """Windows: steckt ein USB-Seriell-Adapter (CH340 …), für den kein Treiber läuft (kein COM-Anschluss)?
    → Chipname, sonst leer."""
    if not sys.platform.startswith("win"):
        return ""
    import subprocess

    script = ("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_(1A86|10C4|0403|067B)' "
              "-and $_.Status -ne 'OK' } | ForEach-Object { $_.InstanceId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=15, creationflags=0x08000000).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        for vid, name in CHIP_NAMES.items():
            if f"VID_{vid:04X}" in line.upper():
                return f"{name} (USB {vid:04X})"
    return ""


# --------------------------------------------------------------------------- Namen der Plätze
def _slots_file() -> Path:
    from ..config import config_dir

    return config_dir() / "fingerprint-slots.json"


def load_slots() -> dict[str, dict]:
    try:
        return json.loads(_slots_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_slots(slots: dict[str, dict]) -> None:
    path = _slots_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(slots, indent=1), encoding="utf-8")


def current_user() -> str:
    import getpass

    return getpass.getuser()


# --------------------------------------------------------------------------- Personen
# Mehrere Personen können Finger anlernen (z. B. 5 Personen × 10 Finger = 50 Plätze). Alle angelernten Personen
# dürfen das Konto entsperren, unter dem AluPC läuft. Die Person steht nur in der Plätze-Datei (Namen/Zuordnung).
_person = ""


def set_person(name: str) -> None:
    global _person
    _person = (name or "").strip()


def get_person() -> str:
    return _person or current_user()


def persons(slots: dict | None = None) -> list[str]:
    """Personen mit Fingern im eigenen Konto (Reihenfolge: zuerst die aktuelle, dann alphabetisch)."""
    user = current_user()
    names = {info.get("person") or user for info in (slots if slots is not None else load_slots()).values()
             if info.get("user") == user}
    return sorted(names, key=lambda n: (n != get_person(), n.lower()))


# --------------------------------------------------------------------------- Dual-Boot-Abgleich
# Die Fingerabdrücke selbst liegen im Modul – Windows und Linux sehen also dieselben Plätze. Abgeglichen werden
# nur die Namen (Person, Finger) und die Zuordnung zum Konto. Der Benutzername des anderen Systems wird auf den
# eigenen umgeschrieben (Windows „Noah“ → Linux „noah“).
def sync_export() -> dict:
    slots = load_slots()
    return {"user": current_user(), "slots": slots} if slots else {}


def merge_synced(local: dict, remote: dict, me: str) -> dict:
    other = remote.get("user", "")
    merged = dict(local)
    for slot, info in (remote.get("slots") or {}).items():
        if not (str(slot).isdigit() and isinstance(info, dict) and isinstance(info.get("finger"), str)):
            continue
        info = {k: v for k, v in info.items() if k in ("finger", "user", "person") and isinstance(v, str)}
        if info.get("user") == other or not info.get("user"):
            info["person"] = info.get("person") or other or me
            info["user"] = me
        merged[str(int(slot))] = info
    return merged


def sync_import(remote: dict) -> bool:
    """Namen vom anderen System übernehmen. True = etwas geändert."""
    if not isinstance(remote, dict) or not remote.get("slots"):
        return False
    local = load_slots()
    merged = merge_synced(local, remote, current_user())
    if merged == local:
        return False
    save_slots(merged)
    if sys.platform.startswith("win"):  # Windows-Anmeldung: eigene Plätze-Datei nachziehen (falls eingerichtet)
        from .windows_serial_login import write_own_slots

        user = current_user()
        write_own_slots(sorted(int(k) for k, v in merged.items() if v.get("user") == user), names=my_names(merged))
    return True


def my_names(slots: dict | None = None) -> dict[int, str]:
    """Platz → Name der Person (eigenes Konto) – für „Hallo Lena“ auf dem Windows-Sperrbildschirm."""
    user = current_user()
    return {int(k): v.get("person") or "" for k, v in (load_slots() if slots is None else slots).items()
            if v.get("user") == user and str(k).isdigit()}


# --------------------------------------------------------------------------- Backend
class SerialFingerprintBackend(FingerprintBackend):
    name = "Modul am seriellen Anschluss"
    can_enroll = True
    can_delete = True
    can_list_enrolled = True
    login_password = False  # Windows: zum Einschalten wird das Windows-Passwort gebraucht

    @property
    def login_toggle(self) -> bool:
        """Linux: PAM · Windows: mitgelieferter Anmeldebaustein (nur in der fertigen Version)."""
        if sys.platform.startswith("linux"):
            return True
        from .windows_serial_login import available

        return available()

    def __init__(self):
        self.usage: tuple[int, int] | None = None  # (belegt, Plätze), nach list_enrolled
        self.login_check = ""  # Windows: kennt die Anmeldung alle Finger? (siehe _check_windows_login)
        self._cancel = threading.Event()
        self._found: dict[str, tuple[int, dict]] = {}

    # ---- Sensoren
    def availability(self):
        if not HAVE_SERIAL:
            return (False, "Python-Paket „pyserial“ fehlt.")
        try:
            sensors = self.list_sensors()
        except SensorError as exc:
            return (False, str(exc))
        if not sensors:
            return (False, "Kein Fingerabdruckmodul am seriellen Anschluss gefunden.")
        return (True, "")

    def list_sensors(self):
        sensors = []
        self._found = {}
        denied = []
        for port in candidate_ports():
            try:
                result = probe(port)
            except SensorError as exc:  # kein Zugriff auf diesen Anschluss → trotzdem die anderen prüfen
                denied.append(str(exc))
                continue
            if result is None:
                continue
            baud, params = result
            self._found[port] = result
            sensors.append(Sensor(id=port, name=f"Fingerabdruckmodul an {port}",
                                  detail=f"{params['capacity']} Plätze, {baud} Baud"))
        if not sensors and denied:
            raise SensorError(denied[0])
        if sensors:
            _remember(sensors[0].id, self._found[sensors[0].id][0])
        return sensors

    def _open(self, port: str) -> tuple[ZWSensor, int]:
        baud, params = self._found.get(port) or probe(port) or (None, None)
        if baud is None:
            raise SensorError(f"Kein Modul an {port}")
        self._found[port] = (baud, params)
        s = ZWSensor(port, baud, timeout=1.0)
        s.open()
        return s, params["capacity"]

    # ---- Finger
    def finger_label(self, key: str) -> str:
        slot = key.split(":", 1)[1] if key.startswith("platz:") else key
        info = load_slots().get(slot, {})
        name = FINGER_NAMES.get(info.get("finger", ""), "Finger")
        who = info.get("user")
        if not info and slot.isdigit() and int(slot) in foreign_slots():
            return f"Finger eines anderen Benutzers (Platz {slot})"
        person = info.get("person") or ""
        prefix = f"{person} · " if person else ""
        return f"{prefix}{name} (Platz {slot}" + (f", Konto {who})" if who and who != current_user() else ")")

    def list_enrolled(self, sensor_id):
        s, cap = self._open(sensor_id)
        with s:
            used = s.used_slots(cap)
        slots = load_slots()
        # Plätze, die im Modul gelöscht wurden, auch hier vergessen
        cleaned = {k: v for k, v in slots.items() if int(k) in used}
        if cleaned != slots:
            save_slots(cleaned)
        self.usage = (len(used), cap)  # Belegung (für „12 von 50 Plätzen“)
        if sys.platform.startswith("win"):
            self._check_windows_login()
        # nach Person sortiert (dann Platz), damit die Liste gruppiert erscheint

        def order(slot):
            info = cleaned.get(str(slot), {})
            return ((info.get("person") or info.get("user") or "~").lower(), slot)

        return [f"platz:{slot}" for slot in sorted(used, key=order)]

    def _count(self, s: ZWSensor, cap: int) -> None:
        """Belegung gleich nach Anlernen/Löschen neu zählen (für „x belegt · y frei“)."""
        try:
            self.usage = (len(s.used_slots(cap)), cap)
        except SensorError:
            pass

    def is_enrolled(self, sensor_id, finger) -> bool:
        used = set(self.list_enrolled(sensor_id))
        user, person = current_user(), get_person()
        return any(info.get("finger") == finger and info.get("user") == user
                   and (info.get("person") or user) == person and f"platz:{slot}" in used
                   for slot, info in load_slots().items())

    def _wait_finger(self, s: ZWSensor, status, text, stage, total, timeout=30.0):
        status(text, stage, total)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._cancel.is_set():
                raise Cancelled()
            code = s.get_image()
            if code == OK:
                return
            if code != NO_FINGER:
                status(ERRORS.get(code, "Bitte noch einmal auflegen."), stage, total)
            time.sleep(0.05)
        raise SensorError("Zeit abgelaufen – kein Finger erkannt")

    def _wait_lift(self, s: ZWSensor, status, stage, total):
        status("Finger kurz abheben …", stage, total)
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if self._cancel.is_set():
                raise Cancelled()
            if s.get_image() == NO_FINGER:
                return
            time.sleep(0.05)

    def enroll(self, sensor_id, finger, status):
        self._cancel.clear()
        user, person = current_user(), get_person()
        s, cap = self._open(sensor_id)
        total = SCANS_PER_ENROLL + 1
        with s:
            used = s.used_slots(cap) | foreign_slots(user)
            free = next((i for i in range(cap) if i not in used), None)
            if free is None:
                raise SensorError("Der Speicher des Moduls ist voll – bitte erst Finger löschen.")
            for scan in range(1, SCANS_PER_ENROLL + 1):
                for attempt in range(3):
                    self._wait_finger(s, status, f"Finger auflegen ({scan}/{SCANS_PER_ENROLL}) …", scan - 1, total)
                    try:
                        s.gen_char(scan)
                        break
                    except SensorError as exc:
                        status(f"{exc} – noch einmal.", scan - 1, total)
                        self._wait_lift(s, status, scan - 1, total)
                else:
                    raise SensorError("Der Finger wurde nicht gut erkannt – bitte später erneut versuchen.")
                if scan < SCANS_PER_ENROLL:
                    self._wait_lift(s, status, scan, total)
            status("Speichern …", SCANS_PER_ENROLL, total)
            s.reg_model()
            s.store(1, free)
            # derselbe Finger derselben Person war schon angelernt → alten Platz freigeben
            slots = load_slots()
            for slot, info in list(slots.items()):
                if info.get("finger") == finger and info.get("user") == user and int(slot) != free \
                        and (info.get("person") or user) == person:
                    try:
                        s.delete(int(slot))
                    except SensorError:
                        pass
                    slots.pop(slot)
            slots[str(free)] = {"finger": finger, "user": user, "person": person}
            save_slots(slots)
            self._count(s, cap)
            status("Fertig! Finger gespeichert.", total, total)
        self._sync_login(sensor_id)

    def verify(self, sensor_id, status):
        self._cancel.clear()
        s, cap = self._open(sensor_id)
        with s:
            self._wait_finger(s, status, "Finger auf den Sensor legen …", 0, 0)
            hit = s.identify(cap)
            s.led("ok" if hit is not None else "fail")
        if hit is None:
            return (False, "Nicht erkannt – dieser Finger ist nicht gespeichert.")
        slot, score = hit
        return (True, f"Erkannt: {self.finger_label(f'platz:{slot}')} – Übereinstimmung {score}")

    def delete(self, sensor_id, finger):
        s, cap = self._open(sensor_id)
        with s:
            others = foreign_slots() | {int(k) for k, v in load_slots().items()
                                         if v.get("user") not in (None, current_user())}
            if finger == "*":
                if not others:
                    s.empty()
                    save_slots({})
                else:  # andere Benutzer haben Finger im Modul → nur die eigenen löschen
                    for slot in s.used_slots(cap) - others:
                        s.delete(slot)
                    save_slots({k: v for k, v in load_slots().items() if v.get("user") != current_user()})
                self._count(s, cap)
                self._sync_login(sensor_id)
                return
            slot = int(finger.split(":", 1)[1])
            if slot in others:
                raise SensorError("Dieser Finger gehört einem anderen Benutzer – den kann nur dieser löschen.")
            s.delete(slot)
            self._count(s, cap)
        slots = load_slots()
        slots.pop(str(slot), None)
        save_slots(slots)
        self._sync_login(sensor_id)

    def _sync_login(self, port: str) -> None:
        """Ist die Anmeldung an, muss die Zuordnung Finger → Benutzer nachgezogen werden. Normalerweise ohne
        Abfrage: Linux liest die eigene Plätze-Datei, Windows die Datei fingerprint-<Benutzer>.slots, die beim
        Einschalten für den Benutzer beschreibbar angelegt wird. Nur als Rückfall /etc bzw. Administratorrechte
        (Linux mit mehreren Konten: /etc, damit jeder weiß, welche Plätze anderen gehören)."""
        if sys.platform.startswith("win"):
            if self.login_enabled():
                from .windows_serial_login import update_slots, write_own_slots

                if write_own_slots(self._my_slots(), names=my_names()):  # eigene Plätze-Datei (ohne Adminrechte)
                    return
                baud, params = self._found.get(port, (57600, {}))
                try:
                    update_slots(self._my_slots(), port, baud, int(params.get("capacity", 300)))
                except Exception as exc:  # noqa: BLE001
                    raise SensorError(f"Im Modul erledigt – aber die Windows-Anmeldung wurde nicht aktualisiert "
                                      f"({exc}).") from exc
            return
        if self.login_enabled():
            from .linux_serial_login import read_login, update_login

            if not read_login().get("multi_user"):
                return  # nur ein Konto: die Anmelde-Prüfung liest die eigene Plätze-Datei direkt

            try:
                update_login(login_config(port, self._found.get(port, (57600, {}))[0]))
            except Exception as exc:  # noqa: BLE001
                raise SensorError(f"Im Modul erledigt – aber die Anmeldung wurde nicht aktualisiert ({exc}). "
                                  "Bitte unten „Anmelden mit Fingerabdruck“ aus- und wieder einschalten.") from exc

    def cancel(self):
        self._cancel.set()

    def install_hint(self):
        if sys.platform.startswith("win"):
            missing = windows_adapter_without_driver()
            if missing:
                return (f"USB-Seriell-Adapter gefunden ({missing}), aber ohne Treiber. Windows Update → "
                        "„Optionale Updates“ → Treiberupdates, oder den Treiber vom Chip-Hersteller installieren "
                        f"({DRIVER_PAGES.get(missing.split()[0], DRIVER_PAGES['CH340'])}). Danach neu einstecken.")
            return ("Modul am USB-Seriell-Adapter einstecken. Fehlt der Treiber (CH340), installiert ihn "
                    "Windows Update meist selbst.")
        return "Modul am USB-Seriell-Adapter einstecken."

    def _my_slots(self) -> list[int]:
        user = current_user()
        return sorted(int(slot) for slot, info in load_slots().items() if info.get("user") == user)

    # ---- Anmeldung (Linux: PAM · Windows: Anmeldebaustein)
    def _check_windows_login(self) -> None:
        """Prüfen, ob die Windows-Anmeldung genau die angelernten Finger kennt – und ohne Nachfrage reparieren.
        Ergebnis in `login_check`: "ok", "repariert", "alt" (mit älterer Version eingeschaltet → neu
        einschalten), "fehler" oder "" (Anmeldung aus)."""
        from . import windows_serial_login as wl

        self.login_check = ""
        if not self.login_toggle:
            return
        state = wl.login_enabled()
        if state is None:
            self.login_check = "alt"
            return
        if not state:
            return
        mine, names = self._my_slots(), my_names()
        if wl.own_slots_file_state(mine, names) == "ok":
            self.login_check = "ok"
        elif wl.write_own_slots(mine, names=names):
            self.login_check = "repariert"
        else:
            self.login_check = "fehler"

    def login_enabled(self):
        if not self.login_toggle:
            return None
        if sys.platform.startswith("win"):
            from .windows_serial_login import login_enabled

            return login_enabled()
        try:
            return "--fingerabdruck-pam" in Path("/etc/pam.d/common-auth").read_text(encoding="utf-8")
        except OSError:
            return None

    def set_login_enabled(self, enabled, allow_multi: bool = False, password: str | None = None):
        if sys.platform.startswith("win"):
            from . import windows_serial_login as wl

            if not enabled:
                wl.disable()
                return
            if not password:
                raise SensorError("Für die Windows-Anmeldung wird dein Windows-Passwort gebraucht")
            port = next(iter(self._found), "") or _last_port()
            baud, params = self._found.get(port, (57600, {}))
            try:
                wl.enable(password, self._my_slots(), port, baud, int(params.get("capacity", 300)))
            except wl.LoginError as exc:
                raise SensorError(str(exc)) from exc
            wl.write_own_slots(self._my_slots(), names=my_names())  # Namen der Personen dazu
            return
        from .linux_serial_login import disable_login, enable_login

        if enabled:
            port = next(iter(self._found), "")
            enable_login(login_config(port, self._found.get(port, (57600, {}))[0]), allow_multi)
        else:
            disable_login()


def login_config(port: str, baud: int) -> dict:
    """Was die Anmelde-Prüfung braucht: welche Plätze gehören welchem Benutzer, wo steckt das Modul."""
    user = current_user()
    # nur die eigenen Finger – Einträge anderer Benutzer verwaltet deren AluPC
    mine = [int(slot) for slot, info in load_slots().items() if info.get("user") == user]
    return {"users": {user: sorted(mine)} if mine else {}, "port": port, "baud": baud, "timeout": 6}


def foreign_slots(user: str | None = None) -> set[int]:
    """Plätze, die laut Anmelde-Einstellung anderen Benutzern gehören (für Linux mit mehreren Konten)."""
    if not sys.platform.startswith("linux"):
        return set()
    from .linux_serial_login import read_login

    user = user or current_user()
    return {int(s) for name, slots in read_login().get("users", {}).items() if name != user for s in slots}


def user_slots_from_home(user: str, home: str | None = None) -> set[int] | None:
    """Linux: Plätze eines Benutzers aus seiner eigenen Plätze-Datei (~/.config/AluPC/fingerprint-slots.json).
    So wirken neu angelernte Finger/Personen sofort, ohne dass /etc (Passwortabfrage) geändert werden muss.
    Die Datei zählt nur, wenn sie dem Benutzer gehört – sie kann also nur sein eigenes Konto freigeben.
    None = nicht lesbar (dann gilt die Liste aus /etc)."""
    try:
        import pwd

        pw = pwd.getpwnam(user)
        path = Path(home or pw.pw_dir) / ".config" / "AluPC" / "fingerprint-slots.json"
        st = path.stat()
        if st.st_uid != pw.pw_uid or st.st_size > 1_000_000:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(slot) for slot, info in data.items()
                if isinstance(info, dict) and info.get("user") == user and str(slot).isdigit()}
    except (ImportError, KeyError, OSError, ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------- Anmelde-Prüfung (PAM)
KDE_SERVICES = ("kde", "kde-fingerprint", "kde-smartcard", "kscreensaver", "kscreenlocker")


def after_kde_unlock(env, spawn=None) -> bool:
    """Plasma zeigt nach einer Anmeldung ohne Passwort manchmal noch den Knopf „Entsperren“. Nach erfolgreicher
    Prüfung am KDE-Sperrbildschirm deshalb im Hintergrund zusätzlich logind bitten, die Sitzung zu entsperren
    (harmlos, wenn sie schon offen ist). Nur für den Sperrbildschirm, nicht für sudo & Co."""
    if not sys.platform.startswith("linux") or env.get("PAM_SERVICE", "") not in KDE_SERVICES:
        return False
    import shutil
    import subprocess

    loginctl = shutil.which("loginctl")
    if not loginctl:
        return False
    script = f"sleep 1; {loginctl} unlock-session; sleep 1.5; {loginctl} unlock-session"
    try:
        (spawn or subprocess.Popen)(["/bin/sh", "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def pam_check(env=None, login_file: Path = LOGIN_FILE, out=None, record=None) -> int:
    """Wird von pam_exec aufgerufen (Befehl `alupc --fingerabdruck-pam`). 0 = Finger passt zum Benutzer.
    record(Benutzer, Platz): erkannten Platz merken (für „Willkommen, Lena!“) – beim echten Aufruf automatisch."""
    if record is None and env is None:
        from ..welcome import record_login as record
    env = os.environ if env is None else env
    out = out or sys.stdout
    if hasattr(os, "fork") and env is os.environ:  # echter PAM-Aufruf: nie länger als 30 s blockieren
        import signal

        signal.signal(signal.SIGALRM, lambda *_: os._exit(1))
        signal.alarm(30)
    user = env.get("PAM_USER", "")
    try:
        cfg = json.loads(Path(login_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 1
    if user not in cfg.get("users", {}):
        return 1
    allowed = set(cfg["users"][user])
    own = user_slots_from_home(user)
    if own is not None:  # aktuelle Liste aus AluPC (neue Personen/Finger ohne erneute Passwortabfrage)
        allowed = own
    if not allowed or not HAVE_SERIAL:
        return 1
    ports = [cfg.get("port")] if cfg.get("port") else []
    ports += [p for p in candidate_ports() if p not in ports]
    timeout = float(cfg.get("timeout", 6))
    for port in ports:
        for baud in [cfg.get("baud")] + [b for b in BAUDS if b != cfg.get("baud")]:
            if not baud:
                continue
            try:
                with ZWSensor(port, int(baud), timeout=0.5) as s:
                    if not s.handshake():
                        continue
                    cap = s.sys_params()["capacity"]
                    print("Finger auf den Sensor legen …", file=out, flush=True)
                    end = time.monotonic() + timeout
                    tries = 0
                    while time.monotonic() < end and tries < 3:
                        if s.get_image() != OK:
                            time.sleep(0.05)
                            continue
                        tries += 1
                        hit = s.identify(cap)
                        if hit is not None and hit[0] in allowed:
                            s.led("ok")
                            if record is not None:
                                record(user, hit[0])
                            after_kde_unlock(env)
                            return 0
                        s.led("fail")
                        print("Nicht erkannt.", file=out, flush=True)
                        time.sleep(0.4)
                    return 1
            except Exception:  # noqa: BLE001
                continue
    return 1
