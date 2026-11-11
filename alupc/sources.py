"""Quellen, die auf Monitor 2 angezeigt werden: Kamera, Bildschirm, Programm, Website, …

Jede Quelle ist ein QWidget mit einer Methode `stop()`. Video-Quellen zeichnen ihre
Bilder selbst (über QVideoSink); so funktionieren Standbild und Bild-in-Bild
über ein einfaches `grab()`.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QImageReader,
    QPainter,
    QPixmap,
    QTransform,
)
from PySide6.QtMultimedia import (
    QAudioOutput,
    QCamera,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaPlayer,
    QScreenCapture,
    QVideoSink,
    QWindowCapture,
)
from PySide6.QtWidgets import QWidget

from .scenes import layout_slots
from . import stage

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
MAX_SCENE_DEPTH = 4


class FrameView(QWidget):
    """Zeigt ein Bild (seitenverhältnistreu) oder eine Meldung."""

    def __init__(self, fit: str = "contain", parent=None):
        super().__init__(parent)
        self.fit = fit
        self._image: QImage | None = None
        self._message = ""
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def set_image(self, image: QImage | QPixmap | None) -> None:
        if isinstance(image, QPixmap):
            image = image.toImage()
        self._image = image if image is not None and not image.isNull() else None
        if self._image is not None:
            self._message = ""
        self.update()

    def image(self) -> QImage | None:
        return self._image

    def set_message(self, text: str) -> None:
        self._message = text
        self.update()

    def stop(self) -> None:
        pass

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self._image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(fit_rect(self._image.width(), self._image.height(),
                                 self.width(), self.height(), self.fit), self._image)
        elif self._message:
            p.setPen(QColor("#bbbbbb"))
            font = QFont()
            font.setPixelSize(max(12, min(28, self.height() // 18)))
            p.setFont(font)
            p.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap, self._message)
        p.end()


def fit_rect(iw: int, ih: int, w: int, h: int, fit: str = "contain") -> QRectF:
    if iw <= 0 or ih <= 0 or w <= 0 or h <= 0:
        return QRectF(0, 0, w, h)
    if fit == "stretch":
        return QRectF(0, 0, w, h)
    scale = min(w / iw, h / ih) if fit == "contain" else max(w / iw, h / ih)
    sw, sh = iw * scale, ih * scale
    return QRectF((w - sw) / 2, (h - sh) / 2, sw, sh)


class SinkView(FrameView):
    """FrameView, die Bilder aus einem QVideoSink bekommt.

    Optimierung: Ein Videobild wird erst dann in ein QImage umgewandelt, wenn es wirklich
    gezeichnet (oder für Standbild/Bild-in-Bild abgefragt) wird. Kommen mehr Bilder, als der
    Monitor anzeigen kann, werden die überzähligen gar nicht erst umgewandelt.
    """

    def __init__(self, fit="contain", parent=None):
        super().__init__(fit, parent)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self._pending = None

    def _on_frame(self, frame):
        if frame.isValid():
            self._pending = frame
            self._message = ""
            if self.isVisible():
                self.update()

    def _convert(self):
        if self._pending is not None:
            image = self._pending.toImage()
            self._pending = None
            if not image.isNull():
                self._image = image

    def image(self):
        self._convert()
        return self._image

    def paintEvent(self, event):
        self._convert()
        super().paintEvent(event)


# --------------------------------------------------------------------------- Kamera
def find_camera(device_id: str | None):
    devices = QMediaDevices.videoInputs()
    for dev in devices:
        if bytes(dev.id()).decode(errors="replace") == device_id:
            return dev
    return devices[0] if devices and not device_id else None


def camera_id(dev) -> str:
    return bytes(dev.id()).decode(errors="replace")


# Einstellungen pro Kamera (Zoom, Ausschnitt, Spiegeln, Drehen, Belichtung) – vom Controller aus
# config["camera"] gesetzt, Schlüssel = Kamera-ID
camera_settings: dict[str, dict] = {}
CAMERA_DEFAULTS = {"zoom": 1.0, "x": 0.5, "y": 0.5, "mirror": False, "rotate": 0, "exposure": 0.0,
                   "quality": "hoch"}
MAX_ZOOM = 5.0


def camera_options(device_id: str) -> dict:
    return {**CAMERA_DEFAULTS, **camera_settings.get(device_id, {})}


def zoom_rect(w: int, h: int, zoom: float, x: float, y: float) -> tuple[int, int, int, int]:
    """Ausschnitt (links, oben, Breite, Höhe) für einen digitalen Zoom um den Punkt (x, y) (0…1)."""
    zoom = max(1.0, min(MAX_ZOOM, float(zoom)))
    cw, ch = max(1, round(w / zoom)), max(1, round(h / zoom))
    left = round(min(max(x * w - cw / 2, 0), w - cw))
    top = round(min(max(y * h - ch / 2, 0), h - ch))
    return left, top, cw, ch


def clamp_center(zoom: float, x: float, y: float) -> tuple[float, float]:
    """Mittelpunkt so begrenzen, dass der Ausschnitt im Bild bleibt."""
    half = 0.5 / max(1.0, zoom)
    return min(max(x, half), 1 - half), min(max(y, half), 1 - half)


def pan_to_source(dx: float, dy: float, rotate: int, mirror: bool) -> tuple[float, float]:
    """Verschieben „wie man es sieht“ (rechts/oben im angezeigten Bild) → Richtung im Kamerabild."""
    for _ in range((-int(rotate) // 90) % 4):  # Drehung zurücknehmen (je 90° im Uhrzeigersinn)
        dx, dy = -dy, dx
    return (-dx if mirror else dx), dy


def best_camera_format(formats, max_pixels: int = 1920 * 1080):
    """Schärfstes Format bis Full HD mit flüssiger Bildrate (≥ 24 Bilder/s) – gut für den Zoom."""
    def size(f):
        r = f.resolution()
        return r.width() * r.height()

    smooth = [f for f in formats if f.maxFrameRate() >= 24 and 0 < size(f) <= max_pixels]
    pool = smooth or [f for f in formats if 0 < size(f) <= max_pixels] or list(formats)
    if not pool:
        return None
    return max(pool, key=lambda f: (size(f), f.maxFrameRate()))


class CameraSource(SinkView):
    def __init__(self, cfg, parent=None):
        super().__init__(cfg.get("fit", "contain"), parent)
        self.session = QMediaCaptureSession(self)
        self.camera = None
        self.device_id = ""
        self.name = cfg.get("name", "") or "Kamera"
        self._raw: QImage | None = None
        self._digital = 1.0
        dev = find_camera(cfg.get("device_id"))
        if dev is None:
            self.set_message(f"Kamera „{cfg.get('name', '')}“ nicht gefunden.")
            return
        self.device_id = camera_id(dev)
        self.name = dev.description()
        self.camera = QCamera(dev, self)
        self.camera.errorOccurred.connect(lambda _e, text: self.set_message(f"Kamerafehler: {text}"))
        if self.options()["quality"] == "hoch":
            fmt = best_camera_format(dev.videoFormats())
            if fmt is not None:
                self.camera.setCameraFormat(fmt)
        self.session.setCamera(self.camera)
        self.session.setVideoSink(self.sink)
        self.set_message("Kamera startet …")
        self.camera.activeChanged.connect(lambda _a: self.apply_options())
        self.camera.start()
        self.apply_options()

    # ------------------------------------------------------------ Optionen
    def options(self) -> dict:
        return camera_options(self.device_id)

    def hardware_zoom_max(self) -> float:
        try:
            return float(self.camera.maximumZoomFactor()) if self.camera else 1.0
        except (AttributeError, RuntimeError):
            return 1.0

    def supports_exposure(self) -> bool:
        try:
            return bool(self.camera and self.camera.supportedFeatures() & QCamera.Feature.ExposureCompensation)
        except (AttributeError, RuntimeError, TypeError):
            return False

    def apply_options(self) -> None:
        """Nach jeder Änderung: Hardware-Zoom/Belichtung setzen, Rest digital, Bild neu berechnen."""
        o = self.options()
        zoom = max(1.0, min(MAX_ZOOM, float(o["zoom"])))
        hw = 1.0
        if self.camera is not None and self.hardware_zoom_max() > 1.01:
            hw = min(zoom, self.hardware_zoom_max())
            self.camera.setZoomFactor(hw)
        self._digital = zoom / hw
        if self.supports_exposure():
            self.camera.setExposureCompensation(float(o["exposure"]))
        if self._raw is not None:
            self._image = self._transform(self._raw)
        self.update()

    def _transform(self, img: QImage) -> QImage:
        o = self.options()
        if self._digital > 1.001:
            img = img.copy(*zoom_rect(img.width(), img.height(), self._digital, o["x"], o["y"]))
        if o["mirror"]:
            img = img.flipped(Qt.Horizontal) if hasattr(img, "flipped") else img.mirrored(True, False)
        rotate = int(o["rotate"]) % 360
        if rotate:
            img = img.transformed(QTransform().rotate(rotate))
        return img

    def _convert(self):
        if self._pending is None:
            return
        frame, self._pending = self._pending, None
        image = frame.toImage()
        if not image.isNull():
            self._raw = image
            self._image = self._transform(image)

    def stop(self):
        if self.camera:
            self.camera.stop()


# --------------------------------------------------------------------------- Bildschirm
def find_screen(name: str | None):
    for screen in QGuiApplication.screens():
        if screen.name() == name:
            return screen
    return None


class ScreenSource(SinkView):
    """Nimmt einen ganzen Monitor auf (z. B. für Spiegeln und Bild-in-Bild).

    KDE/Wayland: wenn erlaubt direkt über KWin – ohne Fenster „Welchen Bildschirm teilen?“.
    Sonst über Qt (Windows, X11 ohne Nachfrage; Wayland mit Nachfrage des Systems).
    """

    # Aufnahme liefert kein Bild (Fehler oder nach NO_SIGNAL_SECONDS nichts) – Text = Grund
    no_signal = Signal(str)
    NO_SIGNAL_SECONDS = 4

    def __init__(self, cfg, parent=None):
        super().__init__(cfg.get("fit", "contain"), parent)
        screen = find_screen(cfg.get("screen_name")) or QGuiApplication.primaryScreen()
        self._cursor_on = False
        self.method = "qt"
        self.feed = None
        self.capture = None
        self._screen = screen
        self.frames = 0
        self._reported = False
        self.problem = ""  # Grund, falls die Aufnahme kein Bild liefert (auch wenn es sofort beim Start passiert)
        self._watchdog = QTimer(self, singleShot=True, interval=self.NO_SIGNAL_SECONDS * 1000)
        self._watchdog.timeout.connect(lambda: self._no_signal("Die Aufnahme liefert kein Bild."))
        self._watchdog.start()
        if screen is not None and _kwin_allowed(screen.name()):
            self._start_kwin(screen.name(), int(cfg.get("fps", 30)))
        else:
            self._start_qt(screen)

    def _start_kwin(self, name: str, fps: int):
        from .platform.kwin_capture import KWinScreenFeed

        self.method = "kwin"
        self.feed = KWinScreenFeed(name, fps, cursor=screen_settings["cursor"], parent=self)
        self.feed.frame.connect(self._kwin_frame)
        self.feed.failed.connect(self._kwin_failed)
        self.set_message("Bildschirmaufnahme startet …")
        self.feed.start()

    def _kwin_frame(self, image):
        self.frames += 1
        self._watchdog.stop()
        self._pending = None
        self._image = image
        self._message = ""
        if self.feed is not None:
            self.feed.frame_taken()
        self.update()

    def _kwin_failed(self, text):
        # KWin-Weg geht nicht (mehr) → normale Aufnahme versuchen
        self.feed = None
        self._start_qt(self._screen)

    def _start_qt(self, screen):
        self.method = "qt"
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        from .platform.linux_display import is_wayland as _wayland

        if _wayland() and QGuiApplication.platformName() == "xcb":
            # AluPC läuft unter Wayland als X11-Programm: die X11-Aufnahme sieht nur alte X11-Fenster, echte
            # Wayland-Fenster fehlen (Bild ohne Fenster) → lieber gar nicht, Spiegeln macht dann KDE selbst
            self.problem = "Unter Wayland sieht diese Aufnahme keine Fenster."
            self._reported = True
            self._watchdog.stop()
            self.set_message("Bildschirmaufnahme unter Wayland ohne KDE-Freigabe nicht möglich")
            QTimer.singleShot(0, lambda: self.no_signal.emit(self.problem))
            return
        self.session = QMediaCaptureSession(self)
        self.capture = QScreenCapture(self)
        self.capture.setScreen(screen)
        self.capture.errorOccurred.connect(self._error)
        self.session.setScreenCapture(self.capture)
        self.session.setVideoSink(self.sink)
        self.set_message("Bildschirmaufnahme startet … (evtl. Freigabe bestätigen)")
        self.capture.start()
        from .platform.linux_display import is_wayland as _wl

        # Wayland fragt erst nach dem Bildschirm → dem Menschen Zeit lassen, bevor der Wächter eingreift
        self._watchdog.setInterval((30 if _wl() else self.NO_SIGNAL_SECONDS) * 1000)
        if self.frames == 0:
            self._watchdog.start()
        # Die Aufnahme unter Windows/X11 enthält den Mauszeiger nicht → selbst einzeichnen
        from .platform.linux_display import is_wayland

        if not is_wayland() and not self._cursor_on:
            from .cursor import tracker

            self._cursor_on = True
            tracker().acquire()
            tracker().moved.connect(self._cursor_moved)

    def _cursor_moved(self, _pos):
        if self.isVisible() and screen_settings["cursor"]:
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._cursor_on and screen_settings["cursor"]) or self._image is None or self._screen is None:
            return
        from .cursor import map_to_image, paint_cursor, tracker

        pos = tracker().pos
        if pos is None:
            return
        try:
            geo = self._screen.geometry()
            dpr = self._screen.devicePixelRatio()
        except RuntimeError:  # Monitor wurde abgesteckt
            return
        area = fit_rect(self._image.width(), self._image.height(), self.width(), self.height(), self.fit)
        point = map_to_image(pos, geo, area)
        if point is None:
            return
        p = QPainter(self)
        paint_cursor(p, point, area.width() / max(1, geo.width()), dpr)
        p.end()

    def _error(self, _err, text):
        self.set_message(
            f"Bildschirmaufnahme nicht möglich: {text}\n\n"
            "Tipp: Unter Setup → „System-Spiegeln“ kann Kubuntu/Windows den Bildschirm selbst spiegeln."
        )
        self._no_signal(f"Bildschirmaufnahme nicht möglich: {text}")

    def _on_frame(self, frame):
        super()._on_frame(frame)
        if frame.isValid():
            self.frames += 1
            self._watchdog.stop()

    def _no_signal(self, reason: str):
        if self.frames == 0 and not self._reported:
            self._reported = True
            self.problem = reason
            self.no_signal.emit(reason)

    def stop(self):
        if self.feed is not None:
            self.feed.stop()
            self.feed = None
        if self.capture is not None:
            self.capture.stop()
        if self._cursor_on:
            from .cursor import tracker

            self._cursor_on = False
            try:
                tracker().moved.disconnect(self._cursor_moved)
            except (RuntimeError, TypeError):
                pass
            tracker().release()


# Einstellungen der Bildschirmaufnahme (setzt der Controller): Mauszeiger einzeichnen?
screen_settings = {"cursor": True}


def _kwin_allowed(screen_name: str) -> bool:
    import sys

    if not sys.platform.startswith("linux"):
        return False
    try:
        from .platform import kwin_capture
    except Exception:  # noqa: BLE001
        return False
    return kwin_capture.allowed(screen_name)


# --------------------------------------------------------------------------- Programmfenster
def capturable_windows():
    """Fenster, die aufgenommen werden können – ohne AluPCs eigene Fenster (sonst Endlos-Spiegel)."""
    try:
        windows = list(QWindowCapture.capturableWindows())
    except Exception:  # noqa: BLE001
        return []
    return [w for w in windows if w.description().strip() and not w.description().startswith("AluPC")]


def _app_part(title: str) -> str:
    """„Dokument – Programmname“ → „Programmname“ (Titel ändern sich oft, der Programmname nicht)."""
    for sep in (" - ", " — ", " – "):
        if sep in title:
            return title.rsplit(sep, 1)[1].strip()
    return ""


def find_window(windows, title: str):
    exact = next((w for w in windows if w.description() == title), None)
    if exact is not None or not title:
        return exact
    part = next((w for w in windows if title in w.description() or w.description() in title), None)
    if part is not None:
        return part
    app = _app_part(title)
    return next((w for w in windows if app and _app_part(w.description()) == app), None)


# Einstellungen der Programm-Aufnahme (setzt der Controller aus der Konfiguration)
window_settings = {"restore_minimized": True}
_window_backend = None


def window_backend():
    """Plattform-Fensterfunktionen (Windows: minimierte Fenster im Hintergrund wiederherstellen)."""
    global _window_backend
    if _window_backend is None:
        from .platform import create_window_backend

        try:
            _window_backend = create_window_backend()
        except Exception:  # noqa: BLE001
            from .platform.base import WindowBackend

            _window_backend = WindowBackend()
    return _window_backend


class WindowSource(SinkView):
    """Nimmt ein Programmfenster auf – auch wenn es hinter anderen Fenstern liegt.

    * Fenster geschlossen → wartet, bis das Programm wieder offen ist, und verbindet sich neu
      (auch wenn sich der Titel ändert, z. B. anderer Browser-Tab).
    * Fenster minimiert → ein minimiertes Fenster zeichnet sich nicht; unter Windows wird es
      (einstellbar) im Hintergrund wiederhergestellt, ohne sich nach vorne zu drängen.
    * Kommen keine neuen Bilder, weil sich im Programm nichts bewegt, bleibt das letzte Bild
      stehen – früher wurde dann alle paar Sekunden neu verbunden (Flackern).
    """

    def __init__(self, cfg, parent=None):
        super().__init__(cfg.get("fit", "contain"), parent)
        self.title = cfg.get("title", "")
        self.restore_minimized = bool(cfg.get("restore_minimized", window_settings["restore_minimized"]))
        self.session = QMediaCaptureSession(self)
        self.capture = QWindowCapture(self)
        self.capture.errorOccurred.connect(self._error)
        self.session.setWindowCapture(self.capture)
        self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._got_frame)
        self._last_frame = 0.0
        self._started = 0.0
        self._frames = 0
        self.state = "start"
        self._retry = QTimer(self, interval=2000)
        self._retry.timeout.connect(self._attach)
        self._watch = QTimer(self, interval=2000)
        self._watch.timeout.connect(self._check)
        self._attach()

    def _got_frame(self, frame):
        if frame.isValid():
            self._last_frame = time.monotonic()
            self._frames += 1
            self.state = "live"

    def _error(self, _err, text):
        self.state = "fehler"
        if self._image is None:
            self.set_message(f"Programm-Aufnahme: {text} – versuche es erneut …")
        self.capture.stop()
        self._watch.stop()
        self._retry.start()

    def _attach(self):
        match = find_window(capturable_windows(), self.title)
        if match is None:
            self.state = "wartet"
            if self._image is None:
                self.set_message(f"Programm „{self.title}“ ist nicht geöffnet – warte …")
            self._retry.start()
            return
        self._retry.stop()
        self.title = match.description()
        self.capture.stop()
        self.capture.setWindow(match)
        self._started = time.monotonic()
        self._frames = 0
        self._wake_if_minimized()
        self.capture.start()
        self._watch.start()

    def _wake_if_minimized(self) -> bool:
        backend = window_backend()
        try:
            if not backend.is_minimized(self.title):
                return False
            if self.restore_minimized and backend.can_restore_background:
                return backend.restore_in_background(self.title)
        except Exception:  # noqa: BLE001
            return False
        self.state = "minimiert"
        if self._image is None:
            self.set_message(f"„{self.title}“ ist minimiert – ein minimiertes Programm liefert kein Bild. "
                             "Bitte wiederherstellen (es darf hinter anderen Fenstern liegen).")
        return False

    def _check(self):
        window = self.capture.window()
        try:
            gone = not window.isValid()
        except (AttributeError, RuntimeError):
            gone = False
        if gone:  # Fenster geschlossen oder neu erstellt → neu suchen
            self._watch.stop()
            self._attach()
            return
        now = time.monotonic()
        quiet = now - max(self._last_frame, self._started)
        if quiet < 4:
            return
        if self._wake_if_minimized():
            return
        if self._frames == 0 and quiet > 8:
            # Noch nie ein Bild bekommen → Aufnahme neu starten
            self._watch.stop()
            self._attach()

    def stop(self):
        self._retry.stop()
        self._watch.stop()
        self.capture.stop()


def kwin_window_mode() -> bool:
    """KDE/Wayland: Programmfenster über KWin aufnehmen (sieht auch echte Wayland-Fenster; das Fenster bleibt
    auf Monitor 1). Qt sieht unter Wayland nur alte X11-Programme."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        from .platform import kwin_capture
        from .platform.linux_display import is_wayland

        return is_wayland() and kwin_capture.is_kde() and kwin_capture.window_capture_available()
    except Exception:  # noqa: BLE001
        return False


