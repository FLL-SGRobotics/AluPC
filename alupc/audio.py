"""Ton des PCs: Lautsprecher und Mikrofon – genaue Lautstärke, stumm, Gerät wählen (Linux und Windows).

* Linux: pactl (PulseAudio/PipeWire). Geräte mit Namen, Standardgerät setzen.
* Windows: Core Audio (wie die Lautstärke-Regler von Windows) über comtypes; Standardgerät setzen über die
  Schnittstelle, die auch Windows' eigene Sound-Einstellungen benutzen (IPolicyConfig).
„kind“ ist immer "out" (Lautsprecher/Ausgabe) oder "in" (Mikrofon/Eingabe).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

IS_WINDOWS = sys.platform.startswith("win")
_CACHE: dict = {"t": 0.0, "state": None}


def _run(cmd: list[str], timeout: float = 6) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


# --------------------------------------------------------------------------- Linux (pactl)
def pa_run(cmd: list[str], timeout: float = 6) -> tuple[int, str]:
    """pactl zum Auslesen: nur die normale Ausgabe (Warnungen auf stderr würden JSON kaputt machen) und auf Englisch
    (die Textausgabe ist sonst übersetzt: „Ziel #3“, „Stumm: nein“ …)."""
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "LANGUAGE": "C"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, r.stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


_PA_HEAD = re.compile(r"^(Sink Input|Source Output|Sink|Source|Module|Client|Card) #(\d+)\s*$")


def _pa_text_volume(value: str) -> dict:
    """„front-left: 26214 /  40% / -23.88 dB,   front-right: …“ → wie im JSON {kanal: {value_percent: "40%"}}."""
    vol = {}
    for i, m in enumerate(re.finditer(r"(?:([\w-]+):\s*)?\d+\s*/\s*(\d+)%", value)):
        vol[m.group(1) or f"ch{i}"] = {"value_percent": m.group(2) + "%"}
    return vol


def parse_pa_text(out: str) -> list[dict]:
    """Ausgabe von „pactl list sinks|sources|sink-inputs|source-outputs“ (Text, englisch) → Liste wie bei -f json."""
    items: list[dict] = []
    cur: dict | None = None
    section = ""
    for raw in out.splitlines():
        head = _PA_HEAD.match(raw.strip()) if not raw.startswith("\t") else None
        if head:
            cur = {"index": int(head.group(2)), "properties": {}}
            items.append(cur)
            section = ""
            continue
        if cur is None or not raw.strip():
            continue
        if raw.startswith("\t\t") or raw.startswith("        "):
            if section == "Properties":
                key, sep, value = raw.strip().partition(" = ")
                if sep:
                    cur["properties"][key.strip()] = value.strip().strip('"')
            continue
        key, sep, value = raw.strip().partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        section = key
        low = key.lower()
        if low == "name":
            cur["name"] = value
        elif low == "description":
            cur["description"] = value
        elif low == "mute":
            cur["mute"] = value.lower() in ("yes", "true", "1")
        elif low == "volume":
            cur["volume"] = _pa_text_volume(value)
        elif low in ("sink", "source"):
            try:
                cur[low] = int(value)
            except ValueError:
                cur[low] = value
        elif low == "monitor of sink":
            cur["monitor_of_sink"] = value
        elif low == "driver":
            cur["driver"] = value
    return items


def pa_list(what: str, run=None) -> list[dict]:
    """„pactl list <what>“ als Liste – JSON (pactl ≥ 16), sonst die Textausgabe (pactl 15 kennt -f json nicht; manche
    pactl-16-Versionen geben bei einzelnen Geräten ungültiges JSON aus)."""
    run = run or pa_run
    code, out = run(["pactl", "-f", "json", "list", what])
    if code == 0:
        try:
            data = json.loads(out)
            if isinstance(data, list):
                return data
        except ValueError:
            pass
    code, out = run(["pactl", "list", what])
    return parse_pa_text(out) if code == 0 else []


def _pa_kind(kind: str) -> str:
    return "sink" if kind == "out" else "source"


def _pa_devices(kind: str, run=_run) -> list[dict]:
    what = _pa_kind(kind)
    run = pa_run if run is _run else run
    code, out = run(["pactl", "get-default-" + what])
    default = out.strip() if code == 0 else ""
    devices = []
    for d in pa_list(what + "s", run):
        name = str(d.get("name", ""))
        if not name or name.endswith(".monitor") or d.get("monitor_of_sink") not in (None, "", "n/a"):
            continue  # „Monitor“ = Mitschnitt eines Lautsprechers, kein echtes Mikrofon
        devices.append({"id": name, "name": str(d.get("description") or name), "default": name == default})
    if devices:
        return devices
    code, out = run(["pactl", "list", "short", what + "s"])  # Notfall: nur die Namen
    for line in out.splitlines() if code == 0 else []:
        parts = line.split("\t")
        if len(parts) >= 2 and not parts[1].endswith(".monitor"):
            devices.append({"id": parts[1], "name": parts[1], "default": parts[1] == default})
    return devices


def _pa_level(kind: str, run=_run) -> tuple[int | None, bool]:
    what = _pa_kind(kind)
    target = "@DEFAULT_SINK@" if kind == "out" else "@DEFAULT_SOURCE@"
    code, out = run(["pactl", f"get-{what}-volume", target])
    vol = None
    for part in out.split("/") if code == 0 else []:
        part = part.strip()
        if part.endswith("%"):
            try:
                vol = int(part.rstrip("%"))
                break
            except ValueError:
                pass
    code, out = run(["pactl", f"get-{what}-mute", target])
    return vol, code == 0 and "yes" in out.lower()


# --------------------------------------------------------------------------- Windows (Core Audio)
_LOCAL = threading.local()  # COM-Objekte gelten nur im Thread, der sie angelegt hat
_COM: dict = {"thread": None, "queue": None}


_IN_HELPER = False  # True im Ton-Hilfsprozess (--ton-helfer): dort laufen die Aufrufe wirklich
_HELPER: dict = {"proc": None, "sock": None, "file": None, "lock": threading.Lock()}
_HELPER_MODULES = ("alupc.audio", "alupc.mixer", "alupc.mixer_meters")


def _on_com(fn, *args, timeout: float = 8.0):
    """Windows-Ton aufrufen. Unter Windows läuft das in einem eigenen kleinen Hilfsprozess (AluPC --ton-helfer):
    Core Audio (COM) im Programm selbst ließ AluPC zufällig einfrieren (Qt-Hauptfaden blieb in Windows hängen –
    in der CI hingen so die Oberflächentests). Hängt jetzt etwas, wird nur der Hilfsprozess neu gestartet.
    `fn` muss eine Funktion auf Modulebene sein; Argumente und Ergebnis müssen JSON sein."""
    if IS_WINDOWS and os.environ.get("ALUPC_NO_WINAUDIO"):  # Schalter nur für Fehlersuche in der CI
        raise RuntimeError("Windows-Ton abgeschaltet (ALUPC_NO_WINAUDIO)")
    if IS_WINDOWS and not _IN_HELPER and os.environ.get("ALUPC_TON_IM_PROZESS") != "1":
        return _helper_call(fn, args, timeout)
    return _on_com_local(fn, *args, timeout=timeout)


def _helper_cmd(port: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--ton-helfer", str(port)]
    return [sys.executable, "-m", "alupc", "--ton-helfer", str(port)]


def _helper_stop() -> None:
    proc, sock = _HELPER["proc"], _HELPER["sock"]
    _HELPER.update(proc=None, sock=None, file=None)
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
    if proc is not None and proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass


def _helper_start() -> None:
    import secrets
    import socket

    token = secrets.token_hex(16)
    srv = socket.create_server(("127.0.0.1", 0))
    srv.settimeout(20)
    try:
        env = dict(os.environ, ALUPC_TON_TOKEN=token)
        if not getattr(sys, "frozen", False):  # „python -m alupc“ muss das Paket finden, egal wo gestartet
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env["PYTHONPATH"] = os.pathsep.join(p for p in (root, env.get("PYTHONPATH", "")) if p)
        proc = subprocess.Popen(_helper_cmd(srv.getsockname()[1]), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            sock, _ = srv.accept()
        except OSError:
            proc.kill()
            raise TimeoutError("Ton-Hilfsprozess startet nicht")
    finally:
        srv.close()
    sock.settimeout(20)
    f = sock.makefile("rwb")
    if f.readline().strip().decode("ascii", "replace") != token:  # nur unser eigener Prozess darf antworten
        sock.close()
        proc.kill()
        raise RuntimeError("Ton-Hilfsprozess: falsche Kennung")
    _HELPER.update(proc=proc, sock=sock, file=f)


def _helper_call(fn, args, timeout: float):
    if not _HELPER["lock"].acquire(timeout=timeout + 2):
        raise TimeoutError("Windows-Ton antwortet nicht")
    try:
        if _HELPER["proc"] is None or _HELPER["proc"].poll() is not None:
            _helper_stop()
            _helper_start()
        try:
            _HELPER["sock"].settimeout(timeout + 2)
            _HELPER["file"].write(json.dumps({"m": fn.__module__, "f": fn.__name__, "a": list(args),
                                              "t": timeout}).encode() + b"\n")
            _HELPER["file"].flush()
            line = _HELPER["file"].readline()
        except OSError as exc:  # auch socket.timeout: Hilfsprozess hängt → weg damit, nächstes Mal neu
            _helper_stop()
            raise TimeoutError("Windows-Ton antwortet nicht") from exc
        if not line:
            _helper_stop()
            raise RuntimeError("Ton-Hilfsprozess beendet")
        reply = json.loads(line)
        if reply.get("hang"):
            _helper_stop()
    finally:
        _HELPER["lock"].release()
    if reply.get("hang"):
        raise TimeoutError("Windows-Ton antwortet nicht")
    if not reply.get("ok"):
        raise RuntimeError(reply.get("e", "Fehler"))
    return reply.get("v")


def helper_main(port: str) -> int:
    """Der Ton-Hilfsprozess: nimmt Aufträge (JSON-Zeilen) vom AluPC-Hauptprogramm an. Endet mit dem Hauptprogramm."""
    import importlib
    import socket

    global _IN_HELPER
    _IN_HELPER = True
    try:
        sock = socket.create_connection(("127.0.0.1", int(port)), timeout=20)
    except (OSError, ValueError):
        return 1
    sock.settimeout(None)
    f = sock.makefile("rwb")
    f.write(os.environ.get("ALUPC_TON_TOKEN", "").encode() + b"\n")
    f.flush()
    for line in f:
        try:
            job = json.loads(line)
            if job["m"] not in _HELPER_MODULES or not job["f"].startswith("_win"):
                raise ValueError("nicht erlaubt")
            fn = getattr(importlib.import_module(job["m"]), job["f"])
            reply = {"ok": True, "v": _on_com_local(fn, *job["a"], timeout=float(job.get("t", 8)))}
        except Exception as exc:  # noqa: BLE001 - als Text zurück
            reply = {"ok": False, "e": f"{type(exc).__name__}: {exc}" if not isinstance(exc, RuntimeError) else str(exc),
                     "hang": isinstance(exc, TimeoutError)}
        try:
            f.write(json.dumps(reply).encode() + b"\n")
            f.flush()
        except (OSError, TypeError, ValueError):
            return 1
        if reply.get("hang"):
            os._exit(2)  # Core Audio hängt hier → sofort beenden; das Hauptprogramm startet beim nächsten Aufruf neu
    return 0


def _win_ping(delay: float = 0) -> int:
    """Hilfsprozess lebt? (ohne Core Audio; für Tests: delay)"""
    if delay:
        time.sleep(delay)
    return os.getpid()


def _on_com_local(fn, *args, timeout: float = 8.0):
    """JEDEN Core-Audio-Aufruf in genau einem eigenen Thread ausführen (COM-Objekte dürfen nicht zwischen
    Threads wandern – sonst kann das Programm abstürzen). Wartet auf das Ergebnis; Fehler kommen als Ausnahme."""
    import queue

    if _COM["thread"] is None or not _COM["thread"].is_alive():
        q: queue.Queue = queue.Queue()

        def loop():
            if IS_WINDOWS:
                # COM als MTA anmelden, BEVOR comtypes geladen wird: Ein STA-Faden braucht eine Nachrichtenschleife,
                # dieser hier wartet aber nur auf Aufträge. Schickt Windows dann eine Rundsendung an alle Fenster
                # (z. B. Einstellungs-Änderung), wartet der Absender – oft der Qt-Hauptfaden – für immer auf ihn:
                # AluPC friert ein (in der CI: Oberflächentests hingen zufällig). MTA hat dieses Problem nicht.
                sys.coinit_flags = 0  # COINIT_MULTITHREADED – gilt fürs erste „import comtypes“ (nur hier)
                try:
                    import ctypes

                    ctypes.windll.ole32.CoInitializeEx(None, 0)
                except Exception:  # noqa: BLE001 - ohne COM gibt es eben keinen Windows-Ton
                    pass
            while True:
                job = q.get()
                if job is None:
                    return
                func, a, box, done = job
                try:
                    box["value"] = func(*a)
                except BaseException as exc:  # noqa: BLE001 - an den Aufrufer weitergeben
                    # Nur Text weitergeben, NICHT die Ausnahme selbst: ihr Traceback hält COM-Objekte fest, die dann
                    # in einem anderen Thread freigegeben würden – unter Windows ein zufälliger Absturz („access
                    # violation“, z. B. ohne Audiogerät bei jedem Lesen der Lautstärke)
                    box["error"] = RuntimeError(f"{type(exc).__name__}: {exc}")
                    exc.__traceback__ = None
                func = a = job = None  # auch hier keine Verweise bis zum nächsten Auftrag halten
                if q.empty() and getattr(_LOCAL, "win", None) is not None:
                    # Keine Windows-Audio-Objekte (Geräte-Aufzählung, Richtlinien) dauerhaft festhalten – hier im
                    # eigenen Faden freigeben; beim nächsten Auftrag neu holen (dauert nur Millisekunden).
                    _LOCAL.win = None
                    import gc

                    gc.collect()
                done.set()

        _COM["queue"] = q
        _COM["thread"] = threading.Thread(target=loop, name="alupc-ton", daemon=True)
        _COM["thread"].start()
    if threading.current_thread() is _COM["thread"]:
        return fn(*args)
    box: dict = {}
    done = threading.Event()
    _COM["queue"].put((fn, args, box, done))
    if not done.wait(timeout):
        raise TimeoutError("Windows-Ton antwortet nicht")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _win():
    """Core-Audio-Schnittstellen (je Thread einmal aufgebaut). Fehler, wenn comtypes fehlt."""
    if getattr(_LOCAL, "win", None) is not None:
        return _LOCAL.win
    import ctypes
    from ctypes import POINTER, Structure, c_float, c_uint, c_ushort, c_void_p, c_wchar_p
    from ctypes.wintypes import BOOL, DWORD, LPCWSTR

    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class PROPERTYKEY(Structure):
        _fields_ = [("fmtid", GUID), ("pid", DWORD)]

    class PROPVARIANT(Structure):
        _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                    ("val", c_void_p), ("pad", c_void_p)]

    class IAudioEndpointVolume(IUnknown):
        _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        _methods_ = [
            COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(c_uint))),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], c_float), (["in"], POINTER(GUID))),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], c_float), (["in"], POINTER(GUID))),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (["out"], POINTER(c_float))),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (["out"], POINTER(c_float))),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], c_uint), (["in"], c_float), (["in"], POINTER(GUID))),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], c_uint), (["in"], c_float),
                      (["in"], POINTER(GUID))),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], c_uint), (["out"], POINTER(c_float))),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], c_uint), (["out"], POINTER(c_float))),
            COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL), (["in"], POINTER(GUID))),
            COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL))),
        ]

    class IPropertyStore(IUnknown):
        _iid_ = GUID("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(DWORD))),
            COMMETHOD([], HRESULT, "GetAt", (["in"], DWORD), (["out"], POINTER(PROPERTYKEY))),
            COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PROPERTYKEY)), (["out"], POINTER(PROPVARIANT))),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID)), (["in"], DWORD), (["in"], c_void_p),
                      (["out"], POINTER(POINTER(IUnknown)))),
            COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], DWORD), (["out"], POINTER(POINTER(IPropertyStore)))),
            COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_wchar_p))),
            COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD))),
        ]

    class IMMDeviceCollection(IUnknown):
        _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint))),
            COMMETHOD([], HRESULT, "Item", (["in"], c_uint), (["out"], POINTER(POINTER(IMMDevice)))),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], DWORD), (["in"], DWORD),
                      (["out"], POINTER(POINTER(IMMDeviceCollection)))),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], DWORD), (["in"], DWORD),
                      (["out"], POINTER(POINTER(IMMDevice)))),
            COMMETHOD([], HRESULT, "GetDevice", (["in"], LPCWSTR), (["out"], POINTER(POINTER(IMMDevice)))),
        ]

    class IPolicyConfig(IUnknown):  # nicht offiziell dokumentiert – so setzen auch die Sound-Einstellungen
        _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
        _methods_ = [COMMETHOD([], HRESULT, name) for name in (
            "GetMixFormat", "GetDeviceFormat", "ResetDeviceFormat", "SetDeviceFormat", "GetProcessingPeriod",
            "SetProcessingPeriod", "GetShareMode", "SetShareMode", "GetPropertyValue", "SetPropertyValue")] + [
            COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], LPCWSTR), (["in"], DWORD)),
        ]

    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)  # MTA, siehe _on_com
    enum = comtypes.CoCreateInstance(GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"), IMMDeviceEnumerator,
                                     comtypes.CLSCTX_ALL)
    name_key = PROPERTYKEY(GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14)
    ole32 = ctypes.windll.ole32

    def friendly(dev) -> str:
        try:
            store = dev.OpenPropertyStore(0)
            pv = store.GetValue(ctypes.byref(name_key))
            text = ctypes.wstring_at(pv.val) if pv.vt == 31 and pv.val else ""
            ole32.PropVariantClear(ctypes.byref(pv))
            return text
        except (OSError, comtypes.COMError):
            return ""

    def volume(dev):
        unk = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        return unk.QueryInterface(IAudioEndpointVolume)

    def set_default(dev_id: str) -> None:
        pc = comtypes.CoCreateInstance(GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}"), IPolicyConfig,
                                       comtypes.CLSCTX_ALL)
        for role in (0, 1, 2):  # Konsole, Multimedia, Kommunikation
            pc.SetDefaultEndpoint(dev_id, role)

    _LOCAL.win = {"enum": enum, "friendly": friendly, "volume": volume, "set_default": set_default,
                  "COMError": comtypes.COMError}
    return _LOCAL.win


