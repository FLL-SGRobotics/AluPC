"""Audio-Mixer (Seite „Audio“): alle Ausgänge und Eingänge – echte (physisch) und virtuelle –, Lautstärke je Gerät
und je Programm, Programme auf andere Ausgänge/Eingänge legen und das virtuelle Mikro von AluPC.

Virtuelle Geräte:
* Linux (PipeWire/PulseAudio, über pactl): AluPC legt selbst an
    - „AluPC Virtueller Ausgang“ (alupc_out) – Programme hier hinein spielen, OBS/Discord nehmen dessen „Monitor“ auf
    - „AluPC Virtuelles Mikro“ (alupc_mic) – in Zoom/Teams/Discord/OBS als Mikrofon wählen. Hinein geht, was man
      einschaltet: ein oder mehrere echte Mikrofone und/oder der Ton vom virtuellen Ausgang.
    - „Mithören“: den virtuellen Ausgang zusätzlich auf den Lautsprechern hören.
  Umgesetzt mit den pactl-Modulen null-sink, remap-source und loopback (läuft im Audio-Dienst, kostet AluPC nichts).
* Windows: virtuelle Geräte kommen vom Treiber VB-CABLE (installiert der AluPC-Installer): „CABLE Input“ ist der
  virtuelle Ausgang, „CABLE Output“ das virtuelle Mikro. Mikrofone ins virtuelle Mikro und „Mithören“ leitet AluPC
  selbst weiter (mixer_route.py). Programme auf einen anderen Ausgang legen geht unter Windows nur in den
  Windows-Einstellungen („App-Lautstärke und Geräteeinstellungen“) – die Seite öffnet sie.
"""

from __future__ import annotations

import os
import re
import shutil

from .audio import IS_WINDOWS, _on_com, _run, pa_list, pa_run

OUT_NAME = "alupc_out"
MIX_NAME = "alupc_mic_mix"
MIC_NAME = "alupc_mic"
OUT_DESC = "AluPC Virtueller Ausgang"
MIC_DESC = "AluPC Virtuelles Mikro"
VIRTUAL_HINTS = ("cable", "vb-audio", "voicemeeter", "virtual", "virtuell", "null", "alupc", "loopback", "monitor of")


DEFAULT_DEVICES = [{"id": OUT_NAME, "kind": "out", "name": OUT_DESC}, {"id": MIC_NAME, "kind": "mic", "name": MIC_DESC}]


def new_device_id(name: str, kind: str, taken: set[str]) -> str:
    """Geräte-ID für ein selbst angelegtes virtuelles Gerät (nur Buchstaben/Ziffern, eindeutig)."""
    base = "alupc_" + ("mic_" if kind == "mic" else "out_") + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24]
                                                             or "geraet")
    dev_id, n = base, 2
    while dev_id in taken or dev_id + "_mix" in taken:
        dev_id, n = f"{base}_{n}", n + 1
    return dev_id


def is_virtual_name(name: str) -> bool:
    low = (name or "").lower()
    return any(h in low for h in VIRTUAL_HINTS)


# =========================================================================== Linux (pactl)
def _pa_json(what: str, run=None) -> list[dict]:
    """Liste von pactl (JSON oder – ältere/fehlerhafte Versionen – Textausgabe, siehe audio.pa_list)."""
    return pa_list(what, run)


def _pa_percent(volume) -> int:
    vals = []
    for ch in (volume or {}).values() if isinstance(volume, dict) else []:
        try:
            vals.append(int(str(ch.get("value_percent", "0")).rstrip("%")))
        except (ValueError, AttributeError):
            pass
    return round(sum(vals) / len(vals)) if vals else 0


def _pa_default(kind: str, run=None) -> str:
    code, out = (run or pa_run)(["pactl", "get-default-" + ("sink" if kind == "out" else "source")])
    return out.strip() if code == 0 else ""


