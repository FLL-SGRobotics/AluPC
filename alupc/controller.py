"""Zentrale Steuerung: Was läuft gerade auf Monitor 2?"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from .config import Config
from .output_window import OutputWindow, ScreenGrabber, screen_by_name
from .platform import create_display_backend, create_fingerprint_backend, create_window_backend
from .scenes import describe_source
from .sources import create_source, media_sources, window_settings


HANDY_NOTES = ("iPhone/iPad", "Miracast")  # Monitor 2 zeigt ein Handy-Fenster (AirPlay, Miracast)


def evening_key() -> str:
    """Ein „Abend“ geht bis 6 Uhr früh – so zählt ein Spieleabend über Mitternacht als einer."""
    import datetime

    return (datetime.datetime.now() - datetime.timedelta(hours=6)).date().isoformat()


class Controller(QObject):
    changed = Signal()
    message = Signal(str)
    # Tastenbox: was gerade an der Box passiert (Anzeige unten links am Hauptmonitor) –
    # {"art": "taste"|"folge"|"code"|"regel"|"regler"|"zahl", …}
    box_input = Signal(dict)
    settings_imported = Signal(list)  # Dual-Boot: Einstellungen vom anderen System übernommen
    sync_status = Signal(str)
    games_changed = Signal()  # Minispiele: Spieler/Phase geändert (Steuerfenster)
    hotspot_changed = Signal()  # Hotspot an/aus (Kachel, Spiele-Fenster)
    window_requested = Signal()  # AluPC-Hauptfenster nach vorn (Tastenkürzel, auch als System-Desktop)
    guests_changed = Signal()  # Chat/Foto-Wand: neue Nachricht, neues Foto, an/aus (Seite „Gäste“)
    access_requested = Signal(str, str, str)  # Handy bittet um Freigabe zum Steuern: (Anfrage-ID, Name, IP)
    video_resume = Signal(str, int)  # Video schon mal geschaut: (Titel, Position ms) → „Weiterschauen?“
    board_requested = Signal()  # Whiteboard-Fenster am PC öffnen (macht die Oberfläche)
    browser_control_requested = Signal()  # Fenster „Browser steuern“ öffnen (z. B. für Kahoot)
    sender_requested = Signal()  # Fenster „An Fernseher senden“ (Chromecast/AirPlay) öffnen
    board_guest_requested = Signal(str, str)  # Gast möchte aufs Whiteboard schreiben: (Geräte-ID, Name)
    duel_requested = Signal()  # Schiffe versenken: Fenster für Spieler 1 öffnen (macht das Hauptfenster)
    studio_changed = Signal()  # Studio-Modus an/aus oder neue Vorschau (Fenster „Studio-Modus“)
    presenter_requested = Signal()  # Fenster „Zeigen & Zeichnen“ öffnen (macht die Oberfläche)  # kurze Meldung für die Statusleiste / Benachrichtigung

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        # Studio-Modus (wie OBS): Inhalte landen erst in der Vorschau, „Übergang“ schaltet sie live
        self.studio = False
        self.preview: dict | None = None
        self.display = create_display_backend()
        self.windows = create_window_backend()
        self.fingerprint = create_fingerprint_backend()
        # Finger als Schnelltaste: Befehl kommt aus einem Hintergrund-Thread → Qt stellt ihn in den GUI-Thread
        from .finger_shortcuts import FingerShortcuts

        self.finger_shortcuts = FingerShortcuts(config, self)
        self.finger_shortcuts.triggered.connect(self.run_command)
        from .tastenbox import ButtonBox, VolumeKeeper

        self.buttonbox = ButtonBox(self)
        self.volume_keeper = VolumeKeeper(self)
        self.knob_wait = False  # Regler-Ziel gewechselt: wartet, bis der Regler auf 0 steht
        self.buttonbox.key.connect(self._box_key)
        self.buttonbox.long_key.connect(self._box_long_key)
        self.buttonbox.volume.connect(self._box_volume)
        self.buttonbox.fine.connect(self._box_fine)
        self.buttonbox.mute.connect(self._box_mute)
        self.buttonbox.state_changed.connect(self._box_state)
        self.volume_keeper.overridden.connect(self._volume_overridden)
        self._led_level: int | None = None
        self._led_extra: tuple | None = None
        self._override_note = 0.0
        # Wenn → Dann: Code-Eingabe auf den 9 Tasten und Reglerzahl (siehe automations)
        from PySide6.QtCore import QTimer

        self.box_code: str | None = None  # None = keine Code-Eingabe, sonst die bisher getippten Ziffern
        self._code_timer = QTimer(self, singleShot=True, timeout=self._code_done)
        self._value_timer = QTimer(self, singleShot=True, timeout=self._value_settled)
        # Regler drehen schickt viele Werte pro Sekunde – die Oberfläche nur gebündelt neu zeichnen (sonst ruckelt alles)
        self._knob_changed = QTimer(self, singleShot=True, interval=120, timeout=self.changed.emit)
        # Tastenfolgen (z. B. 7-1-2): Tasten, die eine Folge beginnen könnten, warten kurz mit ihrer eigenen Aktion
        self._seq_keys: list[tuple[int, str]] = []  # (Taste, ihre zurückgehaltene Aktion)
        self._seq_timer = QTimer(self, singleShot=True, timeout=self._seq_timeout)
        from .buzzer import BuzzerPlayer

        self.buzzer = BuzzerPlayer(lambda line: self.buttonbox.send(line), self)
        self._value_hits: set[str] | None = None  # Regeln, die beim letzten ruhigen Wert passten (None = noch keiner)
        self._rule_depth = 0
        self._fine_level: int | None = None  # zuletzt gesetzte Stufe bei Zählwerk-Skala
        self.finger_shortcuts.apply()
        # Linux: Sperrbildschirm mit dem Finger entsperren, ohne Enter (wie unter Windows)
        from .finger_unlock import FingerUnlock

        self.finger_unlock = FingerUnlock(config, self)
        self.finger_unlock.apply()
        # Sprachbefehle („Monitor schwarz“) – offline mit Vosk, nur wenn eingeschaltet
        from .voice import VoiceControl

        self.voice = VoiceControl(config, scenes=config.scene_names, parent=self)
        from .speech import Speaker

        self.speaker = Speaker(config, self)
        from .assistant import Assistant

        self.assistant = Assistant(self)
        self.voice.command.connect(self._voice_command)
        self.voice.not_understood.connect(self._voice_not_understood)
        self.voice.direct_changed.connect(lambda _on: self.changed.emit())
        self.speaker.speaking.connect(self.voice.mute)  # Echo-Sperre: eigene Antwort nicht als Befehl hören
        if config["voice"].get("on"):
            from PySide6.QtCore import QTimer as _VT

            _VT.singleShot(1500, self.voice.apply)  # nach dem Start, damit AluPC nicht langsamer startet
        from .weather import service as weather_service

        self.weather = weather_service(config)
        self.wheel_left: list[str] | None = None  # Glücksrad: noch nicht gezogene Einträge (bei „Gezogene raus“)
        self._scene_volume: dict | None = None
        self._mirror_hint_shown = False
        self._system_main: str | None = None
        self.output = OutputWindow()
        self.grabber = ScreenGrabber(self)
        self.grabber.done.connect(self._frozen_grab_done)
        self.grabber.failed.connect(self._frozen_grab_failed)
        self.pip = None  # wird von der Oberfläche gesetzt
        self._welcome = None  # laufende Begrüßung (Fenster)
        self._games_timer = None
        self._games_version = -1
        self.resume_offer: dict | None = None  # {"path", "pos", "title", "until"} – Frage „Weiterschauen?“ offen
        from PySide6.QtCore import QTimer as _RT

        self._video_timer = _RT(self, interval=5000)  # Videoposition merken (fürs Weiterschauen)
        self._video_timer.timeout.connect(self._remember_video_positions)
        self._video_timer.start()
        self._resume_timer = _RT(self, singleShot=True)
        self._resume_timer.timeout.connect(lambda: self.video_resume_choice("weiter"))
        self.game_sounds = None  # Töne der Minispiele (games_sounds.GameSounds), beim ersten Start angelegt

        self.mode = "desktop"  # "content" = AluPC zeigt etwas, "desktop" = normaler zweiter Desktop
        self.content: dict | None = None
        self.desktop_note = "Erweitert"
        self.frozen = False
        self.privacy = False
        from .cursor import CursorGuard
        from .laser import LaserWindow

        self.cursor_guard = CursorGuard(self)
        self.cursor_guard.display = self.display
        self.laser = LaserWindow(self)
        from . import board as board_mod

        self.board = board_mod.shared(config)  # die Tafel (Whiteboard): Seiten, Zoom, Striche – Monitor 2 zeigt sie
        self.board.changed.connect(self._mark_preview_dirty)
        self.output.after_raise.append(self.laser.raise_above)
        self.laser.changed_cb = self.save_drawings
        from .overlays import OverlayWindow

        self.tile_overlays: dict[str, dict] = {}  # Kachel-ID → gerade eingeblendetes Overlay dieser Kachel
        self.live_overlays: dict[str, dict] = {}  # z. B. „chat“ → Chat-Overlay (eigener Schalter, nicht „Overlays“)
        from .mixer_route import AudioRouting

        self.audio_routing = AudioRouting(config, self)  # virtuelles Mikro / Mithören (Seite „Audio“)
        from .vcam import VirtualCam

        from .monitor_feed import MonitorFeed

        self.monitor_feed = MonitorFeed(self, self)  # echtes Bild von Monitor 2 bei „Erweitern“ (Handy, Kamera, Live)
        self.vcam = VirtualCam(self, self)  # Monitor 2 als virtuelle Kamera
        from .sender import ScreenSender

        self.sender = ScreenSender(self, self)  # Monitor 2 an Chromecast / AirPlay senden
        self.sender.changed.connect(self.changed)
        self.overlay_window = OverlayWindow(self)
        # Reihenfolge: Ausgabe → Overlays → Zeichnungen/Laser ganz oben
        self.output.after_raise.insert(0, self.overlay_window.raise_above)
        self.output.after_visibility.append(self.overlay_window.place)  # Monitor 2 zeigt etwas/nichts mehr
        from .handy import airplay_server

        self.airplay = airplay_server(config)
        self.airplay.output_screen = self.output_screen  # iPhone-Bild in der Größe von Monitor 2
        self.airplay.failed.connect(self._airplay_failed)
        self.airplay.notice.connect(self._airplay_notice)
        self.airplay.settings_changed.connect(self._airplay_settings_changed)
        self.airplay.connected.connect(self._iphone_connected)
        self.airplay.disconnected.connect(self._iphone_disconnected)
        from . import perf

        perf.configure(self.config["appearance"].get("performance", "auto"))
        s = self.config["handy"]
        if not s.get("airplay_simple"):  # AirPlay ohne Einstellungen: einmalig Name „AluPC“, kein Code
            self.config["handy"] = {**s, "airplay_simple": True, "pin": "", "airplay_idle": "bereit",
                                    "airplay_name": "AluPC", "airplay_always": True,
                                    "airplay_auto_show": True, "airplay_borderless": True}
        self._airplay_bg_timer = None
        self.recent_messages: list[str] = []  # für „Diagnose kopieren“
        self.message.connect(lambda m: self.recent_messages.append(m) or
                             self.recent_messages.__delitem__(slice(0, -30)))
        self.last_mirror_problem = ""
        self._handy_window = ""  # „airplay“, solange das iPhone-Fenster auf Monitor 2 liegt
        from .cast_server import cast_server

        self.cast = cast_server(config)
        self.cast.wifi_provider = self.games_wifi  # AluPC-WLAN läuft → Lobby zeigt dessen WLAN-Code
        self.cast.board_info = lambda: (self.board.settings()["guests"], self.whiteboard_active())
        self.cast.wifi_status_provider = self.games_wifi_status  # nur über das WLAN: was steht statt des Codes?
        self._games_wifi_starting = False
        import threading

        self._wlan_lock = threading.Lock()  # AluPC-WLAN nie zweimal gleichzeitig starten/beenden
        self.cast.request.connect(self._cast_request)
        from PySide6.QtCore import QTimer as _QTimer

        self._cast_timer = _QTimer(self, interval=900)  # Live-Bild/Status fürs Handy
        self._cast_timer.timeout.connect(self._cast_tick)
        self._cast_timer.start()
        self._preview_dirty, self._preview_at = True, 0.0
        # Zeichnen am iPad: neues Live-Bild, sobald sich etwas ändert (höchstens 25×/s), sonst ~9×/s
        self._fast_timer = _QTimer(self, interval=40)
        self._fast_timer.timeout.connect(self._fast_preview)
        self._fast_timer.start()
        self.changed.connect(self.update_cursor_guard)
        self.changed.connect(self._cast_snapshot)
        self.changed.connect(self._overlays_visibility)
        self.changed.connect(self.update_box_led)
        _QTimer.singleShot(0, self.apply_buttonbox)  # Tastenbox im Hintergrund suchen
        self._overlays_shown = None
        self.overlay_window.reload()
        if self.cast.settings().get("autostart"):
            self.cast.start()
        from .rgb_manager import RgbManager

        self.rgb = RgbManager(self)
        # Dual-Boot-Abgleich: nach Änderungen (kurz gesammelt) in den gemeinsamen Ordner schreiben
        from PySide6.QtCore import QTimer

        self._syncing = False
        self._sync_timer = QTimer(self, singleShot=True, interval=4000)
        self._sync_timer.timeout.connect(self.run_sync)
        # Regelmäßig abgleichen: Laufwerk später eingehängt, Änderung vom anderen System, Schreiben ging vorher nicht
        self._sync_poll = QTimer(self, interval=60_000)
        self._sync_poll.timeout.connect(lambda: self.config.data["sync"].get("enabled") and not self._syncing
                                        and self.run_sync(mount=False))
        self._sync_poll.start()
        config.listeners.append(self._config_saved)
        self.apply_output_settings()

        from .sounds import SoundPlayer

        self.sounds = SoundPlayer(config)
        from .screensaver import ScreensaverManager

        self.screensaver = ScreensaverManager(self)
        self.screensaver.changed.connect(self.changed.emit)

        # Timer beobachten: Ton bei „noch 1 Minute“ und bei Ablauf
        from PySide6.QtCore import QTimer

        self._timer_state = ("", False)
        self._timer_watch = QTimer(self, interval=250)
        self._timer_watch.timeout.connect(self._watch_timer)
        self._timer_watch.start()

        app = QGuiApplication.instance()
        app.screenAdded.connect(self._screen_added)
        app.screenRemoved.connect(lambda _s: self.update_screens())
        app.primaryScreenChanged.connect(lambda _s: self.update_screens())
        for screen in QGuiApplication.screens():
            self._watch(screen)
        self.update_screens()

    def _watch(self, screen) -> None:
        # Auflösung/Position geändert (z. B. über Setup) → Fenster neu platzieren
        screen.geometryChanged.connect(lambda _g: self.update_screens())

    def _screen_added(self, screen) -> None:
        self._watch(screen)
        self.update_screens()

    # ------------------------------------------------------------ Monitore
    def _primary(self):
        """Hauptmonitor (Monitor 1). KDE/Wayland: wie in den Systemeinstellungen, nicht Qts Reihenfolge."""
        screen = screen_by_name(self._system_main) if self._system_main else None
        return screen or QGuiApplication.primaryScreen()

    def output_screen(self):
        screens = QGuiApplication.screens()
        wanted = self.config["output_screen"]
        if wanted:
            screen = screen_by_name(wanted)
            if screen is not None:
                return screen
        primary = self._primary()
        for s in screens:
            if s is not primary:
                return s
        return None

    def main_screen(self):
        out = self.output_screen()
        primary = self._primary()
        if primary is not None and primary is not out:
            return primary
        for s in QGuiApplication.screens():
            if s is not out:
                return s
        return primary

    def update_screens(self) -> None:
        self._system_main = self.display.main_name()
        self.output.place_on(self.output_screen())
        if self.laser.needed():
            self.laser.place()
        if hasattr(self, "overlay_window"):
            self.overlay_window.place()
        self.changed.emit()

    # ------------------------------------------------------------ Maus
    def cursor_should_stay_home(self) -> bool:
        """Maus auf Monitor 1 festhalten? Ja, solange Monitor 2 nicht der normale Desktop („Erweitern“) ist."""
        # Nur der Modus zählt – nicht, ob das Fenster auf Monitor 2 gerade (z. B. beim Umschalten) kurz
        # unsichtbar ist. „Programm direkt auf Monitor 2“ ist Erweitern → dort darf die Maus hin.
        saver = getattr(self, "screensaver", None)
        showing = self.mode == "content" or self.frozen or self.privacy or bool(saver and saver.active)
        return bool(self.config["output"].get("confine_cursor", True)) and showing \
            and self.output_screen() is not None

    def update_cursor_guard(self) -> None:
        self.cursor_guard.set_active(self.cursor_should_stay_home(), self.main_screen(), self.output_screen())

    # ------------------------------------------------------------ Zeichnungen
    def _content_key(self) -> str:
        import json

        return json.dumps(self.content, sort_keys=True) if self.mode == "content" and self.content else "desktop"

    def save_drawings(self) -> None:
        """Zeichnungen merken – sie bleiben, bis man sie löscht oder etwas anderes auf Monitor 2 kommt
        (auch über einen Neustart von AluPC hinweg)."""
        strokes = [dict(s, points=[list(p) for p in s["points"]]) for s in self.laser.strokes]
        self.config["draw"] = {**self.config["draw"], "strokes": strokes,
                               "strokes_for": self._content_key() if strokes else ""}
        self.changed.emit()

    def _restore_drawings(self) -> None:
        d = self.config["draw"]
        if d.get("strokes") and d.get("strokes_for") == self._content_key():
            self.laser.strokes = [dict(s, points=[tuple(p) for p in s.get("points", [])]) for s in d["strokes"]]
            self.laser.place()
            self.laser.update()

    # ------------------------------------------------------------ Kamera: Zoom, Ausschnitt, Drehen …
    def _sync_camera_settings(self) -> None:
        from .sources import camera_settings

        camera_settings.clear()
        camera_settings.update({k: dict(v) for k, v in self.config["camera"].items()})

    def cameras_on_output(self) -> list:
        from .sources import camera_sources

        return camera_sources(self.output.content) if self.mode == "content" else []

    def set_camera_option(self, device_id: str, **changes) -> dict:
        """Kamera-Einstellung ändern (gilt sofort und bleibt gespeichert). Rückgabe: neue Einstellungen."""
        from .sources import CAMERA_DEFAULTS, MAX_ZOOM, clamp_center

        opts = {**CAMERA_DEFAULTS, **self.config["camera"].get(device_id, {}), **changes}
        opts["zoom"] = round(max(1.0, min(MAX_ZOOM, float(opts["zoom"]))), 2)
        opts["x"], opts["y"] = clamp_center(opts["zoom"], float(opts["x"]), float(opts["y"]))
        opts["rotate"] = int(opts["rotate"]) % 360
        opts["exposure"] = max(-2.0, min(2.0, float(opts["exposure"])))
        self.config["camera"] = {**self.config["camera"], device_id: opts}
        self._sync_camera_settings()
        for cam in self.cameras_on_output():
            if cam.device_id == device_id:
                cam.apply_options()
        return opts

    def reset_camera(self, device_id: str) -> None:
        from .sources import CAMERA_DEFAULTS

        keep = self.config["camera"].get(device_id, {}).get("quality", CAMERA_DEFAULTS["quality"])
        self.set_camera_option(device_id, **{**CAMERA_DEFAULTS, "quality": keep})

    def camera_zoom(self, factor: float | None) -> None:
        """Zoom der (ersten) Kamera auf Monitor 2: mal `factor`, None = zurück auf 1×."""
        cams = self.cameras_on_output()
        if not cams:
            self.message.emit("Keine Kamera auf dem Bildschirm.")
            return
        cam = cams[0]
        zoom = 1.0 if factor is None else cam.options()["zoom"] * factor
        self.set_camera_option(cam.device_id, zoom=zoom)

    def _apply_cursor_settings(self) -> None:
        """Mauszeiger beim Spiegeln einzeichnen – aber nicht, wenn der Laserpointer an ist. Dazu: Aussehen der
        Publikumsansicht (Bühne/Schwarz)."""
        from . import stage
        from .sources import ScreenSource, screen_settings

        stage.configure(self.config["output"].get("stage", "buehne"))
        if self.output.content is not None:
            self.output.content.update()

        show = bool(self.config["output"].get("mirror_cursor", True))
        screen_settings["cursor"] = show
        content = self.output.content
        if isinstance(content, ScreenSource):
            if content.feed is not None:
                content.feed.cursor = show
            content.update()

    def screens_overlap(self) -> bool:
        out, main = self.output_screen(), self.main_screen()
        return out is not None and main is not None and out.geometry().intersects(main.geometry())

    def ensure_extended(self) -> None:
        """AluPC braucht einen eigenen Bereich auf Monitor 2 – System-Spiegeln ggf. aufheben."""
        if not self.screens_overlap() or not self.display.available():
            return
        try:
            self.display.extend(self.main_screen().name(), self.output_screen().name(), "right")
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Konnte nicht auf Erweitern umschalten: {exc}")

    # ------------------------------------------------------------ Modi
    def apply_output_settings(self) -> None:
        cfg = self.config["output"]
        self.output.hide_taskbar = bool(cfg.get("hide_taskbar", True))
        self.output.freeze_layer.badge = bool(cfg.get("freeze_badge", True))
        self.output.freeze_layer.update()
        if not self.output.hide_taskbar:
            self.output.taskbar.restore()
        self._apply_cursor_settings()
        self.update_cursor_guard()

    def _guard(self) -> bool:
        # Jede Bedienung zählt als Aktivität und beendet einen laufenden Bildschirmschoner
        self.screensaver.activity()
        return True

    def show_source(self, cfg: dict, remember: bool = True, sound: bool = True, live: bool = False,
                    transition: tuple[str, int] | None = None) -> None:
        if self.studio and not live:  # Studio-Modus: erst vorbereiten, dann mit „Übergang“ live schalten
            self.preview = cfg
            self.studio_changed.emit()
            return
        if not self._guard():
            return
        if self.output_screen() is None:
            self.message.emit("Kein Bildschirm gefunden.")
        self.ensure_extended()
        self._unfreeze()
        self.screensaver.stop()
        self.mode = "content"
        self.content = cfg
        self._scene_volume = None
        self._content_switched()
        self._stop_handy_window()
        window_settings["restore_minimized"] = bool(self.config["program"].get("restore_minimized", True))
        self._sync_camera_settings()
        self._remember_video_positions()  # bevor das alte Video verschwindet
        self.resume_offer = None
        self._resume_timer.stop()
        self.output.set_content(create_source(cfg, self.config.get_scene), transition or self.transition_for(cfg))
        self._offer_resume()
        if cfg.get("type") == "airplay":
            self._follow_airplay_window()
        if remember:
            self.config["last_content"] = cfg
            from . import media_library

            media_library.remember(self.config, cfg)
        if sound and remember:
            self.sounds.play_event("szene" if cfg.get("type") == "scene" else "inhalt")
        self.changed.emit()

    # ------------------------------------------------------------ Studio-Modus
    def set_studio(self, on: bool) -> None:
        """Studio-Modus an/aus. Beim Einschalten steht der aktuelle Inhalt auch in der Vorschau."""
        self.studio = bool(on)
        if self.studio and self.preview is None and self.mode == "content" and self.content:
            self.preview = dict(self.content)
        self.studio_changed.emit()
        self.changed.emit()

    def take(self, transition: tuple[str, int] | None = None) -> bool:
        """Vorschau live schalten (wie „Übergang“ in OBS). Der bisherige Inhalt wandert in die Vorschau –
        so kann man mit einem zweiten Klick zurückwechseln."""
        if self.preview is None:
            return False
        old = dict(self.content) if self.mode == "content" and self.content else None
        self.show_source(self.preview, live=True, transition=transition)
        self.preview = old if old is not None else self.preview
        self.studio_changed.emit()
        return True

    def clear_preview(self) -> None:
        self.preview = None
        self.studio_changed.emit()

    # ------------------------------------------------------------ Ton der Medien auf Monitor 2
    def media_state(self) -> dict | None:
        """Lautstärke des aktuellen Inhalts (Video/Website, auch in Szenen) – None, wenn ohne Ton."""
        if self.mode != "content" or not media_sources(self.output.content):
            return None
        cfg = self.content or {}
        if cfg.get("type") == "scene":
            cfg = self._scene_volume or {}
        return {"volume": int(cfg.get("volume", 100)), "muted": bool(cfg.get("muted", False))}

    def set_media_volume(self, volume: int | None = None, muted: bool | None = None) -> None:
        """Lautstärke live ändern. Bei einer einzelnen Quelle wird sie in deren Einstellung gemerkt;
        in Szenen gilt sie für alle Videos/Websites bis zum nächsten Wechsel."""
        sources = media_sources(self.output.content)
        if not sources:
            return
        for src in sources:
            src.set_volume(volume, muted)
        changes = {k: v for k, v in (("volume", volume), ("muted", muted)) if v is not None}
        if self.content and self.content.get("type") in ("video", "website"):
            self.content = {**self.content, **changes}
            self.config["last_content"] = self.content
        else:
            self._scene_volume = {**(self._scene_volume or {}), **changes}
        self.changed.emit()

    def transition_for(self, cfg: dict | None) -> tuple[str, int]:
        """Übergang zum neuen Inhalt: Einstellung im Setup, eine Szene kann sie überschreiben."""
        from .transitions import resolve

        base = dict(self.config["transition"])
        if not self.config["appearance"].get("fade", True):
            base["type"] = "schnitt"  # alte Einstellung „nicht überblenden“ = harter Schnitt …
        scene = self.config.get_scene(cfg.get("scene")) if cfg and cfg.get("type") == "scene" else None
        # … aber ein Übergang, der bei der Szene selbst eingestellt ist, gilt trotzdem
        return resolve(base, (scene or {}).get("transition"))

    def mirror(self) -> None:
        if self.config["output"].get("mirror_method") == "system" and self.display.available():
            self.system_mirror()  # Ersteinrichtung hat festgestellt: Bildaufnahme klappt hier nicht
            return
        main = self.main_screen()
        if self._capture_would_ask(main) and self.display.available():
            # Wayland ohne KWin-Freigabe: die Aufnahme würde jedes Mal „Welchen Bildschirm teilen?“ fragen
            # (man müsste den Monitor von Hand wählen) → KDE spiegelt selbst, ohne Nachfrage
            self.system_mirror()
            return
        self.show_source({"type": "screen", "screen_name": main.name() if main else "", "mirror": True})
        content = self.output.content
        if hasattr(content, "no_signal"):
            content.no_signal.connect(self._mirror_no_signal)
            if getattr(content, "problem", ""):  # schon beim Start gescheitert (z. B. Windows ohne Aufnahme)
                self._mirror_no_signal(content.problem)
        if getattr(content, "method", "") == "qt" and not self._mirror_hint_shown:
            from .platform.linux_display import is_wayland

            if is_wayland():
                self._mirror_hint_shown = True
                self.message.emit("Wayland fragt nach dem Bildschirm (mit .deb unter KDE nicht)")

    @staticmethod
    def _capture_would_ask(screen) -> bool:
        """Würde die Bildaufnahme den Menschen nach dem Bildschirm fragen? (Linux/Wayland ohne KWin-Freigabe)"""
        import sys

        if not sys.platform.startswith("linux") or screen is None:
            return False
        from . import sources
        from .platform.linux_display import is_wayland

        return is_wayland() and not sources._kwin_allowed(screen.name())

    def extend(self) -> None:
        if not self._guard():
            return
        self.ensure_extended()
        self._set_desktop("Erweitert")
        self.config["last_content"] = None

    def _mirror_no_signal(self, reason: str) -> None:
        """Spiegeln per Aufnahme klappt auf diesem PC nicht → automatisch über das Betriebssystem spiegeln."""
        if not (self.content and self.content.get("mirror")):
            return
        self.last_mirror_problem = reason
        if not self.display.available():
            self.message.emit(f"Spiegeln geht nicht: {reason} · Setup → Diagnose kopieren")
            return
        self.message.emit(f"Spiegeln: {reason} AluPC spiegelt jetzt über {self.display.name}.")
        self.system_mirror()

    def system_mirror(self) -> None:
        """Monitor 2 vom Betriebssystem spiegeln lassen (Kubuntu: kscreen-doctor, Windows: wie Win+P)."""
        from .ui.util import run_async

        pair = self.prepare_system_mirror()
        if pair is None:
            return
        main, out = pair
        run_async(lambda: self.display.mirror(main, out), lambda _r: self.changed.emit(),
                  lambda text: self.message.emit(f"System-Spiegeln fehlgeschlagen: {text}"))

    def prepare_system_mirror(self) -> tuple[str, str] | None:
        """AluPC-Anzeige beenden; liefert (Hauptmonitor, Monitor 2) für display.mirror()."""
        if not self._guard():
            return None
        out, main = self.output_screen(), self.main_screen()
        if out is None or main is None:
            self.message.emit("Kein Bildschirm gefunden.")
            return None
        if self.privacy:
            self.toggle_privacy()
        self._set_desktop("System-Spiegeln (vom Betriebssystem)")
        self.config["last_content"] = None
        return main.name(), out.name()

    def program_moved(self, title: str) -> None:
        self._set_desktop(f"Programm direkt auf dem Bildschirm: {title}")

    # ------------------------------------------------------------ Handy → Monitor 2
    def start_airplay(self) -> None:
        """iPhone/iPad auf Monitor 2. Monitor 2 zeigt sofort einen Warte-Bildschirm (Name, Code) – kein
        „Erweitert“. Kann UxPlay das Bild an AluPC weitergeben (-vrtp ab 1.73, unter Linux auch 1.6x über
        -vd/-vc/-vs), erscheint es direkt darin; sonst legt
        AluPC UxPlays eigenes Fenster randlos und im Vordergrund darüber, sobald sich das iPhone verbindet."""
        if not self.airplay.binary():
            self.message.emit("AirPlay: Empfänger fehlt · Handy → Einrichten")
            return
        if self.output_screen() is None:
            self.message.emit("Kein Bildschirm gefunden.")
            return
        self.show_source({"type": "airplay"})
        name = self.airplay.settings()["airplay_name"]
        code = f" · Code {self.airplay.pin_code}" if self.airplay.pin_code else ""
        self.message.emit(f"AirPlay bereit · „{name}“ wählen{code}")

    def start_miracast(self) -> None:
        """Miracast empfangen (nur Windows): eingebaute „Drahtlose Anzeige“ starten und ihr Fenster auf Monitor 2."""
        from .platform import miracast
        from .ui.util import run_async

        if not miracast.IS_WINDOWS:
            self.message.emit("Miracast empfangen geht nur unter Windows (Linux hat keinen gepflegten "
                              "Miracast-Empfänger) – hier: AirPlay oder Handy-Steuerung.")
            return
        if self.output_screen() is None:
            self.message.emit("Kein Bildschirm gefunden.")
            return

        def checked(result):
            app, support = result
            if not support:
                why = ("Kein WLAN-Adapter gefunden" if support is None else
                       "WLAN-Adapter oder Grafiktreiber können „Drahtlose Anzeige“ nicht")
                self.message.emit(f"Miracast geht auf diesem PC nicht: {why} (braucht WLAN mit Wi-Fi Direct).")
                return
            if app is None:
                self.message.emit("Miracast: Windows-App „Drahtlose Anzeige“ fehlt – Pfeil an der Kachel "
                                  "„Miracast“ → „Empfänger installieren“.")
                return
            self._stop_handy_window()
            self.ensure_extended()
            miracast.launch(app)
            self._handy_window = "miracast"
            self._set_desktop("Miracast (Windows „Drahtlose Anzeige“)")
            self.message.emit("Miracast bereit: am Handy/Laptop „Bildschirm übertragen“ bzw. „Smart View“ → "
                              "diesen PC wählen.")
            self._place_handy_window(app["name"], "Drahtlose Anzeige", "Wireless Display")

        run_async(lambda: (miracast.find_app(), miracast.wireless_display_support()), checked,
                  lambda text: self.message.emit(f"Miracast: {text}"))

    def install_miracast(self) -> None:
        """Windows: „Drahtlose Anzeige“ nachinstallieren · Linux: GNOME Network Displays (zum Senden)."""
        import subprocess

        from .platform import miracast
        from .ui.util import run_async

        def work():
            if miracast.IS_WINDOWS:
                miracast.install()
                return "Miracast-Empfänger installiert."
            cmd = miracast.linux_install_command()
            if not cmd:
                raise RuntimeError("Paketverwaltung unbekannt – bitte gnome-network-displays selbst installieren")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if proc.returncode != 0 or not miracast.linux_sender_command():
                raise RuntimeError(((proc.stderr or proc.stdout or "abgebrochen").strip().splitlines()
                                    or ["abgebrochen"])[-1])
            return "GNOME Network Displays installiert – jetzt „Miracast senden“."

        self.message.emit("Miracast: wird installiert (Administrator-Frage) …")
        run_async(work, self.message.emit, lambda text: self.message.emit(f"Miracast: {text}"))

    def send_miracast(self) -> None:
        """Bildschirm an einen Miracast-Fernseher (Windows „Verbinden“, Linux GNOME Network Displays)."""
        from .platform import miracast

        note = miracast.start_sending()
        self.message.emit(note.lstrip("!"))

    def _follow_airplay_window(self) -> None:
        """UxPlay zeigt das Bild im eigenen Fenster → dieses Fenster dauerhaft auf Monitor 2 legen."""
        from .sources import AirPlaySource

        src = self.output.content
        if isinstance(src, AirPlaySource) and src.mode == "fenster":
            self._handy_window = "airplay-quelle"  # UxPlay gehört der Quelle (gibt es beim Wechsel selbst frei)
            self.output.set_yield(True)  # iPhone-Fenster darf über den Warte-Bildschirm
            name = self.airplay.settings()["airplay_name"]
            # uxplay-windows/uxplay.exe: Fenster am Programm erkennen (Titel je nach Version verschieden)
            self._place_handy_window(name, name.replace(" ", "\u00a0"), "UxPlay", "AirPlay Video",
                                     apps=("uxplay-windows", "uxplay"))

    # ------------------------------------------------------------ AirPlay „immer bereit“
    def airplay_background(self, on: bool | None = None) -> None:
        """AirPlay dauerhaft im Hintergrund – mit AluPCs Name und Code. Fremde UxPlay-Autostarts (z. B. von
        uxplay-windows: Standardname, kein Code) werden abgeschaltet, sonst sieht das iPhone die statt AluPC.
        Verbindet sich ein iPhone, schaltet Monitor 2 von selbst aufs iPhone-Bild."""
        from PySide6.QtCore import QTimer

        from . import handy

        s = self.config["handy"]
        if on is None:
            on = bool(s.get("airplay_always", True)) and bool(s.get("setup_done")) and bool(self.airplay.binary())
        if not on:
            self.airplay.set_background(False)
            if self._airplay_bg_timer is not None:
                self._airplay_bg_timer.stop()
            return
        done, failed = handy.disable_uxplay_autostarts()
        if done:
            self.message.emit("UxPlay-Autostart aus – AluPC übernimmt AirPlay (Name/Code aus AluPC)")
        if failed:
            self.message.emit("UxPlay startet selbst mit Windows (für alle Benutzer) – nur mit Adminrechten "
                              "abschaltbar: " + ", ".join(failed))
        self.airplay.set_background(True)
        self._airplay_firewall_once()
        if self._airplay_bg_timer is None:
            # Windows: Fensterliste ist billig; Linux: wmctrl-Aufruf → seltener (UxPlay meldet Verbindungen ohnehin)
            self._airplay_bg_timer = QTimer(self, interval=2000 if __import__("sys").platform.startswith("win") else 5000)
            self._airplay_bg_timer.timeout.connect(self._airplay_bg_poll)
        self._airplay_bg_timer.start()

    def _airplay_firewall_once(self) -> None:
        """Windows: Firewall-Freigabe für AirPlay einmal nachholen (neue Regeln, z. B. auch für „öffentliche“
        WLANs) – sonst sieht das iPhone „AluPC“, kann sich aber nicht verbinden. Eine Windows-Abfrage (UAC)."""
        import sys as _sys

        from . import handy

        s = self.config["handy"]
        if not _sys.platform.startswith("win") or int(s.get("firewall_version", 0)) >= handy.FIREWALL_VERSION \
                or int(s.get("firewall_asked", 0)) >= handy.FIREWALL_VERSION:
            return
        self.config["handy"] = {**s, "firewall_asked": handy.FIREWALL_VERSION}  # nur einmal fragen
        from .ui.util import run_async

        cmd = handy.windows_firewall_command(_sys.executable, int(self.config["cast"].get("port", 8765)))
        self.message.emit("AirPlay: Windows fragt einmal nach Admin-Rechten (Firewall-Freigabe fürs iPhone)")

        def done(errors):
            if not errors:
                self.config["handy"] = {**self.config["handy"], "firewall_done": True,
                                        "firewall_version": handy.FIREWALL_VERSION}
                self.message.emit("AirPlay: Firewall freigegeben – iPhone kann sich jetzt verbinden")

        run_async(lambda: handy.run_plan([("Firewall", cmd)]), done,
                  lambda t: self.message.emit(f"Firewall-Freigabe nicht möglich: {t}"))

    def _airplay_shown(self) -> bool:
        from .sources import AirPlaySource

        return self.mode == "content" and isinstance(self.output.content, AirPlaySource)

    def _uxplay_window_present(self) -> bool:
        name = self.airplay.settings()["airplay_name"]
        titles = [name, name.replace(" ", "\u00a0"), "UxPlay", "AirPlay Video"]
        try:
            windows = self.windows.list_windows()
        except Exception:  # noqa: BLE001
            return False
        from .handy import is_iphone_window

        return any(is_iphone_window(w.title, w.app, titles) for w in windows)

    def _airplay_bg_poll(self) -> None:
        if not self.airplay.running() or self._airplay_shown():
            return
        if self.airplay.mode == "fenster" and self._uxplay_window_present():
            self._iphone_connected()

    def _iphone_connected(self) -> None:
        """iPhone verbindet sich → Monitor 2 zeigt es (auch wenn gerade etwas anderes lief)."""
        if self._airplay_shown() and self._handy_window == "airplay-quelle":
            if not getattr(self, "_follow_token", None):
                return  # Windows/X11: AluPC sieht das Fenster selbst und blendet dann aus (_place_handy_window)
            # KDE (KWin legt das UxPlay-Fenster hin): Warte-Bildschirm weg, damit er es auf keinen Fall verdeckt
            # (unter Wayland sieht AluPC fremde Fenster nicht und kann nicht prüfen, ob es schon oben liegt)
            self.output.set_suspended(True)
            return
        if self._airplay_shown() or self.output_screen() is None:
            return
        if not self.config["handy"].get("airplay_auto_show", True):
            return
        self.start_airplay()

    def _iphone_disconnected(self) -> None:
        if self._airplay_shown() and self._handy_window == "airplay-quelle" \
                and getattr(self, "_follow_token", None):
            self.output.set_suspended(False)  # wieder „AirPlay bereit“

    def _airplay_notice(self, text: str) -> None:
        self.message.emit(text)

    def _airplay_settings_changed(self) -> None:
        """Name/Randlos geändert, während das iPhone-Fenster verfolgt wird → mit neuem Namen weiter verfolgen
        (sonst sucht AluPC das Fenster unter dem alten Titel und es bleibt auf Monitor 1)."""
        if self._handy_window == "airplay-quelle":
            self._follow_airplay_window()

    def _airplay_failed(self, reason: str) -> None:
        self.message.emit(f"AirPlay läuft nicht: {reason}")
        if self._handy_window in ("airplay", "airplay-quelle"):
            self._stop_following()
            self._handy_window = ""

    def _config_saved(self) -> None:
        if not self._syncing and self.config.data["sync"].get("enabled"):
            self._sync_timer.start()

    def request_sync(self) -> None:
        """Etwas außerhalb der Einstellungen hat sich geändert (z. B. Fingerabdruck-Namen) → bald abgleichen."""
        self._config_saved()

    def run_sync(self, mount: bool = True) -> str:
        from .settings_sync import sync_once

        self._syncing = True
        try:
            msg, changed = sync_once(self.config, mount)
        except Exception as exc:  # noqa: BLE001
            msg, changed = f"Abgleich fehlgeschlagen: {exc}", []
        finally:
            self._syncing = False
        if changed:
            self.settings_imported.emit(changed)
        self.sync_status.emit(msg)
        return msg

    # ------------------------------------------------------------ AluCast (Handy per Browser)
    def start_cast(self) -> None:
        """Monitor 2: WLAN-Code + Seiten-Code (Mitspielen, Abstimmen – öffnet im Browser). Der Steuer-Code (Adresse
        mit Code) steht nur im Fenster am PC. Startet das WLAN, falls noch keins von AluPC läuft."""
        if not self.cast.start():
            self.message.emit("Handy-Steuerung konnte nicht starten: Netzwerk-Anschluss belegt.")
            return
        self._cast_snapshot()
        self.ensure_wlan()
        self.show_source({"type": "cast"})

    def stop_cast(self) -> None:
        self.cast.stop()
        self.config["cast"] = {**self.config["cast"], "autostart": False}
        if self.content and self.content.get("type") == "cast":
            self.extend()
        self.message.emit("AluCast beendet – Handys können nichts mehr senden.")

    # ------------------------------------------------------------ Videos: weiterschauen oder von vorn
    RESUME_MIN_LENGTH = 120_000  # nur bei Videos ab 2 Minuten
    RESUME_MARGIN = 30_000  # nicht, wenn erst 30 s geschaut oder fast zu Ende
    RESUME_WAIT = 15_000  # so lange wartet die Frage, dann geht es von selbst weiter

    def _remember_video_positions(self) -> None:
        import time as _time

        from .sources import video_sources

        if self.mode != "content":
            return
        saved = dict(self.config.data.get("video_positions") or {})
        changed = False
        for v in video_sources(self.output.content):
            path = getattr(v, "path", "")
            if not path or (self.resume_offer and self.resume_offer.get("path") == path):
                continue  # während die Frage offen ist, nichts überschreiben
            pos, dur = v.position(), v.duration()
            if dur < self.RESUME_MIN_LENGTH:
                continue
            if self.RESUME_MARGIN <= pos <= dur - self.RESUME_MARGIN:
                saved[path] = {"pos": pos, "dur": dur, "t": int(_time.time())}
                changed = True
            elif path in saved and pos > dur - self.RESUME_MARGIN:  # zu Ende geschaut → beim nächsten Mal von vorn
                saved.pop(path)
                changed = True
        if changed:
            newest = sorted(saved.items(), key=lambda kv: -kv[1].get("t", 0))[:50]
            self.config["video_positions"] = dict(newest)

    def _offer_resume(self) -> None:
        from pathlib import Path

        from .sources import video_sources

        saved = self.config.data.get("video_positions") or {}
        for v in video_sources(self.output.content):
            entry = saved.get(getattr(v, "path", ""))
            if not entry:
                continue
            v.player.pause()  # warten, bis entschieden ist
            title = Path(v.path).stem
            self.resume_offer = {"path": v.path, "pos": int(entry["pos"]), "title": title}
            self._resume_timer.start(self.RESUME_WAIT)
            self.video_resume.emit(title, int(entry["pos"]))
            return

    def video_info(self) -> dict | None:
        """Erstes Video auf Monitor 2: Titel, Position/Länge (s), läuft, Tempo, Wiederholen – fürs Handy."""
        from pathlib import Path

        from .sources import video_sources

        videos = video_sources(self.output.content) if self.mode == "content" else []
        if not videos:
            return None
        v = videos[0]
        try:  # nie den Handy-Status kaputtmachen, nur weil eine Quelle etwas nicht kann
            return {"title": Path(getattr(v, "path", "") or "Video").stem, "pos": v.position() // 1000,
                    "dur": v.duration() // 1000, "playing": v.playing(), "rate": round(v.rate(), 2),
                    "loop": v.looping()}
        except (AttributeError, RuntimeError, TypeError):
            return None

    def recent_videos(self, limit: int = 6) -> list[dict]:
        """Zuletzt geschaute Videos mit gemerkter Stelle (neueste zuerst) – zum Fortsetzen vom Handy."""
        from pathlib import Path

        saved = self.config.data.get("video_positions") or {}
        out = []
        for path, e in sorted(saved.items(), key=lambda kv: -kv[1].get("t", 0)):
            if Path(path).is_file():
                out.append({"title": Path(path).stem, "pos": int(e.get("pos", 0)) // 1000,
                            "dur": int(e.get("dur", 0)) // 1000, "path": path})
            if len(out) >= limit:
                break
        return out

    def continue_video(self, index: int) -> bool:
        """Video aus „Zuletzt geschaut“ auf Monitor 2 an der gemerkten Stelle weiterspielen."""
        from .sources import video_sources

        recent = self.recent_videos()
        if not 0 <= index < len(recent):
            return False
        item = recent[index]
        self.show_source({"type": "video", "path": item["path"], "loop": False})
        self.resume_offer = None  # nicht erst fragen – „Fortsetzen“ war die Antwort
        self._resume_timer.stop()
        for v in video_sources(self.output.content):
            if getattr(v, "path", "") == item["path"]:
                v.seek_when_ready(item["pos"] * 1000)
        self.changed.emit()
        return True

    def video_command(self, cmd: str) -> bool:
        """Video-Befehle vom Handy/aus Kürzeln. True = war ein Video-Befehl."""
        from .sources import video_sources

        if cmd.startswith("video_fortsetzen:"):
            self.continue_video(int(cmd.split(":", 1)[1]))
            return True
        videos = video_sources(self.output.content) if self.mode == "content" else []
        name, _, arg = cmd.partition(":")
        actions = {"video_pause": lambda v: v.toggle_play(), "video_vor": lambda v: v.skip(10_000),
                   "video_zurueck": lambda v: v.skip(-10_000), "video_vor30": lambda v: v.skip(30_000),
                   "video_zurueck30": lambda v: v.skip(-30_000), "video_neu": lambda v: (v.seek_to(0), v.player.play()),
                   "video_wiederholen": lambda v: v.set_loop(not v.looping()),
                   "video_pos": lambda v: v.seek_to(int(arg) * 1000), "video_tempo": lambda v: v.set_rate(float(arg))}
        if name not in actions:
            return False
        if videos:
            actions[name](videos[0])
        return True

    def video_resume_choice(self, choice: str) -> None:
        """„weiter“ = ab der gemerkten Stelle, „neu“ = von vorn."""
        from .sources import video_sources

        offer, self.resume_offer = self.resume_offer, None
        self._resume_timer.stop()
        if not offer:
            return
        for v in video_sources(self.output.content) if self.mode == "content" else []:
            if getattr(v, "path", "") == offer["path"]:
                v.seek_when_ready(offer["pos"] if choice == "weiter" else 0)
                break
        if choice == "neu":
            saved = dict(self.config.data.get("video_positions") or {})
            saved.pop(offer["path"], None)
            self.config["video_positions"] = saved
        self.changed.emit()

    def _cast_snapshot(self) -> None:
        from .sources import video_sources
        from .timer import clock

        state = self.media_state()
        content = self.content or {}
        rgb = self.rgb.settings()["mode"] if self.rgb.connected else ""
        self.cast.snapshot = {
            "now": self.describe(),
            "scenes": self.config.scene_names(),
            "scene": content.get("scene", "") if self.mode == "content" and content.get("type") == "scene" else "",
            "volume": int((state or {}).get("volume", 100)),
            "sound": state is not None,
            "video": bool(video_sources(self.output.content)) if self.mode == "content" else False,
            "resume": ({"title": self.resume_offer["title"], "pos": self.resume_offer["pos"] // 1000}
                       if self.resume_offer else None),
            "vid": self.video_info(),
            "audio": __import__("alupc.audio", fromlist=["state"]).state(),
            "recent": [{k: r[k] for k in ("title", "pos", "dur")} for r in self.recent_videos()],
            "timer": clock.text(),
            "keys": __import__("alupc.platform.keys", fromlist=["available"]).available(),
            "allow": {k: self.cast.allowed(k) for k in ("senden", "steuern", "live", "laser")},
            "ablauf": bool(self.step_sources()),
            "punkt": self.step_label(),
            "rgb": rgb,
            "boxled": {"on": self.buttonbox.connected, "version": self.buttonbox.version,
                       **{k: self.config["tasten"].get(k, d) for k, d in (
                           ("led", "live"), ("led_effekt", "ruhig"), ("led_farbe", "#00ffa0"),
                           ("led_hell", 100), ("led_schema", "einfarbig"))}},
            "reply": {"id": self.assistant.reply_seq, "text": self.assistant.last_reply},
            "flags": {"schwarz": self.privacy, "standbild": self.frozen, "schoner": self.screensaver.active,
                      "spiegeln": bool(self.mode == "content" and content.get("mirror")),
                      "erweitern": self.mode == "desktop" and self.desktop_note.startswith("Erweitert")},
            "board": self.board_state() if self.whiteboard_active() else None,
        }

    def whiteboard_active(self) -> bool:
        return self.mode == "content" and (self.content or {}).get("type") == "whiteboard"

    def board_state(self) -> dict:
        from .board import FORMATS
        from .whiteboard import BACKGROUNDS

        b = self.board
        cfg = b.settings()
        return {"page": b.doc["page"] + 1, "pages": len(b.doc["pages"]), "zoom": round(b.zoom * 100),
                "zoom_f": b.zoom, "page_h": b.page_size()[1],  # iPad: Strichbreite wie auf dem Bildschirm
                "bg": b.page["bg"], "undo": b.can_undo(), "redo": b.can_redo(), "sel": len(b.sel),
                "name": b.doc["name"], "colors": cfg["colors"], "pencil_only": bool(cfg["pencil_only"]),
                "width": cfg["width"], "marker_width": cfg["marker_width"], "guests": cfg["guests"],
                "settings": {k: cfg[k] for k in ("format", "background", "width", "marker_width", "smooth", "pressure",
                                                 "pencil_only", "eraser", "text_size", "autosave", "guests")},
                "boards": b.boards()[:30], "thumbs": getattr(self, "_thumbs_version", 0),
                "papers": [[k, v[0]] for k, v in BACKGROUNDS.items()],
                "formats": [[k, v[0]] for k, v in FORMATS.items()]}

    def board_action(self, op: str, arg: str = "") -> bool:
        """Whiteboard bedienen (Handy, Sprache, Befehle): zoom_in/out, fit, seite_neu/vor/zurueck/loeschen,
        rueckgaengig, wiederholen, leeren, auswahl_loeschen, hintergrund:<key>."""
        b = self.board
        acts = {"zoom_in": lambda: b.zoom_by(1.25), "zoom_out": lambda: b.zoom_by(0.8), "fit": b.fit,
                "seite_neu": b.new_page, "seite_vor": lambda: b.goto(b.doc["page"] + 1),
                "seite": lambda: b.goto(max(0, int(arg or 1) - 1)),
                "tafel_neu": lambda: b.new_board(arg.strip()),
                "tafel_oeffnen": lambda: b.load(arg) if arg in b.boards() else None,
                "tafel_umbenennen": lambda: b.rename(arg) if arg.strip() else None,
                "seite_zurueck": lambda: b.goto(b.doc["page"] - 1), "seite_loeschen": b.delete_page,
                "rueckgaengig": b.undo, "wiederholen": b.redo, "leeren": b.clear,
                "auswahl_loeschen": b.delete_selection,
                "hintergrund": lambda: b.set_background(arg)}
        fn = acts.get(op)
        if fn is None:
            return False
        if not self.whiteboard_active():
            self.show_whiteboard(draw=False)
        fn()
        return True

    def _phone_draw(self, req: dict) -> None:
        """Mit dem Finger auf Monitor 2 zeichnen (Live-Bild auf dem Handy = Monitor 2)."""
        from PySide6.QtCore import QPointF

        phase = req.get("phase")
        if self.whiteboard_active():  # Tafel zu sehen: auf der Tafel zeichnen (mit Zoom, Seiten, Druck)
            if phase == "up":
                last = getattr(self, "_board_last", None) or QPointF(0, 0)
                self.board.input("up", last.x(), last.y(), tool=getattr(self, "_board_tool", "pen"))
                return
            tool = {"stift": "pen", "radierer": "eraser"}.get(req.get("tool", "stift"), req.get("tool", "pen"))
            w = self.board.to_world(float(req["x"]), float(req["y"]))
            self._board_last, self._board_tool = w, tool
            width = req.get("width")
            self.board.input(phase, w.x(), w.y(), float(req.get("pressure", 0.5)), tool, req.get("color", "#111827"),
                             float(width) if width is not None else None)
            return
        if phase == "up":
            self.laser.end_stroke()
            return
        point = QPointF(float(req["x"]), float(req["y"]))
        if req.get("tool") == "radierer":
            self.laser.erase_at(point, 0.03)
            return
        if phase == "down":
            self.laser.begin_stroke("marker" if req.get("tool") == "marker" else "pen", req.get("color", "#ef4444"),
                                    0.005, point)  # Marker: breiter und halb durchsichtig (laser.stroke_pen)
        else:
            self.laser.extend_stroke(point)

    def _phone_mouse(self, req: dict) -> None:
        """Handy als Touchpad: Zeiger bewegen, klicken, scrollen (Windows und Linux/X11)."""
        from PySide6.QtGui import QCursor

        from .platform import keys

        if "click" in req:
            ok = keys.click(req["click"])
        elif "scroll" in req:
            ok = keys.scroll(int(req["scroll"]))
        else:
            dx, dy = round(float(req.get("dx", 0))), round(float(req.get("dy", 0)))
            if not keys.move(dx, dy):  # Wayland: nur über ydotool; X11/Windows: Qt setzt die Position
                pos = QCursor.pos()
                QCursor.setPos(pos.x() + dx, pos.y() + dy)
            ok = True
        if not ok and not getattr(self, "_mouse_hint", False):
            self._mouse_hint = True
            self.message.emit("Handy-Klicks: unter Wayland nur mit „ydotool“ (Paket ydotool + Dienst ydotoold)")

    def _phone_laser(self, x, y) -> None:
        """Laserpointer vom Handy (Finger auf dem Live-Bild)."""
        from PySide6.QtCore import QPointF

        if x is None:
            self.laser.remote_point(None)
            if getattr(self, "_phone_laser_on", False):
                self._phone_laser_on = False
                if not self.laser_owner_open():
                    self.laser.set_remote(False)
            return
        if not self.laser.remote:
            self._phone_laser_on = True
            self.laser.set_remote(True)
        self.laser.remote_point(QPointF(x, y))

    def laser_owner_open(self) -> bool:
        """Ist „Zeigen & Zeichnen“ offen? (dann bleibt der Laser dort an)"""
        return bool(getattr(self, "presenter_open", False))

    def _board_thumbs(self) -> None:
        """Seiten der Tafel als kleine Bilder für die Heft-Ansicht am iPad (nur wenn sie gerade jemand anschaut)."""
        import time

        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        if not self.cast.running() or time.monotonic() - self.cast.thumbs_wanted > 20:
            return
        b = self.board
        key = (b.doc["name"], b.doc["page"], tuple((len(p["strokes"]), p["bg"]) for p in b.doc["pages"]),
               len(b._undo), len(b._redo), id(b._undo[-1]) if b._undo else 0)  # jeder Strich/Schritt ändert es
        if key == getattr(self, "_thumbs_key", None):
            return
        self._thumbs_key = key
        out = []
        for i in range(min(40, len(b.doc["pages"]))):
            img = b.render_page(i, 240)
            data = QByteArray()
            buf = QBuffer(data)
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "JPEG", 75)
            out.append(bytes(data))
        self.cast.board_thumbs = out
        self._thumbs_version = getattr(self, "_thumbs_version", 0) + 1

    def _cast_tick(self) -> None:
        """Solange ein Handy zuschaut: Status und Live-Bild von Monitor 2 auffrischen."""
        import time

        self._board_thumbs()
        if not self.cast.running() or time.monotonic() - self.cast.preview_wanted > 5:
            return
        self._cast_snapshot()
        if not self._fast_wanted():
            self.cast.set_preview(self._preview_jpeg())

    def _fast_wanted(self) -> bool:
        import time

        return self.cast.running() and time.monotonic() - getattr(self.cast, "preview_fast", 0.0) < 2.5

    def _fast_preview(self) -> None:
        """Solange am iPad gezeichnet wird: Live-Bild schnell und in höherer Auflösung – sofort nach jeder Änderung
        (Strich, Zoom, Seite), sonst alle ~110 ms (Videos, Uhr …)."""
        import time

        if not self._fast_wanted():
            return
        now = time.monotonic()
        if self._preview_dirty or now - self._preview_at >= 0.11:
            self._preview_dirty, self._preview_at = False, now
            self.cast.set_preview(self._preview_jpeg(960, 540))

    def _mark_preview_dirty(self, *_a) -> None:
        self._preview_dirty = True

    def _preview_jpeg(self, w: int = 640, h: int = 360) -> bytes:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize

        from .output_window import grab_scaled

        out = self.output
        if out.isVisible() and out.width() > 0:
            img = grab_scaled(out, QSize(w, h))
            from .laser import draw_overlay

            draw_overlay(self, img)
        else:  # Erweitern: Monitor 2 ist ein normaler Bildschirm → den echten Bildschirm aufnehmen
            from .monitor_feed import fit_into, waiting_image

            live = self.monitor_feed.image()
            img = fit_into(live, w, h) if live is not None else waiting_image(w, h, self.monitor_feed.problem())
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "JPEG", 70 if w <= 640 else 75)
        buf.close()
        return bytes(data)

    def _cast_request(self, req: dict) -> None:
        """Anfrage vom Handy (kommt aus dem Webserver-Thread, läuft hier im Qt-Hauptthread)."""
        kind = req.get("kind")
        if kind in ("draw", "board", "laser"):
            self._preview_dirty = True  # Live-Bild fürs iPad gleich neu
            self.cast.applied = max(self.cast.applied, int(req.get("_no") or 0))  # ab jetzt in jedem Live-Bild
        if kind == "freigabe":  # Handy möchte steuern (ohne Code) → am PC fragen (Hauptfenster zeigt die Frage)
            self.access_requested.emit(req["id"], req.get("name", "Handy"), req.get("ip", ""))
            return
        if kind == "file":
            cfg = {"type": req["type"], "path": req["path"]}
            if req["type"] == "video":
                cfg.update(loop=False, volume=100)
            self.show_source(cfg)
            self.message.emit(("Foto" if req["type"] == "image" else "Video") + " vom Handy auf dem Bildschirm.")
        elif kind == "link":
            self.show_source({"type": "website", "url": req["url"]})
            self.message.emit(f"Link vom Handy: {req.get('original', req['url'])}")
        elif kind == "text":
            if req.get("live"):
                self.live_text(req["text"])
            else:
                self.show_text(req["text"])
        elif kind == "chat":
            self.overlay_window.chat_changed()
            self.guests_changed.emit()
            return
        elif kind == "fotowand":
            self.guests_changed.emit()
            return
        elif kind == "tafel_frage":  # Gast möchte aufs Whiteboard schreiben → am PC fragen
            self.board_guest_requested.emit(req["g"], req.get("name", "Gast"))
            return
        elif kind == "tafel_strich":  # fertiger Strich eines Gastes (Punkte 0…1 im sichtbaren Ausschnitt)
            if self.whiteboard_active():
                cfg = self.board.settings()
                base = cfg["marker_width" if req["tool"] == "marker" else "width"]
                width = base * {1: 0.6, 2: 1.0, 3: 2.2}.get(req.get("size", 2), 1.0)
                pts = []
                for x, y, pressure in req["pts"]:
                    w = self.board.to_world(x, y)
                    pts.append((w.x(), w.y(), pressure))
                self.board.add_stroke(req["tool"], req["color"], width, pts)
            return
        elif kind == "ask":  # „Frag AluPC“ vom Handy (getippt oder mit der Tastatur diktiert)
            self.ask(req.get("text", ""), source="handy")
        elif kind == "laser":
            self._phone_laser(req.get("x"), req.get("y"))
            return
        elif kind == "draw":
            self._phone_draw(req)
            return
        elif kind == "board":  # Whiteboard vom Handy: Ansicht (zoomen/verschieben) oder Aktion
            if req.get("op") == "view":
                b = self.board
                if req.get("zoom"):
                    anchor = b.to_world(float(req.get("ax", 0.5)), float(req.get("ay", 0.5)))
                    b.zoom_by(float(req["zoom"]), anchor)
                if req.get("dx") or req.get("dy"):
                    b.pan_screen(float(req.get("dx", 0)), float(req.get("dy", 0)))
            elif req.get("op") == "einstellung":  # Einstellungen vom iPad (wie das Fenster „Einstellungen“)
                self.board.set_settings(**{req["key"]: req["value"]})
                if req["key"] == "format":
                    self.board.fit()
            elif req.get("op") == "text":
                w = self.board.to_world(float(req["x"]), float(req["y"]))
                self.board.add_text(w.x(), w.y(), req.get("text", ""), req.get("color", "#111827"))
            else:
                self.board_action(str(req.get("op", "")), str(req.get("arg", "")))
            return
        elif kind == "mouse":
            self._phone_mouse(req)
            return
        elif kind == "spiel":  # Handy ist beigetreten / hat gestartet → Kachel aktualisieren
            self.changed.emit()
            return
        elif kind == "cmd":
            cmd = req.get("cmd", "")
            if cmd.startswith("taste:"):  # Präsentations-Fernbedienung: Taste ans aktive Programm
                from .platform import keys

                if not keys.send(cmd.split(":", 1)[1]):
                    self.message.emit("Handy-Tasten: unter Wayland nur mit „ydotool“ (Paket ydotool + Dienst ydotoold) oder X11")
            elif cmd.startswith("timer:"):  # Timer-Vorgabe vom Handy (Sekunden), gleich zeigen und starten
                from .timer import clock

                seconds = max(1, min(6 * 3600, int(cmd.split(":", 1)[1])))
                clock.set(seconds, "countdown", self.config["timer"].get("finished_text", ""))
                self.show_source(self.timer_source())  # nicht show_timer: setzt frische Timer auf die Setup-Dauer
                clock.start()
                self.sounds.play_event("timer_start")
                self.changed.emit()
            elif cmd == "timer_stopp":
                self.timer_action("reset")
            elif cmd == "zeichnung_zurueck":
                self.laser.undo()
            elif cmd == "kamera":
                self.start_camera()
            elif cmd == "airplay":
                self.start_airplay()
            elif cmd == "qr":
                self.start_cast()
            elif cmd.startswith("lautstaerke:"):
                self.set_media_volume(volume=max(0, min(100, int(cmd.split(":", 1)[1]))), muted=False)
            elif cmd.startswith("whiteboard:"):  # Whiteboard mit Hintergrund (vom Handy)
                self.show_whiteboard(cmd.split(":", 1)[1], draw=False)
            elif self.video_command(cmd):
                pass
            else:
                self.run_command(cmd)
        self._cast_snapshot()

    def _place_handy_window(self, *titles: str, apps: tuple[str, ...] = ()) -> None:
        """iPhone-Fenster (UxPlay) auf Monitor 2 legen – dauerhaft: auch wenn es erst
        viel später erscheint (UxPlay 1.68 öffnet sein Fenster erst, wenn sich das iPhone verbindet) oder nach
        einer neuen Verbindung neu aufgeht."""
        from PySide6.QtCore import QTimer

        self._stop_following()
        screen = self.output_screen()
        if screen is None or not self._handy_window:
            return
        g = screen.geometry()
        rect = (g.x(), g.y(), g.width(), g.height())
        titles = [t for t in titles if t]
        try:
            self._follow_token = self.windows.follow_windows(
                titles, screen.name(), rect, bool(self.config["handy"].get("airplay_borderless", True)))
        except Exception:  # noqa: BLE001
            self._follow_token = None
        if self._follow_token:
            return  # KDE: KWin erledigt das ab jetzt selbst
        placed: set[str] = set()

        def poll():
            if not self._handy_window:
                return
            try:
                windows = self.windows.list_windows()
            except Exception:  # noqa: BLE001
                return
            from .handy import is_iphone_window

            present = set()
            for w in windows:
                if is_iphone_window(w.title, w.app, titles):
                    present.add(w.id)
                    fits = getattr(self.windows, "window_fits", None)
                    if w.id in placed and fits is not None and self.config["handy"].get("airplay_borderless", True):
                        try:  # UxPlay hat sich auf Videogröße verkleinert (Bild kam/drehte sich) → wieder groß
                            if not fits(w.id, screen.name(), rect):
                                placed.discard(w.id)
                        except Exception:  # noqa: BLE001
                            pass
                    if w.id not in placed:
                        try:
                            place = getattr(self.windows, "present_window", None) \
                                if self.config["handy"].get("airplay_borderless", True) else None
                            if place:  # Windows: randlos, genau Monitor 2, im Vordergrund
                                place(w.id, screen.name(), rect)
                            else:
                                self.windows.move_window(w.id, screen.name(), rect, True)
                            placed.add(w.id)
                        except Exception:  # noqa: BLE001
                            pass
            placed.intersection_update(present)  # geschlossene Fenster vergessen → neue wieder platzieren
            if self._handy_window == "airplay-quelle":  # iPhone-Bild da → Warte-Bildschirm ausblenden
                self.output.set_suspended(bool(present))

        self._follow_timer = QTimer(self, interval=1000)  # Fenster schnell wieder groß ziehen
        self._follow_timer.timeout.connect(poll)
        self._follow_timer.start()
        poll()

    def _stop_following(self) -> None:
        timer = getattr(self, "_follow_timer", None)
        if timer is not None:
            timer.stop()
            self._follow_timer = None
        token = getattr(self, "_follow_token", None)
        if token:
            try:
                self.windows.stop_follow(token)
            except Exception:  # noqa: BLE001
                pass
        self._follow_token = None

    def _stop_handy_window(self) -> None:
        self._stop_following()
        self.output.set_yield(False)
        if self._handy_window == "airplay":
            self.airplay.release()
        self._handy_window = ""

    def _content_switched(self) -> None:
        """Neuer Inhalt auf Monitor 2 (Szenenwechsel) → alte Zeichnungen weg."""
        if self.laser.strokes:
            self.laser.clear_strokes()

    def _set_desktop(self, note: str) -> None:
        self._content_switched()
        if not note.startswith(HANDY_NOTES):
            self._stop_handy_window()  # Handy-Fenster schließen, wenn etwas anderes kommt
        self._unfreeze()
        self.screensaver.stop()
        self.mode = "desktop"
        self.content = None
        self.desktop_note = note
        self.output.set_content(None)
        self.changed.emit()

    def describe(self) -> str:
        if self.mode == "content" and self.content:
            if self.content.get("mirror"):
                return "Spiegeln (Monitor 1 wird gezeigt)"
            if self.content.get("type") == "countdown":
                from .timer import clock

                return f"Timer {clock.status()}"
            return describe_source(self.content)
        return self.desktop_note

    def default_camera(self) -> dict | None:
        """Kamera für die Kachel: gewählte Standard-Kamera, sonst die erste gefundene (None = keine da)."""
        from PySide6.QtMultimedia import QMediaDevices

        from .sources import camera_id

        devices = QMediaDevices.videoInputs()
        if not devices:
            return None
        wanted = self.config.get("default_camera", "")
        dev = next((d for d in devices if camera_id(d) == wanted), devices[0])
        return {"type": "camera", "device_id": camera_id(dev), "name": dev.description(),
                "fit": self.config.get("camera_fit", "cover") or "cover"}

    def start_camera(self, cfg: dict | None = None) -> None:
        cfg = cfg or self.default_camera()
        if cfg is None:
            self.message.emit("Keine Kamera gefunden.")
            return
        self.show_source(cfg)

    def start_content_setting(self) -> str:
        """Einstellung „Beim Start zeigen“ – ältere Konfigurationen: „letzten Inhalt“ an/aus."""
        value = self.config.get("start_content", "last") or "last"
        if value == "last" and not self.config["restore_last_content"]:
            return "none"
        return value

    def restore_last(self) -> None:
        what = self.start_content_setting()
        last = self.config["last_content"]
        if what == "last" and last:
            self.show_source(last, remember=False)
        elif what == "mirror":
            self.mirror()
        elif what == "camera":
            self.start_camera()
        elif what == "airplay":
            self.start_airplay()
        elif what == "cast":
            self.start_cast()
        elif what.startswith("scene:"):
            name = what.split(":", 1)[1]
            if self.config.get_scene(name):
                self.show_source({"type": "scene", "scene": name}, remember=False)
            else:
                self.message.emit(f"Start-Szene „{name}“ gibt es nicht mehr.")
        self._restore_drawings()  # Zeichnungen zum wiederhergestellten Inhalt wieder anzeigen

    # ------------------------------------------------------------ Standbild
    def toggle_freeze(self) -> None:
        if not self._guard():
            return
        if self.frozen:
            self._unfreeze()
            self.sounds.play_event("standbild_aus")
            self.changed.emit()
            return
        self.sounds.play_event("standbild_an")
        if self.mode == "content" and self.output.content is not None:
            self.frozen = True
            self.output.set_frozen(self._content_snapshot())
            self.changed.emit()
            return
        if self.screens_overlap():
            self.message.emit("Standbild: nicht bei System-Spiegeln")
            return
        # Normaler Desktop auf Monitor 2: Bildschirmfoto machen und darüberlegen
        self.frozen = True
        self.changed.emit()
        self.grabber.grab(self.output_screen())

    def _content_snapshot(self):
        """Bild für das Standbild. Echtes Bildschirmfoto, wo möglich – das erfasst auch Websites
        und Videos zuverlässig, die sich per grab() nicht immer abfotografieren lassen."""
        from .platform.linux_display import is_wayland

        screen = self.output_screen()
        if screen is not None and not is_wayland() and QGuiApplication.platformName() != "offscreen" \
                and self.output.isVisible():
            pixmap = screen.grabWindow(0)
            if not pixmap.isNull():
                return pixmap
        return self.output.snapshot()

    def current_web_view(self):
        """Die Website, die gerade auf Monitor 2 läuft (auch als Teil einer Szene) – oder None."""
        from .sources import WebsiteSource

        content = self.output.content
        if content is None or self.mode != "content":
            return None
        if isinstance(content, WebsiteSource):
            return content.view
        found = content.findChildren(WebsiteSource)
        return found[0].view if found else None

    def current_media(self) -> dict | None:
        """Bild/Video/Diashow, die gerade auf Monitor 2 läuft (sonst None)."""
        cfg = self.content if self.mode == "content" else None
        return cfg if cfg and cfg.get("type") in ("image", "video", "slideshow") else None

    def save_media(self, cfg: dict, title: str | None = None) -> None:
        from . import media_library

        item = media_library.save(self.config, cfg, title)
        self.message.emit(f"„{item['title']}“ in der Mediathek gespeichert.")
        self.changed.emit()

    def save_website(self, title: str, url: str) -> None:
        favs = [f for f in self.config["websites"].get("favorites", []) if f.get("url") != url]
        favs.insert(0, {"title": title, "url": url})
        self.config["websites"] = {**self.config["websites"], "favorites": favs}
        self.message.emit(f"„{title}“ unter „Website“ gespeichert.")
        self.changed.emit()

    def lock_computer(self) -> None:
        """Wie Win+L: Computer sperren (Windows) bzw. Bildschirmsperre (Linux)."""
        from .platform.window_tools import lock_computer

        try:
            lock_computer()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(str(exc))

    def _frozen_grab_done(self, image) -> None:
        if self.frozen:
            self.output.set_frozen(image)
            self.changed.emit()

    def _frozen_grab_failed(self, error: str) -> None:
        self.frozen = False
        self.changed.emit()
        self.message.emit(f"Standbild nicht möglich: {error}")

    def _unfreeze(self) -> None:
        if self.frozen:
            self.frozen = False
            self.output.set_frozen(None)

    # ------------------------------------------------------------ Sichtschutz
    def toggle_privacy(self) -> None:
        if not self._guard():
            return
        if self.screens_overlap() and not self.privacy:
            self.message.emit("Sichtschutz: nicht bei System-Spiegeln")
            return
        self.privacy = not self.privacy
        self.sounds.play_event("schwarz_an" if self.privacy else "schwarz_aus")
        p = self.config["privacy"]
        self.output.set_privacy(self.privacy, p.get("image", ""), p.get("text", ""))
        self.changed.emit()

    # ------------------------------------------------------------ Befehle (Tastenkürzel, Kommandozeile)
    def live_text(self, text: str) -> None:
        """Live-Modus: jeder getippte Buchstabe sofort auf Monitor 2 – ohne Überblendung, ohne Ton.
        Läuft schon ein Text, wird nur dessen Inhalt ausgetauscht (flackert nicht)."""
        from .sources import TextSource

        text = text[:2000]
        cur = self.output.content
        if self.mode == "content" and isinstance(cur, TextSource) and (self.content or {}).get("type") == "text" \
                and not self.frozen and not self.privacy:
            cur.set_text(text)
            self.content = {**self.content, "text": text}
            self.config.data["last_content"] = self.content  # ohne ständiges Speichern auf die Platte
            self._live_changed()
            return
        self.show_source({"type": "text", "text": text, "live": True}, remember=True, sound=False)

    def _live_changed(self) -> None:
        # Vorschau & Handy nicht bei jedem Buchstaben komplett neu aufbauen – kurz gebündelt melden
        from PySide6.QtCore import QTimer

        if getattr(self, "_live_timer", None) is None:
            self._live_timer = QTimer(self, singleShot=True, interval=300)
            self._live_timer.timeout.connect(self.changed.emit)
        self._live_timer.start()

    def show_file(self, path: str) -> bool:
        """Datei aus dem Dateimanager (Rechtsklick) oder per `alupc --zeigen` auf Monitor 2."""
        from .platform.system_integration import file_source

        cfg = file_source(path)
        if cfg is None:
            self.message.emit(f"Geht nicht auf dem Bildschirm: {Path(path).name or path}")
            return False
        self.show_source(cfg)
        return True

    def show_text(self, text: str) -> None:
        """Text groß auf Monitor 2 (vom PC oder Handy) – die letzten Texte werden gemerkt."""
        text = text.strip()
        if not text:
            return
        self.show_source({"type": "text", "text": text}, remember=True)
        recent = [t for t in self.config.get("recent_texts", []) if t != text]
        self.config["recent_texts"] = [text] + recent[:7]

    def step_sources(self) -> list:
        """Alle gerade sichtbaren Seiten mit mehreren Punkten (Ablauf, Tabelle, Quiz, Siegerehrung …)."""
        from .screens import DesignSource, step_range

        content = self.output.content
        if self.mode != "content" or content is None:
            return []
        found = [content] if isinstance(content, DesignSource) else content.findChildren(DesignSource)
        return [s for s in found if step_range(s.cfg) is not None]

    agenda_sources = step_sources  # alter Name

    def step_label(self) -> str:
        from .screens import step_label

        sources = self.step_sources()
        return step_label(sources[0].cfg) if sources else ""

    def step_page(self, delta: int) -> None:
        """Nächsten/vorherigen Punkt (Kachel, Tastenkürzel, Handy, Seitenleiste) – mit Animation."""
        sources = self.step_sources()
        if not sources:
            self.message.emit("Auf dem Bildschirm ist gerade nichts zum Weiterschalten.")
            return
        for src in sources:
            src.step(delta)
        if self.content and self.content.get("type") == "design":  # merken (auch für den nächsten Start)
            self.content = {**self.content, "current": sources[0].cfg["current"]} \
                if "current" in sources[0].cfg else self.content
            self.config["last_content"] = self.content
        self.changed.emit()

    step_agenda = step_page  # alter Name

    # ------------------------------------------------------------ Tastenbox (9 Tasten + Lautstärkeregler)
    VOLUME_COMMANDS = ("pc_lauter", "pc_leiser", "pc_stumm_an", "pc_stumm_aus")

    def apply_buttonbox(self) -> None:
        """Einstellungen übernehmen: Box suchen (oder nicht), Lautstärke-Sperre, LED."""
        cfg = self.config["tasten"]
        if cfg.get("on", True) and not os.environ.get("ALUPC_NO_TASTENBOX"):  # Tests: keine echte Suche
            if self.buttonbox._thread is None or self.buttonbox.wanted_port != cfg.get("port", ""):
                self.buttonbox.start(cfg.get("port", ""))
        else:
            self.buttonbox.stop()
        self._box_state()

    def volume_locked(self) -> bool:
        """Lautstärke nur am Regler: Box verbunden, Regler hat gemeldet, Einstellung an."""
        return bool(self.config["tasten"].get("nur_regler", True) and self.buttonbox.connected
                    and self.knob_level() is not None and self.knob_target() == "pc")

    # Wofür der Regler ist: PC-Ton, Ton auf Bildschirm 2, Mikrofon oder ein Programm („app:Spotify“)
    KNOB_TARGETS = {"pc": "PC-Lautstärke", "medien": "Ton auf Bildschirm 2", "mikro": "Mikrofon",
                    "wert": "Zahl 0–999 (für Wenn → Dann)"}

    def knob_target(self) -> str:
        return self.config["tasten"].get("regler", "pc") or "pc"

    def knob_target_label(self, target: str | None = None) -> str:
        target = target or self.knob_target()
        return self.KNOB_TARGETS.get(target) or (f"Programm: {target[4:]}" if target.startswith("app:") else target)

    def set_knob_target(self, target: str) -> None:
        """Neues Ziel für den Regler. Der Regler hat eine Skala – deshalb startet das neue Ziel bei 0, und der Regler
        greift erst, wenn er auch auf 0 gedreht wurde (sonst spränge der Ton auf die alte Reglerstellung)."""
        if target == self.knob_target():
            return
        self.config["tasten"] = {**self.config["tasten"], "regler": target}
        self._value_hits = None  # erst ab dem nächsten Drehen auslösen
        if target == "wert" or not self.config["tasten"].get("abgleich", True):  # ohne Null-Abgleich: Ziel folgt sofort dem Regler
            self.knob_wait = False
            self._box_state()
            if self.knob_level() is not None:
                self._knob_apply(target, self.knob_level())
            self.changed.emit()
            return
        self.knob_wait = True
        self._knob_apply(target, 0)
        self._box_state()
        if self.buttonbox.connected and (self.knob_level() or 0) <= 2:
            self.knob_wait = False  # steht schon auf 0
        else:
            self.message.emit(f"Regler: {self.knob_target_label(target)} – jetzt auf 0 drehen.")
        self.changed.emit()

    def _knob_apply(self, target: str, value: int) -> None:
        from .ui.util import run_async

        if target == "pc":
            if self.config["tasten"].get("nur_regler", True) and self.buttonbox.connected:
                self.volume_keeper.set_target(value)
            else:  # Regler dreht die Lautstärke, andere dürfen auch
                from . import pc_control

                run_async(lambda: pc_control.set_volume(value), lambda _r: None, lambda _e: None)
        elif target == "medien":
            self.set_media_volume(volume=value)
        elif target == "mikro":
            from . import audio

            run_async(lambda: audio.set_level("in", value), lambda _r: None, lambda _e: None)
        elif target.startswith("app:"):
            from . import mixer

            name = target[4:]

            def work():
                for s in mixer.streams("out"):
                    if s.get("app") == name:
                        mixer.set_stream("out", s["id"], volume=value)

            run_async(work, lambda _r: None, lambda _e: None)

    def box_keys(self) -> dict[str, str]:
        from .tastenbox import DEFAULT_KEYS

        return {**DEFAULT_KEYS, **(self.config["tasten"].get("keys") or {})}

    def _box_key(self, n: int) -> None:
        if self.box_code is not None:  # Code-Eingabe: Tasten sind Ziffern
            return self._code_digit(n)
        action = self.box_keys().get(str(n), "")
        self._box_blink()
        self._box_click()
        held = self._seq_push(n, action)
        buf = self._seq_buffer() if held else ""
        self.box_input.emit({"art": "taste", "n": n, "aktion": action, "lang": False, "folge": buf,
                             "weiter": self._seq_next(buf), "warten_ms": self._seq_timer.remainingTime()
                             if buf and self._seq_timer.isActive() else 0})
        if held:
            return
        if action:
            self.run_command(action)

    # ------------------------------------------------------------ Tastenfolgen
    def _seq_push(self, n: int, action: str) -> bool:
        """Taste in die laufende Folge aufnehmen. True = Aktion wird zurückgehalten (oder fällt weg, weil die Folge
        komplett ist); False = gehört zu keiner Folge, sofort ausführen."""
        from .automations import rules_for_sequence, sequence_rules

        seqs = [r["wenn"]["folge"] for r in sequence_rules(self.box_rules())]
        if not seqs:
            self._seq_flush()
            return False
        self._seq_keys.append((n, action))
        if self.config["tasten"].get("folge_warten", True):
            # Jede Taste wartet kurz – sonst sähe man an den sofort reagierenden Tasten, womit eine Folge anfängt
            keys = self._seq_buffer()
            done = next((f for f in sorted(seqs, key=len, reverse=True) if keys.endswith(f)
                         and not any(o != f and o.startswith(f) for o in seqs)), None)
            if done:  # eine Folge ist eindeutig fertig: Tasten davor normal, dann die Regel – ohne zu warten
                self._seq_timer.stop()
                before, self._seq_keys = self._seq_keys[:-len(done)], []
                self._seq_run_plain(before)
                self._seq_fire(done, rules_for_sequence)
                return True
            self._seq_timer.start(self.seq_gap_ms())
            return True
        # vorne Tasten abgeben (= normal ausführen), bis der Rest der Anfang einer Folge ist
        while self._seq_keys and not any(s.startswith(self._seq_buffer()) for s in seqs):
            _k, act = self._seq_keys.pop(0)
            if act:
                self.run_command(act)
        if not self._seq_keys:
            self._seq_timer.stop()
            return True  # die Aktion dieser Taste lief eben schon mit
        keys = self._seq_buffer()
        if keys in seqs and not any(s != keys and s.startswith(keys) for s in seqs):
            self._seq_timer.stop()  # eindeutig fertig: sofort auslösen
            self._seq_keys = []
            self._seq_fire(keys, rules_for_sequence)
            return True
        self._seq_timer.start(self.seq_gap_ms())
        return True

    def seq_gap_ms(self) -> int:
        """Wie lange auf die nächste Taste einer Folge gewartet wird (Einstellung, 300–2000 ms)."""
        from .automations import SEQ_GAP_MS

        return max(300, min(2000, int(self.config["tasten"].get("folge_ms", SEQ_GAP_MS) or SEQ_GAP_MS)))

    def _seq_run_plain(self, keys: list[tuple[int, str]]) -> None:
        """Tasten, die zu keiner Folge gehören, normal ausführen (und der Anzeige melden)."""
        if keys:
            self.box_input.emit({"art": "freigabe", "tasten": "".join(str(k) for k, _a in keys)})
        for _k, act in keys:
            if act:
                self.run_command(act)

    def _seq_next(self, buf: str) -> str:
        """Tasten, mit denen eine angefangene Folge weitergehen kann (für die Anzeige). Wartet jede Taste (Folgen
        geheim halten), verrät die Anzeige das nicht."""
        if not buf or self.config["tasten"].get("folge_warten", True):
            return ""
        from .automations import sequence_rules

        return "".join(sorted({f[len(buf)] for f in (r["wenn"]["folge"] for r in sequence_rules(self.box_rules()))
                               if f.startswith(buf) and len(f) > len(buf)}))

    def _seq_buffer(self) -> str:
        return "".join(str(k) for k, _a in self._seq_keys)

    def _seq_timeout(self) -> None:
        """Pause nach der letzten Taste: von links her Folgen erkennen (die längste zuerst), alle anderen Tasten
        normal ausführen – in der Reihenfolge, in der sie gedrückt wurden."""
        from .automations import rules_for_sequence, sequence_rules

        seqs = sorted({r["wenn"]["folge"] for r in sequence_rules(self.box_rules())}, key=len, reverse=True)
        keys, self._seq_keys = self._seq_keys, []
        buf = "".join(str(k) for k, _a in keys)
        plain: list[tuple[int, str]] = []
        i = 0
        while i < len(buf):
            match = next((f for f in seqs if buf.startswith(f, i)
                          and rules_for_sequence(self.box_rules(), f, self._seq_value())), None)
            if match:
                self._seq_run_plain(plain)
                plain = []
                self._seq_fire(match, rules_for_sequence)
                i += len(match)
            else:
                plain.append(keys[i])
                i += 1
        self._seq_run_plain(plain)

    def _seq_value(self) -> int | None:
        return self.box_number() if self.knob_target() == "wert" else None

    def _seq_fire(self, keys: str, find) -> None:
        hits = find(self.box_rules(), keys, self._seq_value())
        if not hits:  # Folge stimmt, aber z. B. der Regler nicht
            self.message.emit(f"Tastenfolge {'-'.join(keys)}: Regler passt nicht.")
        for rule in hits:
            self.run_rule(rule)

    def _seq_flush(self) -> None:
        """Zurückgehaltene Tasten doch normal ausführen (Folge abgebrochen)."""
        self._seq_timer.stop()
        keys, self._seq_keys = self._seq_keys, []
        self._seq_run_plain(keys)

    # ------------------------------------------------------------ Buzzer
    def buzzer_ready(self) -> tuple[bool, str]:
        from .buzzer import MIN_VERSION

        if not self.buttonbox.connected:
            return False, "Keine Tastenbox verbunden."
        if self.buttonbox.version < MIN_VERSION:
            return False, (f"Buzzer braucht das Tastenbox-Programm ab Version {MIN_VERSION} "
                           f"(auf der Box: {self.buttonbox.version or '?'}) – neu hochladen.")
        return True, ""

    def play_buzzer(self, notes: list[tuple[int, int]], name: str) -> bool:
        ok, why = self.buzzer_ready()
        if not ok:
            self.message.emit(why)
            return False
        self.buzzer.play(notes, name)
        return True

    def play_buzzer_song(self, name: str) -> bool:
        from .buzzer import SONGS, song_notes

        if name not in SONGS:
            self.message.emit(f"Melodie „{name}“ gibt es nicht.")
            return False
        return self.play_buzzer(song_notes(name), SONGS[name][0])

    def play_buzzer_midi(self, path: str) -> bool:
        from pathlib import Path

        from .buzzer import midi_notes

        try:
            notes = midi_notes(path)
        except (OSError, ValueError) as exc:
            self.message.emit(f"MIDI „{Path(path).name}“ geht nicht: {exc}")
            return False
        return self.play_buzzer(notes, Path(path).name)

    def _box_click(self) -> None:
        """Kurzer Klick auf dem Buzzer als Rückmeldung (Einstellung) – nicht mitten in eine Melodie."""
        from .buzzer import CLICK

        if self.config["tasten"].get("buzzer_klick") and self.buzzer_ready()[0] and not self.buzzer.playing:
            self.buttonbox.send(f"TON {CLICK[0]} {CLICK[1]}")

    def _box_long_key(self, n: int) -> None:
        """Lange gedrückt: eigener Befehl – ohne einen solchen wie ein kurzer Druck. Die Code-Taste startet die
        Code-Eingabe (nur, wenn es eine Regel mit Code gibt)."""
        if self.box_code is not None:
            return self._code_digit(n)
        self._seq_flush()  # langer Druck beendet eine angefangene Tastenfolge
        if n == self.code_key() and any(r["wenn"]["code"] for r in self.box_rules() if r["an"]):
            return self._code_start()
        action = (self.config["tasten"].get("lang") or {}).get(str(n), "")
        if not action:
            return self._box_key(n)
        self._box_blink()
        self._box_click()
        self.box_input.emit({"art": "taste", "n": n, "aktion": action, "lang": True, "folge": ""})
        self.run_command(action)

    def _box_blink(self) -> None:
        """LED blitzt kurz als Rückmeldung für einen Tastendruck (Einstellung)."""
        if not (self.config["tasten"].get("led_blink", False) and self.buttonbox.connected):
            return
        from PySide6.QtCore import QTimer

        before = self._led_level or 0
        self.buttonbox.set_led(0 if before else self.box_led_brightness())
        self._led_level = None
        QTimer.singleShot(140, self.update_box_led)

    def box_led_brightness(self) -> int:
        return max(1, min(100, int(self.config["tasten"].get("led_hell", 100) or 100)))

    def knob_value(self, raw: int | None) -> int | None:
        """Reglerstellung → Wert fürs Ziel: auf Wunsch umgekehrt und mit Höchstwert (z. B. nie lauter als 80 %)."""
        if raw is None:
            return None
        cfg = self.config["tasten"]
        v = 100 - raw if cfg.get("umkehren") else raw
        top = max(1, min(100, int(cfg.get("max", 100) or 100)))
        return round(v * top / 100)

    # Skala des Reglers: „prozent“ = ganzer Drehweg 0–100 %; mit Zählwerk (Programm 5) „zahl_10“ = 000–999 ÷ 10,
    # „zahl_direkt“ = 000–100 ist gleich die Prozentzahl (darüber 100 %)
    KNOB_SCALES = {"prozent": "Ganzer Drehweg = 0–100 %", "zahl_10": "Zählwerk 000–999 ÷ 10",
                   "zahl_direkt": "Zählwerk 000–100 = Prozent"}
    # Voreinstellungen: (Name, Ziel, Skala)
    KNOB_PRESETS = [
        ("Lautstärke – ganzer Drehweg 0–100 %", "pc", "prozent"),
        ("Lautstärke – Zählwerk 000–100", "pc", "zahl_direkt"),
        ("Lautstärke – Zählwerk 000–999 ÷ 10 (fein)", "pc", "zahl_10"),
        ("Ton Bildschirm 2 – Zählwerk ÷ 10", "medien", "zahl_10"),
        ("Mikrofon – Zählwerk ÷ 10", "mikro", "zahl_10"),
        ("Zahl 0–999 für Wenn → Dann", "wert", "prozent"),
    ]

    def knob_scale(self) -> str:
        scale = self.config["tasten"].get("skala", "prozent")
        return scale if scale in self.KNOB_SCALES else "prozent"

    def knob_level(self) -> int | None:
        """Reglerstellung fürs Ziel (0–100) nach Skala, Richtung und Höchstwert; None ohne Meldung der Box."""
        scale = self.knob_scale()
        if scale == "prozent":
            return self.knob_value(self.buttonbox.knob)
        n = self.box_number()  # schon umgekehrt, falls gewünscht
        if n is None:
            return None
        pct = min(100, n) if scale == "zahl_direkt" else round(n / 9.99)
        top = max(1, min(100, int(self.config["tasten"].get("max", 100) or 100)))
        return round(pct * top / 100)

    def apply_knob_preset(self, index: int) -> None:
        _name, target, scale = self.KNOB_PRESETS[index]
        self.config["tasten"] = {**self.config["tasten"], "skala": scale}
        if target == self.knob_target():
            self._box_state()
            if self.knob_level() is not None and target != "wert":
                self._knob_apply(target, self.knob_level())
            self.changed.emit()
        else:
            self.set_knob_target(target)

    def _box_volume(self, value: int) -> None:
        if self.knob_scale() != "prozent":  # Skala mit Zählwerk: zählt WERT (siehe _box_fine)
            return
        self._knob_moved(self.knob_value(value))

    def _knob_moved(self, value: int) -> None:
        if self.knob_wait:  # neues Ziel: erst auf 0 drehen, dann greift der Regler
            if value > 2:
                self._changed_soon()
                return
            self.knob_wait = False
        self._knob_apply(self.knob_target(), value)
        self._changed_soon()
        self.box_input.emit({"art": "regler", "wert": value, "ziel": self.knob_target_label()})

    def _changed_soon(self) -> None:
        if not self._knob_changed.isActive():
            self._knob_changed.start()

    # ------------------------------------------------------------ Wenn → Dann
    def box_rules(self) -> list[dict]:
        from .automations import clean_rule

        return [clean_rule(r) for r in self.config["tasten"].get("regeln") or []]

    def code_key(self) -> int:
        return max(1, min(9, int(self.config["tasten"].get("code_taste", 9) or 9)))

    def _code_start(self) -> None:
        from .automations import CODE_TIMEOUT_MS

        self.box_code = ""
        self._code_timer.start(CODE_TIMEOUT_MS)
        if self.buttonbox.connected:
            self.buttonbox.set_effect("blinken")
            self._led_extra = None  # nach der Eingabe wieder wie eingestellt
        self.message.emit("Code eingeben …")
        self.box_input.emit({"art": "code", "code": "", "fertig": False})
        self.changed.emit()

    def _code_digit(self, n: int) -> None:
        from .automations import CODE_PAUSE_MS

        self.box_code = (self.box_code or "") + str(n)
        self._code_timer.start(CODE_PAUSE_MS)
        self._box_blink()
        self.message.emit("Code: " + "-".join(self.box_code))
        self.box_input.emit({"art": "code", "code": self.box_code, "fertig": False})
        self.changed.emit()

    def _code_done(self) -> None:
        from .automations import rules_for_code

        code, self.box_code = self.box_code or "", None
        self.update_box_led()
        self.changed.emit()
        self.box_input.emit({"art": "code", "code": code, "fertig": True})
        if not code:
            self.message.emit("Code-Eingabe abgebrochen.")
            return
        value = self.box_number() if self.knob_target() == "wert" else None
        hits = rules_for_code(self.box_rules(), code, value)
        if not hits:
            self.message.emit(f"Code {'-'.join(code)}: keine passende Regel.")
        for rule in hits:
            self.run_rule(rule)

    def box_number(self) -> int | None:
        """Reglerzahl 0–999 (auf Wunsch umgekehrt), None ohne Box."""
        raw = self.buttonbox.fine_value
        if raw is None or not self.buttonbox.connected:
            return None
        return 999 - raw if self.config["tasten"].get("umkehren") else raw

    def _box_fine(self, value: int) -> None:
        from .automations import VALUE_SETTLE_MS

        if self.knob_target() == "wert":
            self._value_timer.start(VALUE_SETTLE_MS)
            self.box_input.emit({"art": "zahl", "wert": self.box_number()})
            self._changed_soon()
        elif self.knob_scale() != "prozent" and self.knob_level() is not None:
            level = self.knob_level()
            if level != self._fine_level or self.knob_wait:  # ÷ 10: 10 Zahlen = 1 %
                self._fine_level = level
                self._knob_moved(level)

    def _value_settled(self) -> None:
        """Regler steht 1 s ruhig: Regeln ohne Code mit dieser Zahl auslösen – jede nur einmal, bis der Regler den
        Bereich verlassen hat. Der erste Wert nach dem Verbinden löst nichts aus (Box eingesteckt ≠ gedreht)."""
        from .automations import rules_for_value

        value = self.box_number()
        if value is None or self.knob_target() != "wert":
            return
        hits = rules_for_value(self.box_rules(), value)
        before, self._value_hits = self._value_hits, {r["name"] for r in hits}
        if before is None:
            return
        for rule in hits:
            if rule["name"] not in before:
                self.run_rule(rule)

    def run_rule(self, rule: dict) -> None:
        """Das „Dann“ einer Regel ausführen – alle Aktionen nacheinander."""
        from .automations import actions_of

        self.box_input.emit({"art": "regel", "name": rule["name"]})
        if self._rule_depth > 3:  # Regel ruft Regel ruft Regel …
            self.message.emit("Wenn → Dann: zu viele Regeln hintereinander.")
            return
        self._rule_depth += 1
        try:
            for action in actions_of(rule):
                self._run_rule_action(rule["name"], action["art"], action["ziel"])
        finally:
            self._rule_depth -= 1

    def _run_rule_action(self, name: str, art: str, ziel: str) -> None:
        from .automations import run_external

        if art in ("programm", "webseite", "befehl"):
            ok, text = run_external(art, ziel)
            self.message.emit(f"{name}: {text}")
        elif art == "webseite_m2":
            url = ziel if "://" in ziel else "https://" + ziel
            self.show_source({"type": "website", "url": url})
        elif art == "datei":
            self.show_file(ziel)
        elif art == "klang":
            self.sounds.play(ziel)
        elif art == "buzzer":
            self.play_buzzer_song(ziel)
        elif art == "buzzer_midi":
            self.play_buzzer_midi(ziel)
        else:
            self.run_command(ziel)

    def _box_mute(self) -> None:
        if self.volume_locked():
            self.volume_keeper.toggle_mute()
            self.changed.emit()

    def _box_state(self) -> None:
        if not self.buttonbox.connected:
            self._value_hits = None
        if self.volume_locked():
            self.volume_keeper.set_target(0 if self.knob_wait else self.knob_level())
        else:
            self.volume_keeper.set_target(None)
        self._led_level = None
        self._led_extra = None
        self.update_box_led()
        self.changed.emit()

    def _volume_overridden(self) -> None:
        import time as _time

        if _time.monotonic() - self._override_note > 15:  # nicht bei jedem Versuch melden
            self._override_note = _time.monotonic()
            self.message.emit("Lautstärke geht nur am Regler – zurückgestellt.")

    def update_box_led(self) -> None:
        """LED der Box: aus, an, oder „live“ (leuchtet, solange etwas auf Bildschirm 2 läuft)."""
        if not self.buttonbox.connected:
            return
        cfg = self.config["tasten"]
        mode = cfg.get("led", "live")
        live = self.mode == "content"
        scheme = cfg.get("led_schema", "einfarbig")
        level = self.box_led_brightness() if mode == "an" or (mode == "live" and live) else 0
        if scheme == "zustand" and mode != "aus":  # Zustand braucht Licht, auch wenn nichts läuft
            level = self.box_led_brightness()
        # Effekt und Farbe (ab Box-Programm 3): „bei Live“ blinken/atmen lassen, sonst ruhig
        effect = cfg.get("led_effekt", "ruhig") if mode == "an" or live else "ruhig"
        if scheme == "regenbogen":
            effect = "regenbogen"
        extra = (effect, cfg.get("led_farbe", "#00ffa0"), scheme, tuple(self.box_pixels()))
        if extra != self._led_extra:
            self.buttonbox.set_effect(extra[0])
            self.buttonbox.set_color(extra[1])
            for i, color in enumerate(extra[3]):
                self.buttonbox.set_pixel(i, color)
            self._led_extra = extra
        if level != self._led_level and self.buttonbox.set_led(level):
            self._led_level = level

    def box_pixels(self) -> list[str]:
        """Farben der 4 Ecken für das Farbschema (die Lichtleiste bekommt die Grundfarbe). Leer = alle gleich."""
        from .tastenbox import CORNER_COLORS

        cfg = self.config["tasten"]
        scheme = cfg.get("led_schema", "einfarbig")
        if scheme == "ecken":
            corners = list(cfg.get("led_ecken") or CORNER_COLORS)
            return (corners + CORNER_COLORS)[:4]
        if scheme == "zustand":  # Rot = läuft auf Bildschirm 2, Gelb = Schwarz/Standbild, sonst die Grundfarbe
            if self.privacy or self.frozen:
                return ["#ffb000"] * 4
            return ["#ff2020"] * 4 if self.mode == "content" else [cfg.get("led_farbe", "#00ffa0")] * 4
        return []

    def box_led_command(self, command: str) -> bool:
        """Befehle für die LEDs der Tastenbox (auch vom Handy): box_led:an|aus|live, box_effekt:…, box_farbe:#rrggbb,
        box_hell:5–100, box_schema:einfarbig|ecken|zustand|regenbogen."""
        from .tastenbox import LED_EFFECTS, LED_SCHEMES

        name, _, arg = command.partition(":")
        cfg = dict(self.config["tasten"])
        if name == "box_led" and arg in ("an", "aus", "live"):
            cfg["led"] = arg
        elif name == "box_effekt" and arg in LED_EFFECTS:
            cfg["led_effekt"] = arg
        elif name == "box_farbe" and len(arg.lstrip("#")) == 6:
            cfg["led_farbe"] = "#" + arg.lstrip("#").lower()
        elif name == "box_hell" and arg.isdigit():
            cfg["led_hell"] = max(5, min(100, int(arg)))
        elif name == "box_schema" and arg in LED_SCHEMES:
            cfg["led_schema"] = arg
        else:
            return False
        self.config["tasten"] = cfg
        self._led_level = None
        self._led_extra = None
        self.update_box_led()
        self.changed.emit()
        return True

    def toggle_box_led(self) -> None:
        cfg = dict(self.config["tasten"])
        cfg["led"] = "aus" if self._led_level else "an"
        self.config["tasten"] = cfg
        self.update_box_led()

    def run_command(self, command: str) -> None:
        command = command.strip()
        if self.volume_locked() and (command in self.VOLUME_COMMANDS or command.startswith(
                ("pc_lautstaerke:", "ton_laut:", "ton_stumm:"))):
            self.message.emit("Lautstärke geht nur am Regler.")
            return
        if command == "pc_sperren":  # wie Win+L
            self.lock_computer()
            return
        if command.startswith("pc_") and command not in ("pc_herunterfahren", "pc_neustart", "pc_schlafen",
                                                         "pc_abmelden"):
            from . import pc_control

            self.message.emit(pc_control.run(command))  # Lautstärke, Programme, Fenster … (Ein/Aus nur mit Rückfrage)
            return
        if command.startswith("regel:"):
            rule = next((r for r in self.box_rules() if r["name"] == command[6:]), None)
            if rule is None:
                self.message.emit(f"Regel „{command[6:]}“ gibt es nicht.")
            else:
                self.run_rule(rule)
            return
        if command.startswith("buzzer:"):
            self.play_buzzer_song(command[7:])
            return
        if command.startswith("buzzer_midi:"):
            self.play_buzzer_midi(command[12:])
            return
        if command == "buzzer_stopp":
            self.buzzer.stop()
            return
        if command.startswith("klang:"):
            self.sounds.play(command[6:])
            return
        if command.startswith("box_"):
            if not self.box_led_command(command):
                self.message.emit("Unbekannter LED-Befehl.")
            return
        if command.startswith("szene:"):
            name = command[6:]
            if self.config.get_scene(name):
                self.show_source({"type": "scene", "scene": name})
            else:
                self.message.emit(f"Szene „{name}“ gibt es nicht.")
            return
        if command.startswith("datei:"):
            self.show_file(command[6:])
            return
        if command.startswith("link_unbekannt:"):
            self.message.emit(f"Unbekannter AluPC-Link: {command[15:]}")
            return
        if command.startswith("kachel:"):
            self.run_tile(command[7:])
            return
        if command.startswith(("ton_", "mic_")):  # Lautsprecher/Mikrofon des PCs (Handy, Sprache)
            from . import audio

            self.message.emit(audio.run(command))
            return
        if command.startswith("spiel:"):
            self.game_action(command)
            return
        if self._run_switch(command):
            return
        actions = {
            "standbild": self.toggle_freeze,
            "schwarz": self.toggle_privacy,
            "sichtschutz": self.toggle_privacy,
            "studio": lambda: self.set_studio(not self.studio),
            "studio_uebergang": lambda: self.take() if self.studio else self.message.emit("Studio-Modus ist aus."),
            "tasten_led": self.toggle_box_led,
            "spiegeln": self.mirror,
            "erweitern": self.extend,
            "bild-in-bild": self.toggle_pip,
            "bild_in_bild": self.toggle_pip,
            "bildschirmschoner": self.toggle_screensaver,
            "naechste_szene": lambda: self.step_scene(1),
            "naechste-szene": lambda: self.step_scene(1),
            "vorherige_szene": lambda: self.step_scene(-1),
            "vorherige-szene": lambda: self.step_scene(-1),
            "sperren": self.lock_computer,
            "timer_zeigen": self.show_timer,
            "timer_start_pause": lambda: self.timer_action("toggle"),
            "timer_neustart": lambda: self.timer_action("restart"),
            "timer_plus": lambda: self.timer_action("plus"),
            "timer_minus": lambda: self.timer_action("minus"),
            # Laserpointer gibt es nur noch im Fenster „Zeigen & Zeichnen“
            "laserpointer": self.presenter_requested.emit,
            "laser": self.presenter_requested.emit,
            "zeichnen": self.presenter_requested.emit,
            "zeichnungen_loeschen": lambda: self.laser.clear_strokes(),
            "ablauf_weiter": lambda: self.step_agenda(1),
            "ablauf_zurueck": lambda: self.step_agenda(-1),
            "kamera_zoom_plus": lambda: self.camera_zoom(1.25),
            "kamera_zoom_minus": lambda: self.camera_zoom(0.8),
            "kamera_zoom_aus": lambda: self.camera_zoom(None),
            "rgb_farbe": lambda: self.rgb.set_mode("farbe"),
            "rgb_monitor2": lambda: self.rgb.set_mode("monitor2"),
            "rgb_aus": lambda: self.rgb.set_mode("aus"),
            "musik_zeigen": self.show_now_playing,
            "musik_pause": lambda: self.media_control("play_pause"),
            "musik_weiter": lambda: self.media_control("next"),
            "musik_zurueck": lambda: self.media_control("previous"),
            "overlays": lambda: self.set_overlays(None),
            "overlays_an": lambda: self.set_overlays(True),
            "overlays_aus": lambda: self.set_overlays(False),
            "whiteboard": self.show_whiteboard,
            **{f"tafel_{op}": (lambda o=op: self.board_action(o)) for op in (
                "zoom_in", "zoom_out", "fit", "seite_neu", "seite_vor", "seite_zurueck", "rueckgaengig",
                "wiederholen", "leeren")},
            "wetter": self.show_weather,
            "system": lambda: self.show_source({"type": "system"}),
            # gab es bisher nur für das Handy – per Sprache kam „Unbekannter Befehl“
            "kamera": self.start_camera,
            "airplay": self.start_airplay,
            "qr": self.start_cast,
            "gluecksrad": self.show_wheel,
            "gluecksrad_drehen": self.spin_wheel,
            "umfrage_zeigen": lambda: self.show_source({"type": "umfrage"}, remember=False),
            "umfrage_ende": lambda: self.poll_action("ende"),
            "spiele": self.start_games,
            "spiel_start": lambda: self.game_action("start"),
            "schiffe": self.start_duel,
            "kahoot": self.start_kahoot,
            "tafel_gaeste_aus": lambda: self.set_board_guests("aus"),
            "tafel_gaeste_alle": lambda: self.set_board_guests("alle"),
            "tafel_gaeste_frage": lambda: self.set_board_guests("frage"),
            "schiffe_aus": self.stop_duel,
            "spiel_bestenliste": lambda: self.game_action("bestenliste"),
            "video_weiterschauen": lambda: self.video_resume_choice("weiter"),
            "video_von_vorn": lambda: self.video_resume_choice("neu"),
            "spiel_weiter": lambda: self.game_action("weiter"),
            "spiel_ende": lambda: self.game_action("ende"),
            "spiel_lobby": lambda: self.game_action("lobby"),
            "spiel_teams": lambda: self.game_action("teams"),
            "spiel_toene": lambda: self.game_action("toene"),
            "chat": lambda: self.stop_chat() if self.cast.chat is not None else self.start_chat(),
            "chat_an": self.start_chat,
            "chat_aus": self.stop_chat,
            "chat_overlay": lambda: self.set_chat_overlay(None),
            "chat_popup": lambda: (self.update_chat_settings(
                mode="liste" if self.config["chat"].get("mode") == "popup" else "popup"),
                self.set_chat_overlay(True)),
            "chat_wand": lambda: (self.start_chat(overlay=False) if self.cast.chat is None else None,
                                  self.show_source({"type": "chatwand"}, remember=False)),
            "fotowand": lambda: self.start_wall(show=True),
            "fotowand_aus": self.stop_wall,
            "alupc_oeffnen": self.window_requested.emit,
            "vcam": self._toggle_vcam,
            "vcam_an": lambda: self._toggle_vcam(True),
            "vcam_aus": lambda: self._toggle_vcam(False),
            "senden": self.sender_requested.emit,  # Fenster „An Fernseher senden“
            "senden_aus": self.sender.stop,
            "miracast": self.start_miracast,  # empfangen (Windows)
            "miracast_senden": self.send_miracast,
        }
        action = actions.get(command)
        if action:
            action()
        else:
            self.message.emit(f"Unbekannter Befehl: {command}")

    # ------------------------------------------------------------ Timer
    def timer_source(self) -> dict:
        cfg = self.config["timer"]
        return {"type": "countdown", "minutes": cfg.get("minutes", 5), "seconds": cfg.get("seconds", 0),
                "mode": cfg.get("mode", "countdown"), "finished_text": cfg.get("finished_text", ""),
                "warn_colors": cfg.get("warn_colors", True), "size": cfg.get("size", 30),
                "autostart": False, "shared": True, "background": "#000000", "color": "#ffffff"}

    def show_timer(self) -> None:
        """Timer im Vollbild auf Monitor 2 zeigen (Zeit läuft dabei unverändert weiter)."""
        from .timer import clock

        cfg = self.config["timer"]
        if clock.fresh():
            clock.set(float(cfg.get("minutes", 5)) * 60 + float(cfg.get("seconds", 0)),
                      cfg.get("mode", "countdown"), cfg.get("finished_text", ""))
        self.show_source(self.timer_source())

    def timer_action(self, action: str) -> None:
        from .timer import clock

        if not self._guard():
            return
        cfg = self.config["timer"]
        if clock.fresh() and action in ("toggle", "restart"):
            clock.set(float(cfg.get("minutes", 5)) * 60 + float(cfg.get("seconds", 0)),
                      cfg.get("mode", "countdown"), cfg.get("finished_text", ""))
        was_running = clock.running
        {"toggle": clock.toggle, "restart": clock.restart, "reset": clock.reset,
         "plus": lambda: clock.add(60), "minus": lambda: clock.add(-60)}[action]()
        if clock.running and not was_running:
            self.sounds.play_event("timer_start")
        elif was_running and not clock.running and action == "toggle":
            self.sounds.play_event("timer_pause")
        self.changed.emit()

    def _watch_timer(self) -> None:
        from .timer import clock

        state = clock.urgency()
        finished = clock.finished()
        old_state, old_finished = self._timer_state
        if clock.running or finished:
            if state == "bald" and old_state == "normal":
                self.sounds.play_event("timer_minute")
            if finished and not old_finished:
                self.sounds.play_event("timer_ende")
                self.changed.emit()
        self._timer_state = (state, finished)

    def timer_visible(self) -> bool:
        """Zeigt Monitor 2 gerade irgendwo einen Timer (direkt oder in einer Szene)?"""
        def has(cfg, depth=0):
            if not cfg or depth > 4:
                return False
            if cfg.get("type") == "countdown":
                return True
            if cfg.get("type") == "scene":
                scene = self.config.get_scene(cfg.get("scene"))
                return any(has(s, depth + 1) for s in (scene or {}).get("slots", []))
            return False

        return self.mode == "content" and has(self.content)

    def toggle_screensaver(self) -> None:
        if self._guard():
            self.screensaver.toggle()

    def step_scene(self, direction: int) -> None:
        """Zur nächsten/vorherigen eigenen Szene wechseln (in der Reihenfolge der Liste)."""
        names = self.config.scene_names()
        if not names:
            self.message.emit("Es gibt noch keine Szene.")
            return
        current = self.content.get("scene") if self.content and self.content.get("type") == "scene" else None
        if current in names:
            index = (names.index(current) + direction) % len(names)
        else:
            index = 0 if direction > 0 else len(names) - 1
        self.show_source({"type": "scene", "scene": names[index]})

    # ------------------------------------------------------------ Overlays über Monitor 2
    def overlays_changed(self) -> None:
        """Nach dem Bearbeiten (Editor, Kachel, Handy): Overlay-Fenster neu aufbauen."""
        self.config["overlays"] = dict(self.config["overlays"])  # speichern
        self.overlay_window.reload()
        self._overlays_shown = self.overlay_window.needed()
        self.changed.emit()

    def set_overlays(self, on: bool | None = None) -> None:
        """Alle Overlays an/aus (None = umschalten). Aus blendet auch die Overlays von Kacheln aus."""
        cfg = dict(self.config["overlays"])
        cfg["on"] = (not cfg.get("on")) if on is None else bool(on)
        self.config["overlays"] = cfg
        if not cfg["on"]:  # „Overlays aus“ heißt: nichts mehr über Monitor 2 – auch der Chat nicht
            self.tile_overlays.clear()
            if self.live_overlays.pop("chat", None) is not None:
                self.config["chat"] = {**self.config["chat"], "overlay": False}
                self.guests_changed.emit()
        if cfg["on"] and not any(it.get("on", True) for it in cfg.get("items", [])):
            self.message.emit("Overlays: noch keins angelegt · Pfeil an der Kachel → Bearbeiten")
        self.overlays_changed()

    def toggle_tile_overlay(self, tile_id: str, item: dict) -> None:
        """Eigene Kachel mit Overlay: Klick = einblenden, nochmal = ausblenden (wie Bildschirmschoner-Kacheln)."""
        if tile_id in self.tile_overlays:
            del self.tile_overlays[tile_id]
        elif self.output_screen() is None:
            self.message.emit("Kein Bildschirm gefunden.")
            return
        else:
            self.tile_overlays[tile_id] = {**item, "id": f"kachel-{tile_id}", "on": True}
        self.overlay_window.reload()
        self._overlays_shown = self.overlay_window.needed()
        self.changed.emit()

    def refresh_tile_overlays(self) -> None:
        """Startseite gespeichert: eingeblendete Kachel-Overlays auf den neuen Stand bringen (gelöscht → weg)."""
        tiles = {t.get("id"): t for t in self.config["start_page"].get("custom", [])}
        for tile_id in list(self.tile_overlays):
            action = (tiles.get(tile_id) or {}).get("action") or {}
            if action.get("kind") == "overlay" and action.get("overlay"):
                self.tile_overlays[tile_id] = {**action["overlay"], "id": f"kachel-{tile_id}", "on": True}
            else:
                del self.tile_overlays[tile_id]
        self.overlay_window.reload()
        self.changed.emit()

    def toggle_overlay_item(self, item_id: str) -> None:
        cfg = dict(self.config["overlays"])
        items = [dict(it) for it in cfg.get("items", [])]
        for it in items:
            if it.get("id") == item_id:
                it["on"] = not it.get("on", True)
                if it["on"]:
                    cfg["on"] = True  # einzelnes Overlay eingeschaltet → Overlays auch
        cfg["items"] = items
        self.config["overlays"] = cfg
        self.overlays_changed()

    def _overlays_visibility(self) -> None:
        """Bei „Schwarz“, Bildschirmschoner, Monitorwechsel: Overlays aus- bzw. wieder einblenden."""
        shown = self.overlay_window.needed()
        if shown != self._overlays_shown:
            self._overlays_shown = shown
            self.overlay_window.reload()

    # ------------------------------------------------------------ „Läuft gerade“ (Musik am PC)
    def show_now_playing(self) -> None:
        self.show_source({"type": "nowplaying"})

    def show_whiteboard(self, background: str | None = None, draw: bool = True) -> None:
        """Whiteboard auf Monitor 2 (Hintergrund gilt für die aktuelle Seite) und gleich das Whiteboard-Fenster
        zum Draufzeichnen. Die Stiftfarbe passt sich an (weiß auf Tafeln, dunkel auf Papier)."""
        from . import whiteboard

        if background in whiteboard.BACKGROUNDS:
            self.board.set_background(background)  # gilt für die aktuelle Seite
        bg = self.board.page["bg"]
        draw_cfg = self.config["draw"]
        color = whiteboard.pen_color_for(bg, draw_cfg.get("color", "#ef4444"))
        tool = draw_cfg.get("tool", "laser")
        self.config["draw"] = {**draw_cfg, "color": color, "tool": "pen" if tool == "laser" else tool}
        if not self.whiteboard_active():
            self.show_source({"type": "whiteboard", "background": bg})
        else:
            self.content = {**self.content, "background": bg}  # läuft schon: nur den Stand nachziehen
        if draw:
            self.board_requested.emit()

    def media_control(self, action: str) -> None:
        """Abspielen/Pause, Weiter, Zurück beim Player des PCs (Spotify, Browser …) – im Hintergrund."""
        from .now_playing_view import feed

        feed().control(action)

    def run_tile(self, tile_id: str) -> None:
        """Eigene Kachel der Startseite ausführen (auch per Tastenkürzel)."""
        tile = next((t for t in self.config["start_page"].get("custom", []) if t.get("id") == tile_id), None)
        if tile is None:
            self.message.emit("Diese Kachel gibt es nicht mehr.")
            return
        action = tile.get("action") or {}
        kind = action.get("kind")
        # Hat die Kachel einen eigenen Ton (oder gibt es einen Kachel-Ton), ersetzt er die normalen Töne
        tile_sound = tile.get("sound") or self.config["sounds"].get("events", {}).get("kachel", "")
        normal = not tile_sound
        if kind == "command":
            self.run_command(action.get("command", ""))
        elif kind == "run":  # wie Win+R – eigener Befehl je System (Dual-Boot) oder ein gemeinsamer
            from .pc_control import run_line, run_line_for_os

            line = run_line_for_os(action)
            ok, why = run_line(line)
            self.message.emit(f"▶ {tile.get('title', '')}" if ok else f"„{tile.get('title', '')}“: {why}")
        elif kind == "source" and action.get("source"):
            self.show_source(action["source"], remember=True, sound=normal)
        elif kind == "screensaver":
            # eigener Bildschirmschoner dieser Kachel: nochmal klicken = beenden
            if not self._guard():
                return
            if self.screensaver.active and self.screensaver.override_id == tile_id:
                self.screensaver.stop()
            else:
                self.screensaver.stop()
                self.screensaver.start(manual=True, override={**(action.get("screensaver") or {}),
                                                              "_id": tile_id})
        elif kind == "overlay" and action.get("overlay"):
            self.toggle_tile_overlay(tile_id, action["overlay"])
        elif kind == "timer":
            from .timer import clock

            t = action.get("timer") or {}
            clock.set(float(t.get("minutes", 5)) * 60 + float(t.get("seconds", 0)), t.get("mode", "countdown"),
                      t.get("finished_text", self.config["timer"].get("finished_text", "")))
            self.show_source(self.timer_source(), sound=normal)
            if t.get("autostart", True):
                clock.start()  # direkt starten – timer_action würde auf die Standarddauer zurückstellen
                self.sounds.play_event("timer_start")
                self.changed.emit()
        if tile_sound:
            self.sounds.play_event("kachel", tile_sound)

    def toggle_pip(self) -> None:
        if self.pip is not None and self._guard():
            if self.cast.duel is not None and not self.pip.isVisible():
                self.message.emit("Bild-in-Bild ist bei Schiffe versenken aus – sonst sähe Spieler 1 die Schiffe "
                                  "von Spieler 2.")
                return
            self.pip.toggle()
            self.changed.emit()

    # ------------------------------------------------------------ Wetter, Glücksrad, Abstimmung
    def show_weather(self) -> None:
        self.show_source({"type": "wetter"})
        if "lat" not in self.config["weather"]:
            self.message.emit("Wetter: Ort einstellen (Kachel „Wetter & Uhr“ → Pfeil → „Ort ändern …“).")

    def wheel_names(self) -> list[str]:
        from .wheel import clean_names, default_names

        return clean_names(self.config["wheel"].get("names") or []) or default_names()

    def wheel_widget(self):
        """Das Glücksrad auf Monitor 2 (oder None, wenn es gerade nicht zu sehen ist)."""
        from .wheel import WheelSource

        widget = self.output.content if self.mode == "content" else None
        return widget if isinstance(widget, WheelSource) else None

    def show_wheel(self):
        """Glücksrad auf Monitor 2 zeigen – es dreht sich NICHT von selbst (dafür „Drehen“)."""
        names = self.wheel_names()
        if self.config["wheel"].get("remove_picked"):
            if not self.wheel_left or not set(self.wheel_left) <= set(names):
                self.wheel_left = list(names)
            names = self.wheel_left
        widget = self.wheel_widget()
        if widget is None:
            self.show_source({"type": "zufall", "names": names})
            widget = self.wheel_widget()
            if widget is None:
                return None
        elif widget.names != names and not widget.spinning:
            widget.set_names(names)
        if not getattr(widget, "_alupc_connected", False):
            widget.finished.connect(self._wheel_done)
            widget._alupc_connected = True
        return widget

    def spin_wheel(self) -> None:
        """Glücksrad drehen (wird es noch nicht gezeigt: erst zeigen, dann drehen)."""
        widget = self.show_wheel()
        if widget is not None and not widget.spinning:
            widget.spin()
            self.changed.emit()

    def _wheel_done(self, name: str) -> None:
        self.changed.emit()
        if self.config["sounds"].get("enabled", True):
            self.sounds.play("builtin:ding")
        if self.config["wheel"].get("remove_picked") and self.wheel_left and name in self.wheel_left:
            self.wheel_left.remove(name)
            if not self.wheel_left:
                self.wheel_left = None
                self.message.emit("Glücksrad: alle Einträge waren dran – beim nächsten Drehen sind wieder alle dabei.")

    # ------------------------------------------------------------ AluPC-WLAN: Geräte
    def wlan_devices(self) -> list[dict]:
        """Geräte im AluPC-WLAN: IP, MAC, Name (falls es beim Mitmachen einen eingegeben hat)."""
        from .hotspot import NAMES, hotspot, neighbors

        if not hotspot.running:
            return []
        return [{"ip": ip, "mac": mac, "name": NAMES.get(ip) or "Gerät"}
                for ip, mac in sorted(neighbors().items(), key=lambda kv: tuple(int(x) for x in kv[0].split(".")))]

    def ensure_wlan(self) -> None:
        """Handys kommen nur übers AluPC-WLAN herein – läuft noch keins von AluPC, im Hintergrund starten."""
        import os
        import threading

        from .hotspot import hotspot, supported

        if hotspot.running or os.environ.get("ALUPC_NO_AUTO_WIFI"):
            return
        ok, why = supported()
        if not ok:
            hotspot.message = why
            self.message.emit(f"Handys kommen nur übers AluPC-WLAN herein – {why}")
            return
        self.message.emit("AluPC-WLAN startet …")
        threading.Thread(target=lambda: self.set_hotspot(True, "normal"), name="alupc-wlan-an", daemon=True).start()

    # ------------------------------------------------------------ Gäste: Chat und Foto-Wand (übers AluPC-WLAN)
    def start_chat(self, overlay: bool | None = None) -> None:
        """Chat öffnen: Handys schreiben übers AluPC-WLAN (QR-Code „Chat“ auf Monitor 2), Nachrichten direkt als Overlay."""
        from .chat import Chat

        cfg = self.config["chat"]
        if self.cast.chat is None:
            self.cast.chat = Chat(cfg.get("words"), cfg.get("builtin", True), cfg.get("hide_links", True))
        self.cast.chat.set_open(True)
        if not self.cast.start():
            self.message.emit("Chat: Netzwerk-Anschluss belegt.")
        self.ensure_wlan()
        if cfg.get("overlay", True) if overlay is None else overlay:
            self.set_chat_overlay(True)
        self.guests_changed.emit()
        self.changed.emit()

    def stop_chat(self) -> None:
        if self.cast.chat is None:
            return
        self.cast.chat = None
        self.set_chat_overlay(False)
        if self.mode == "content" and (self.content or {}).get("type") == "chatwand":
            self.extend()
        self.message.emit("Chat beendet.")
        self.guests_changed.emit()
        self.changed.emit()

    def chat_overlay_item(self) -> dict:
        cfg = self.config["chat"]
        keys = ("mode", "count", "seconds", "qr", "x", "y", "size", "style", "color")
        return {"id": "live-chat", "type": "chat", "name": "Chat", "on": True, **{k: cfg[k] for k in keys if k in cfg}}

    def set_chat_overlay(self, on: bool | None) -> None:
        """Chat-Overlay auf Monitor 2 an/aus (None = umschalten) – eigener Schalter, unabhängig von „Overlays“."""
        on = "chat" not in self.live_overlays if on is None else bool(on)
        if on and self.cast.chat is None:
            self.start_chat(overlay=False)
        if on:
            self.live_overlays["chat"] = self.chat_overlay_item()
        else:
            self.live_overlays.pop("chat", None)
        self.config["chat"] = {**self.config["chat"], "overlay": on}
        self.overlay_window.reload()
        self._overlays_shown = self.overlay_window.needed()
        self.guests_changed.emit()
        self.changed.emit()

    def update_chat_settings(self, **values) -> None:
        cfg = {**self.config["chat"], **values}
        self.config["chat"] = cfg
        if self.cast.chat is not None and {"words", "builtin", "hide_links"} & set(values):
            self.cast.chat.set_filter(cfg.get("words") or [], cfg.get("builtin", True), cfg.get("hide_links", True))
        if "chat" in self.live_overlays:
            self.live_overlays["chat"] = self.chat_overlay_item()
            self.overlay_window.reload()
        self.guests_changed.emit()

    def chat_say(self, text: str) -> bool:
        """Nachricht vom PC (hervorgehoben, ohne Filter-Sperren)."""
        if self.cast.chat is None:
            self.start_chat()
        msg, _why = self.cast.chat.post("AluPC", text, host=True)
        if msg is not None:
            self.overlay_window.chat_changed()
            self.guests_changed.emit()
        return msg is not None

    def start_wall(self, show: bool = True) -> None:
        """Foto-Wand öffnen: Handys schicken Fotos übers AluPC-WLAN (QR-Code der Foto-Wand auf Monitor 2)."""
        from pathlib import Path

        from .photowall import PhotoWall, default_folder

        cfg = self.config["photowall"]
        folder = Path(cfg["folder"]) if cfg.get("folder") else default_folder()
        if self.cast.wall is None or self.cast.wall.folder != folder:
            self.cast.wall = PhotoWall(folder)
        self.cast.wall.set_open(True)
        if not self.cast.start():
            self.message.emit("Foto-Wand: Netzwerk-Anschluss belegt.")
        self.ensure_wlan()
        if show:
            self.show_source({"type": "fotowand"}, remember=False)
        self.guests_changed.emit()
        self.changed.emit()

    def stop_wall(self) -> None:
        if self.cast.wall is None:
            return
        self.cast.wall = None
        if self.mode == "content" and (self.content or {}).get("type") == "fotowand":
            self.extend()
        self.message.emit("Foto-Wand beendet – die Fotos bleiben im Ordner.")
        self.guests_changed.emit()
        self.changed.emit()

    def _toggle_vcam(self, on: bool | None = None) -> None:
        """Monitor 2 als virtuelle Kamera an/aus (None = umschalten)."""
        on = not self.vcam.running if on is None else on
        if on:
            ok, why = self.vcam.start()
            self.message.emit("Virtuelle Kamera „AluPC Bildschirm“ an." if ok else why)
        else:
            self.vcam.stop()
            self.message.emit("Virtuelle Kamera aus.")
        self.changed.emit()

    def start_poll(self, question: str, options: list[str]) -> None:
        from .polls import Poll

        poll = Poll(question, options)
        self.cast.poll = poll
        self.config["poll"] = {"question": poll.question, "options": poll.options}
        self.ensure_wlan()  # abstimmen nur übers AluPC-WLAN (QR-Code der Abstimmung)
        self.show_source({"type": "umfrage"}, remember=False)
        self.changed.emit()

    # ------------------------------------------------------------ Schalter mit festem Ziel (an/aus statt umschalten)
    def _run_switch(self, command: str) -> bool:
        """„schwarz_an“, „standbild_aus“, „rgb_farbe:#ff0000“, „timer_set:300“ … – True, wenn erledigt."""
        if command.startswith("rgb_farbe:"):
            color = command[10:]
            if len(color) == 7 and color.startswith("#"):
                self.rgb.update_settings(color=color, mode="farbe")
                if not self.rgb.connected:
                    self.rgb.connect_async(start_if_needed=True)
            return True
        if command.startswith("timer_set:"):
            from .timer import clock

            try:
                seconds = max(1, min(24 * 3600, int(command[10:])))
            except ValueError:
                return True
            clock.set(seconds, "countdown", self.config["timer"].get("finished_text", ""))
            self.show_source(self.timer_source())
            clock.start()
            self.sounds.play_event("timer_start")
            self.changed.emit()
            return True
        if command in ("timer_start", "timer_pause"):
            from .timer import clock

            if clock.running != (command == "timer_start"):
                self.timer_action("toggle")
            return True
        if command == "musik_play":
            self.media_control("play_pause")
            return True
        if command in ("rgb_heller", "rgb_dunkler"):
            s = self.rgb.settings()
            step = 25 if command == "rgb_heller" else -25
            self.rgb.update_settings(brightness=max(10, min(100, int(s.get("brightness", 100)) + step)),
                                     mode="farbe" if s["mode"] == "aus" else s["mode"])
            return True
        if command in ("rgb", "rgb_an"):
            mode = self.rgb.settings()["mode"]
            self.rgb.set_mode("farbe" if mode == "aus" else ("aus" if command == "rgb" else mode))
            return True
        if command in ("zuhoeren_an", "zuhoeren_aus", "zuhoeren"):
            self.toggle_listening(None if command == "zuhoeren" else command == "zuhoeren_an")
            return True
        base, _, state = command.rpartition("_")
        toggles = {"schwarz": (lambda: self.privacy, self.toggle_privacy),
                   "standbild": (lambda: self.frozen, self.toggle_freeze),
                   "bildschirmschoner": (lambda: self.screensaver.active, self.toggle_screensaver),
                   "bild_in_bild": (lambda: self.pip is not None and self.pip.isVisible(), self.toggle_pip)}
        if state in ("an", "aus") and base in toggles:
            is_on, toggle = toggles[base]
            if bool(is_on()) != (state == "an"):
                toggle()
            return True
        return False

    def _voice_command(self, command: str, label: str, text: str) -> None:
        hub = self.cast.games
        if command == "naechste_szene" and hub is not None and hub.phase == "running":
            command, label = "spiel_weiter", "Minispiel: weiter"  # „Alu PC, weiter“ im Spiel = nächste Frage
        if command != "frage:ja":
            self.message.emit(f"🎤 {label}")
        self.assistant.handle(command, label, text)

    # Befehle, die das Handy auch als Satz („Frag AluPC“) nicht auslösen darf – nur am PC
    PHONE_FORBIDDEN = {"sperren", "zuhoeren", "zuhoeren_an", "zuhoeren_aus", "spiel_start", "pc_herunterfahren",
                       "pc_neustart", "pc_schlafen", "pc_abmelden", "bestaetigen"}

    def ask(self, text: str, source: str = "pc") -> dict:
        """Getippten (oder diktierten) Satz verstehen und ausführen – wie ein Sprachbefehl, nur ohne Mikrofon.
        Rückgabe: {"ok", "command", "label", "reply"}."""
        from .intents import understand
        from .voice import fold, wake_span

        words = fold(text).split()
        span = wake_span(words)
        if span:
            words = words[:span[0]] + words[span[1]:]
        try:
            found = understand(words, self.config.scene_names(), self.config["voice"].get("custom") or []) \
                if words else None
        except Exception:  # noqa: BLE001
            found = None
        if not found:
            reply = "Das habe ich nicht verstanden. Zum Beispiel: „Licht blau“, „Timer 5 Minuten“, „Kamera zeigen“."
            self.assistant.remember(reply)
            return {"ok": False, "command": "", "label": "", "reply": reply}
        command, label = found
        if source == "handy" and (command in self.PHONE_FORBIDDEN or command.startswith("spiel:")):
            reply = "Das geht nur direkt am PC."
            self.assistant.remember(reply)
            return {"ok": False, "command": command, "label": label, "reply": reply}
        self.message.emit(f"{'📱' if source == 'handy' else '⌨'} {label}")
        reply = self.assistant.handle(command, label, text, voice=False)
        return {"ok": True, "command": command, "label": label, "reply": reply}

    def _voice_not_understood(self, text: str) -> None:
        if self.voice.dry_run:  # „Sprache testen“ zeigt das selbst an
            return
        self.message.emit(f"🎤 Nicht verstanden: „{text}“")
        self.assistant.not_understood(text)

    def toggle_listening(self, on: bool | None = None) -> None:
        """Mikrofon-Schalter: an = AluPC hört ohne Startwort zu, aus = wieder nur mit Startwort (bzw. ganz aus)."""
        from .voice import model_ready, vosk_available

        on = (not self.voice.direct) if on is None else bool(on)
        if on and not (vosk_available() and model_ready()):
            self.message.emit("Mikrofon: erst im Setup → Sprache das Sprachmodell herunterladen.")
            return
        self.voice.set_direct(on)
        self.message.emit("🎤 Ich höre zu – ohne Startwort" if on else "🎤 Mikrofon-Schalter aus")
        self.changed.emit()

    def games_wifi(self) -> tuple[str, str, bool] | None:
        """Das AluPC-WLAN (für die Lobby): WLAN-Code scannen, dann Spiel-Code → Name → mitspielen."""
        from .hotspot import hotspot

        if hotspot.running:
            return hotspot.ssid, hotspot.password, hotspot.hidden
        return None

    def guest_wifi(self) -> tuple[str, str, bool] | None:
        """WLAN, in das Handys sollen: laufender Hotspot von AluPC oder das im Spiele-Fenster eingetragene WLAN.
        (Name, Passwort, unsichtbar?)"""
        from .hotspot import hotspot

        if hotspot.running:
            return hotspot.ssid, hotspot.password, hotspot.hidden
        return None  # nur das AluPC-WLAN – kein anderes Netz

    def set_hotspot(self, on: bool, kind: str = "normal") -> tuple[bool, str]:
        """AluPC-WLAN an/aus (blockiert einige Sekunden – aus einem Hintergrund-Thread aufrufen). Es gibt nur EIN
        WLAN (Hotspot-Kachel, Minispiele, Abstimmen …) – kind ist nur noch aus Kompatibilität da."""
        from .hotspot import hotspot, settings

        if on and hotspot.running:  # läuft schon → NICHT neu starten (Handys flögen raus)
            self.cast.start()
            return True, "AluPC-WLAN läuft schon."
        lock = self._wlan_lock
        if not lock.acquire(blocking=False):  # ein zweiter Start/Stopp mittendrin bräche das WLAN ab
            return False, "AluPC-WLAN wird gerade schon gestartet bzw. beendet …"
        try:
            return self._set_hotspot(on, hotspot, settings)
        finally:
            lock.release()

    def _set_hotspot(self, on: bool, hotspot, settings) -> tuple[bool, str]:
        if on:
            # Die Handy-Seiten kommen von AluPCs Webserver (Port belegt → nimmt er den nächsten freien)
            if not self.cast.start():
                msg = "WLAN nicht gestartet: AluPCs Webserver findet keinen freien Port (8765–8774)."
                self.message.emit(msg)
                return False, msg
            hs = settings(self.config)
            ok, msg = hotspot.start(hs["ssid"], hs["password"],
                                    hidden=bool(hs.get("hidden", True)))  # Linux: unsichtbar (Windows kann es nicht)
        else:
            ok, msg = hotspot.stop()
        self.message.emit(msg)
        self.hotspot_changed.emit()
        return ok, msg

    def _auto_games_wifi(self) -> bool:
        """Minispiele brauchen das AluPC-WLAN: läuft es noch nicht, startet es mit den Spielen (und bleibt danach an).
        Handys scannen in der Lobby den WLAN-Code, dann den Spiel-Code → Name → mitspielen."""
        import os
        import threading

        from .hotspot import hotspot, supported

        if not self.config["games"].get("auto_wifi", True) or os.environ.get("ALUPC_NO_AUTO_WIFI"):
            return False
        if hotspot.running:
            return False
        ok, why = supported()
        if not ok:
            hotspot.message = why
            return False
        self.message.emit("AluPC-WLAN startet …")
        self._games_wifi_starting = True

        def run():
            try:
                self.set_hotspot(True)
            finally:
                self._games_wifi_starting = False

        threading.Thread(target=run, name="spiele-wlan-an", daemon=True).start()
        return True

    def games_wifi_only(self) -> bool:
        """Mitspielen nur über das AluPC-WLAN – immer, es gibt keinen anderen Weg."""
        return True

    def games_wifi_status(self) -> tuple[bool, str]:
        """(nur über WLAN?, Text statt des Codes, falls das AluPC-WLAN gerade nicht läuft)."""
        from .hotspot import hotspot

        only = self.games_wifi_only()
        if not only or hotspot.running:
            return only, ""
        if self._games_wifi_starting:
            return only, "AluPC-WLAN startet …"
        why = hotspot.message if hotspot.message and not hotspot.running else ""
        return only, ("AluPC-WLAN ist aus" + (f" – {why}" if why else "") +
                      " · am PC: Kachel „Hotspot“ oder Minispiele-Fenster → „WLAN …“")

    def show_wifi_help(self) -> bool:
        """Anleitung „Handy fliegt aus dem WLAN?“ groß auf Monitor 2 (mit WLAN-Code zum Wiederverbinden)."""
        self.show_source({"type": "wlanhilfe"}, remember=False)
        return True

    def show_wifi_qr(self) -> bool:
        """WLAN-QR-Code des laufenden Hotspots groß auf Monitor 2."""
        from .hotspot import hotspot

        if not hotspot.running:
            self.message.emit("Hotspot ist aus – erst einschalten, dann den WLAN-QR-Code zeigen.")
            return False
        self.show_source({"type": "wlan", "ssid": hotspot.ssid, "password": hotspot.password,
                          "hidden": hotspot.hidden}, remember=False)
        return True

    def start_games(self, key: str | None = None) -> None:
        """Minispiele: Lobby mit QR-Code auf Monitor 2 (eine laufende Runde bleibt erhalten).
        Gestartet wird nur am PC (Steuerfenster, Kachel-Menü, eigene Kachel/Finger-Befehl „spiel_start“)."""
        from .games import GAMES, GameHub

        key = key if key in GAMES else None
        cfg = self.config["games"]
        if self.cast.games is None:
            hub = GameHub(key or cfg.get("last") or "schlangen")
            for game, opts in (cfg.get("options") or {}).items():
                for opt, value in (opts or {}).items():
                    if game in GAMES:
                        hub.set_option(opt, value, game)
            board = cfg.get("board") or {}
            if board.get("abend") == evening_key():  # Bestenliste vom selben Abend weiterführen
                hub.board = {str(k): int(v) for k, v in (board.get("punkte") or {}).items()}
                hub.board_games = int(board.get("spiele", 0))
            self.cast.games = hub
            self._auto_games_wifi()
        elif key:
            self.cast.games.set_game(key)
        if key:
            self.config["games"] = {**cfg, "last": key}
        if self._games_timer is None:
            from PySide6.QtCore import QTimer

            self._games_timer = QTimer(self, interval=33)  # Spiel-Uhr – läuft auch, wenn Monitor 2 etwas anderes zeigt
            self._games_timer.timeout.connect(self._games_tick)
            from .game_sounds import GameSounds

            self.game_sounds = GameSounds(self.config, self.sounds)
        self._games_timer.start()
        if not (self.mode == "content" and (self.content or {}).get("type") == "spiel"):
            self.show_source({"type": "spiel"}, remember=False)
        self.changed.emit()

    def set_board_guests(self, mode: str) -> None:
        """Whiteboard für Gäste im AluPC-WLAN: „aus“, „alle“ (jeder darf schreiben) oder „frage“ (erst am PC
        erlauben). Beim Wechsel werden alte Erlaubnisse vergessen."""
        from .board_guests import MODES

        if mode not in MODES:
            return
        self.board.set_settings(guests=mode)  # (alte Erlaubnisse vergisst board_guests beim Wechsel selbst)
        self.message.emit("Whiteboard für Gäste: " + MODES[mode])
        self.changed.emit()

    def answer_board_guest(self, gid: str, allow: bool) -> None:
        self.cast.board_guests.answer(gid, allow)

    def start_kahoot(self, text: str = "") -> None:
        """Das echte Kahoot auf Monitor 2 (Website mit gespeicherter Anmeldung), bedient am PC über „Browser
        steuern“. text: Kahoot-Link oder Quiz-ID (leer = Kahoot-Startseite)."""
        from .kahoot import host_url

        self.show_source({"type": "website", "url": host_url(text), "keep_login": True, "name": "Kahoot"},
                         remember=False)
        self.browser_control_requested.emit()
        self.message.emit("Kahoot auf dem Bildschirm – am PC im Fenster „Browser steuern“ anmelden, Quiz wählen, Starten. "
                          "Mitspieler: kahoot.it am Handy + PIN vom Bildschirm (Internet nötig).")

    def start_duel(self, open_window: bool = True) -> None:
        """Schiffe versenken: Spieler 1 im Fenster auf Monitor 1, Spieler 2 auf Monitor 2 (Handy als Steuerung oder
        Tastatur). Bild-in-Bild geht dabei aus. Ein laufendes Spiel bleibt erhalten."""
        from .duel import Battleship

        if self.cast.duel is None:
            self.cast.duel = Battleship()
            self._auto_games_wifi()  # fürs Handy von Spieler 2 (Tastatur geht auch ohne)
        self.cast.start()
        if not (self.mode == "content" and (self.content or {}).get("type") == "schiffe"):
            self.show_source({"type": "schiffe"}, remember=False)
        # Bild-in-Bild (Monitor 2 auf Monitor 1) würde Spieler 1 die Schiffe von Spieler 2 zeigen → aus
        if self.pip is not None and self.pip.isVisible():
            self._pip_before_duel = True
            self.pip.hide()
        if open_window:
            self.duel_requested.emit()
        self.changed.emit()

    def stop_duel(self) -> None:
        if self.cast.duel is None:
            return
        self.cast.duel = None
        if self.mode == "content" and (self.content or {}).get("type") == "schiffe":
            self.extend()  # Monitor 2 wieder normaler Desktop
        if getattr(self, "_pip_before_duel", False) and self.pip is not None:
            self._pip_before_duel = False
            self.pip.show_on_main()  # war vor dem Spiel an → wieder an
        self.changed.emit()

    def _games_tick(self) -> None:
        hub = self.cast.games
        if hub is None:
            self._games_timer.stop()
            return
        hub.tick()
        if self.game_sounds is not None:
            self.game_sounds.update(hub)
        if hub.version != self._games_version:
            self._games_version = hub.version
            self._save_board(hub)
            self.games_changed.emit()

    def _save_board(self, hub) -> None:
        board = {"abend": evening_key(), "punkte": dict(hub.board), "spiele": hub.board_games}
        if self.config["games"].get("board") != board:
            self.config["games"] = {**self.config["games"], "board": board}

    def save_game_options(self) -> None:
        hub = self.cast.games
        if hub is not None:
            self.config["games"] = {**self.config["games"], "last": hub.game_key,
                                    "options": {k: dict(v) for k, v in hub.options.items()}}

    def game_action(self, action: str) -> None:
        """„start“ (Spiel starten/neu), „weiter“ (nächste Frage/Runde), „ende“ (sofort Ergebnis), „lobby“,
        „aus“ (Minispiele beenden), „spiel:<key>“ (Spiel wählen)."""
        hub = self.cast.games
        if action.startswith("spiel:"):
            self.start_games(action.split(":", 1)[1])
            return
        if hub is None:
            if action == "start":
                self.start_games()
                self.message.emit("Minispiele: erst Handys mitspielen lassen (QR-Code auf dem Bildschirm).")
            return
        if action == "start":
            if not hub.start(restart=True):
                self.message.emit("Minispiele: noch niemand dabei – QR-Code auf dem Bildschirm scannen.")
            if not (self.mode == "content" and (self.content or {}).get("type") == "spiel"):
                self.show_source({"type": "spiel"}, remember=False)
        elif action == "weiter":
            hub.next()
        elif action == "ende":
            hub.finish()
        elif action == "lobby":
            hub.to_lobby()
        elif action == "teams":
            if hub.phase != "running":
                hub.shuffle_teams()
        elif action == "toene":
            self.config["games"] = {**self.config["games"], "sound": not self.config["games"].get("sound", True)}
        elif action == "bestenliste":
            hub.show_board()
            if not (self.mode == "content" and (self.content or {}).get("type") == "spiel"):
                self.show_source({"type": "spiel"}, remember=False)
        elif action == "bestenliste_neu":
            hub.reset_board()
            self._save_board(hub)
        elif action == "aus":
            self.save_game_options()
            self.cast.games = None
            if self._games_timer is not None:
                self._games_timer.stop()
            if self.mode == "content" and (self.content or {}).get("type") == "spiel":
                self.extend()
        self.games_changed.emit()
        self.changed.emit()

    def poll_action(self, action: str) -> None:
        """„ende“ (Ergebnis zeigen), „weiter“ (wieder abstimmen), „neu“ (Stimmen löschen), „aus“ (Abstimmung weg)."""
        poll = self.cast.poll
        if poll is None:
            return
        if action == "ende":
            poll.close()
        elif action == "weiter":
            poll.reopen()
        elif action == "neu":
            poll.reset()
        elif action == "aus":
            self.cast.poll = None
            if self.mode == "content" and (self.content or {}).get("type") == "umfrage":
                self.extend()  # Monitor 2 wieder normaler Desktop
        self.changed.emit()

    def show_welcome(self, name: str, person: str = "", style: str | None = None) -> None:
        """„Willkommen, Lena!“ auf Monitor 1 (nach der Anmeldung mit dem Finger).
        Hat die Person heute Geburtstag: Konfetti und „Alles Gute zum Geburtstag!“."""
        from .ui.welcome_window import WelcomeWindow
        from .welcome import is_birthday

        cfg = self.config["welcome"]
        if self._welcome is not None:
            self._welcome.finish()
        birthday = is_birthday(self.config, person)
        window = WelcomeWindow(name, "konfetti" if birthday else (style or cfg.get("style", "aurora")),
                               cfg.get("text", ""), bool(cfg.get("sound", True)), self, birthday=birthday)
        self._welcome = window
        window.finished.connect(lambda: setattr(self, "_welcome", None) if self._welcome is window else None)
        window.play()

    def shutdown(self) -> None:
        from .hotspot import hotspot

        if hotspot.running and hotspot.started_here:  # das AluPC-WLAN gehört zu AluPC – mit ihm beenden
            hotspot.stop()
        self._timer_watch.stop()
        if self._games_timer is not None:
            self._games_timer.stop()
        self.finger_shortcuts.stop()
        self.buttonbox.stop()
        self.volume_keeper.set_target(None)
        self.finger_unlock.stop()
        self.voice.stop()
        self.speaker.shutdown()
        self._stop_handy_window()
        self.laser.close()
        self.overlay_window.shutdown()
        self.audio_routing.shutdown()
        self.vcam.stop(remember=False)
        self.sender.stop()
        self.cursor_guard.shutdown()
        from .cursor import tracker

        tracker().shutdown()
        self.screensaver.timer.stop()
        self.screensaver.keep_awake.set(False)  # System darf wieder abdunkeln
        if self.pip is not None:
            self.pip.shutdown()  # KWin-Skript „immer oben“ entfernen
        self.output.set_screensaver(None)
        self.output.set_content(None)
        self.airplay.shutdown()
        self.cast.stop()
        self._cast_timer.stop()
        self._fast_timer.stop()
        if self._airplay_bg_timer is not None:
            self._airplay_bg_timer.stop()
        for sig, slot in ((self.airplay.failed, self._airplay_failed), (self.airplay.notice, self._airplay_notice),
                          (self.airplay.settings_changed, self._airplay_settings_changed),
                          (self.airplay.connected, self._iphone_connected),
                          (self.airplay.disconnected, self._iphone_disconnected)):
            try:  # der AirPlay-Server ist ein Einzelstück – nicht an einen beendeten Controller gebunden lassen
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:  # der Server ist ein Einzelstück – nicht an einen beendeten Controller gebunden lassen
            self.cast.request.disconnect(self._cast_request)
        except (RuntimeError, TypeError):
            pass
        self.rgb.shutdown()
        self._sync_poll.stop()
        if self.config.data["sync"].get("enabled"):
            self._sync_timer.stop()
            self.run_sync()
            from . import settings_sync

            if settings_sync.copy_thread is not None:  # Bilder/Videos fertig in den Sync-Ordner kopieren
                settings_sync.copy_thread.join(20)
        if self._config_saved in self.config.listeners:
            self.config.listeners.remove(self._config_saved)
        from .ui.util import wait_for_background

        wait_for_background(3000)
        self.output.shutdown()
        self.output.close()