def _flow(kind: str) -> int:
    return 0 if kind == "out" else 1  # eRender / eCapture


def _win_default(kind: str):
    return _win()["enum"].GetDefaultAudioEndpoint(_flow(kind), 0)


def _win_devices(kind: str) -> list[dict]:
    w = _win()
    try:
        default_id = _win_default(kind).GetId()
    except (OSError, w["COMError"]):
        default_id = ""
    coll = w["enum"].EnumAudioEndpoints(_flow(kind), 1)  # nur aktive
    out = []
    for i in range(coll.GetCount()):
        dev = coll.Item(i)
        dev_id = dev.GetId()
        out.append({"id": dev_id, "name": w["friendly"](dev) or dev_id, "default": dev_id == default_id})
    return out


def _win_level(kind: str) -> tuple[int | None, bool]:
    vol = _win()["volume"](_win_default(kind))
    return round(vol.GetMasterVolumeLevelScalar() * 100), bool(vol.GetMute())


def _win_check() -> bool:
    _win()
    return True


def _win_set_level(kind: str, percent: int) -> None:
    vol = _win()["volume"](_win_default(kind))
    vol.SetMasterVolumeLevelScalar(percent / 100, None)
    vol.SetMute(False, None)


def _win_set_mute(kind: str, on: bool) -> None:
    _win()["volume"](_win_default(kind)).SetMute(bool(on), None)