def _pa_devices(kind: str, run=None) -> list[dict]:
    what = "sinks" if kind == "out" else "sources"
    default = _pa_default(kind, run)
    out = []
    for d in _pa_json(what, run):
        name = str(d.get("name", ""))
        props = d.get("properties") or {}
        if kind == "in" and (name.endswith(".monitor") or props.get("device.class") == "monitor"):
            continue  # Mitschnitte von Ausgängen sind keine Mikrofone
        if name.startswith("alupc_") and name.endswith("_mix"):
            continue  # interner Mischer eines virtuellen Mikros
        desc = str(d.get("description") or props.get("device.description") or name)
        physical = bool(props.get("device.api")) and props.get("device.api") not in ("null",) and \
            not name.startswith("alupc_")
        out.append({"id": name, "index": d.get("index"), "name": desc, "virtual": not physical,
                    "volume": _pa_percent(d.get("volume")), "muted": bool(d.get("mute")), "default": name == default,
                    "alupc": name.startswith("alupc_")})
    out.sort(key=lambda x: (x["virtual"], x["name"].lower()))
    return out


def _pa_streams(kind: str, run=None) -> list[dict]:
    """Programme, die gerade abspielen (out) oder aufnehmen (in)."""
    what, dev_key, devs = ("sink-inputs", "sink", "sinks") if kind == "out" else ("source-outputs", "source", "sources")
    names = {d.get("index"): str(d.get("name", "")) for d in _pa_json(devs, run)}
    out = []
    for s in _pa_json(what, run):
        props = s.get("properties") or {}
        app = str(props.get("application.name") or props.get("application.process.binary") or
                  props.get("media.name") or "Programm")
        if props.get("media.role") == "event" or str(props.get("node.name", "")).startswith("alupc"):
            continue
        if str(props.get("application.process.id", "")) == str(os.getpid()) or \
                str(props.get("application.name", "")).startswith("alupc"):
            continue  # AluPC selbst (Pegelanzeige, Töne)
        if "loopback" in app.lower() and ("alupc" in str(props).lower() or kind == "in"):
            continue  # AluPCs eigene Weiterleitungen
        target = names.get(s.get(dev_key), "")
        if kind == "in" and target.endswith(".monitor") and target.startswith("alupc"):
            continue
        out.append({"id": s.get("index"), "app": app, "title": str(props.get("media.name") or ""),
                    "volume": _pa_percent(s.get("volume")), "muted": bool(s.get("mute")), "device": target})
    return out


def _pa_modules(run=None) -> list[dict]:
    code, out = (run or pa_run)(["pactl", "list", "short", "modules"])
    mods = []
    for line in out.splitlines() if code == 0 else []:
        parts = line.split("\t")
        if len(parts) >= 2:
            mods.append({"id": parts[0], "name": parts[1], "args": parts[2] if len(parts) > 2 else ""})
    return mods


def _pa_load(args: list[str], run=_run) -> tuple[bool, str]:
    code, out = run(["pactl", "load-module", *args])
    return code == 0, out.strip()


def _pa_loopbacks(run=_run) -> list[dict]:
    """AluPCs Weiterleitungen: [{id, source, sink}]"""
    found = []
    for m in _pa_modules(run):
        if m["name"] != "module-loopback" or "alupc_route" not in m["args"]:
            continue
        args = dict(a.split("=", 1) for a in m["args"].split() if "=" in a)
        found.append({"id": m["id"], "source": args.get("source", ""), "sink": args.get("sink", "")})
    return found