class _Named:
    """Fenster aus KWins Liste mit dem gleichen „description()“ wie Qt-Fenster (für find_window)."""

    def __init__(self, info: dict):
        self.info = info

    def description(self) -> str:
        return self.info.get("title", "")


class KWinWindowSource(SinkView):
    """Programmfenster unter KDE/Wayland über KWin – als Kopie auf Monitor 2, das Original bleibt auf Monitor 1.
    Fenster geschlossen oder Titel geändert → sucht es wieder (wie WindowSource)."""

    def __init__(self, cfg, parent=None):
        super().__init__(cfg.get("fit", "contain"), parent)
        self.title = cfg.get("title", "")
        self.method = "kwin"
        self.feed = None
        self.state = "start"
        self.handle = ""
        self._retry = QTimer(self, interval=2000)
        self._retry.timeout.connect(self._attach)
        self._attach()

    def _attach(self):
        from .platform import kwin_capture

        wins = [_Named(w) for w in kwin_capture.window_list()]
        match = find_window(wins, self.title)
        if match is None:
            self.state = "wartet"
            if self._image is None:
                self.set_message(f"Programm „{self.title}“ ist nicht geöffnet – warte …")
            self._retry.start()
            return
        self._retry.stop()
        self.title = match.description()
        self.handle = match.info["id"]
        self._stop_feed()
        self.feed = kwin_capture.KWinScreenFeed(self.handle, 15, cursor=False, parent=self, window=True)
        self.feed.frame.connect(self._frame)
        self.feed.failed.connect(self._failed)
        self.feed.start()

    def _frame(self, image):
        self.state = "live"
        self._pending = None
        self._image = image
        self._message = ""
        if self.feed is not None:
            self.feed.frame_taken()
        self.update()

    def _failed(self, _text):
        # Fenster geschlossen/neu → neu suchen (letztes Bild bleibt stehen)
        self._stop_feed()
        self._retry.start()

    def _stop_feed(self):
        if self.feed is not None:
            try:
                self.feed.frame.disconnect()
                self.feed.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.feed.stop()
            self.feed = None

    def stop(self):
        self._retry.stop()
        self._stop_feed()


