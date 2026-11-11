"""Mauszeiger: Position verfolgen, beim Spiegeln ins Bild zeichnen, auf Monitor 1 festhalten.

* `CursorTracker` meldet die Mausposition (Windows/X11: abfragen; KDE/Wayland: KWin-Skript).
* `paint_cursor` zeichnet den echten Zeiger (Windows/X11) oder einen Standardpfeil.
* `CursorGuard` hält die Maus auf Monitor 1, solange Monitor 2 nicht „Erweitern“ ist.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen

from .platform.linux_display import is_wayland

IS_WINDOWS = sys.platform.startswith("win")


def wayland_kde() -> bool:
    import os

    return sys.platform.startswith("linux") and is_wayland() and \
        "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


# =========================================================================== Position
class CursorTracker(QObject):
    """Meldet Mausbewegungen, solange jemand zuhört (`acquire`/`release`)."""

    moved = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pos: QPoint | None = None
        self.users = 0
        self.method = "keine"
        self._timer = QTimer(self, interval=16)  # ~60 Abfragen/s
        self._timer.timeout.connect(self._poll)
        self._kwin = None

    def acquire(self) -> None:
        self.users += 1
        if self.users == 1:
            self._start()

    def release(self) -> None:
        self.users = max(0, self.users - 1)
        if self.users == 0:
            self._stop()

    def _start(self):
        if is_wayland():
            # Unter Wayland liefert QCursor.pos() nur über eigenen Fenstern etwas Brauchbares
            if wayland_kde():
                try:
                    from .platform.kwin_cursor import CursorReceiver

                    self._kwin = CursorReceiver(self)
                    self._kwin.moved.connect(self._set)
                    self._kwin.start()
                    self.method = "kwin"
                    return
                except Exception:  # noqa: BLE001
                    if self._kwin is not None:
                        self._kwin.stop()  # D-Bus-Verbindung und Thread nicht liegen lassen
                    self._kwin = None
            self.method = "keine"
            return
        self.method = "abfragen"
        self._timer.start()
        self._poll()

    def _stop(self):
        self._timer.stop()
        if self._kwin is not None:
            self._kwin.stop()
            self._kwin = None
        self.method = "keine"

    def _poll(self):
        self._set(QCursor.pos())

    def _set(self, pos: QPoint):
        if pos != self.pos:
            self.pos = QPoint(pos)
            self.moved.emit(self.pos)

    def available(self) -> bool:
        return self.method != "keine"

    def shutdown(self):
        self.users = 0
        self._stop()


_tracker: CursorTracker | None = None


def tracker() -> CursorTracker:
    global _tracker
    if _tracker is None:
        _tracker = CursorTracker()
    return _tracker


# =========================================================================== Zeichnen
ARROW = [(0, 0), (0, 17), (4.2, 13.2), (7, 19.5), (9.6, 18.4), (6.9, 12.2), (12.3, 12.2)]


def paint_arrow(p: QPainter, tip: QPointF, scale: float) -> None:
    """Standard-Mauspfeil (weiß mit schwarzem Rand), Spitze bei `tip`."""
    path = QPainterPath(QPointF(tip.x() + ARROW[0][0] * scale, tip.y() + ARROW[0][1] * scale))
    for x, y in ARROW[1:]:
        path.lineTo(tip.x() + x * scale, tip.y() + y * scale)
    path.closeSubpath()
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0), max(1.0, 1.1 * scale), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(QColor(255, 255, 255))
    p.drawPath(path)
    p.restore()


def paint_cursor(p: QPainter, pos: QPointF, scale: float, device_ratio: float = 1.0) -> None:
    """Echten Mauszeiger (falls abfragbar) oder Pfeil zeichnen. `pos` = Zeigerspitze im Bild,
    `scale` = Vergrößerung Monitor → Bild, `device_ratio` = Bildpunkte je Monitor-Einheit."""
    from .platform.cursor_native import cursor_image

    shape = cursor_image()
    if shape is None:
        paint_arrow(p, pos, scale)
        return
    image, (hx, hy), _key = shape
    k = scale / max(0.1, device_ratio)
    target = QRectF(pos.x() - hx * k, pos.y() - hy * k, image.width() * k, image.height() * k)
    p.save()
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(target, image)
    p.restore()


def map_to_image(pos: QPoint, screen_rect: QRect, image_rect: QRectF) -> QPointF | None:
    """Mausposition auf dem Monitor → Punkt im (eingepassten) Bild dieses Monitors."""
    if not screen_rect.contains(pos) or screen_rect.width() <= 0 or screen_rect.height() <= 0:
        return None
    rx = (pos.x() - screen_rect.x()) / screen_rect.width()
    ry = (pos.y() - screen_rect.y()) / screen_rect.height()
    return QPointF(image_rect.x() + rx * image_rect.width(), image_rect.y() + ry * image_rect.height())


# =========================================================================== Festhalten
GAP = 2000  # KDE/Wayland: Abstand zwischen Monitor 1 und 2 (logische Pixel)
MIN_GAP = 100


def gap_between(a: QRect, b: QRect) -> int:
    """Abstand zweier Monitore in Pixeln (0: sie berühren oder überlappen sich)."""
    return max(0, b.left() - a.right() - 1, a.left() - b.right() - 1,
               b.top() - a.bottom() - 1, a.top() - b.bottom() - 1)


class CursorGuard(QObject):
    """Hält die Maus auf Monitor 1 (Windows: ClipCursor, X11: unsichtbare Wände + Nachkorrektur).

    KDE/Wayland: Programme dürfen die Maus nicht festhalten oder versetzen. KWin lässt sie aber nicht über eine
    Lücke zwischen zwei Monitoren springen – AluPC rückt Monitor 2 darum mit Abstand (`GAP`) weg, sobald die Maus
    auf Monitor 1 ist, und schließt die Lücke bei „Erweitern“ und beim Beenden wieder. Steht die Maus doch auf
    Monitor 2 (z. B. Grafiktablett), geht die Lücke zu, damit sie zurück kann."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.main_rect: QRect | None = None
        self.out_rect: QRect | None = None
        self.main_name = ""
        self._barriers = None
        self.supported = IS_WINDOWS or (sys.platform.startswith("linux") and not is_wayland())
        # Windows setzt die Begrenzung bei vielen Gelegenheiten zurück (Fensterwechsel, Strg+Alt+Entf …),
        # Grafiktablets/Touch umgehen X11-Wände → oft nachsehen und nachkorrigieren
        self._timer = QTimer(self, interval=50)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._enforce)
        self._hook = None
        self._ticks = 0
        self.out_name = ""
        self.display = None  # Monitor-Steuerung (vom Controller) – für die Lücke unter KDE/Wayland
        self._tracking = False
        self._gap_busy = False
        self._gap_want = False

    @property
    def wayland_gap(self) -> bool:
        return wayland_kde() and self.display is not None and self.display.available()

    def has_gap(self) -> bool:
        return self.main_rect is not None and self.out_rect is not None and \
            gap_between(self.main_rect, self.out_rect) >= MIN_GAP

    def set_active(self, on: bool, main_screen=None, out_screen=None) -> None:
        both = main_screen is not None and out_screen is not None and main_screen is not out_screen
        on = bool(on and (self.supported or self.wayland_gap) and both
                  and not main_screen.geometry().intersects(out_screen.geometry()))  # System-Spiegeln
        if both and self.wayland_gap:
            self.main_rect, self.out_rect = main_screen.geometry(), out_screen.geometry()
            self.main_name, self.out_name = main_screen.name(), out_screen.name()
            self._wayland(on)
            return
        if on:
            self.main_rect = main_screen.geometry()
            self.out_rect = out_screen.geometry()
            self.main_name = main_screen.name()
            self.out_name = out_screen.name()
            self._dpr = out_screen.devicePixelRatio()
        if on == self.active and not on:
            return
        self.active = on
        if on:
            self._move_home()
            self._apply()
            self._timer.start()
        else:
            self._timer.stop()
            self._release()

    # ---- KDE/Wayland: Lücke zwischen den Monitoren
    def _wayland(self, on: bool) -> None:
        self.active = on
        if on and not self._tracking:
            t = tracker()
            t.acquire()
            t.moved.connect(self._wayland_pos)
            self._tracking = True
        elif not on and self._tracking:
            t = tracker()
            t.moved.disconnect(self._wayland_pos)
            t.release()
            self._tracking = False
        if not on and (self.has_gap() or self._gap_busy):
            self._set_gap(False)  # „Erweitern“: Maus darf wieder auf Monitor 2 (auch wenn die Lücke gerade entsteht)
        elif on and tracker().pos is not None:
            self._wayland_pos(tracker().pos)

    def _wayland_pos(self, pos: QPoint) -> None:
        if not self.active or self.main_rect is None or self.out_rect is None:
            return
        if self.main_rect.contains(pos) and not self.has_gap():
            self._set_gap(True)
        elif self.out_rect.contains(pos) and self.has_gap():
            self._set_gap(False)  # sonst säße sie auf Monitor 2 fest

    def _set_gap(self, on: bool) -> None:
        self._gap_want = on
        if self._gap_busy:
            return  # läuft gerade – danach wird der zuletzt gewünschte Zustand nachgeholt (sonst blieb z. B. beim
            # Wechsel auf „Erweitern“ die Lücke bestehen und die Maus kam nicht mehr auf Monitor 2)
        from .ui.util import run_async

        self._gap_busy = True
        main, out, display = self.main_name, self.out_name, self.display

        def done(*_):
            self._gap_busy = False
            if self._gap_want != on:
                self._set_gap(self._gap_want)

        run_async(lambda: display.separate(main, out, GAP) if on else display.join(main, out), done, done)

    def _move_home(self):
        """Steht die Maus gerade auf Monitor 2, zurück in die Mitte von Monitor 1."""
        pos = QCursor.pos()
        if self.out_rect is not None and self.out_rect.contains(pos) and self.main_rect is not None:
            QCursor.setPos(self.main_rect.center())

    def _native_out_rect(self):
        r, dpr = self.out_rect, getattr(self, "_dpr", 1.0) or 1.0
        # Qt verschiebt den Ursprung eines Monitors nicht, nur die Größe wird skaliert
        return (r.x(), r.y(), round(r.width() * dpr), round(r.height() * dpr))

    def _apply(self):
        import os

        if os.environ.get("ALUPC_NO_CONFINE"):  # Schalter nur für Fehlersuche in der CI
            return
        try:
            if IS_WINDOWS:
                from .platform.cursor_native import clip_cursor
                from .platform.windows_display import monitor_rect

                rect = monitor_rect(self.main_name)
                if rect:
                    clip_cursor(rect)
                    self._install_hook(rect)
            else:
                from .platform.cursor_native import X11Barriers, is_x11

                if not is_x11():
                    return

                if self._barriers is None:
                    self._barriers = X11Barriers()
                self._barriers.set(self._native_out_rect())
        except Exception:  # noqa: BLE001
            pass

    def _install_hook(self, home):
        from .platform.cursor_native import MouseBlock
        from .platform.windows_display import monitor_rect

        block = monitor_rect(self.out_name)
        if not block:
            return
        if self._hook is None:
            self._hook = MouseBlock()
        # alle ~3 s neu einhängen (falls Windows den Hook still entfernt hat)
        if self._hook.hook is None or self._hook.block != block or self._ticks % 60 == 0:
            self._hook.install(block, home)

    def _enforce(self):
        if not self.active:
            return
        self._ticks += 1
        if IS_WINDOWS:
            self._apply()
        # Sicherheitsnetz für alle Systeme: Maus doch auf Monitor 2 (z. B. per Tastatur verschoben)?
        pos = QCursor.pos()
        if self.out_rect is not None and self.out_rect.contains(pos) and self.main_rect is not None:
            m = self.main_rect
            QCursor.setPos(QPoint(min(max(pos.x(), m.left()), m.right()), min(max(pos.y(), m.top()), m.bottom())))

    def _release(self):
        try:
            if IS_WINDOWS:
                from .platform.cursor_native import clip_cursor

                clip_cursor(None)
                if self._hook is not None:
                    self._hook.uninstall()
            elif self._barriers is not None:
                self._barriers.clear()
        except Exception:  # noqa: BLE001
            pass

    def shutdown(self):
        self._timer.stop()
        if self.wayland_gap and self.has_gap():
            try:  # beim Beenden keine Lücke hinterlassen – sonst käme die Maus nie mehr auf Monitor 2
                self.display.join(self.main_name, self.out_name)
            except Exception:  # noqa: BLE001
                pass
        if self._tracking:
            tracker().moved.disconnect(self._wayland_pos)
            tracker().release()
            self._tracking = False
        if self.active:
            self.active = False
            self._release()


