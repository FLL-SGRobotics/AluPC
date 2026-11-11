"""Virtuelle Geräte und Weiterleitungen (Seite „Audio“) – was eingestellt ist, merkt sich AluPC und stellt es beim
Start wieder her.

* Virtuelle Geräte: ein virtueller Ausgang und ein virtuelles Mikro sind immer da (wenn eingeschaltet); unter Linux
  kann man beliebig viele dazu anlegen und wieder entfernen (z. B. „Discord“, „Musik für den Stream“). Unter
  Windows kommen sie von VB-CABLE (pro installiertem Kabel ein Ausgang + ein Mikro).
* Weiterleitungen (Quelle → Ziel): Quelle = ein Mikrofon oder ein virtueller Ausgang, Ziel = ein virtuelles Mikro
  oder „Lautsprecher“ (Mithören). Beispiel: Headset → Discord-Mikro, Musik-Ausgang → Discord-Mikro + Lautsprecher.

Linux: Module im Audio-Dienst (mixer.set_route) – laufen auch ohne AluPC weiter, bis man sie ausschaltet.
Windows: AluPC leitet selbst weiter (Qt: Aufnahme → Wiedergabe), solange AluPC läuft; virtueller Ausgang und
Mikro desselben VB-CABLE sind ohnehin verbunden.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from . import mixer
from .audio import IS_WINDOWS

SPEAKER = "lautsprecher"  # Ziel „Mithören“: der Standard-Lautsprecher
DEFAULTS = {"virtual": False, "devices": None, "routes": None}


def _qt_device(kind: str, dev_id: str, name: str = ""):
    """QAudioDevice zu einer Geräte-ID (Windows: Endpunkt-ID, Linux: pactl-Name) – sonst über den Namen."""
    from PySide6.QtMultimedia import QMediaDevices

    devs = QMediaDevices.audioInputs() if kind == "in" else QMediaDevices.audioOutputs()
    for d in devs:
        if bytes(d.id()).decode(errors="ignore") == dev_id:
            return d
    for d in devs:
        if name and d.description() == name:
            return d
    if not dev_id:
        return QMediaDevices.defaultAudioInput() if kind == "in" else QMediaDevices.defaultAudioOutput()
    return None


def common_format(src, dst):
    """Ein Format, das Aufnahme- und Wiedergabegerät beide können (oder None)."""
    from PySide6.QtMultimedia import QAudioFormat

    candidates = [src.preferredFormat()]
    for rate in (48000, 44100):
        for ch in (2, 1):
            f = QAudioFormat()
            f.setSampleRate(rate)
            f.setChannelCount(ch)
            f.setSampleFormat(QAudioFormat.Int16)
            candidates.append(f)
    for f in candidates:
        if src.isFormatSupported(f) and dst.isFormatSupported(f):
            return f
    return None


class Pump(QObject):
    """Ton von einem Aufnahmegerät zu einem Wiedergabegerät weiterreichen (im Qt-Hauptthread, kleine Puffer)."""

    def __init__(self, src_dev, dst_dev, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioSink, QAudioSource

        if src_dev is None or dst_dev is None:
            raise ValueError("Gerät nicht gefunden")
        fmt = common_format(src_dev, dst_dev)
        if fmt is None:
            raise ValueError("Die Geräte haben kein gemeinsames Tonformat")
        self.source = QAudioSource(src_dev, fmt, self)
        self.sink = QAudioSink(dst_dev, fmt, self)
        bytes_per_10ms = fmt.bytesForDuration(10_000)
        self.source.setBufferSize(bytes_per_10ms * 4)
        self.sink.setBufferSize(bytes_per_10ms * 8)
        self._out = self.sink.start()
        self._in = self.source.start()
        if self._in is None or self._out is None:
            raise OSError("Gerät ließ sich nicht öffnen")
        self._in.readyRead.connect(self._move)

    def _move(self):
        data = self._in.readAll()
        if data and self._out is not None:
            self._out.write(data)

    def stop(self):
        try:
            self.source.stop()
            self.sink.stop()
        except RuntimeError:
            pass


class AudioRouting(QObject):
    changed = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._pumps: dict[tuple[str, str], Pump] = {}
        self.last_error = ""
        if not IS_WINDOWS:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(2000, self, self._cleanup_when_off)

    def _cleanup_when_off(self) -> None:
        """Linux: „Virtuelle Geräte“ aus, aber vom letzten Mal noch da (Audio-Dienst lief weiter) → entfernen, damit
        sie weder im System noch in Programmen auftauchen."""
        if self.cfg()["virtual"]:
            return
        from .ui.util import run_async

        run_async(mixer.remove_virtual, None, lambda _e: None, owner=self)

    # ---- Einstellungen (alte Form: mic_inputs/out_to_mic/listen → Weiterleitungen)
    def cfg(self) -> dict:
        raw = dict(self.config.data.get("audio_mixer") or {})
        cfg = {**DEFAULTS, **raw}
        if cfg["devices"] is None:
            cfg["devices"] = [dict(d) for d in mixer.DEFAULT_DEVICES]
        if cfg["routes"] is None:
            routes = [[dev, mixer.MIC_NAME] for dev in raw.get("mic_inputs") or []]
            if raw.get("out_to_mic", True):
                routes.append([mixer.OUT_NAME, mixer.MIC_NAME])
            if raw.get("listen"):
                routes.append([mixer.OUT_NAME, SPEAKER])
            cfg["routes"] = routes
        return cfg

    def _save(self, **values) -> None:
        cfg = {**self.cfg(), **values}
        for old in ("mic_inputs", "out_to_mic", "listen"):
            cfg.pop(old, None)
        self.config["audio_mixer"] = cfg

    def devices(self, snap: dict | None = None) -> list[dict]:
        """Virtuelle Geräte (Windows: die von VB-CABLE, Linux: die eingestellten). Fragt das System ab – nicht im
        Oberflächen-Thread aufrufen; snap: Ergebnis von mixer.snapshot() (Windows: Geräte nicht nochmal lesen)."""
        configured = None if IS_WINDOWS else self.cfg()["devices"]
        if snap:
            return mixer.virtual_devices(configured, snap.get("out"), snap.get("in"))
        return mixer.virtual_devices(configured)


    # ---- Wirklichkeit
    def _resolve(self) -> set[tuple[str, str]]:
        """Eingestellte Weiterleitungen → (Aufnahme-Quelle, Wiedergabe-Ziel); "" = Standard-Lautsprecher."""
        cfg = self.cfg()
        if not cfg["virtual"]:
            return set()
        devs = {d["id"]: d for d in self.devices()}
        want = set()
        for src, dst in cfg["routes"]:
            s = devs.get(src)
            source = (s["source"] if s and s["kind"] == "out" else src)
            if dst == SPEAKER:
                target = ""
            else:
                d = devs.get(dst)
                if d is None or d["kind"] != "mic" or not d["present"]:
                    continue
                target = d["sink"]
                if s is not None and IS_WINDOWS and s["sink"] == d["sink"]:
                    continue  # gleiches VB-CABLE: Ausgang und Mikro sind schon verbunden
            if source and (s is None or s["present"]):
                want.add((source, target))
        return want

    def apply(self) -> tuple[bool, str]:
        cfg = self.cfg()
        self.last_error = ""
        if cfg["virtual"] and not IS_WINDOWS:
            ok, why = mixer.create_virtual(cfg["devices"])
            if not ok:
                self.last_error = why
                self.changed.emit()
                return False, why
        want = self._resolve()
        if IS_WINDOWS:
            for key in [k for k in self._pumps if k not in want]:
                self._pumps.pop(key).stop()
            for src, dst in want - set(self._pumps):
                try:
                    self._pumps[(src, dst)] = Pump(_qt_device("in", src), _qt_device("out", dst), self)
                except (ValueError, OSError, TypeError, AttributeError) as exc:
                    self.last_error = f"Weiterleitung ging nicht: {exc}"
        else:
            have = {(r["source"], r["sink"]) for r in mixer.routes()}
            for src, dst in have - want:
                mixer.set_route(src, dst, False)
            for src, dst in want - have:
                ok, why = mixer.set_route(src, dst, True)
                if not ok:
                    self.last_error = f"Weiterleitung ging nicht: {why[:140]}"
        self.changed.emit()
        return not self.last_error, self.last_error

    # ---- Schalter
    def set_virtual(self, on: bool) -> tuple[bool, str]:
        self._save(virtual=bool(on))
        if not on:
            for pump in self._pumps.values():
                pump.stop()
            self._pumps.clear()
            if not IS_WINDOWS:
                mixer.remove_virtual()
            self.changed.emit()
            return True, ""
        return self.apply()

    def set_route(self, src: str, dst: str, on: bool) -> tuple[bool, str]:
        routes = [r for r in self.cfg()["routes"] if r != [src, dst]] + ([[src, dst]] if on else [])
        self._save(routes=routes, virtual=True if on else self.cfg()["virtual"])
        return self.apply()

    def add_device(self, kind: str, name: str) -> tuple[bool, str]:
        """Neues virtuelles Gerät (Linux). kind: "out" (Ausgang) oder "mic" (Mikro)."""
        name = " ".join(str(name or "").split())[:40]
        if IS_WINDOWS:
            return False, ("Unter Windows gibt es so viele virtuelle Geräte, wie VB-CABLE-Kabel installiert sind "
                           "(weitere: „VB-CABLE A+B“ bei vb-audio.com).")
        if not name:
            return False, "Bitte einen Namen eingeben."
        cfg = self.cfg()
        taken = {d["id"] for d in cfg["devices"]} | {str(d.get("name")) for d in mixer._pa_json("sinks")}
        dev = {"id": mixer.new_device_id(name, kind, taken), "kind": "mic" if kind == "mic" else "out", "name": name}
        self._save(devices=cfg["devices"] + [dev], virtual=True)
        ok, why = self.apply()
        return ok, why or f"„{name}“ angelegt."

    def remove_device(self, dev_id: str) -> tuple[bool, str]:
        if dev_id in (mixer.OUT_NAME, mixer.MIC_NAME):
            return False, "Die beiden Standard-Geräte bleiben – „Ausschalten“ entfernt alle."
        cfg = self.cfg()
        self._save(devices=[d for d in cfg["devices"] if d["id"] != dev_id],
                   routes=[r for r in cfg["routes"] if dev_id not in r])
        ok, why = mixer.remove_device(dev_id)
        self.apply()
        return ok, why

    def restore(self) -> None:
        if self.cfg()["virtual"]:
            self.apply()

    def shutdown(self) -> None:
        for pump in self._pumps.values():
            pump.stop()
        self._pumps.clear()