# =========================================================================== Windows (Core Audio)
def _win_sessions_api():
    """Schnittstellen für die Lautstärke je Programm (Audio-Sitzungen) – einmal je COM-Thread."""
    from . import audio

    cache = getattr(audio._LOCAL, "sessions", None)
    if cache is not None:
        return cache
    from ctypes import POINTER, c_float, c_int, c_void_p, c_wchar_p
    from ctypes.wintypes import BOOL, DWORD

    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class ISimpleAudioVolume(IUnknown):
        _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], c_float), (["in"], POINTER(GUID))),
            COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], POINTER(c_float))),
            COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL), (["in"], POINTER(GUID))),
            COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL))),
        ]

    class IAudioSessionControl2(IUnknown):
        _iid_ = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int))),
            COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(c_wchar_p))),
            COMMETHOD([], HRESULT, "SetDisplayName"),
            COMMETHOD([], HRESULT, "GetIconPath"),
            COMMETHOD([], HRESULT, "SetIconPath"),
            COMMETHOD([], HRESULT, "GetGroupingParam"),
            COMMETHOD([], HRESULT, "SetGroupingParam"),
            COMMETHOD([], HRESULT, "RegisterAudioSessionNotification"),
            COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification"),
            COMMETHOD([], HRESULT, "GetSessionIdentifier"),
            COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier"),
            COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(DWORD))),
            COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
            COMMETHOD([], HRESULT, "SetDuckingPreference"),
        ]

    class IAudioSessionEnumerator(IUnknown):
        _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int))),
            COMMETHOD([], HRESULT, "GetSession", (["in"], c_int), (["out"], POINTER(POINTER(IUnknown)))),
        ]

    class IAudioSessionManager2(IUnknown):
        _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetAudioSessionControl"),
            COMMETHOD([], HRESULT, "GetSimpleAudioVolume"),
            COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(IAudioSessionEnumerator)))),
            COMMETHOD([], HRESULT, "RegisterSessionNotification", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "UnregisterSessionNotification", (["in"], c_void_p)),
            COMMETHOD([], HRESULT, "RegisterDuckNotification"),
            COMMETHOD([], HRESULT, "UnregisterDuckNotification"),
        ]

    cache = {"ISimpleAudioVolume": ISimpleAudioVolume, "IAudioSessionControl2": IAudioSessionControl2,
             "IAudioSessionManager2": IAudioSessionManager2}
    audio._LOCAL.sessions = cache
    return cache


def _win_device_list(kind: str) -> list[dict]:
    from . import audio

    w = audio._win()
    devs = []
    for d in audio._win_devices(kind):
        vol_pct, muted = 0, False
        try:
            dev = w["enum"].GetDevice(d["id"])
            vol = w["volume"](dev)
            vol_pct, muted = round(vol.GetMasterVolumeLevelScalar() * 100), bool(vol.GetMute())
        except (OSError, w["COMError"]):
            pass
        devs.append({"id": d["id"], "index": None, "name": d["name"], "virtual": is_virtual_name(d["name"]),
                     "volume": vol_pct, "muted": muted, "default": d["default"],
                     "alupc": "cable" in d["name"].lower()})
    devs.sort(key=lambda x: (x["virtual"], x["name"].lower()))
    return devs