def _win_set_default(dev_id: str) -> None:
    _win()["set_default"](dev_id)


# --------------------------------------------------------------------------- gemeinsam
def available() -> bool:
    if IS_WINDOWS:
        try:
            return bool(_on_com(_win_check))
        except Exception:  # noqa: BLE001 - comtypes fehlt / kein Audio-Dienst
            return False
    return shutil.which("pactl") is not None


def devices(kind: str) -> list[dict]:
    """[{id, name, default}] – nur aktive Geräte."""
    try:
        return _on_com(_win_devices, kind) if IS_WINDOWS else _pa_devices(kind)
    except Exception:  # noqa: BLE001
        return []


def level(kind: str) -> tuple[int | None, bool]:
    """(Lautstärke 0–100 oder None, stumm)."""
    try:
        return tuple(_on_com(_win_level, kind)) if IS_WINDOWS else _pa_level(kind)
    except Exception:  # noqa: BLE001
        return None, False


def set_level(kind: str, percent: int) -> tuple[bool, str]:
    percent = max(0, min(100, int(percent)))
    _CACHE["t"] = 0
    try:
        if IS_WINDOWS:
            _on_com(_win_set_level, kind, percent)
            return True, ""
        what, target = _pa_kind(kind), ("@DEFAULT_SINK@" if kind == "out" else "@DEFAULT_SOURCE@")
        code, out = _run(["pactl", f"set-{what}-volume", target, f"{percent}%"])
        if code == 0:
            _run(["pactl", f"set-{what}-mute", target, "0"])
        return code == 0, out.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def set_mute(kind: str, on: bool) -> tuple[bool, str]:
    _CACHE["t"] = 0
    try:
        if IS_WINDOWS:
            _on_com(_win_set_mute, kind, bool(on))
            return True, ""
        what, target = _pa_kind(kind), ("@DEFAULT_SINK@" if kind == "out" else "@DEFAULT_SOURCE@")
        code, out = _run(["pactl", f"set-{what}-mute", target, "1" if on else "0"])
        return code == 0, out.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def set_default(kind: str, dev_id: str) -> tuple[bool, str]:
    _CACHE["t"] = 0
    try:
        if IS_WINDOWS:
            _on_com(_win_set_default, dev_id)
            return True, ""
        code, out = _run(["pactl", "set-default-" + _pa_kind(kind), dev_id])
        return code == 0, out.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def state(max_age: float = 2.0) -> dict | None:
    """Alles auf einmal (fürs Handy, höchstens alle 2 s neu gelesen):
    {"out": {"vol", "muted", "devices"}, "in": {...}} – None, wenn hier nichts geht."""
    now = time.monotonic()
    if _CACHE["state"] is not None and now - _CACHE["t"] < max_age:
        return _CACHE["state"]
    if not available():
        return None
    result = {}
    for kind in ("out", "in"):
        vol, muted = level(kind)
        result[kind] = {"vol": vol, "muted": muted, "devices": devices(kind)}
    _CACHE.update(t=now, state=result)
    return result