# --------------------------------------------------------------------------- Handy (AirPlay)
class AirPlaySource(SinkView):
    """iPhone/iPad per AirPlay: UxPlay empfängt und leitet das Bild als Videostrom hierher weiter."""

    def __init__(self, cfg, parent=None):
        super().__init__(cfg.get("fit", "contain"), parent)
        from .handy import airplay_server

        self.server = airplay_server()
        self.player = None
        self._last_frame = 0.0
        self._started = 0.0
        self._had_frames = False
        self.mode = self.server.acquire(want_stream=True)
        self._watch = QTimer(self, interval=1000)
        self._watch.timeout.connect(self._check)
        self._restart_soon = QTimer(self, singleShot=True, interval=300)
        self._restart_soon.timeout.connect(self._start_player)
        self._replay_timer = QTimer(self, interval=700)
        self._replay_timer.timeout.connect(self._replay_if_needed)
        self._player_frames = 0
        self._generation = getattr(getattr(self.server, "relay", None), "generation", 0)
        if self.mode == "fehlt":
            self.set_message("AirPlay-Empfang: Das Programm UxPlay fehlt.\n\nSeite „Handy“ → „Automatisch "
                             "einrichten“ installiert es.")
            return
        self.server.failed.connect(self._failed)
        self.server.status.connect(self._status)
        if self.mode == "fenster":
            # UxPlay zeigt das Bild im eigenen Fenster, AluPC legt es über diesen Warte-Bildschirm
            self._show_waiting()
            return
        self.sink.videoFrameChanged.connect(self._got_frame)
        self._show_waiting()
        self._start_player()
        self._watch.start()

    def _failed(self, reason: str):
        try:
            self.set_message(f"AirPlay-Empfang läuft nicht\n\n{reason}")
        except RuntimeError:
            pass

    def _status(self, state: str):
        if state in ("neu gestartet", "pin") and self._image is None:  # laufendes Bild nicht wegnehmen
            try:
                self._show_waiting()
            except RuntimeError:
                pass

    def paintEvent(self, event):
        self._convert()
        if self._image is not None or not getattr(self, "_waiting", False):
            return super().paintEvent(event)
        s = self.server.settings()
        paint_airplay_waiting(self, s["airplay_name"], self.server.pin_code, s.get("airplay_idle", "bereit"))

    def set_message(self, text: str) -> None:
        self._waiting = False
        super().set_message(text)

    def _show_waiting(self):
        self._waiting = True
        s = self.server.settings()
        text = (f"iPhone/iPad: Kontrollzentrum → Bildschirmsynchronisierung → „{s['airplay_name']}“\n"
                "(gleiches WLAN wie dieser PC)")
        if self.server.pin_code:
            text += f"\n\nCode: {self.server.pin_code}"
        if self.mode == "fenster":
            text += "\n\nSobald sich das iPhone verbindet, erscheint sein Bild hier."
        self._image = None
        self._pending = None
        SinkView.set_message(self, text)  # Text für Tests/Vorschau; gezeichnet wird der Warte-Bildschirm
        self._waiting = True

    def _start_player(self):
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())  # Anschluss sofort freigeben – sonst „Address already in use“ beim neuen
            self.player.deleteLater()
        self.player = QMediaPlayer(self)
        self.player.setVideoSink(self.sink)
        # Gibt der Player auf (z. B. „Connection timed out“ nach ~10 s ohne Daten), sofort neu – sonst verpasst er
        # das einzige Schlüsselbild beim Verbinden und zeigt nie etwas (Fehler bis 0.58: Ton ja, Bild nein)
        self.player.errorOccurred.connect(lambda *_: self._restart_soon.start())
        self.player.setSource(QUrl.fromLocalFile(str(self.server.sdp_path())))
        self.player.play()
        self._started = time.monotonic()
        # Relais schickt das gemerkte Bild (Schlüsselbild + alles danach) – so lange, bis dieser Player ein Bild hat
        # (wann er wirklich lauscht, sagt Qt nicht; zu frühe Pakete gehen verloren)
        self._player_frames = 0
        self._replays = 0
        self._replay_timer.start()

    def _replay_if_needed(self):
        if self._player_frames or self._replays >= 15:
            self._replay_timer.stop()
            return
        self._replays += 1
        self.server.replay()

    def _got_frame(self, frame):
        if frame.isValid():
            self._last_frame = time.monotonic()
            self._had_frames = True
            self._player_frames += 1

    def _check(self):
        now = time.monotonic()
        relay = getattr(self.server, "relay", None)
        generation = getattr(relay, "generation", 0)
        if generation != self._generation:  # neue Bildgröße (iPad gedreht …) → Player neu, sonst friert das Bild ein
            self._generation = generation
            self._start_player()
            return
        # Ein ruhiger iPad-Bildschirm schickt keine neuen Bilder – das ist kein Trennen. Zurück zu „AirPlay bereit“
        # nur, wenn UxPlay das Trennen meldet (oder sehr lange gar nichts mehr kommt).
        gone = not self.server.is_connected or now - self._last_frame > 120
        if self._had_frames and now - self._last_frame > 4 and gone:
            # Handy hat aufgehört zu senden → Hinweis zeigen und für die nächste Verbindung neu bereit machen
            self._had_frames = False
            self._show_waiting()
            self._start_player()
        elif not self._had_frames and now - self._started > 30:
            self._start_player()  # vorsorglich neu öffnen, falls der Player hängen geblieben ist
        if self.server.pin_code and self._image is None and "Code:" not in self._message:
            self._show_waiting()

    def stop(self):
        self._watch.stop()
        self._restart_soon.stop()
        self._replay_timer.stop()
        for sig, slot in ((self.server.failed, self._failed), (self.server.status, self._status)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if self.player is not None:
            # Vom Strom trennen, nicht nur anhalten: Sonst wartet Qt beim Aufräumen auf den Lese-Thread des Players,
            # der auf Netzwerkdaten hängt – AluPC fror ein und wurde „abgestürzt“ beendet (Fehler bis 0.64).
            self.player.stop()
            self.player.setSource(QUrl())
        if self.mode in ("stream", "fenster"):
            self.server.release()


AIRPLAY_IDLE = {"bereit": "„AirPlay bereit“", "schwarz": "Schwarz", "anleitung": "Anleitung"}


def paint_airplay_waiting(widget, name: str, code: str, style: str = "bereit") -> None:
    """Monitor 2, solange kein iPhone verbunden ist: „AirPlay bereit“ (klein, ruhig), schwarz oder Anleitung."""
    from PySide6.QtGui import QPen

    w, h = widget.width(), widget.height()
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    if style == "schwarz":
        p.fillRect(widget.rect(), QColor("#000000"))
        p.end()
        return
    if style != "anleitung":
        stage.paint_background(p, QRectF(widget.rect()))
        unit = max(6, min(w, h) // 54)
        # AirPlay-Symbol: Bildschirm mit Dreieck
        sw, sh = unit * 7, unit * 4.6
        sx, sy = (w - sw) / 2, h / 2 - unit * 6
        p.setPen(QPen(QColor("#6b7280"), max(2, unit // 3)))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(sx, sy, sw, sh), unit * 0.5, unit * 0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#6b7280"))
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF
        tip = sy + sh - unit * 1.2
        p.drawPolygon(QPolygonF([QPointF(w / 2, tip), QPointF(w / 2 - unit * 2, tip + unit * 2.6),
                                 QPointF(w / 2 + unit * 2, tip + unit * 2.6)]))

        def line(y, size, value, color, bold=False):
            font = QFont()
            font.setPixelSize(int(size))
            font.setBold(bold)
            p.setFont(font)
            p.setPen(QColor(color))
            p.drawText(QRectF(unit, y, w - 2 * unit, size * 1.6), Qt.AlignCenter, value)

        y = tip + unit * 3.6
        line(y, unit * 1.8, "AirPlay bereit", "#d1d5db", True)
        y += unit * 3
        line(y, unit * 1.1, name + (f"  ·  Code {code}" if code else ""), "#6b7280")
        p.end()
        return
    stage.paint_background(p, QRectF(widget.rect()))  # Publikumsansicht: überall gleich
    unit = max(8, min(w, h) // 36)
    # stilisiertes Handy mit Spiegel-Symbol
    pw, ph = unit * 6, unit * 11
    px, py = (w - pw) / 2, h * 0.14
    p.setPen(QPen(QColor("#8ab4ff"), max(2, unit // 3)))
    p.setBrush(QColor(255, 255, 255, 18))
    p.drawRoundedRect(QRectF(px, py, pw, ph), unit, unit)
    p.drawLine(int(px + pw * 0.38), int(py + unit * 0.9), int(px + pw * 0.62), int(py + unit * 0.9))
    p.drawRoundedRect(QRectF(px + pw * 0.25, py + ph * 0.36, pw * 0.5, ph * 0.28), unit * 0.3, unit * 0.3)

    def text(y, size, value, color, bold=False):
        font = QFont()
        font.setPixelSize(int(size))
        font.setBold(bold)
        p.setFont(font)
        p.setPen(QColor(color))
        p.drawText(QRectF(unit, y, w - 2 * unit, size * 1.6), Qt.AlignCenter | Qt.TextWordWrap, value)

    y = py + ph + unit * 1.6
    text(y, unit * 1.3, "iPhone / iPad – Bildschirmsynchronisierung", "#9aa7c7")
    y += unit * 2.4
    text(y, unit * 2.8, name, "#ffffff", True)
    y += unit * 4.4
    if code:
        text(y, unit * 1.3, "Code am iPhone eingeben", "#9aa7c7")
        y += unit * 2.0
        text(y, unit * 3.2, "  ".join(code), "#ffd166", True)
        y += unit * 5.0
    text(y, unit * 1.05, "Kontrollzentrum öffnen → Bildschirmsynchronisierung → Namen wählen · gleiches WLAN",
         "#7f8bab")
    p.end()


# --------------------------------------------------------------------------- AluCast (QR-Code)
def qr_image(text: str, border: int = 4) -> QImage:
    """QR-Code als kleines Schwarz-Weiß-Bild (1 Pixel je Modul, zum Hochskalieren ohne Glätten). Rand 4 Module
    (wie in der Norm): mit weniger erkennen viele Handy-Kameras den Code auf dunklem Hintergrund nicht."""
    import segno

    rows = list(segno.make(text, error="m").matrix_iter(border=border))
    img = QImage(len(rows[0]), len(rows), QImage.Format_RGB32)
    img.fill(QColor("#ffffff"))
    black = QColor("#000000").rgb()
    for y, row in enumerate(rows):
        for x, bit in enumerate(row):
            if bit:
                img.setPixel(x, y, black)
    return img


def draw_qr(p: QPainter, rect: QRectF, img: QImage) -> QRectF:
    """QR-Code scharf zeichnen: jedes Modul gleich viele ganze Pixel (sonst werden manche Module 2, andere 3 Pixel
    breit – auf echten Bildschirmen/Beamern lesen Handys das schlechter). Gibt das tatsächlich genutzte Rechteck."""
    n = max(1, img.width())
    scale = max(1, int(min(rect.width(), rect.height()) // n))
    side = scale * n
    x = round(rect.center().x() - side / 2)
    y = round(rect.center().y() - side / 2)
    target = QRectF(x, y, side, side)
    p.save()
    p.setRenderHint(QPainter.SmoothPixmapTransform, False)
    p.drawImage(target, img)
    p.restore()
    return target


def qr_pixmap(text: str, px: int, dpr: float = 1.0):
    """QR-Code als QPixmap für Fenster: ganzzahlig vergrößert, scharf auch bei Bildschirm-Skalierung."""
    from PySide6.QtGui import QPixmap

    img = qr_image(text)
    n = img.width()
    scale = max(1, int(px * dpr) // n)
    pm = QPixmap.fromImage(img.scaled(n * scale, n * scale, Qt.IgnoreAspectRatio, Qt.FastTransformation))
    pm.setDevicePixelRatio(dpr)
    return pm


class WifiQrSource(QWidget):
    """WLAN-Code groß auf Monitor 2 (Name, Passwort). Die einzelnen Seiten (Spiel, Chat, Abstimmung …) zeigen ihren
    eigenen zweiten Code. Ohne Name in cfg: folgt dem laufenden AluPC-WLAN (startet es gerade, steht das da)."""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.live = not cfg.get("ssid")
        self.ssid, self.password, self.hidden, self._qr = "", "", False, None
        self.note = ""
        self._set(cfg.get("ssid", ""), cfg.get("password", ""), bool(cfg.get("hidden")))
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._follow()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._follow)
        self._timer.start(1000)

    def _set(self, ssid, password, hidden):
        from .screens import wifi_payload

        if (ssid, password, hidden) == (self.ssid, self.password, self.hidden) and (self._qr or not ssid):
            return False
        self.ssid, self.password, self.hidden = ssid, password, hidden
        self._qr = qr_image(wifi_payload(ssid, password, hidden)) if ssid else None
        return True

    def _follow(self):
        from .hotspot import hotspot

        changed, note = False, ""
        if self.live:
            changed = self._set(hotspot.ssid, hotspot.password, hotspot.hidden) if hotspot.running else \
                self._set("", "", False)
            note = "" if hotspot.running else (hotspot.message or "AluPC-WLAN startet …")
        if changed or note != self.note:
            self.note = note
            self.update()

    @staticmethod
    def _panel(p, rect: QRectF, step: str, title: str, qr, lines) -> None:
        """Ein QR-Code mit Nummer, Überschrift und Zeilen darunter – mittig in rect."""
        u = min(rect.width() * 0.8, rect.height() * 0.62)  # Größe des QR-Codes
        head_px = max(12, int(u * 0.11))
        y = rect.y()
        head = f"{step}  {title}"
        font = fitted_font(p, head, int(rect.width() * 0.95), head_px)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(rect.x(), y, rect.width(), head_px * 1.4), Qt.AlignCenter, head)
        y += head_px * 1.4 + u * 0.08
        pad = u * 0.05
        qr_rect = QRectF(rect.center().x() - u / 2, y, u, u)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(qr_rect.adjusted(-pad, -pad, pad, pad), pad, pad)
        draw_qr(p, qr_rect, qr)
        y = qr_rect.bottom() + pad + u * 0.06
        for text, color, size, bold in lines:
            px = max(10, int(u * size))
            if text:
                font = fitted_font(p, text, int(rect.width() * 0.95), px)
                font.setBold(bold)
                p.setFont(font)
                p.setPen(QColor(color))
                p.drawText(QRectF(rect.x(), y, rect.width(), px * 1.5), Qt.AlignCenter, text)
            y += px * 1.5

    def paintEvent(self, _e):

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        stage.paint_background(p, QRectF(self.rect()))  # Publikumsansicht: überall gleich
        if self._qr is None:
            msg = self.note or "Hotspot ist aus"
            p.setPen(QColor("#e2e8f0"))
            p.setFont(fitted_font(p, msg[:40], int(w * 0.8), max(14, h // 16)))
            p.drawText(self.rect().adjusted(w // 10, 0, -w // 10, 0), Qt.AlignCenter | Qt.TextWordWrap, msg)
            p.end()
            return
        wifi_lines = [(f"Name: {self.ssid}", "#67e8f9", 0.085, True),
                      (f"Passwort: {self.password}" if self.password else "ohne Passwort", "#fbbf24", 0.085, True),
                      ("unsichtbar – nur per QR-Code" if self.hidden else "", "#94a3b8", 0.055, False)]
        m = min(w, h) * 0.05
        self._panel(p, QRectF(m, m, w - 2 * m, h - 2 * m), "", "WLAN verbinden", self._qr, wifi_lines)
        p.end()

    def stop(self):
        pass


class WifiHelpSource(WifiQrSource):
    """„Handy fliegt aus dem WLAN?“ groß auf Monitor 2: WLAN-Code (quer links, hochkant oben) und die Anleitung
    (Android, iPhone/iPad, was immer klappt) – derselbe Text wie im Hotspot-Fenster."""

    def __init__(self, cfg, parent=None):
        super().__init__({}, parent)  # folgt immer dem laufenden AluPC-WLAN

    def paintEvent(self, _e):
        from PySide6.QtGui import QFont, QFontMetricsF

        from .hotspot import STAY_STEPS, STAY_WHY

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        stage.paint_background(p, QRectF(self.rect()))  # Publikumsansicht: überall gleich
        m = min(w, h) * 0.05
        portrait = h > w * 1.1
        x0, y0 = m, m
        if self._qr is not None:  # WLAN-Code – quer links, hochkant oben
            codes = [(self._qr, [(self.ssid, "#67e8f9"),
                                 (f"Passwort: {self.password}" if self.password else "", "#fbbf24")])]
            label_px = max(12, int(min(w, h) * (0.03 if len(codes) == 1 else 0.026)))
            labels_h = label_px * 1.55 * 2 + m * 0.3
            n = len(codes)
            if portrait:
                cell_w = (w - (n + 1) * m) / n
                side = min(cell_w * 0.9, h * 0.24)
                cells = [QRectF(m + i * (cell_w + m), m, cell_w, side + labels_h) for i in range(n)]
            else:
                cell_w = w * 0.3 - m
                side = min(cell_w, (h - 2 * m - n * labels_h - (n - 1) * m) / n)
                total = n * (side + labels_h) + (n - 1) * m
                top = (h - total) / 2
                cells = [QRectF(m, top + i * (side + labels_h + m), cell_w, side + labels_h) for i in range(n)]
            y_end = 0.0
            for (img, labels), cell in zip(codes, cells):
                qr = QRectF(cell.center().x() - side / 2, cell.y(), side, side)
                pad = side * 0.05
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#ffffff"))
                p.drawRoundedRect(qr.adjusted(-pad, -pad, pad, pad), pad, pad)
                draw_qr(p, qr, img)
                y = qr.bottom() + pad * 2
                for text, color in labels:
                    if text:
                        f = fitted_font(p, text, int(cell.width() + m * 0.6), label_px)
                        f.setBold(True)
                        p.setFont(f)
                        p.setPen(QColor(color))
                        p.drawText(QRectF(cell.x() - m * 0.3, y, cell.width() + m * 0.6, label_px * 1.5),
                                   Qt.AlignHCenter | Qt.AlignVCenter, text)
                    y += label_px * 1.55
                y_end = max(y_end, y)
            if portrait:
                y0 = y_end + m * 0.5
            else:
                x0 = w * 0.3 + m
        width = w - x0 - m
        # rechts: Anleitung – Schrift so groß, dass alles passt
        blocks = [("Handy fliegt aus dem WLAN?", "#ffffff", 1.6, True, 0), (STAY_WHY, "#cbd5e1", 0.9, False, 0)]
        colors = {"Android": "#4ade80", "iPhone / iPad": "#93c5fd",
                  "Klappt immer": "#fbbf24"}
        for title, lines in STAY_STEPS:
            blocks.append((title, colors.get(title, "#ffffff"), 1.15, True, 0))
            blocks += [("•  " + line, "#e2e8f0", 0.9, False, 1) for line in lines]

        def layout(px):
            rects, y = [], y0
            for text, _c, scale, bold, indent in blocks:
                f = QFont(p.font())
                f.setPixelSize(max(9, int(px * scale)))
                f.setBold(bold)
                ind = indent * px * 1.2
                r = QFontMetricsF(f).boundingRect(QRectF(0, 0, width - ind, 10_000), Qt.TextWordWrap, text)
                if bold and rects:
                    y += px * 0.5  # Luft vor jeder Überschrift, nicht danach
                rects.append((f, QRectF(x0 + ind, y, width - ind, r.height())))
                y += r.height() + px * 0.35
            return rects, y

        px = min(h * 0.045, w * 0.04)
        rects, bottom = layout(px)
        while bottom > h - m and px > 8:
            px *= 0.93
            rects, bottom = layout(px)
        for (text, color, _s, _b, _i), (f, r) in zip(blocks, rects):
            p.setFont(f)
            p.setPen(QColor(color))
            p.drawText(r, Qt.TextWordWrap, text)
        p.end()


# --------------------------------------------------------------------------- Website
def allow_autoplay(view) -> None:
    """Ton ohne Klick erlauben. Chromium spielt Videos/Musik sonst erst nach einem Klick auf die Seite mit Ton ab
    (YouTube bleibt stumm oder startet nicht) – auf Monitor 2 klickt aber niemand."""
    from PySide6.QtWebEngineCore import QWebEngineSettings

    view.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)


def allow_fullscreen(view) -> None:
    """Vollbild-Knopf der Seiten (YouTube, Vimeo, Präsentationen …) erlauben. Ohne das meldet YouTube
    „Vollbildmodus nicht verfügbar“. Das Element füllt dann die ganze Website-Fläche auf Monitor 2
    (Esc oder der Knopf auf der Seite beendet es wieder)."""
    from PySide6.QtWebEngineCore import QWebEngineSettings

    view.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    view.page().fullScreenRequested.connect(lambda request: request.accept())


class WebsiteSource(QWidget):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        if cfg.get("keep_login"):  # z. B. Kahoot: Anmeldung bleibt über Neustarts erhalten
            from PySide6.QtWebEngineCore import QWebEnginePage

            self.view.setPage(QWebEnginePage(persistent_profile(), self.view))
        layout.addWidget(self.view)
        allow_autoplay(self.view)
        allow_fullscreen(self.view)
        url = normalize_url(cfg.get("url", ""))
        self.view.setZoomFactor(float(cfg.get("zoom", 1.0) or 1.0))
        self.volume = int(cfg.get("volume", 100))
        self.muted = bool(cfg.get("muted", False))
        self._install_volume_script()
        self.view.load(QUrl(url))
        self._timer = None
        seconds = int(cfg.get("reload_seconds", 0) or 0)
        if seconds > 0:
            self._timer = QTimer(self, interval=seconds * 1000)
            self._timer.timeout.connect(self.view.reload)
            self._timer.start()

    def _volume_js(self) -> str:
        return VOLUME_JS % (max(0, min(100, self.volume)) / 100)

    def _install_volume_script(self):
        """Lautstärke für alle <video>/<audio> der Seite – auch für später geladene (z. B. YouTube)."""
        from PySide6.QtWebEngineCore import QWebEngineScript

        page = self.view.page()
        page.setAudioMuted(self.muted)
        scripts = page.scripts()
        for old in scripts.find("alupc-volume"):
            scripts.remove(old)
        script = QWebEngineScript()
        script.setName("alupc-volume")
        script.setSourceCode(self._volume_js())
        script.setInjectionPoint(QWebEngineScript.DocumentReady)
        script.setWorldId(QWebEngineScript.MainWorld)
        script.setRunsOnSubFrames(True)
        scripts.insert(script)

    def set_volume(self, volume: int | None = None, muted: bool | None = None) -> None:
        if volume is not None:
            self.volume = int(volume)
        if muted is not None:
            self.muted = bool(muted)
        self._install_volume_script()
        self.view.page().runJavaScript(self._volume_js())

    def stop(self):
        if self._timer:
            self._timer.stop()
        self.view.stop()
        self.view.setUrl(QUrl("about:blank"))


# Setzt die Lautstärke aller Medien der Seite und merkt sie sich für Medien, die erst später starten
VOLUME_JS = """(function () {
  window.__alupcVolume = %.2f;
  var set = function (m) { try { m.volume = window.__alupcVolume; } catch (e) {} };
  document.querySelectorAll('video, audio').forEach(set);
  if (!window.__alupcVolumeHook) {
    window.__alupcVolumeHook = true;
    document.addEventListener('play', function (e) { set(e.target); }, true);
  }
})();"""


_PROFILE = None


def persistent_profile():
    """Browser-Profil mit gespeicherten Anmeldungen (Cookies) im AluPC-Einstellungsordner – für Seiten wie Kahoot,
    bei denen man angemeldet bleiben will. Normale Websites auf Monitor 2 nutzen es nicht."""
    global _PROFILE
    if _PROFILE is None:
        from PySide6.QtWebEngineCore import QWebEngineProfile

        from .config import config_dir

        _PROFILE = QWebEngineProfile("AluPC-Web")
        _PROFILE.setPersistentStoragePath(str(config_dir() / "web-profil"))
        _PROFILE.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    return _PROFILE


def normalize_url(url: str) -> str:
    """„beispiel.de“ → „https://beispiel.de“; Adressen mit Schema (https:, file:, data: …) bleiben."""
    url = url.strip()
    if url and "://" not in url and not url.startswith(("about:", "file:", "data:", "view-source:")):
        url = "https://" + url
    return url


# --------------------------------------------------------------------------- Bild, Video, Diashow
def load_image(path: str) -> QImage:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    return reader.read()


class ImageSource(FrameView):
    def __init__(self, cfg, parent=None):
        super().__init__(cfg.get("fit", "contain"), parent)
        image = load_image(cfg.get("path", ""))
        if image.isNull():
            self.set_message(f"Bild nicht gefunden: {cfg.get('path', '')}")
        else:
            self.set_image(image)


class VideoSource(SinkView):
    def __init__(self, cfg, parent=None):
        super().__init__(cfg.get("fit", "contain"), parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.set_volume(int(cfg.get("volume", 100)), bool(cfg.get("muted", False)))
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.sink)
        path = cfg.get("path", "")
        if path and not Path(path).exists():
            self.set_message(f"Video nicht gefunden: {path}" + (
                " · Windows-Laufwerk einhängen: Setup → Sichern & Sync" if re.match(r"^[A-Za-z]:[\\/]", path) else ""))
        else:
            self.player.errorOccurred.connect(lambda _e, text: self.set_message(f"Video-Fehler: {text}"))
        if cfg.get("loop", True):
            self.player.setLoops(QMediaPlayer.Infinite)
        self.title = Path(cfg.get("path", "")).stem or "Video"
        self.path = str(cfg.get("path", ""))
        self.player.setSource(QUrl.fromLocalFile(cfg.get("path", "")))
        self.player.play()

    def set_volume(self, volume: int | None = None, muted: bool | None = None) -> None:
        if volume is not None:
            self.audio.setVolume(max(0, min(100, int(volume))) / 100)
        if muted is not None:
            self.audio.setMuted(bool(muted))

    # ---- Steuerung (Mediensteuerung im Hauptfenster)
    def playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def toggle_play(self) -> None:
        if self.playing():
            self.player.pause()
        else:
            self.player.play()

    def duration(self) -> int:
        return max(0, int(self.player.duration()))

    def position(self) -> int:
        return max(0, int(self.player.position()))

    def seek_to(self, ms: int) -> None:
        d = self.duration()
        self.player.setPosition(max(0, min(int(ms), d - 1 if d else int(ms))))

    def skip(self, ms: int) -> None:
        self.seek_to(self.position() + ms)

    def rate(self) -> float:
        return float(self.player.playbackRate())

    def set_rate(self, rate: float) -> None:
        self.player.setPlaybackRate(max(0.25, min(3.0, float(rate))))

    def looping(self) -> bool:
        return self.player.loops() != 1

    def set_loop(self, on: bool) -> None:
        self.player.setLoops(QMediaPlayer.Infinite if on else 1)

    def seek_when_ready(self, ms: int, play: bool = True) -> None:
        """Springen – auch wenn das Video noch lädt (dann sobald es bereit ist)."""
        ready = (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferingMedia, QMediaPlayer.BufferedMedia)

        def go():
            self.player.setPosition(max(0, int(ms)))
            if play:
                self.player.play()

        if self.player.mediaStatus() in ready:
            go()
            return

        def on_status(status):
            if status in ready:
                self.player.mediaStatusChanged.disconnect(on_status)
                go()

        self.player.mediaStatusChanged.connect(on_status)

    def stop(self):
        self.player.stop()


def list_images(folder: str) -> list[str]:
    try:
        files = sorted(Path(folder).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    return [str(p) for p in files if p.suffix.lower() in IMAGE_SUFFIXES]


class SlideshowSource(FrameView):
    def __init__(self, cfg, parent=None):
        super().__init__(cfg.get("fit", "contain"), parent)
        self.folder = cfg.get("folder", "")
        self.index = -1
        self.timer = QTimer(self, interval=max(1, int(cfg.get("interval", 5))) * 1000)
        self.timer.timeout.connect(self.next)
        self.next()
        self.timer.start()

    def next(self):
        files = list_images(self.folder)  # neu einlesen: neue Bilder erscheinen automatisch
        if not files:
            self.set_image(None)
            self.set_message(f"Keine Bilder im Ordner: {self.folder}")
            return
        self.index = (self.index + 1) % len(files)
        self.set_image(load_image(files[self.index]))

    def stop(self):
        self.timer.stop()


# --------------------------------------------------------------------------- Text, Uhr, Countdown, Farbe
class TextBase(QWidget):
    """Zeichnet Text; Schriftgröße in Prozent der Feldhöhe, passt also auf jeden Monitor."""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.size_percent = float(cfg.get("size", 12))
        self.color = QColor(cfg.get("color", "#ffffff"))
        self.background = QColor(cfg.get("background", "#000000"))

    def text(self) -> str:
        return ""

    def fit_text(self, text: str) -> str:
        """Text, nach dem die Schriftgröße bemessen wird (Ziffern → „8“: Größe springt nicht)."""
        return text

    def text_color(self) -> QColor:
        return self.color

    def stop(self):
        pass

    def paintEvent(self, _event):
        p = QPainter(self)
        if stage.is_default_black(self.background):  # Standard-Hintergrund → Bühne wie alle Anzeigen
            p.setRenderHint(QPainter.Antialiasing)
            stage.paint_background(p, QRectF(self.rect()))
        else:
            p.fillRect(self.rect(), self.background)
        margin = int(self.width() * 0.03)
        area = self.rect().adjusted(margin, 0, -margin, 0)
        shown = self.text()
        text = self.fit_text(shown)
        font = QFont()
        size = max(8, int(self.height() * self.size_percent / 100))
        # Schrift verkleinern, bis der Text ins Feld passt
        for _ in range(20):
            font.setPixelSize(size)
            p.setFont(font)
            needed = p.boundingRect(area, Qt.AlignCenter | Qt.TextWordWrap, text)
            fits_lines = all(p.fontMetrics().horizontalAdvance(line) <= area.width() for line in text.split("\n"))
            if (needed.height() <= area.height() and fits_lines) or size <= 8:
                break
            size = max(8, int(size * 0.9))
        p.setPen(self.text_color())
        p.drawText(area, Qt.AlignCenter | Qt.TextWordWrap, shown)
        p.end()


class TextSource(TextBase):
    def __init__(self, cfg, parent=None):
        super().__init__(cfg, parent)
        self._text = cfg.get("text", "")

    def text(self):
        return self._text

    def set_text(self, text: str) -> None:
        """Live-Modus: Text austauschen, ohne die Quelle neu anzulegen."""
        if text != self._text:
            self._text = text
            self.update()


class ClockSource(TextBase):
    def __init__(self, cfg, parent=None):
        cfg = {"size": 25, **cfg}
        super().__init__(cfg, parent)
        self.show_date = bool(cfg.get("show_date", True))
        self.show_seconds = bool(cfg.get("show_seconds", True))
        # 4× pro Sekunde nachsehen, aber nur neu zeichnen, wenn sich die Anzeige ändert:
        # die Sekunde springt pünktlich um (max. ¼ s spät) und es wird trotzdem kaum gezeichnet
        self._shown = ""
        self.timer = QTimer(self, interval=250)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        text = self.text()
        if text != self._shown:
            self._shown = text
            self.update()

    def stop(self):
        self.timer.stop()

    def text(self):
        fmt = "%H:%M:%S" if self.show_seconds else "%H:%M"
        text = time.strftime(fmt)
        if self.show_date:
            text += "\n" + format_date_de(time.localtime())
        return text

    def fit_text(self, text: str) -> str:
        return "".join("8" if ch.isdigit() else ch for ch in text)

    def paintEvent(self, _event):
        if not self.show_date:
            return super().paintEvent(_event)
        # Uhrzeit groß, Datum kleiner darunter; beides passt sich der Feldbreite an
        p = QPainter(self)
        if stage.is_default_black(self.background):  # Standard → Bühne wie alle Anzeigen
            stage.paint_background(p, QRectF(self.rect()))
        else:
            p.fillRect(self.rect(), self.background)
        p.setPen(self.color)
        margin = int(self.width() * 0.04)
        area = self.rect().adjusted(margin, 0, -margin, 0)
        now = time.localtime()
        clock = time.strftime("%H:%M:%S" if self.show_seconds else "%H:%M", now)
        date = format_date_de(now)
        template = "".join("8" if ch.isdigit() else ch for ch in clock)  # Größe springt nicht
        big = fitted_font(p, template, area.width(), int(self.height() * self.size_percent / 100 * 1.4))
        small = fitted_font(p, date, area.width(), max(8, big.pixelSize() // 3))
        h_big = QFontMetrics(big).height()
        h_small = QFontMetrics(small).height()
        gap = h_small * 0.6
        top = area.top() + (area.height() - h_big - h_small - gap) // 2
        p.setRenderHint(QPainter.Antialiasing)
        big.setWeight(QFont.DemiBold)
        p.setFont(big)
        if self.show_seconds:  # Sekunden leiser – die Uhrzeit springt ins Auge, die Sekunden laufen mit
            main, secs = clock[:-3], clock[-3:]
            fm = QFontMetrics(big)
            total = fm.horizontalAdvance(template)
            x = area.left() + (area.width() - total) / 2
            p.drawText(QRectF(x, top, fm.horizontalAdvance(template[:-3]), h_big), Qt.AlignCenter, main)
            dim = QColor(self.color)
            dim.setAlphaF(0.45)
            p.setPen(dim)
            p.drawText(QRectF(x + fm.horizontalAdvance(template[:-3]), top, fm.horizontalAdvance(template[-3:]),
                              h_big), Qt.AlignCenter, secs)
        else:
            p.drawText(area.left(), top, area.width(), h_big, Qt.AlignCenter, clock)
        # kurzer Strich in AluPC-Blau zwischen Uhrzeit und Datum
        line_w = area.width() * 0.08
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(stage.ACCENT))
        p.drawRoundedRect(QRectF(area.center().x() - line_w / 2, top + h_big + gap * 0.35, line_w,
                                 max(2.0, h_small * 0.08)), 2, 2)
        soft = QColor(self.color)
        soft.setAlphaF(0.75)
        p.setPen(soft)
        p.setFont(small)
        p.drawText(area.left(), int(top + h_big + gap), area.width(), h_small, Qt.AlignCenter, date)
        p.end()


def fitted_font(painter, text: str, width: int, size: int) -> QFont:
    """Schrift in der Wunschgröße – kleiner, falls der Text sonst nicht in die Breite passt."""
    font = QFont()
    size = max(8, size)
    font.setPixelSize(size)
    font.setBold(True)  # fett gemessen: passt dann auch, wenn der Aufrufer fett schreibt (sonst abgeschnitten)
    advance = QFontMetrics(font).horizontalAdvance(text)
    font.setBold(False)
    if advance > width > 0:
        font.setPixelSize(max(8, int(size * width / advance)))
    return font


WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def format_date_de(t) -> str:
    return f"{WEEKDAYS[t.tm_wday]}, {t.tm_mday}. {MONTHS[t.tm_mon - 1]} {t.tm_year}"


class CountdownSource(TextBase):
    """Zeigt den gemeinsamen Timer (Countdown oder Stoppuhr) – läuft weiter, auch wenn die
    Anzeige neu aufgebaut wird. Farbe: letzte Minute orange, letzte 10 s rot, Ende blinkt."""

    def __init__(self, cfg, parent=None):
        from .timer import clock

        cfg = {"size": 30, **cfg}
        super().__init__(cfg, parent)
        self.clock = clock
        seconds = float(cfg.get("minutes", 5)) * 60 + float(cfg.get("seconds", 0))
        mode = cfg.get("mode", "countdown")
        # Nur neu stellen, wenn der Timer nicht schon läuft (sonst würde er wieder von vorn beginnen).
        # „shared“ = Timer-Kachel: zeigt den Timer so, wie er gerade steht.
        if not cfg.get("shared") and not clock.running and (clock.fresh() or clock.finished() or clock.mode != mode
                                  or abs(clock.duration - seconds) > 0.5):
            clock.set(seconds, mode, cfg.get("finished_text", "Zeit ist um!"))
        if cfg.get("autostart", True) and clock.fresh():
            clock.start()
        self.warn_colors = bool(cfg.get("warn_colors", True))
        self._shown = None
        self.timer = QTimer(self, interval=100)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        # nur neu zeichnen, wenn sich Zahl oder Farbe (Warnung/Blinken) ändert
        state = (self.text(), self.text_color().rgba())
        if state != self._shown:
            self._shown = state
            self.update()

    def text(self):
        return self.clock.text()

    def fit_text(self, text: str) -> str:
        return "".join("8" if ch.isdigit() else ch for ch in text)

    def text_color(self) -> QColor:
        if not self.warn_colors:
            return self.color
        state = self.clock.urgency()
        if state == "bald":
            return QColor("#f59e0b")
        if state == "gleich":
            return QColor("#ef4444")
        if state == "ende":
            # blinken: halbe Sekunde rot, halbe Sekunde normal
            return QColor("#ef4444") if int(time.monotonic() * 2) % 2 == 0 else self.color
        return self.color

    def paintEvent(self, _event):
        if not stage.is_default_black(self.background):  # eigener Hintergrund: schlicht wie Text
            return super().paintEvent(_event)
        # Standard: Bühne mit Etikett, großen Ziffern, Restzeit-Balken und „Ende um …“
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        stage.paint_background(p, QRectF(self.rect()))
        unit = min(w, h / 0.5625) / 100
        c = self.clock
        countdown = c.mode != "stoppuhr"
        color = self.text_color()
        if unit >= 3:  # in kleinen Szenen-Feldern nur die Zahl
            stage.eyebrow(p, unit * 6, unit * 4, "Timer" if countdown else "Stoppuhr", QColor(stage.ACCENT), unit)
            stage.clock_corner(p, w, unit, time.time())
        shown = self.text()
        area = QRectF(w * 0.06, h * 0.18, w * 0.88, h * 0.5)
        font = stage.font(min(area.height(), w * 0.3), 600)
        fm = QFontMetrics(font)
        template = self.fit_text(shown)
        while fm.horizontalAdvance(template) > area.width() and font.pixelSize() > 10:
            font.setPixelSize(int(font.pixelSize() * 0.92))
            fm = QFontMetrics(font)
        p.setFont(font)
        p.setPen(color)
        p.drawText(area, Qt.AlignCenter, shown)
        if countdown and not c.finished():
            frac = max(0.0, min(1.0, c.remaining() / max(1.0, c.duration)))
            bar = QRectF(w * 0.2, h * 0.74, w * 0.6, max(4.0, h * 0.014))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
            fill = QColor(color if c.urgency() in ("bald", "gleich") else QColor(stage.ACCENT))
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height()),
                              bar.height() / 2, bar.height() / 2)
            if c.running and unit >= 3:
                end = time.strftime("%H:%M", time.localtime(time.time() + c.remaining()))
                p.setPen(QColor(255, 255, 255, 170))
                p.setFont(stage.font(unit * 2.6, 500))
                p.drawText(QRectF(0, bar.bottom() + unit * 2, w, unit * 5), Qt.AlignCenter, f"Ende um {end}")
        elif not c.running and not c.fresh() and not c.finished() and unit >= 3:
            p.setPen(QColor(255, 255, 255, 170))
            p.setFont(stage.font(unit * 2.6, 500))
            p.drawText(QRectF(0, h * 0.74, w, unit * 5), Qt.AlignCenter, "Pause")
        p.end()

    def stop(self):
        self.timer.stop()


class ColorSource(QWidget):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.color = QColor(cfg.get("color", "#000000"))

    def stop(self):
        pass

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.color)
        p.end()


# --------------------------------------------------------------------------- Szene
class SceneSource(QWidget):
    """Setzt mehrere Quellen nach einer Layout-Vorlage zusammen."""

    def __init__(self, cfg, scene_lookup, depth=0, parent=None):
        super().__init__(parent)
        self.children_sources: list[tuple[tuple, QWidget]] = []
        name = cfg.get("scene")
        # „inline“: Szene direkt aus einer Vorlage zeigen, ohne sie zu speichern
        scene = cfg.get("inline") or (scene_lookup(name) if name else None)
        self.background = QColor((scene or {}).get("background", "#000000"))
        if scene is None:
            self._add((0, 0, 1, 1), ColorSource({"color": "#000000"}, self))
            self._add((0, 0.4, 1, 0.2), TextSource({"text": f"Szene „{name}“ fehlt", "size": 40}, self))
            return
        for rect, slot in zip(layout_slots(scene.get("layout", "vollbild")), scene.get("slots", [])):
            if not slot:
                continue
            widget = create_source(slot, scene_lookup, depth + 1, self)
            self._add(rect[:4], widget)

    def _add(self, rect, widget):
        self.children_sources.append((rect, widget))
        widget.show()

    def resizeEvent(self, _event):
        w, h = self.width(), self.height()
        for (x, y, rw, rh), widget in self.children_sources:
            widget.setGeometry(round(x * w), round(y * h), round(rw * w), round(rh * h))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.background)
        p.end()

    def stop(self):
        for _rect, widget in self.children_sources:
            widget.stop()


def video_sources(widget) -> list:
    """Alle Videos in einem Inhalt – auch in Feldern eigener Szenen."""
    return [w for w in media_sources(widget) if isinstance(w, VideoSource)]


def camera_sources(widget) -> list:
    """Alle Kameras in einem Inhalt – auch in Feldern eigener Szenen."""
    if widget is None:
        return []
    if isinstance(widget, SceneSource):
        found = []
        for _rect, child in widget.children_sources:
            found += camera_sources(child)
        return found
    return [widget] if isinstance(widget, CameraSource) and widget.camera is not None else []


def media_sources(widget) -> list:
    """Alle Quellen mit Ton (Video, Website) in einem Inhalt – auch in Szenen-Feldern."""
    if widget is None:
        return []
    if isinstance(widget, SceneSource):
        found = []
        for _rect, child in widget.children_sources:
            found += media_sources(child)
        return found
    return [widget] if hasattr(widget, "set_volume") else []


def _now_playing_source(cfg, parent=None):
    from .now_playing_view import NowPlayingSource

    return NowPlayingSource(cfg, parent)


# --------------------------------------------------------------------------- Fabrik
def create_source(cfg: dict, scene_lookup, depth: int = 0, parent=None) -> QWidget:
    t = (cfg or {}).get("type")
    try:
        if t == "scene":
            if depth >= MAX_SCENE_DEPTH:
                return TextSource({"text": "Szenen zu tief verschachtelt", "size": 10}, parent)
            return SceneSource(cfg, scene_lookup, depth, parent)
        if t == "design":  # gestaltete Seite (screens.py)
            from .screens import DesignSource

            return DesignSource(cfg, parent)
        if t == "whiteboard":
            from .whiteboard import WhiteboardSource

            return WhiteboardSource(cfg, parent)
        if t == "umfrage":
            from .poll_source import PollSource

            return PollSource(cfg, parent)
        if t in ("chatwand", "fotowand"):
            from .guest_sources import ChatWallSource, PhotoWallSource

            return (ChatWallSource if t == "chatwand" else PhotoWallSource)(cfg, parent)
        if t == "spiel":
            from .game_source import GameSource

            return GameSource(cfg, parent)
        if t == "schiffe":
            from .duel_source import DuelSource

            return DuelSource(cfg, parent)
        if t == "zufall":
            from .wheel import WheelSource

            return WheelSource(cfg, parent)
        if t == "wetter":
            from .weather import WeatherSource

            return WeatherSource(cfg, parent)
        if t == "system":
            from .sysinfo_draw import SystemSource

            return SystemSource(cfg, parent)
        if t in ("image", "video", "slideshow"):  # Dual-Boot: Pfad vom anderen System hier finden
            from .platform.shared_paths import resolve_cfg

            cfg = resolve_cfg(cfg)
        factory = {
            "camera": CameraSource,
            "screen": ScreenSource,
            "window": KWinWindowSource if kwin_window_mode() else WindowSource,
            "airplay": AirPlaySource,
            "cast": WifiQrSource,  # früher Link-QR – jetzt nur noch übers AluPC-WLAN
            "wlan": WifiQrSource,
            "wlanhilfe": WifiHelpSource,
            "website": WebsiteSource,
            "image": ImageSource,
            "video": VideoSource,
            "slideshow": SlideshowSource,
            "text": TextSource,
            "clock": ClockSource,
            "countdown": CountdownSource,
            "color": ColorSource,
            "nowplaying": _now_playing_source,
        }.get(t)
        if factory is None:
            return TextSource({"text": f"Unbekannte Quelle: {t}", "size": 8}, parent)
        return factory(cfg, parent)
    except Exception as exc:  # noqa: BLE001 - eine kaputte Quelle darf nicht alles stoppen
        return TextSource({"text": f"Fehler in Quelle ({t}): {exc}", "size": 6}, parent)