def _win_sessions() -> list[dict]:
    """Programme mit Ton (Audio-Sitzungen aller aktiven Ausgänge)."""
    import comtypes

    from . import audio

    w, api = audio._win(), _win_sessions_api()
    out = []
    coll = w["enum"].EnumAudioEndpoints(0, 1)
    for i in range(coll.GetCount()):
        dev = coll.Item(i)
        dev_name = w["friendly"](dev)
        try:
            mgr = dev.Activate(api["IAudioSessionManager2"]._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(
                api["IAudioSessionManager2"])
            sessions = mgr.GetSessionEnumerator()
        except (OSError, comtypes.COMError):
            continue
        for k in range(sessions.GetCount()):
            try:
                ctl = sessions.GetSession(k).QueryInterface(api["IAudioSessionControl2"])
                if ctl.GetState() == 2:  # abgelaufen
                    continue
                pid = int(ctl.GetProcessId())
                vol = ctl.QueryInterface(api["ISimpleAudioVolume"])
            except (OSError, comtypes.COMError):
                continue
            if pid == os.getpid() or (audio._IN_HELPER and pid == os.getppid()):
                continue  # AluPC selbst
            app = "Systemklänge" if pid == 0 else _process_name(pid)
            out.append({"id": f"{i}:{pid}", "pid": pid, "app": app, "title": "", "device": dev_name,
                        "volume": round(vol.GetMasterVolume() * 100), "muted": bool(vol.GetMute())})
    return out


def _process_name(pid: int) -> str:
    try:
        import psutil

        name = psutil.Process(pid).name()
        return name[:-4] if name.lower().endswith(".exe") else name
    except Exception:  # noqa: BLE001
        return f"Programm {pid}"


def _win_set_session(pid: int, volume: int | None = None, muted: bool | None = None) -> bool:
    import comtypes

    from . import audio

    w, api = audio._win(), _win_sessions_api()
    hit = False
    coll = w["enum"].EnumAudioEndpoints(0, 1)
    for i in range(coll.GetCount()):
        try:
            mgr = coll.Item(i).Activate(api["IAudioSessionManager2"]._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(
                api["IAudioSessionManager2"])
            sessions = mgr.GetSessionEnumerator()
        except (OSError, comtypes.COMError):
            continue
        for k in range(sessions.GetCount()):
            try:
                ctl = sessions.GetSession(k).QueryInterface(api["IAudioSessionControl2"])
                if int(ctl.GetProcessId()) != pid:
                    continue
                vol = ctl.QueryInterface(api["ISimpleAudioVolume"])
                if volume is not None:
                    vol.SetMasterVolume(max(0, min(100, volume)) / 100, None)
                if muted is not None:
                    vol.SetMute(bool(muted), None)
                hit = True
            except (OSError, comtypes.COMError):
                continue
    return hit


def _win_set_device(dev_id: str, volume: int | None = None, muted: bool | None = None) -> None:
    from . import audio

    w = audio._win()
    vol = w["volume"](w["enum"].GetDevice(dev_id))
    if volume is not None:
        vol.SetMasterVolumeLevelScalar(max(0, min(100, volume)) / 100, None)
    if muted is not None:
        vol.SetMute(bool(muted), None)


# =========================================================================== gemeinsam
def available() -> tuple[bool, str]:
    if IS_WINDOWS:
        from . import audio

        return (True, "") if audio.available() else (False, "Windows-Ton (Core Audio) nicht erreichbar.")
    if not shutil.which("pactl"):
        return False, ("Ton-Werkzeug „pactl“ fehlt (Paket „pulseaudio-utils“ – kommt nicht mit PipeWire mit). "
                       "„Installieren“ richtet es ein.")
    code, out = _run(["pactl", "info"])
    return (True, "") if code == 0 else (False, "Kein Audio-Dienst (PipeWire/PulseAudio) erreichbar.")


def devices(kind: str) -> list[dict]:
    """[{id, name, virtual, volume, muted, default, alupc}] – physische zuerst, dann virtuelle."""
    try:
        return _on_com(_win_device_list, kind) if IS_WINDOWS else _pa_devices(kind)
    except Exception:  # noqa: BLE001
        return []


def streams(kind: str = "out") -> list[dict]:
    """Programme mit Ton: [{id, app, title, volume, muted, device}] (Windows: nur Abspielen)."""
    try:
        if IS_WINDOWS:
            return merge_sessions(_on_com(_win_sessions)) if kind == "out" else []
        return _pa_streams(kind)
    except Exception:  # noqa: BLE001
        return []


def merge_sessions(items: list[dict]) -> list[dict]:
    """Windows meldet je Ausgabegerät eine eigene Sitzung – „Systemklänge“ und Programme kamen dadurch mehrfach
    (z. B. 6× bei 6 Geräten). Je Programm (Prozess) nur einmal; Lauter/Leiser gilt ohnehin für alle seine
    Sitzungen (_win_set_session)."""
    out: dict = {}
    for it in items:
        key = it.get("pid", it.get("id"))
        if key not in out:
            out[key] = {**it, "id": f"p:{key}"}
    return list(out.values())


def set_device(kind: str, dev_id: str, volume: int | None = None, muted: bool | None = None) -> tuple[bool, str]:
    try:
        if IS_WINDOWS:
            _on_com(_win_set_device, dev_id, volume, muted)
            return True, ""
        what = "sink" if kind == "out" else "source"
        if volume is not None:
            code, out = _run(["pactl", f"set-{what}-volume", dev_id, f"{max(0, min(150, int(volume)))}%"])
            if code != 0:
                return False, out.strip()
        if muted is not None:
            code, out = _run(["pactl", f"set-{what}-mute", dev_id, "1" if muted else "0"])
            if code != 0:
                return False, out.strip()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def set_default(kind: str, dev_id: str) -> tuple[bool, str]:
    from . import audio

    return audio.set_default(kind, dev_id)


def set_stream(kind: str, sid, volume: int | None = None, muted: bool | None = None) -> tuple[bool, str]:
    try:
        if IS_WINDOWS:
            pid = int(str(sid).split(":")[-1])
            return (_on_com(_win_set_session, pid, volume, muted), "")
        what = "sink-input" if kind == "out" else "source-output"
        if volume is not None:
            code, out = _run(["pactl", f"set-{what}-volume", str(sid), f"{max(0, min(150, int(volume)))}%"])
            if code != 0:
                return False, out.strip()
        if muted is not None:
            code, out = _run(["pactl", f"set-{what}-mute", str(sid), "1" if muted else "0"])
            if code != 0:
                return False, out.strip()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def move_stream(kind: str, sid, dev_id: str) -> tuple[bool, str]:
    """Programm auf einen anderen Ausgang (out) bzw. Eingang (in) legen – nur Linux; Windows: Einstellungen."""
    if IS_WINDOWS:
        return False, "Unter Windows: Einstellungen → System → Sound → App-Lautstärke und Geräteeinstellungen"
    code, out = _run(["pactl", "move-sink-input" if kind == "out" else "move-source-output", str(sid), dev_id])
    return code == 0, out.strip()


def can_move_streams() -> bool:
    return not IS_WINDOWS


def open_app_settings() -> None:
    """Windows: „App-Lautstärke und Geräteeinstellungen“ (Programm → Ausgabegerät)."""
    if IS_WINDOWS:
        import os

        os.startfile("ms-settings:apps-volume")  # noqa: S606 – feste Adresse der Windows-Einstellungen


# --------------------------------------------------------------------------- virtuelle Geräte
def virtual_status() -> dict:
    """{"supported", "out", "mic", "why"} – out/mic: Geräte-ID oder "" (nicht da)."""
    if IS_WINDOWS:
        outs, ins = devices("out"), devices("in")
        cable_out = next((d["id"] for d in outs if "cable input" in d["name"].lower()), "")
        cable_in = next((d["id"] for d in ins if "cable output" in d["name"].lower()), "")
        why = "" if cable_out and cable_in else \
            "VB-CABLE ist nicht installiert – installiert der AluPC-Installer (oder hier „Installieren“)."
        return {"supported": True, "out": cable_out, "mic": cable_in, "why": why, "driver": "VB-CABLE"}
    ok, why = available()
    if not ok:
        return {"supported": False, "out": "", "mic": "", "why": why, "driver": ""}
    sinks = {str(d.get("name")) for d in _pa_json("sinks")}
    sources = {str(d.get("name")) for d in _pa_json("sources")}
    return {"supported": True, "out": OUT_NAME if OUT_NAME in sinks else "",
            "mic": MIC_NAME if MIC_NAME in sources else "", "why": "", "driver": "PipeWire/PulseAudio"}


def _props(what: str, description: str) -> str:
    """Modul-Argument mit Leerzeichen im Namen: der Wert muss mit einem Anführungszeichen beginnen."""
    return f"""{what}_properties='device.description="{description}"'"""


def create_virtual(devices: list[dict] | None = None, run=_run) -> tuple[bool, str]:
    """Linux: virtuelle Geräte anlegen (Standard: ein Ausgang + ein Mikro; gibt es sie schon, passiert nichts)."""
    if IS_WINDOWS:
        st = virtual_status()
        return (True, "") if st["out"] and st["mic"] else (False, st["why"])
    sinks = {str(d.get("name")) for d in _pa_json("sinks", run)}
    sources = {str(d.get("name")) for d in _pa_json("sources", run)}
    steps = []
    for dev in devices or DEFAULT_DEVICES:
        dev_id, name = dev["id"], dev.get("name") or dev["id"]
        if dev["kind"] == "out" and dev_id not in sinks:
            steps.append(["module-null-sink", f"sink_name={dev_id}", _props("sink", name)])
        elif dev["kind"] == "mic":
            if dev_id + "_mix" not in sinks:
                steps.append(["module-null-sink", f"sink_name={dev_id}_mix", _props("sink", name + " (Mischer)")])
            if dev_id not in sources:
                steps.append(["module-remap-source", f"master={dev_id}_mix.monitor", f"source_name={dev_id}",
                              _props("source", name)])
    for args in steps:
        ok, why = _pa_load(args, run)
        if not ok:
            return False, f"Virtuelles Gerät ging nicht: {why[:160]}"
    return True, ""


def remove_device(dev_id: str, run=_run) -> tuple[bool, str]:
    """Linux: ein virtuelles Gerät (mit seinen Weiterleitungen) entfernen."""
    if IS_WINDOWS:
        return False, "Unter Windows kommen virtuelle Geräte von VB-CABLE – entfernen über „Apps“ in den Einstellungen."
    names = {dev_id, dev_id + "_mix", dev_id + ".monitor", dev_id + "_mix.monitor"}
    for m in _pa_modules(run):
        args = dict(a.split("=", 1) for a in m["args"].split() if "=" in a)
        if args.get("sink_name") in names or args.get("source_name") in names or \
                (m["name"] == "module-loopback" and (args.get("source") in names or args.get("sink") in names)):
            run(["pactl", "unload-module", m["id"]])
    return True, ""


def virtual_devices(configured: list[dict] | None = None, outs: list[dict] | None = None,
                    ins: list[dict] | None = None) -> list[dict]:
    """Virtuelle Geräte für Mixer und Weiterleitung: [{id, kind ("out"/"mic"), name, sink, source, present,
    removable}] – sink: wohin Ton muss, damit er im Gerät ist; source: woher man ihn abgreift.
    outs/ins: schon gelesene Geräte (Windows) – spart die zweite Abfrage."""
    if IS_WINDOWS:
        outs = devices("out") if outs is None else outs
        ins = devices("in") if ins is None else ins
        found = []
        for o in outs:  # VB-CABLE: „CABLE Input“ ↔ „CABLE Output“ (auch CABLE-A/B …)
            low = o["name"].lower()
            if "cable" not in low or "input" not in low:
                continue
            key = low.split("(")[0].replace("input", "").strip()
            mic = next((i for i in ins if i["name"].lower().split("(")[0].replace("output", "").strip() == key), None)
            found.append({"id": o["id"], "kind": "out", "name": o["name"], "sink": o["id"],
                          "source": mic["id"] if mic else "", "present": True, "removable": False})
            if mic:
                found.append({"id": mic["id"], "kind": "mic", "name": mic["name"], "sink": o["id"],
                              "source": mic["id"], "present": True, "removable": False})
        return found
    sinks = {str(d.get("name")) for d in _pa_json("sinks")}
    sources = {str(d.get("name")) for d in _pa_json("sources")}
    out = []
    for dev in configured or DEFAULT_DEVICES:
        mic = dev["kind"] == "mic"
        out.append({"id": dev["id"], "kind": dev["kind"], "name": dev.get("name") or dev["id"],
                    "sink": dev["id"] + "_mix" if mic else dev["id"],
                    "source": dev["id"] if mic else dev["id"] + ".monitor",
                    "present": (dev["id"] in sources) if mic else (dev["id"] in sinks),
                    "removable": dev["id"] not in (OUT_NAME, MIC_NAME)})
    return out


def remove_virtual(run=_run) -> tuple[bool, str]:
    """Linux: AluPCs virtuelle Geräte und Weiterleitungen wieder entfernen."""
    if IS_WINDOWS:
        return False, "VB-CABLE entfernt man unter Windows über „Apps“ (oder lässt es einfach installiert)."
    for m in _pa_modules(run):
        if m["name"] in ("module-null-sink", "module-remap-source", "module-loopback") and "alupc" in m["args"]:
            run(["pactl", "unload-module", m["id"]])
    return True, ""


def routes() -> list[dict]:
    """Linux: aktive Weiterleitungen [{id, source, sink}] (Windows: siehe mixer_route.RouteManager)."""
    return [] if IS_WINDOWS else _pa_loopbacks()


def set_route(source: str, sink: str, on: bool, run=_run) -> tuple[bool, str]:
    """Linux: Ton von `source` (Mikrofon oder *.monitor) nach `sink` weiterleiten (an/aus)."""
    if IS_WINDOWS:
        return False, "Windows: über mixer_route"
    existing = [r for r in _pa_loopbacks(run) if r["source"] == source and r["sink"] == sink]
    if on and not existing:  # sink "" = Standard-Lautsprecher (folgt dem Standard, wenn er wechselt)
        return _pa_load(["module-loopback", f"source={source}", *([f"sink={sink}"] if sink else []),
                         "latency_msec=30", "source_dont_move=true", *(["sink_dont_move=true"] if sink else []),
                         "sink_input_properties=media.name=alupc_route",
                         "source_output_properties=media.name=alupc_route"], run)
    if not on:
        for r in existing:
            run(["pactl", "unload-module", r["id"]])
    return True, ""


def no_devices_hint() -> str:
    """Kein einziges Gerät gefunden: was der Audio-Dienst sagt (damit man den Grund sieht statt nur „Keine Geräte“)."""
    if IS_WINDOWS:
        return ("Windows meldet keine Ton-Geräte (Core Audio). Ist ein Lautsprecher/Kopfhörer angeschlossen und in "
                "den Sound-Einstellungen aktiviert?")
    _code, version = pa_run(["pactl", "--version"])
    code, info = pa_run(["pactl", "info"])
    server = next((line.split(":", 1)[1].strip() for line in info.splitlines() if line.startswith("Server Name")), "")
    _code, short = pa_run(["pactl", "list", "short", "sinks"])
    first = (version.splitlines() or ["pactl ?"])[0]
    if code != 0:
        return f"Audio-Dienst antwortet nicht ({first})."
    return (f"Audio-Dienst „{server or '?'}“ meldet keine Geräte ({first}). "
            + ("Rohliste: " + short.strip().replace("\t", " ")[:200] if short.strip()
               else "Auch „pactl list short sinks“ ist leer – kein Ton-Gerät erkannt (Treiber/Kabel?)."))


def visible_devices(items: list[dict], show_virtual: bool, configured_ids: set[str] = frozenset()) -> list[dict]:
    """Geräte für die Listen in AluPC: „CABLE In 16ch“ (zweiter, unnötiger Eingang von VB-CABLE) nie; virtuelle
    Geräte (VB-CABLE bzw. die von AluPC angelegten) nur, wenn „Virtuelle Geräte“ an ist."""
    def virtual(d: dict) -> bool:
        low, dev_id = str(d.get("name", "")).lower(), str(d.get("id", ""))
        if "vb-audio" in low or ("cable" in low and ("input" in low or "output" in low)):
            return True
        return dev_id in configured_ids or dev_id.startswith("alupc_") or dev_id.removesuffix(".monitor").endswith("_mix")

    return [d for d in items if "cable in 16" not in str(d.get("name", "")).lower()
            and (show_virtual or not virtual(d))]


def snapshot() -> dict:
    """Alles für die Seite „Audio“ auf einmal (im Hintergrund lesen)."""
    snap = {"out": devices("out"), "in": devices("in"), "apps": streams("out"), "rec": streams("in"),
            "virtual": virtual_status(), "routes": routes(), "ok": available()}
    if snap["ok"][0] and not snap["out"] and not snap["in"]:
        try:
            snap["ok"] = (False, no_devices_hint())
        except Exception:  # noqa: BLE001
            snap["ok"] = (False, "Keine Ton-Geräte gefunden.")
    return snap