_REFRESH = {"busy": False}


def cached_state(max_age: float = 2.0) -> dict | None:
    """Wie state(), blockiert aber nie: gibt sofort den zuletzt gelesenen Stand zurück und liest – wenn er älter als
    max_age ist – im Hintergrund neu. Für die Oberfläche: state() fragt bis zu fünfmal das System ab (je mehr
    Ton-Geräte, desto länger); im Oberflächen-Thread ließ das alle 2 s das ganze Fenster kurz hängen."""
    if time.monotonic() - _CACHE["t"] >= max_age and not _REFRESH["busy"]:
        _REFRESH["busy"] = True

        def work():
            try:
                if state(0) is None:
                    _CACHE.update(t=time.monotonic(), state=None)  # „geht hier nicht“ auch merken
            except Exception:  # noqa: BLE001 - Ton-Dienst weg o. Ä.: alten Stand behalten
                _CACHE["t"] = time.monotonic()
            finally:
                _REFRESH["busy"] = False

        threading.Thread(target=work, name="alupc-ton-stand", daemon=True).start()
    return _CACHE["state"]


def run(cmd: str) -> str:
    """Befehle (Handy/Sprache): ton_laut:N, ton_stumm:0|1, ton_geraet:i, mic_laut:N, mic_stumm:0|1, mic_geraet:i."""
    name, _, arg = cmd.partition(":")
    kind = "out" if name.startswith("ton_") else "in"
    what = "Lautsprecher" if kind == "out" else "Mikrofon"
    try:
        if name.endswith("_laut"):
            ok, why = set_level(kind, int(arg))
            return f"{what} {int(arg)} %." if ok else f"{what}: {why or 'geht nicht'}"
        if name.endswith("_stumm"):
            on = arg == "1"
            ok, why = set_mute(kind, on)
            return (f"{what} stumm." if on else f"{what} wieder an.") if ok else f"{what}: {why or 'geht nicht'}"
        if name.endswith("_geraet"):
            devs = devices(kind)
            i = int(arg)
            if not 0 <= i < len(devs):
                return f"{what}: Gerät nicht gefunden."
            ok, why = set_default(kind, devs[i]["id"])
            return f"{what}: {devs[i]['name']}." if ok else f"{what}: {why or 'geht nicht'}"
    except ValueError:
        pass
    return "Unbekannter Ton-Befehl."
