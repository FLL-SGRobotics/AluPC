"""Eigene, selbst gezeichnete Bausteine: Kacheln, Navigation, Statuskarte, Hinweise, Ring …"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..scenes import layout_slots
from . import icons, theme


def font(size_pt: float | None = None, weight: QFont.Weight = QFont.Normal, head: bool = False) -> QFont:
    """Schrift der Oberfläche (Inter); head=True: Schrift für Titel und Kachelnamen (im Holz-Stil Barlow)."""
    t = theme.current()
    f = QFont(t.head_font) if head or t.matrix else QFont()
    theme.set_features(f, t)
    if size_pt:
        f.setPointSizeF(size_pt * t.font_scale)
    f.setWeight(weight)
    return f


# Zustands-Etiketten kommen intern in Großbuchstaben („LÄUFT“) – angezeigt werden sie in normaler Schreibweise
_LABELS = {"BILD-IN-BILD": "Bild-in-Bild", "KEIN MONITOR": "Kein Monitor", "LIVE": "Live"}


def display_label(text: str) -> str:
    if not text or not text.isupper():
        return text
    return _LABELS.get(text, text[0] + text[1:].lower())


def button(text: str, icon_name: str | None = None, primary: bool = False, danger: bool = False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    if icon_name:
        b.setProperty("iconName", icon_name)  # zum Neu-Einfärben beim Wechsel des Designs
        t = theme.current()
        color = t.on_accent if primary else (t.danger if danger else t.text)
        b.setIcon(icons.icon(icon_name, color, 18))
        b.setIconSize(QSize(18, 18))
    b.setCursor(Qt.PointingHandCursor)
    return b


# Symbol je Seitenkopf (automatisch nach Titel – so bekommen alle Dialoge ein einheitliches Aussehen)
PAGE_ICONS = {"Setup": "sliders", "Meine Szenen": "scenes", "Fingerabdruck": "fingerprint", "Mediathek": "image",
              "Handy auf dem Bildschirm": "phone", "Website anzeigen": "globe", "Programm auf dem Bildschirm": "window",
              "Bildschirmschoner": "moon", "Quelle wählen": "plus", "Startseite anpassen": "edit",
              "Eigene Kachel": "plus", "Fingerabdruck einrichten": "fingerprint", "Neue Szene": "scenes",
              "Szene bearbeiten": "scenes"}


def menu_header(menu, text: str) -> None:
    """Kleine Überschrift in einem Menü (z. B. „Hintergrund“) – gliedert längere Menüs.
    Ein normaler, ausgegrauter Eintrag (kein eingebettetes Widget: das vertragen Taskleisten-Menüs nicht)."""
    action = menu.addAction(display_label(text) if text.isupper() else text)
    action.setEnabled(False)
    action.setData("header")
    action.setFont(font(10.5, QFont.DemiBold, head=True))


def mark_current(action, on: bool) -> None:
    """Aktuelle Auswahl in einem Menü: fett und mit Haken am Ende (auch bei Einträgen mit Symbol sichtbar)."""
    f = action.font()
    f.setBold(bool(on))
    action.setFont(f)
    text = action.text().removesuffix("  ✓")
    action.setText(text + "  ✓" if on else text)


def page_header(title: str, subtitle: str = "", icon_name: str | None = None) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 8)
    row.setSpacing(14)
    icon_name = icon_name or PAGE_ICONS.get(title)
    if icon_name:
        chip = QLabel()
        chip.setObjectName("PageIcon")
        t_ = theme.current()
        chip.setFixedSize(40, 40)
        chip.setAlignment(Qt.AlignCenter)
        color = t_.accent if t_.technical else (t_.text if t_.wood else (t_.muted if t_.regie else t_.on_accent))
        chip.setPixmap(icons.pixmap(icon_name, color, 20))
        row.addWidget(chip, 0, Qt.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("PageTitle")
    col.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("PageSubtitle")
        s.setWordWrap(True)
        col.addWidget(s)
    row.addLayout(col, 1)
    return w


def rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


class HoverMixin:
    """Weiche Hover-Animation (0 → 1) für selbst gezeichnete Knöpfe."""

    def _init_hover(self):
        self._hover = 0.0
        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _get_hover(self):
        return self._hover

    def _set_hover(self, v):
        self._hover = v
        self.update()

    def _animate_hover(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        self._animate_hover(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_hover(0.0)
        super().leaveEvent(e)


class Tile(HoverMixin, QAbstractButton):
    """Kachel der Startseite: Symbol, Name, Untertitel – läuft sie, steht statt des Untertitels der Zustand
    („Läuft“, „An“, „04:12“) in Rot. Form je Stil: Normal = Karte, Technisch = dichte Zeile, Holz = Alu-Platte
    mit Lampe.

    Mit `set_menu(menu, split=True)` öffnet nur der Pfeil oben rechts das Menü; ein Klick auf
    den Rest der Kachel löst `activated` aus.
    """

    activated = Signal()

    def __init__(self, icon_name: str, title: str, subtitle: str = "", color: str | None = None, parent=None):
        super().__init__(parent)
        self.icon_name, self.title, self.subtitle = icon_name, title, subtitle
        self.color = color
        self.active = False
        self.alert = False
        self.badge = ""
        self.cued = False  # Studio-Modus: liegt in der Vorschau (grün markiert)
        self.menu = None
        self.split = False
        self._press_pos = None
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(subtitle)
        self._init_hover()
        self.clicked.connect(self._show_menu)

    hover = Property(float, HoverMixin._get_hover, HoverMixin._set_hover)

    def set_menu(self, menu, split: bool = False):
        theme.round_popup(menu)
        self.menu = menu
        self.split = split
        self.update()

    def menu_zone(self) -> QRectF:
        """Pfeil oben rechts (großzügige Klickfläche)."""
        return QRectF(self.width() - 50, 0, 50, 50)

    def mousePressEvent(self, e):
        self._press_pos = e.position()
        super().mousePressEvent(e)

    def _show_menu(self):
        in_zone = self._press_pos is not None and self.menu_zone().contains(self._press_pos)
        self._press_pos = None
        if self.menu is not None and (not self.split or in_zone):
            self.menu.popup(self.mapToGlobal(self.rect().bottomLeft()))
        else:
            self.activated.emit()

    def set_state(self, active: bool = False, alert: bool = False, badge: str = ""):
        if (active, alert, badge) != (self.active, self.alert, self.badge):
            self.active, self.alert, self.badge = active, alert, badge
            self.update()

    def sizeHint(self):
        t = theme.current()
        base = {"technisch": 92, "holz": 106, "regie": 88}.get(t.kind, 118)
        return QSize(200, round(base * t.density * (0.5 + 0.5 * t.font_scale)))

    def minimumSizeHint(self):
        return QSize(160, self.sizeHint().height())

    def set_cued(self, on: bool) -> None:
        if on != self.cued:
            self.cued = on
            self.update()

    def paintEvent(self, _e):
        style = theme.current().kind
        {"technisch": self._paint_pad, "holz": self._paint_plate, "regie": self._paint_flat}.get(style, self._paint_card)()
        if self.cued and not (self.active or self.alert):
            self._paint_cue()

    def _paint_cue(self) -> None:
        """Grüner Rahmen + Etikett „Vorschau“: ist im Studio-Modus als Nächstes dran."""
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -4)
        green = QColor("#2fd27a")
        p.setPen(QPen(green, 2.2))
        p.setBrush(Qt.NoBrush)
        rad = max(2, t.radii[0] - 1)
        p.drawRoundedRect(r, rad, rad)
        f = font(7.6, QFont.Bold)
        label = "Vorschau"
        tw = QFontMetrics(f).horizontalAdvance(label) + 14
        pill = QRectF(r.right() - tw - 8, r.bottom() - 26, tw, 18)
        p.setPen(Qt.NoPen)
        p.setBrush(green)
        p.drawRoundedRect(pill, 9 if t.style == "normal" else 3, 9 if t.style == "normal" else 3)
        p.setPen(QColor("#062b16"))
        p.setFont(f)
        p.drawText(pill, Qt.AlignCenter, label)
        p.end()

    def _state(self) -> tuple[bool, QColor, str]:
        """(läuft?, Lampenfarbe, Zustand in Worten)."""
        t = theme.current()
        on = self.active or self.alert
        text = display_label(self.badge) if self.badge else ("Fehler" if self.alert else "Läuft")
        return on, QColor(t.warning if self.alert else t.live), text

    @staticmethod
    def _chevron(p: QPainter, cx: float, cy: float, color: QColor) -> None:
        p.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)])

    def _texts(self, p: QPainter, x: float, right: float, base: float, title_font: QFont, sub_size: float,
               dot: bool = True) -> None:
        """Name (Unterkante bei `base`) und darunter Untertitel – oder, wenn es läuft, der Zustand in Rot."""
        t = theme.current()
        on, lamp, state = self._state()
        width = max(10.0, right - x)
        p.setFont(title_font)
        name_rect = QRectF(x, base - 22, width, 22)
        name = QFontMetrics(title_font).elidedText(self.title, Qt.ElideRight, int(width))
        if t.wood:  # Gravur: Lichtkante unter (helles Holz) bzw. Schatten über der Schrift (dunkles Holz)
            p.setPen(QColor(0, 0, 0, 150) if t.dark else QColor(255, 255, 255, 140))
            p.drawText(name_rect.translated(0, -1 if t.dark else 1), Qt.AlignLeft | Qt.AlignBottom, name)
        p.setPen(QColor(t.text))
        p.drawText(name_rect, Qt.AlignLeft | Qt.AlignBottom, name)
        line = QRectF(x, base + 1, width, 18)
        if on:
            sf = font(sub_size, QFont.DemiBold)
            if t.technical and any(ch.isdigit() for ch in state):  # Zeiten/Zahlen als Messwert
                sf = QFont(theme.MONO_FONT)
                sf.setPointSizeF(sub_size - 0.4)
                sf.setBold(True)
            if dot:
                p.setPen(Qt.NoPen)
                p.setBrush(lamp)
                p.drawEllipse(QPointF(x + 3.5, line.center().y()), 3.5, 3.5)
                line = line.adjusted(12, 0, 0, 0)
            p.setFont(sf)
            p.setPen(QColor("#ffffff") if t.technical else lamp)  # Technisch: Pad leuchtet rot → weiße Schrift
            p.drawText(line, Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(sf).elidedText(state, Qt.ElideRight, int(line.width())))
        elif self.subtitle:
            sf = font(sub_size)
            p.setFont(sf)
            p.setPen(QColor(t.muted))
            p.drawText(line, Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(sf).elidedText(self.subtitle, Qt.ElideRight, int(line.width())))

    def _glyph_color(self) -> QColor:
        t = theme.current()
        c = QColor(self.color or t.accent)
        return c.lighter(135) if t.dark else tile_color(c.name())

    def _paint_card(self):
        """Normal: weiche, stark gerundete Karte ohne Rand; oben links das Symbol im getönten Kreis, oben rechts der
        Menüknopf als kleiner runder Knopf, unten Name und Untertitel. Läuft sie: roter Ring."""
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on, lamp, _state = self._state()
        radius = t.radii[0]
        r = r.adjusted(2, 2, -2, -4)  # Platz für Schatten/Leuchten
        lift = 0 if self.isDown() else 2 * self._hover
        r = r.translated(0, -lift)
        if on:  # weiches rotes Leuchten rundherum
            for i, alpha in ((6, 0.06), (4, 0.10), (2, 0.16)):
                glow = QColor(lamp)
                glow.setAlphaF(alpha)
                p.fillPath(rounded(r.adjusted(-i / 2, -i / 2, i / 2, i / 2), radius + i / 2), glow)
        elif self._hover > 0:  # Schatten unter der angehobenen Karte
            shade = QColor(0, 0, 0, int((70 if t.dark else 34) * self._hover))
            p.fillPath(rounded(r.translated(0, 3), radius), shade)
        face = t.mix(t.surface, t.surface2, 0.75 * self._hover + (0.8 if self.isDown() else 0))
        p.fillPath(rounded(r, radius), face)
        if on:
            p.setPen(QPen(lamp, 2.2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(rounded(r.adjusted(1.1, 1.1, -1.1, -1.1), max(0, radius - 1)))
        circle = QRectF(r.left() + 14, r.top() + 14, 40, 40)
        tint = QColor(self.color or t.accent)
        tint.setAlphaF(0.22 if t.dark else 0.14)
        p.setPen(Qt.NoPen)
        p.setBrush(tint)
        p.drawEllipse(circle)
        icons.paint(p, self.icon_name, circle.adjusted(10, 10, -10, -10), self._glyph_color().name(), 2.0)
        if self.menu is not None:
            knob = QRectF(r.right() - 14 - 28, r.top() + 14 + 6, 28, 28)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.mix(t.surface2, t.text, 0.06 + 0.06 * self._hover)))
            p.drawEllipse(knob)
            self._chevron(p, knob.center().x(), knob.center().y(), QColor(t.mix(t.muted, t.text, self._hover)))
        self._texts(p, r.left() + 16, r.right() - 14, r.bottom() - 30, font(11.5, QFont.DemiBold), 9.2)
        p.end()

    def _paint_flat(self):
        """Regie: flache Karte, dunkler als der Grund, 1-px-Rand. Graues Umriss-Symbol ohne Kreis, Name und
        Untertitel. Läuft sie: der Rand wird zur roten 1-px-Linie (wie das Rotlicht an der Kamera) – sonst keine Farbe."""
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        on, lamp, _state = self._state()
        radius = t.radii[0]
        face = QColor(t.surface2) if self.isDown() else t.mix(t.surface, t.surface2, 0.6 * self._hover)
        p.fillPath(rounded(r, radius), face)
        edge = QColor(lamp) if on else t.mix(t.border, "#5a5a60", self._hover)
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(rounded(r, radius))
        icons.paint(p, self.icon_name, QRectF(r.left() + 14, r.center().y() - 9, 18, 18),
                    t.text if (on or self._hover > 0.5) else t.muted, 1.6)
        right = r.right() - 12
        if self.menu is not None:
            self._chevron(p, r.right() - 20, r.center().y(), QColor(t.mix(t.muted, t.text, self._hover)))
            right -= 22
        self._texts(p, r.left() + 44, right, r.center().y() + 1, font(10.5, QFont.DemiBold), 9)
        p.end()

    def _paint_pad(self):
        """Technisch: beleuchtetes Pad wie an einem DJ-Controller – gummierte Fläche in einer Mulde, unten eine
        Lichtleiste in der Farbe der Quelle (heller beim Überfahren). Läuft es: das ganze Pad leuchtet rot."""
        from PySide6.QtGui import QRadialGradient

        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        well = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on, lamp, _state = self._state()
        rw = max(2.0, t.radii[0] * 1.6)
        # Mulde im Pult
        p.fillPath(rounded(well, rw), t.mix(t.bg, "#000000", 0.35 if t.dark else 0.10))
        p.setPen(QPen(QColor(t.border), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(rounded(well, rw))
        pad = well.adjusted(4, 4, -4, -4).translated(0, 1 if self.isDown() else 0)
        rp = max(2.0, rw - 2)
        src = QColor(self.color or t.accent)
        face = QLinearGradient(pad.topLeft(), pad.bottomLeft())
        top = t.mix(t.surface2, "#ffffff", 0.05 + 0.04 * self._hover) if t.dark else t.mix(t.surface2, "#ffffff", 0.5)
        face.setColorAt(0, top)
        face.setColorAt(1, t.mix(t.surface, "#000000", 0.12 if t.dark else 0.03))
        p.fillPath(rounded(pad, rp), face)
        under = QLinearGradient(pad.bottomLeft(), QPointF(pad.left(), pad.top() + pad.height() * 0.35))
        s0, s1 = QColor(src), QColor(src)
        s0.setAlphaF((0.16 + 0.12 * self._hover) if t.dark else 0.10)
        s1.setAlphaF(0.0)
        under.setColorAt(0, s0)
        under.setColorAt(1, s1)
        p.fillPath(rounded(pad, rp), under)  # Hintergrundlicht der Quelle von unten
        if on:  # Pad leuchtet von innen
            glow = QRadialGradient(QPointF(pad.center().x(), pad.bottom()), pad.width() * 0.75)
            g0, g1 = QColor(lamp), QColor(lamp)
            g0.setAlphaF(0.55)
            g1.setAlphaF(0.08)
            glow.setColorAt(0, g0)
            glow.setColorAt(1, g1)
            p.fillPath(rounded(pad, rp), glow)
        # Lichtkante oben, Lichtleiste unten
        p.setPen(QPen(QColor(255, 255, 255, 22 if t.dark else 120), 1))
        p.drawLine(QPointF(pad.left() + rp, pad.top() + 0.5), QPointF(pad.right() - rp, pad.top() + 0.5))
        strip = QRectF(pad.left() + 10, pad.bottom() - 6, pad.width() - 20, 3)
        led = QColor(lamp if on else src)
        led.setAlphaF(1.0 if on else 0.7 + 0.3 * self._hover)
        p.setPen(Qt.NoPen)
        if led.alphaF() > 0:
            halo = QColor(led)
            halo.setAlphaF((0.35 if on else 0.18 + 0.15 * self._hover) * led.alphaF())
            p.setBrush(halo)
            p.drawRoundedRect(strip.adjusted(-3, -3, 3, 3), 3, 3)
        p.setBrush(led)
        p.drawRoundedRect(strip, 1.5, 1.5)
        if on:
            p.setPen(QPen(lamp, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawPath(rounded(pad.adjusted(0.8, 0.8, -0.8, -0.8), rp))
        icons.paint(p, self.icon_name, QRectF(pad.left() + 12, pad.top() + 11, 20, 20),
                    (QColor("#ffffff") if on else self._glyph_color()).name(), 1.9)
        if self.menu is not None:  # „Shift“-Taste oben rechts öffnet das Menü
            key = QRectF(pad.right() - 34, pad.top() + 8, 26, 22)
            p.setPen(QPen(QColor(t.border), 1))
            p.setBrush(QColor(t.mix(t.surface, "#000000", 0.2) if t.dark else t.surface))
            p.drawRoundedRect(key, 3, 3)
            self._chevron(p, key.center().x(), key.center().y(), QColor(t.mix(t.muted, t.text, self._hover)))
        self._texts(p, pad.left() + 12, pad.right() - 12, pad.bottom() - 26, font(10.6, QFont.DemiBold), 8.8,
                    dot=False)
        p.end()

    def _paint_plate(self):
        """Holz: Furnierplatte der gewählten Holzart, Lampe oben links (aus = dunkel, läuft = leuchtet rot),
        Symbol in der Kachelfarbe, Name graviert."""
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -3)
        on, lamp, _state = self._state()
        radius = t.radii[0]
        p.fillPath(rounded(r.translated(0, 2), radius), QColor(0, 0, 0, 80))  # Schatten aufs Holz
        theme.paint_panel(p, rounded(r, radius), r, t, pressed=self.isDown(), hover=self._hover)
        inlay = QColor(t.accent)
        inlay.setAlphaF(0.45 if t.dark else 0.55)
        p.setPen(QPen(inlay, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(rounded(r.adjusted(4.5, 4.5, -4.5, -4.5), max(0, radius - 3)))  # Messing-Einlegeleiste
        paint_led(p, QPointF(r.left() + 17, r.top() + 17), on, lamp)
        right = r.right() - 12
        if self.menu is not None:
            self._chevron(p, right - 5, r.top() + 17, QColor(t.mix(t.muted, t.text, self._hover)))
        icons.paint(p, self.icon_name, QRectF(r.left() + 32, r.top() + 7, 20, 20), self._glyph_color().name(), 2.0)
        tf = font(13, QFont.DemiBold, head=True)
        self._texts(p, r.left() + 14, r.right() - 12, r.bottom() - 26, tf, 9.2, dot=False)
        p.end()


def paint_led(p: QPainter, c: QPointF, on: bool, color: QColor | str, radius: float = 5.0) -> None:
    """Kleine Kontrolllampe: aus = dunkles Glas, an = leuchtet in `color` mit weichem Schein."""
    from PySide6.QtGui import QRadialGradient

    color = QColor(color)
    p.save()
    p.setPen(Qt.NoPen)
    if on:
        glow = QRadialGradient(c, radius * 2.6)
        g0 = QColor(color)
        g0.setAlphaF(0.45)
        g1 = QColor(color)
        g1.setAlphaF(0.0)
        glow.setColorAt(0, g0)
        glow.setColorAt(1, g1)
        p.setBrush(glow)
        p.drawEllipse(c, radius * 2.6, radius * 2.6)
        core = QRadialGradient(QPointF(c.x() - radius * 0.3, c.y() - radius * 0.3), radius * 1.2)
        core.setColorAt(0, color.lighter(170))
        core.setColorAt(1, color)
    else:
        core = QRadialGradient(QPointF(c.x() - radius * 0.3, c.y() - radius * 0.3), radius * 1.2)
        core.setColorAt(0, QColor("#5a5650"))
        core.setColorAt(1, QColor("#26241f"))
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawEllipse(c, radius + 1.2, radius + 1.2)  # Fassung
    p.setBrush(core)
    p.drawEllipse(c, radius, radius)
    p.restore()


def tile_color(color: str) -> QColor:
    """Kachelfarbe, die weiße Schrift gut lesbar trägt (zu helle Farben wie Gelb werden abgedunkelt)."""
    c = QColor(color)
    for _ in range(8):
        lum = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
        if lum <= 0.42:
            break
        c = c.darker(112)
    return c


# --------------------------------------------------------------------------- Navigation
class NavButton(HoverMixin, QAbstractButton):
    def __init__(self, icon_name: str, text: str, parent=None):
        super().__init__(parent)
        self.icon_name, self.text_ = icon_name, text
        self.compact = False
        self.external_indicator = False  # True: die Markierung zeichnet NavIndicator (gleitet zwischen Knöpfen)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(42)
        self._init_hover()

    hover = Property(float, HoverMixin._get_hover, HoverMixin._set_hover)

    def set_compact(self, on: bool) -> None:
        self.compact = on
        self.update()

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 2, -8, -2)
        if self.compact:  # nur Symbol, mittig
            r = QRectF(self.rect().center().x() - 22, 2, 44, self.height() - 4)
        if self.isChecked() and self.external_indicator:
            pass
        elif self.isChecked():
            paint_nav_mark(p, r, t)
        elif self._hover > 0:
            h = QColor(t.text)
            h.setAlphaF(0.05 * self._hover)
            p.fillPath(rounded(r, 3), h)
        on_pill = self.isChecked() and t.style == "normal"
        col = (t.on_accent if on_pill else t.text if (t.wood or t.regie) else t.accent) if self.isChecked() else t.muted
        if self.compact:
            icons.paint(p, self.icon_name, QRectF(r.center().x() - 10, r.center().y() - 10, 20, 20), col, 2.0)
            p.end()
            return
        icons.paint(p, self.icon_name, QRectF(r.left() + 14, r.center().y() - 10, 20, 20), col, 2.0)
        p.setPen(QColor(t.on_accent if on_pill else t.text if self.isChecked() else t.mix(t.text, t.muted, 0.35)))
        p.setFont(font(10.2, QFont.DemiBold if self.isChecked() else QFont.Medium))
        p.drawText(r.adjusted(46, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, self.text_)
        p.end()


class MonitorCard(QWidget):
    """Seitenleiste unten: Monitor 2 als kleines Live-Bild mit Name und Zustand – auf jeder Seite sichtbar."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.name = ""
        self.detail = ""
        self.state = ("", "#22c55e")
        self.icon_name = "monitor"
        self.compact = False
        self.setFixedHeight(166)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Bildschirm – Klick: zur Startseite")

    def set_compact(self, on: bool) -> None:
        """Schmale Seitenleiste: nur kleines Bild mit Zustandspunkt."""
        self.compact = on
        self.setFixedHeight(64 if on else 166)
        self.update()

    def set(self, name: str, detail: str, state: tuple[str, str], icon_name: str):
        self.name, self.detail, self.state, self.icon_name = name, detail, state, icon_name
        self.update()

    def set_image(self, image):
        self.image = image if image is not None and not image.isNull() else None
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(8, 0, -8, -1)
        if self.compact:
            thumb = QRectF(r.left(), r.top() + 6, r.width(), r.width() * 9 / 16)
            path = rounded(thumb, 4)
            p.fillPath(path, QColor("#000000" if self.image is not None else t.surface2))
            if self.image is not None:
                p.save()
                p.setClipPath(path)
                p.drawImage(thumb, self.image)
                p.restore()
            else:  # ohne Live-Bild: Symbol des Inhalts statt leerer Fläche
                s_ = min(18.0, thumb.height() - 8)
                icons.paint(p, self.icon_name, QRectF(thumb.center().x() - s_ / 2, thumb.center().y() - s_ / 2,
                                                      s_, s_), t.muted, 1.6)
            p.setPen(QPen(QColor(t.border), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(QPen(QColor(t.surface), 1.5))
            p.setBrush(QColor(self.state[1]))
            p.drawEllipse(QPointF(thumb.right() - 4, thumb.top() + 4), 4, 4)
            p.end()
            return
        # Karte: flach mit feinem Rand bzw. Alu-Platte (Holz)
        if t.wood:
            theme.paint_panel(p, rounded(r, t.radii[0]), r, t)
        else:
            p.fillPath(rounded(r, t.radii[0]), QColor(t.surface))
            p.setPen(QPen(QColor(t.border), 1))
            p.drawPath(rounded(r.adjusted(0.5, 0.5, -0.5, -0.5), t.radii[0]))
        thumb = QRectF(r.left() + 8, r.top() + 8, r.width() - 16, (r.width() - 16) * 9 / 16)
        path = rounded(thumb, 2)
        p.fillPath(path, QColor("#000000" if self.image is not None else ("#0d0e10" if t.wood else t.bg)))
        if self.image is not None:
            p.save()
            p.setClipPath(path)
            iw, ih = self.image.width(), self.image.height()
            scale = min(thumb.width() / iw, thumb.height() / ih)
            target = QRectF(0, 0, iw * scale, ih * scale)
            target.moveCenter(thumb.center())
            p.drawImage(target, self.image)
            p.restore()
        else:
            s_ = 30
            icons.paint(p, self.icon_name, QRectF(thumb.center().x() - s_ / 2, thumb.center().y() - s_ / 2, s_, s_),
                        t.muted, 1.8)
        label, color = self.state
        if label:  # Zustand als kleines Etikett oben links im Bild
            label = display_label(label)
            f = font(8.5, QFont.DemiBold, head=True)
            p.setFont(f)
            bw = QFontMetrics(f).horizontalAdvance(label) + 20
            badge = QRectF(thumb.left() + 6, thumb.top() + 6, bw, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(badge, 8, 8)
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(badge.left() + 8, badge.center().y()), 3, 3)
            p.setPen(QColor("#ffffff"))
            p.drawText(badge.adjusted(14, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, label)
        y = thumb.bottom() + 6
        p.setPen(QColor(t.text))
        tf = font(11, QFont.DemiBold, head=True)
        p.setFont(tf)
        p.drawText(QRectF(r.left() + 10, y, r.width() - 20, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(tf).elidedText(self.name, Qt.ElideRight, int(r.width() - 20)))
        p.setPen(QColor(t.muted))
        sf = font(8)
        p.setFont(sf)
        p.drawText(QRectF(r.left() + 10, y + 17, r.width() - 20, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(sf).elidedText(self.detail, Qt.ElideRight, int(r.width() - 20)))
        p.end()


# --------------------------------------------------------------------------- Pill / Status
class Pill(QWidget):
    """Kleines farbiges Etikett, z. B. „LIVE“ oder „STANDBILD“."""

    def __init__(self, text: str = "", color: str = "#22c55e", dot: bool = True, parent=None):
        super().__init__(parent)
        self.text_, self.color, self.dot = text, color, dot
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set(self, text: str, color: str):
        self.text_, self.color = text, color
        self.updateGeometry()
        self.update()

    @staticmethod
    def _font() -> QFont:
        return font(9.5 if theme.current().wood else 8.8, QFont.DemiBold, head=True)

    def _label(self) -> str:
        return display_label(self.text_)

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self._label()) + (28 if self.dot else 16), 22)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.color)
        bg = QColor(c)
        bg.setAlphaF(0.16)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        t = theme.current()
        p.fillPath(rounded(r, 2 if t.technical else r.height() / 2), bg)
        x = 9
        if self.dot:
            if t.wood:
                paint_led(p, QPointF(x + 3, r.center().y()), True, c, 3.2)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawEllipse(QPointF(x + 3, r.center().y()), 3.5, 3.5)
            x += 12
        p.setPen(c)
        p.setFont(self._font())
        p.drawText(r.adjusted(x, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, self._label())
        p.end()


class PreviewThumb(QWidget):
    """Kleines Live-Bild von Monitor 2 (abgerundet, mit Rahmen) – oder ein Symbol, wenn nichts läuft."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.icon_name = "monitor"
        self.live = False  # roter Sendungsrahmen: läuft gerade auf dem Bildschirm
        self.setFixedSize(320, 180)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Live-Vorschau vom Bildschirm – Klick öffnet Bild-in-Bild")

    def set(self, image, icon_name: str):
        self.image = image if image is not None and not image.isNull() else None
        self.icon_name = icon_name
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _e):
        """Bild von Monitor 2. Normal: runde Ecken; Technisch: eckig mit feinem Rand; Holz: dunkler Rahmen wie
        ein kleiner Fernseher. Läuft etwas, ist der Rand rot."""
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = {"technisch": 2, "holz": 8, "regie": 6}.get(t.kind, 16)
        if t.wood:  # Gehäuse
            bezel = QLinearGradient(r.topLeft(), r.bottomLeft())
            bezel.setColorAt(0, QColor("#2c2c2e"))
            bezel.setColorAt(1, QColor("#141415"))
            p.fillPath(rounded(r, radius), bezel)
            r = r.adjusted(7, 7, -7, -7)
            radius = 3
        path = rounded(r, radius)
        if self.image is not None:
            p.fillPath(path, QColor("#000000"))
            p.save()
            p.setClipPath(path)
            iw, ih = self.image.width(), self.image.height()
            scale = min(r.width() / iw, r.height() / ih)
            target = QRectF(0, 0, iw * scale, ih * scale)
            target.moveCenter(r.center())
            p.drawImage(target, self.image)
            p.restore()
        else:
            p.fillPath(path, QColor("#0d0e10" if (t.wood or t.dark) else t.surface2))
            s_ = 46
            icons.paint(p, self.icon_name, QRectF(r.center().x() - s_ / 2, r.center().y() - s_ / 2, s_, s_),
                        t.mix(t.muted, t.text, 0.3).name() if not t.wood else "#8d8a84", 1.9)
        width = 2.5 if self.live else 1
        p.setPen(QPen(QColor(t.live) if self.live else QColor(t.border if not t.wood else "#000000"), width))
        p.setBrush(Qt.NoBrush)
        p.drawPath(rounded(r.adjusted(width / 2 - 0.5, width / 2 - 0.5, 0.5 - width / 2, 0.5 - width / 2), radius))
        p.end()


class StatusCard(QWidget):
    """Oben im Hauptfenster: Monitor 2 groß – Live-Bild, was läuft, Schnellschalter (Schwarz, Standbild …)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Hero")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 16, 20, 16)
        lay.setSpacing(20)
        self.preview = PreviewThumb()
        text = QVBoxLayout()
        text.setSpacing(4)
        self.caption = QLabel()
        self.caption.setObjectName("Muted")
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.pills = QHBoxLayout()
        self.pills.setSpacing(6)
        self.since = QLabel()  # Technisch: wie lange der Inhalt schon läuft (echte Zeit seit dem Umschalten)
        self.since.setObjectName("Muted")
        self._since_t0 = 0.0
        self._since_timer = QTimer(self, interval=1000)
        self._since_timer.timeout.connect(self._tick_since)
        pill_row = QHBoxLayout()
        pill_row.setSpacing(10)
        pill_row.addLayout(self.pills)
        pill_row.addWidget(self.since)
        pill_row.addStretch(1)
        # Schnellschalter (setzt das Hauptfenster ein) – brechen bei Bedarf in eine zweite Zeile um
        self._chip_box = QWidget()
        self.chips = FlowLayout(self._chip_box, spacing=8)
        chip_row = QHBoxLayout()
        chip_row.addWidget(self._chip_box, 1)
        text.addStretch(1)
        text.addWidget(self.caption)
        text.addWidget(self.title)
        text.addLayout(pill_row)
        text.addSpacing(8)
        text.addLayout(chip_row)
        text.addStretch(1)
        self.actions = QVBoxLayout()
        self.actions.setSpacing(8)
        self.screens = QVBoxLayout()  # Studio-Modus: Vorschau (StudioPanel) klappt über dem Live-Bild auf
        self.screens.setSpacing(10)
        self.screens.addWidget(self.preview)
        lay.addLayout(self.screens)
        lay.addLayout(text, 1)
        lay.addLayout(self.actions)
        self._lay = lay
        self.studio = None
        self._narrow = False  # vom Hauptfenster: sehr schmales Fenster
        self._dock = 0  # >0: steht im Live-Dock rechts (Breite) – alles untereinander, Schalter als Raster
        self._mode = ""
        self.apply_style()

    def apply_style(self) -> None:
        """Schriften je Stil: Technisch zeigt Ausgang/Auflösung/Hz und die Laufzeit als Messwerte (Mono)."""
        t = theme.current()
        if t.technical:
            mono = QFont(theme.MONO_FONT)
            mono.setPointSizeF(8.6)
            self.caption.setFont(mono)
            self.since.setFont(mono)
            self._since_timer.start()
        else:
            self.caption.setFont(font(9.5))
            self._since_timer.stop()
        self.since.setVisible(t.technical)
        self.title.setFont(self._title_font(self._mode == "schmal"))
        self._tick_since()
        if self.isVisible():  # Stil gewechselt: Größe des Live-Bilds neu wählen
            self._recheck()

    @staticmethod
    def _title_font(narrow: bool) -> QFont:
        t = theme.current()
        size = {"technisch": 15, "holz": 21}.get(t.kind, 17)
        return font(size - (2 if narrow else 0), QFont.DemiBold, head=True)

    def _tick_since(self) -> None:
        import time as _time

        if not self._since_t0:
            self.since.setText("")
            return
        secs = int(_time.monotonic() - self._since_t0)
        self.since.setText(f"läuft seit {secs // 3600:d}:{secs // 60 % 60:02d}:{secs % 60:02d}")

    def add_studio(self, panel: QWidget) -> None:
        """Studio-Modus: Vorschau-Leiste über dem Live-Bild einsetzen (klappt dort auf)."""
        self.studio = panel
        self.screens.insertWidget(0, panel)
        panel.set_width(self.preview.width())

    def set_compact(self, on: bool) -> None:
        """Sehr schmales Fenster: Vorschau oben, Text darunter. Sonst wählt die Karte ihre Form selbst."""
        self._narrow = on
        self._auto()

    def set_docked(self, width: int) -> None:
        """Im Live-Dock (rechts, feste Breite): Vorschau oben über die ganze Breite, darunter Titel, Schalter mit
        Namen in zwei Spalten, Regler. 0 = wieder als breite Karte über den Kacheln."""
        if width == self._dock:
            return
        self._dock = width
        self._mode = ""
        self._auto()

    def minimumSizeHint(self):
        # Nie breiter verlangen als die schmale Form – sonst ragt die Startseite rechts über den Rand,
        # bevor die Karte auf „nur Symbole“ umschalten kann
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 300), hint.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._auto()

    def showEvent(self, e):
        super().showEvent(e)
        self._recheck()
        QTimer.singleShot(0, self._recheck)  # nach dem ersten Layout (Stylesheet angewendet) nochmal

    def _recheck(self):
        self._mode = ""
        self._auto()

    def _chips(self):
        for i in range(self.chips.count()):
            chip = self.chips.itemAt(i).widget()
            if chip is not None:
                if chip.property("full_text") is None:
                    chip.setProperty("full_text", chip.text())
                full = _chip_width(chip)
                old = int(chip.property("full_w") or 0)
                if full > old:
                    chip.setProperty("full_w", full)
                    if old:  # breiter als gedacht → gleich nochmal entscheiden
                        QTimer.singleShot(0, self._recheck)
                yield chip

    def _full_width(self) -> int:
        """So breit wäre die Karte nebeneinander mit beschrifteten Schnellschaltern."""
        chips = list(self._chips())
        widths = [int(c.property("full_w") or 0) or
                  QFontMetrics(c.font()).horizontalAdvance(c.property("full_text") or "") + 64 for c in chips]
        # auf zwei Zeilen verteilt: so breit wie die breitere Zeile
        half = (len(widths) + 1) // 2
        rows = [widths[:half], widths[half:]] if len(widths) > 4 else [widths]
        chip_w = max(sum(r) + self.chips._space * max(0, len(r) - 1) for r in rows if r) if widths else 0
        actions = max((self.actions.itemAt(i).widget().sizeHint().width() for i in range(self.actions.count())
                       if self.actions.itemAt(i).widget() is not None
                       and not self.actions.itemAt(i).widget().isHidden()), default=0)
        m = self._lay.contentsMargins()
        return m.left() + m.right() + self._preview_size(False).width() + self._lay.spacing() * 2 + chip_w \
            + actions + 24  # etwas Luft

    @staticmethod
    def _preview_size(narrow: bool) -> QSize:
        """Live-Bild neben dem Text: Technisch kompakter (dichtes Pult), sonst groß."""
        if narrow:
            return QSize(240, 135)
        return QSize(256, 144) if theme.current().technical else QSize(320, 180)

    def _auto(self) -> None:
        """Breit: nebeneinander, Schalter mit Namen · mittel: nebeneinander, Schalter nur Symbol ·
        schmal: Vorschau oben, Text darunter."""
        if self._dock:
            mode = "dock"
        else:
            need = max(self._full_width(), getattr(self, "_full_min", 0))
            mode = "schmal" if self._narrow else ("voll" if self.width() >= need else "mittel")
        if mode == self._mode:
            return
        self._mode = mode
        narrow = mode == "schmal"
        dock = mode == "dock"
        stacked = narrow or dock
        self._lay.setDirection(QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight)
        self._lay.setAlignment(self.screens, Qt.AlignHCenter if narrow else Qt.Alignment())
        tech = theme.current().technical
        self._lay.setContentsMargins(*((0, 0, 0, 0) if dock else ((12, 10, 14, 10) if tech else (16, 16, 20, 16))))
        self._lay.setSpacing(14 if dock or tech else 20)
        inner = self._dock
        if dock:
            self.preview.setFixedSize(QSize(inner, round(inner * 9 / 16)))
        else:
            self.preview.setFixedSize(self._preview_size(narrow))
        # Dock: Regler links, „Beenden“ rechts unten
        if self.studio is not None:
            self.studio.set_width(self.preview.width())
        self.actions.setDirection(QBoxLayout.RightToLeft if dock else QBoxLayout.TopToBottom)
        for i in range(self.actions.count()):
            w = self.actions.itemAt(i).widget()
            if isinstance(w, QPushButton):
                self.actions.setAlignment(w, Qt.AlignRight | (Qt.AlignBottom if dock else Qt.AlignTop))
        self.title.setFont(self._title_font(narrow))
        labelled = mode in ("voll", "dock")
        half = (inner - self.chips._space) // 2
        for chip in self._chips():  # Schnellschalter: knapp nur Symbol (Name als Tooltip)
            chip.setText(chip.property("full_text") if labelled else "")
            chip.setToolTip(chip.property("full_text"))
            # selbst gerechnete Mindestbreite: Qt vergisst mit Stylesheet den Abstand Symbol–Text
            if dock:  # Raster aus zwei gleich breiten Spalten
                chip.setFixedWidth(max(half, int(chip.property("full_w"))))
            else:
                chip.setMinimumWidth(int(chip.property("full_w")) if mode == "voll" else 0)
                chip.setMaximumWidth(16777215)
        if mode == "voll":
            QTimer.singleShot(0, self._verify_fit)

    def _verify_fit(self) -> None:
        """Nachmessen: Passen die Schalter mit Namen wirklich? Sonst wurden sie abgeschnitten („Bild-in-Bil…“) –
        dann nur Symbole, bis die Karte breiter ist als jetzt."""
        if self._mode != "voll" or not self.isVisible():
            return
        chips = [c for c in self._chips() if c.isVisible()]
        squeezed = any(c.width() < int(c.property("full_w") or 0) - 1 for c in chips)
        touching = any(a.geometry().right() + 4 > b.geometry().left() for a, b in zip(chips, chips[1:])
                       if a.geometry().top() == b.geometry().top())  # nur in derselben Zeile
        rows = len({c.geometry().top() for c in chips})
        squeezed = squeezed or rows > 2  # mehr als zwei Zeilen → lieber nur Symbole
        if squeezed or touching:
            self._full_min = self.width() + 1
            self._mode = ""
            self._auto()

    def set(self, icon_name: str, caption: str, title: str, pills: list[tuple[str, str]]):
        self.preview.icon_name = icon_name
        if self.preview.image is None:
            self.preview.update()
        self.caption.setText(caption)
        self.preview.live = any(text == "LIVE" for text, _c in pills)
        self.preview.update()
        if title != self.title.text():
            import time as _time

            self._since_t0 = _time.monotonic()
            self._tick_since()
        self.title.setText(title)
        # Etiketten wiederverwenden statt neu anlegen (kein Flackern, keine Reste)
        while self.pills.count() < len(pills):
            self.pills.addWidget(Pill())
        for i in range(self.pills.count()):
            pill = self.pills.itemAt(i).widget()
            if i < len(pills):
                pill.set(*pills[i])
                pill.show()
            else:
                pill.hide()

    def pill_texts(self) -> list[str]:
        pills = (self.pills.itemAt(i).widget() for i in range(self.pills.count()))
        return [p.text_ for p in pills if not p.isHidden()]


def _chip_width(chip) -> int:
    """Breite eines Schnellschalters mit Namen: Text (halbfett) + Symbol + Abstand + Innenrand + Rand."""
    f = QFont(chip.font())
    f.setWeight(QFont.DemiBold)
    text = QFontMetrics(f).horizontalAdvance(chip.property("full_text") or chip.text())
    return text + chip.iconSize().width() + 8 + 2 * 14 + 2 + 6  # +6 Luft (Schriftglättung je System)


def _chip_pixmap(icon_name: str, color: str, size: int):

    dpr = 2
    px = QPixmap(size * dpr, size * dpr)
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    icons.paint(p, icon_name, QRectF(size * 0.24, size * 0.24, size * 0.52, size * 0.52), "#ffffff", 2.0)
    p.end()
    return px


# --------------------------------------------------------------------------- Fader (Schieberegler)
class Fader(QSlider):
    """Senkrechter Regler: Wert oben, Name unten. Normal: runde Säule mit Knopf; Technisch: schmale Schiene mit
    Skala 0–100 und Wert in Mono; Holz: Schlitz in der Platte mit Alu-Kappe und gravierter Skala.
    Volle QSlider-Schnittstelle (value, valueChanged …)."""

    def __init__(self, name: str = "", parent=None, icon_name: str = "sound"):
        super().__init__(Qt.Vertical, parent)
        self.name = name
        self.icon_name = icon_name
        self.setRange(0, 100)
        self.setFixedSize(66, 178)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.valueChanged.connect(lambda _v: self.update())

    def _track(self) -> QRectF:
        t = theme.current()
        if t.technical:
            return QRectF(self.width() / 2 + 8 - 2, 26, 4, self.height() - 26 - 32)
        if t.wood:
            return QRectF(self.width() / 2 + 7 - 3, 30, 6, self.height() - 30 - 36)
        return QRectF(self.width() / 2 - 19, 28, 38, self.height() - 28 - 30)

    def _value_at(self, y: float) -> int:
        tr = self._track()
        frac = 1 - (y - tr.top()) / max(1.0, tr.height())
        return round(self.minimum() + max(0.0, min(1.0, frac)) * (self.maximum() - self.minimum()))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or not self.isEnabled():
            return super().mousePressEvent(e)
        self.setSliderDown(True)
        self.setValue(self._value_at(e.position().y()))
        e.accept()

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            self.setValue(self._value_at(e.position().y()))
            e.accept()

    def mouseReleaseEvent(self, e):
        if self.isSliderDown():
            self.setSliderDown(False)
            e.accept()

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isEnabled()
        tr = self._track()
        span = max(1, self.maximum() - self.minimum())
        frac = (self.value() - self.minimum()) / span if on else 0.0
        y = tr.bottom() - frac * tr.height()
        active = self.hasFocus() or self.isSliderDown()
        # Wert oben (ohne Ablesung: Strich statt erfundener Zahl)
        if t.technical:
            vf = QFont(theme.MONO_FONT)
            vf.setPointSizeF(8.6)
            vf.setBold(True)
        else:
            vf = font(10.5 if t.wood else 9.5, QFont.DemiBold, head=True)
        p.setFont(vf)
        p.setPen(QColor(t.text if on else t.muted))
        p.drawText(QRectF(0, 0, self.width(), 22), Qt.AlignCenter, f"{self.value()} %" if on else "–")
        if t.technical or t.wood:  # Skala links neben der Schiene
            sf = QFont(theme.MONO_FONT) if t.technical else font(7.5, QFont.DemiBold, head=True)
            if t.technical:
                sf.setPointSizeF(6.6)
            p.setFont(sf)
            tick = QColor(t.mix(t.border, t.text, 0.35)) if t.technical else QColor(t.muted)
            for i in range(11):
                ty = tr.bottom() - i / 10 * tr.height()
                long_ = i in (0, 5, 10)
                p.setPen(QPen(tick, 1))
                p.drawLine(QPointF(tr.left() - 5 - (7 if long_ else 3), ty), QPointF(tr.left() - 5, ty))
                if long_:
                    p.setPen(QColor(t.muted))
                    label = str(i * 10) if t.technical else str(i)
                    p.drawText(QRectF(0, ty - 6, tr.left() - 14, 12), Qt.AlignRight | Qt.AlignVCenter, label)
        p.setPen(Qt.NoPen)
        if t.wood:  # Schlitz in der Platte
            p.setBrush(QColor("#151515"))
            p.drawRoundedRect(tr, 3, 3)
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.drawLine(QPointF(tr.left(), tr.bottom() + 1), QPointF(tr.right(), tr.bottom() + 1))
            cap = QRectF(tr.center().x() - 14, y - 10, 28, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRoundedRect(cap.translated(0, 2), 3, 3)
            theme.paint_plate(p, rounded(cap, 3), cap, t, pressed=self.isSliderDown())
            p.setPen(QPen(QColor(t.accent) if active else QColor("#1a1a1a"), 1.6))
            p.drawLine(QPointF(cap.left() + 4, cap.center().y()), QPointF(cap.right() - 4, cap.center().y()))
        elif t.technical:
            p.setBrush(QColor(t.border))
            p.drawRect(tr)
            if frac > 0:
                p.setBrush(QColor(t.accent))
                p.drawRect(QRectF(tr.left(), y, tr.width(), tr.bottom() - y))
            cap = QRectF(tr.center().x() - 12, y - 6, 24, 12)
            p.setPen(QPen(QColor(t.accent) if active else QColor(t.mix(t.border, t.text, 0.35)), 1))
            p.setBrush(QColor(t.surface2))
            p.drawRect(cap)
            p.setPen(QPen(QColor(t.text if on else t.muted), 1.4))
            p.drawLine(QPointF(cap.left() + 4, cap.center().y()), QPointF(cap.right() - 4, cap.center().y()))
        else:  # dicke, runde Säule, füllt sich von unten; Symbol unten in der Säule
            pill = rounded(tr, tr.width() / 2)
            p.fillPath(pill, QColor(t.surface2))
            if frac > 0:
                p.save()
                p.setClipPath(pill)
                p.fillRect(QRectF(tr.left(), y, tr.width(), tr.bottom() - y), QColor(t.accent))
                p.restore()
            if active:
                p.setPen(QPen(QColor(t.accent), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawPath(pill)
            s_ = 18
            icon_rect = QRectF(tr.center().x() - s_ / 2, tr.bottom() - s_ - 10, s_, s_)
            covered = frac > 0 and y < icon_rect.center().y()
            icons.paint(p, self.icon_name, icon_rect, t.on_accent if covered else t.muted, 2.0)
        p.setPen(QColor(t.text if on else t.muted))
        p.setFont(font(10 if t.wood else 9, QFont.DemiBold, head=True))
        p.drawText(QRectF(0, self.height() - 24, self.width(), 22), Qt.AlignCenter, self.name)
        p.end()


# --------------------------------------------------------------------------- Hinweis (Toast)
class Toast(QWidget):
    """Kurzer Hinweis unten im Fenster, blendet sich selbst aus."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text_ = ""
        self.kind = "info"
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0)
        self.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setDuration(220)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(lambda: self._fade(0))
        self.hide()

    def show_text(self, text: str, kind: str = "info", ms: int = 4200):
        self.text_, self.kind = text, kind
        f = font(10, QFont.Medium)
        fm = QFontMetrics(f)
        width = min(self.parent().width() - 60, fm.horizontalAdvance(text) + 70)
        lines = max(1, fm.boundingRect(0, 0, width - 60, 1000, Qt.TextWordWrap, text).height() // fm.height())
        self.resize(width, 24 + lines * fm.height())
        self.reposition()
        self.show()
        self.raise_()
        self._fade(1)
        self.timer.start(ms)

    def reposition(self):
        par = self.parent()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 26)

    def _fade(self, target):
        self.anim.stop()
        self.anim.setStartValue(self.effect.opacity())
        self.anim.setEndValue(target)
        if target == 0:
            self.anim.finished.connect(self._hide_once)
        self.anim.start()

    def _hide_once(self):
        self.anim.finished.disconnect(self._hide_once)
        if self.effect.opacity() == 0:
            self.hide()

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.fillPath(rounded(r, t.radii[1]), QColor("#232427"))
        p.setPen(QPen(QColor("#3a3c40"), 1))
        p.drawPath(rounded(r, t.radii[1]))
        color = {"info": t.accent, "warn": t.warning, "error": t.danger, "ok": t.success}.get(self.kind, t.accent)
        p.fillPath(rounded(QRectF(r.left() + 12, r.center().y() - 5, 10, 10), 5), QColor(color))
        p.setPen(QColor("#f1f1ef"))
        p.setFont(font(10, QFont.Medium))
        p.drawText(r.adjusted(34, 0, -14, 0), Qt.AlignVCenter | Qt.TextWordWrap, self.text_)
        p.end()


# --------------------------------------------------------------------------- Fortschrittsring
class NavIndicator(QWidget):
    """Markierung der gewählten Seite in der Seitenleiste – gleitet beim Seitenwechsel weich zum neuen Knopf
    (statt hart umzuspringen). Liegt hinter den Knöpfen; die Knöpfe zeichnen nur noch Symbol und Text."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.compact = False
        self.target = None
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.hide()

    def follow(self, button, animate: bool = True) -> None:
        if button is None:
            self.hide()
            return
        moved = self.target is not button
        self.target = button
        self.compact = getattr(button, "compact", False)
        goal = button.geometry()
        if animate and moved and self.isVisible() and not theme_reduced_motion():
            self._anim.stop()
            self._anim.setStartValue(self.geometry())
            self._anim.setEndValue(goal)
            self._anim.start()
        else:
            self._anim.stop()
            self.setGeometry(goal)
        self.show()
        self.lower()
        self.update()

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 2, -8, -2)
        if self.compact:
            r = QRectF(self.rect().center().x() - 22, 2, 44, self.height() - 4)
        paint_nav_mark(p, r, t)
        p.end()


def paint_nav_mark(p: QPainter, r: QRectF, t) -> None:
    """Markierung der gewählten Seite: Normal getönte Fläche, Technisch zusätzlich Leiste am Rand,
    Holz eine kleine Alu-Platte auf dem Holz."""
    if t.wood:
        p.fillPath(rounded(r.translated(0, 1.5), t.radii[1]), QColor(0, 0, 0, 70))
        theme.paint_panel(p, rounded(r, t.radii[1]), r, t)
        return
    if t.regie:  # Regie: keine Fläche – nur die 1-px-Linie in der Akzentfarbe am linken Rand der Zeile
        p.fillRect(QRectF(r.left(), r.top() + 4, 1, r.height() - 8), QColor(t.accent))
        return
    if t.style == "normal":  # kräftige Pille in der Akzentfarbe, leicht leuchtend
        glow = QColor(t.accent)
        glow.setAlphaF(0.28)
        p.fillPath(rounded(r.adjusted(-1, 1, 1, 3), t.radii[1] + 1), glow)
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0, QColor(t.accent).lighter(112))
        grad.setColorAt(1, QColor(t.accent))
        p.fillPath(rounded(r, t.radii[1]), grad)
        return
    tint = QColor(t.accent)
    tint.setAlphaF(0.16 if t.dark else 0.11)
    p.fillPath(rounded(r, t.radii[1]), tint)
    if t.technical:
        p.fillRect(QRectF(0, r.top() + 6, 3, r.height() - 12), QColor(t.accent))


class AuroraBackdrop(QWidget):
    """Hintergrund des Hauptfensters: Grundfarbe bzw. Holzmaserung (Holz-Stil).
    Name bleibt, damit vorhandene Fenster ihn weiter nutzen."""

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(self.rect())
        theme.paint_backdrop(p, r)
        t = theme.current()
        if t.style == "normal":  # sanftes Leuchten in der Akzentfarbe (oben rechts, unten links)
            from PySide6.QtGui import QRadialGradient

            for cx, cy, rad, alpha in ((r.right() - r.width() * 0.1, r.top(), r.width() * 0.55, 0.16 if t.dark else 0.10),
                                       (r.left(), r.bottom(), r.width() * 0.45, 0.08 if t.dark else 0.06)):
                g = QRadialGradient(QPointF(cx, cy), rad)
                c0 = QColor(t.accent)
                c0.setAlphaF(alpha)
                c1 = QColor(t.accent)
                c1.setAlphaF(0.0)
                g.setColorAt(0, c0)
                g.setColorAt(1, c1)
                p.fillRect(r, g)
        elif t.matrix:  # „Code-Regen“: senkrechte Zeichenketten, unten hell, oben verblassend
            p.drawPixmap(r.topLeft(), _code_rain(int(r.width()), int(r.height())))
        elif t.technical:  # feines Punktraster wie Millimeterpapier
            p.fillRect(r, QBrush(_dot_grid(t)))
        p.end()


_RAIN_CACHE: dict[tuple, QPixmap] = {}


def _code_rain(w: int, h: int) -> QPixmap:
    """Matrix-Hintergrund: Spalten aus Zeichen (Katakana + Ziffern), jede Spalte mit hellem Kopf und
    verblassendem Schweif. Einmal je Größe gezeichnet (fester Zufall) – kostet beim Neuzeichnen nichts."""
    import random

    key = (w // 40, h // 40)
    if key in _RAIN_CACHE:
        return _RAIN_CACHE[key]
    W, H = max(1, key[0] * 40 + 40), max(1, key[1] * 40 + 40)
    px = QPixmap(W, H)
    px.fill(QColor("#010403"))
    q = QPainter(px)
    rng = random.Random(42)
    glyphs = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜ0123456789Z:+-*<>"
    f = QFont(theme.MONO_FONT)
    f.setPixelSize(15)
    q.setFont(f)
    step = 18
    for x in range(4, W, step):
        if rng.random() < 0.35:
            continue  # Lücken – sonst wird es unruhig
        length = rng.randint(6, 26)
        head = rng.randint(0, H // step + length)
        for i in range(length):
            y = (head - i) * step
            if y < -step or y > H:
                continue
            a = 0.55 if i == 0 else 0.28 * (1 - i / length)
            col = QColor("#c8ffd8" if i == 0 else "#00ff66")
            col.setAlphaF(a * 0.55)
            q.setPen(col)
            q.drawText(QRectF(x, y, step, step), Qt.AlignCenter, rng.choice(glyphs))
    q.end()
    if len(_RAIN_CACHE) > 4:
        _RAIN_CACHE.clear()
    _RAIN_CACHE[key] = px
    return px


_DOT_CACHE: dict[tuple, QPixmap] = {}


def _dot_grid(t) -> QPixmap:
    key = (t.text, t.dark)
    if key not in _DOT_CACHE:
        px = QPixmap(22, 22)
        px.fill(Qt.transparent)
        q = QPainter(px)
        c = QColor(t.text)
        c.setAlphaF(0.13 if t.dark else 0.14)
        q.fillRect(QRectF(0, 0, 1, 1), c)
        q.end()
        _DOT_CACHE[key] = px
    return _DOT_CACHE[key]


class ProgressRing(QWidget):
    """Ring mit Fingerabdruck in der Mitte; Farbe zeigt Erfolg/Fehler."""

    def __init__(self, icon_name: str = "fingerprint", parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.value = 0.0  # 0..1; <0 = unbestimmt (dreht sich)
        self.state = "busy"  # busy | ok | error
        self._spin = 0
        self._timer = QTimer(self, interval=33)  # dreht nur, solange sichtbar und „busy“ (siehe show/hide)
        self._timer.timeout.connect(self._tick)
        self._fx = 1.0  # 0 → 1: Erfolg „ploppt“ auf, Fehler schüttelt den Ring
        self._fx_anim = QPropertyAnimation(self, b"fx", self)
        self._fx_anim.setDuration(520)
        self.setFixedSize(150, 150)

    def _get_fx(self):
        return self._fx

    def _set_fx(self, v):
        self._fx = float(v)
        self.update()

    fx = Property(float, _get_fx, _set_fx)

    def showEvent(self, e):
        if self.value < 0 and self.state == "busy":
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        self._spin = (self._spin + 8) % 360
        if self.value < 0 and self.state == "busy":
            self.update()

    def set_progress(self, value: float):
        self.value = value
        if value < 0 and self.state == "busy":
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def restart(self):
        """Zurück auf „arbeitet …“ (drehender Ring)."""
        self.state = "busy"
        self.set_progress(-1)

    def set_state(self, state: str):
        changed = state != self.state
        self.state = state
        if state != "busy":
            self._timer.stop()
            if changed and not theme_reduced_motion():
                self._fx_anim.stop()
                self._fx_anim.setEasingCurve(QEasingCurve.OutCubic if state == "ok" else QEasingCurve.Linear)
                self._fx_anim.setStartValue(0.0)
                self._fx_anim.setEndValue(1.0)
                self._fx_anim.start()
            else:
                self._fx = 1.0
        self.update()

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(9, 9, -9, -9)
        fx = self._fx
        if self.state == "error" and fx < 1:  # Kopfschütteln: kurz links-rechts, wird schnell ruhiger
            p.translate(math.sin(fx * math.pi * 6) * 9 * (1 - fx), 0)
        p.setPen(QPen(QColor(t.surface2), 9, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(r)
        color = {"ok": t.success, "error": t.danger}.get(self.state, t.accent)
        p.setPen(QPen(QColor(color), 9, Qt.SolidLine, Qt.RoundCap))
        if self.state == "ok" and fx < 1:  # Erfolg: Ring schließt sich, danach ein weicher Lichtring nach außen
            p.drawArc(r, 90 * 16, int(-360 * 16 * min(1.0, fx * 1.6)))
        elif self.state != "busy":
            p.drawEllipse(r)
        elif self.value < 0:
            p.drawArc(r, int((90 - self._spin) * 16), int(-90 * 16))
        elif self.value > 0:
            p.drawArc(r, 90 * 16, int(-360 * 16 * min(1.0, self.value)))
        if self.state == "ok" and 0.25 < fx < 1:
            glow = QColor(color)
            glow.setAlphaF(0.45 * (1 - fx))
            p.setPen(QPen(glow, 3))
            grow = 6 + 3 * fx  # Lichtring außen am Ring, wandert nach außen und verblasst
            p.drawEllipse(r.adjusted(-grow, -grow, grow, grow))
        name = {"ok": "check", "error": "x"}.get(self.state, self.icon_name)
        inner = r.adjusted(34, 34, -34, -34)
        if self.state == "ok" and fx < 1:  # Haken „ploppt“ auf (etwas zu groß, dann zurück)
            k = 0.6 + 0.4 * QEasingCurve(QEasingCurve.OutBack).valueForProgress(min(1.0, fx * 1.4))
            c = inner.center()
            inner = QRectF(c.x() - inner.width() * k / 2, c.y() - inner.height() * k / 2,
                           inner.width() * k, inner.height() * k)
        icons.paint(p, name, inner, color, 1.8)
        if self.state == "busy" and self.value < 0 and self.icon_name == "fingerprint":
            # Scan-Strahl fährt über den Fingerabdruck – „ich warte auf deinen Finger“
            phase = (math.sin(math.radians(self._spin) * 2) + 1) / 2
            y = inner.top() + inner.height() * phase
            beam = QLinearGradient(inner.left(), 0, inner.right(), 0)
            edge = QColor(color)
            edge.setAlphaF(0.0)
            mid = QColor(color)
            mid.setAlphaF(0.85)
            beam.setColorAt(0, edge)
            beam.setColorAt(0.5, mid)
            beam.setColorAt(1, edge)
            p.setPen(QPen(QBrush(beam), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(inner.left() - 6, y), QPointF(inner.right() + 6, y))
        p.end()


# --------------------------------------------------------------------------- Szenen-Vorschau
def paint_scene_thumb(p: QPainter, rect: QRectF, scene: dict | None, layout: str | None = None,
                      numbers: bool = False):
    """Schematische Vorschau einer Szene: Felder in der Farbe ihrer Quelle, mit Symbol."""
    t = theme.current()
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    # ruhiger Rahmen statt hartem Schwarz: Szenen-Hintergrund leicht in die Kartenfarbe gemischt
    bg = QColor((scene or {}).get("background", "#000000")) if scene else QColor(t.surface2)
    bg = t.mix(bg.name(), t.surface2, 0.35 if scene else 0.5)
    outer = {"normal": 12, "holz": 4}.get(t.style, 2)
    inner = {"normal": 8, "holz": 3}.get(t.style, 2)
    p.fillPath(rounded(rect, outer), bg)
    slots = (scene or {}).get("slots", [])
    lay = layout or (scene or {}).get("layout", "vollbild")
    for i, (x, y, w, h, _name) in enumerate(layout_slots(lay)):
        cell = QRectF(rect.left() + x * rect.width(), rect.top() + y * rect.height(),
                      w * rect.width(), h * rect.height()).adjusted(3, 3, -3, -3)
        slot = slots[i] if i < len(slots) else None
        if slot:
            color = QColor(theme.SOURCE_COLORS.get(slot.get("type"), t.muted))
            p.fillPath(rounded(cell, inner), color)
            side = min(cell.width(), cell.height()) * 0.45
            if side >= 8:
                icons.paint(p, icons.SOURCE_ICONS.get(slot.get("type"), "monitor"),
                            QRectF(cell.center().x() - side / 2, cell.center().y() - side / 2, side, side),
                            "#ffffff", 2.2)
        else:
            palette = ["#3b82f6", "#f59e0b", "#10b981", "#ec4899"]
            color = QColor(palette[i % 4]) if numbers else QColor(t.muted)
            fill = QColor(color)
            fill.setAlphaF(0.85 if numbers else 0.18)
            p.fillPath(rounded(cell, 7), fill)
            if numbers and min(cell.width(), cell.height()) > 12:
                p.setPen(QColor("#ffffff"))
                p.setFont(font(9, QFont.Bold))
                p.drawText(cell, Qt.AlignCenter, str(i + 1))
            elif not numbers:
                pen = QPen(QColor(t.muted), 1, Qt.DashLine)
                p.setPen(pen)
                p.drawPath(rounded(cell, 7))
    p.restore()


class SceneCard(HoverMixin, QAbstractButton):
    double_clicked = Signal()

    def __init__(self, scene: dict, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.live = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(236, 200)
        self._init_hover()

    hover = Property(float, HoverMixin._get_hover, HoverMixin._set_hover)

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(e)

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        rad = t.radii[0]
        if t.wood:
            theme.paint_panel(p, rounded(r, rad), r, t, hover=self._hover)
        else:
            p.fillPath(rounded(r, rad), t.mix(t.surface, t.surface2, 0.7 * self._hover))
        line = QColor(t.accent) if self.isChecked() else QColor("transparent" if t.style == "normal" else t.border)
        p.setPen(QPen(line, 2 if self.isChecked() else 1))
        p.drawPath(rounded(r, rad))
        thumb = QRectF(r.left() + 12, r.top() + 12, r.width() - 24, (r.width() - 24) * 9 / 16)
        paint_scene_thumb(p, thumb, self.scene)
        p.setPen(QColor(t.text))
        p.setFont(font(11, QFont.DemiBold))
        fm = QFontMetrics(p.font())
        name = fm.elidedText(self.scene["name"], Qt.ElideRight, int(r.width() - 24))
        p.drawText(QRectF(r.left() + 12, thumb.bottom() + 8, r.width() - 24, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   name)
        filled = sum(1 for s in self.scene.get("slots", []) if s)
        p.setPen(QColor(t.muted))
        p.setFont(font(9))
        p.drawText(QRectF(r.left() + 12, thumb.bottom() + 30, r.width() - 24, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{filled} Quelle{'n' if filled != 1 else ''}")
        if self.live:
            paint_live_pill(p, QRectF(thumb.right() - 56, thumb.top() + 8, 48, 20))
        elif getattr(self, "cued", False):  # Studio-Modus: liegt in der Vorschau
            p.setPen(QPen(QColor("#2fd27a"), 2.2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(rounded(r.adjusted(1, 1, -1, -1), max(0, rad - 1)))
        p.end()


def paint_live_pill(p: QPainter, pill: QRectF) -> None:
    """Kleines rotes Etikett „Live“ (Rot heißt nur: läuft gerade auf dem Bildschirm)."""
    t = theme.current()
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(t.live))
    rad = min(pill.height() / 2, max(2.0, t.radii[1] * pill.height() / 34))
    p.drawRoundedRect(pill, rad, rad)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(pill.left() + 10, pill.center().y()), 3, 3)
    p.setPen(QColor("#ffffff"))
    p.setFont(font(8, QFont.Bold))
    p.drawText(pill.adjusted(14, 0, 0, 0), Qt.AlignCenter, "Live")
    p.restore()


class SceneThumb(QWidget):
    """Große Vorschau der gewählten Szene (Szenen-Seite, rechts): Felder in der Farbe ihrer Quelle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene: dict | None = None
        self.live = False
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, scene: dict | None, live: bool = False) -> None:
        self.scene, self.live = scene, live
        self.update()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return round(w * 9 / 16)

    def sizeHint(self):
        return QSize(320, 180)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = self.heightForWidth(self.width())
        if self.height() != h:
            self.setFixedHeight(h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        paint_scene_thumb(p, r, self.scene)
        if self.live:
            t = theme.current()
            p.setPen(QPen(QColor(t.live), 2.5))
            p.setBrush(Qt.NoBrush)
            rad = {"normal": 12, "holz": 4}.get(t.style, 2)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad, rad)
            paint_live_pill(p, QRectF(r.right() - 60, r.top() + 10, 50, 22))
        p.end()


class NewSceneCard(HoverMixin, QAbstractButton):
    """Letzte Karte im Szenen-Raster: gestrichelter Rahmen mit Plus – „Neue Szene“."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("Neue Szene")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(236, 200)
        self.setToolTip("Neue Szene")
        self._init_hover()

    hover = Property(float, HoverMixin._get_hover, HoverMixin._set_hover)

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        rad = t.radii[0]
        fill = QColor(t.accent)
        fill.setAlphaF(0.05 + 0.07 * self._hover)
        p.fillPath(rounded(r, rad), fill)
        line = t.mix(t.border, t.accent, 0.4 + 0.6 * self._hover)
        p.setPen(QPen(line, 1.5, Qt.DashLine))
        p.drawPath(rounded(r, rad))
        c = QPointF(r.center().x(), r.center().y() - 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.accent))
        p.drawEllipse(c, 22, 22)
        icons.paint(p, "plus", QRectF(c.x() - 11, c.y() - 11, 22, 22), t.on_accent, 2.4)
        p.setPen(QColor(t.text))
        p.setFont(font(10.5, QFont.DemiBold))
        p.drawText(QRectF(r.left(), c.y() + 30, r.width(), 22), Qt.AlignCenter, "Neue Szene")
        p.end()


class EmptyState(QWidget):
    """Hinweis, wenn eine Liste leer ist (z. B. noch keine Szene)."""

    def __init__(self, icon_name: str, title: str, text: str, action: QPushButton | None = None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(_chip_pixmap(icon_name, theme.current().accent, 64))
        ic.setAlignment(Qt.AlignCenter)
        tl = QLabel(title)
        tl.setFont(font(14, QFont.Bold))
        tl.setAlignment(Qt.AlignCenter)
        tx = QLabel(text)
        tx.setObjectName("Muted")
        tx.setAlignment(Qt.AlignCenter)
        tx.setWordWrap(True)
        lay.addWidget(ic)
        lay.addWidget(tl)
        lay.addWidget(tx)
        if action is not None:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(action)
            row.addStretch(1)
            lay.addLayout(row)


class Banner(QWidget):
    """Hinweiszeile mit farbigem Symbol (ok / warn / info / busy)."""

    def __init__(self, text: str = "", kind: str = "info", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(36, 36)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        lay.addWidget(self.label, 1)
        self.set(text, kind)

    def set(self, text: str, kind: str = "info"):
        t = theme.current()
        color, name = {"ok": (t.success, "check"), "warn": (t.warning, "alert"), "error": (t.danger, "x"),
                       "busy": (t.accent, "refresh")}.get(kind, (t.accent, "info"))
        self.icon.setPixmap(_chip_pixmap(name, color, 36))
        self.label.setText(text)


class SectionHeader(QWidget):
    """Überschrift eines Startseiten-Bereichs: Pfeil, Name, Anzahl, Linie – Klick klappt ein/aus."""

    toggled = Signal(bool)  # True = eingeklappt

    def __init__(self, name: str, count: int = 0, collapsed: bool = False, tiles=None, parent=None):
        super().__init__(parent)
        self.name, self.count, self.collapsed = name, count, collapsed
        self.tiles = list(tiles or [])  # (Symbol, Farbe) – eingeklappt als Mini-Symbole zu sehen
        self._hover = False
        self.setFixedHeight(34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Klick: ein-/ausklappen")
        self.setAttribute(Qt.WA_Hover, True)

    def set(self, name: str, count: int, collapsed: bool) -> None:
        self.name, self.count, self.collapsed = name, count, collapsed
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.collapsed = not self.collapsed
            self.update()
            self.toggled.emit(self.collapsed)

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        if self._hover:
            hov = QColor(t.text)
            hov.setAlphaF(0.07)
            p.fillPath(rounded(QRectF(self.rect()).adjusted(0, 2, 0, -2), t.radii[1]), hov)
        # Pfeil: ▾ offen, ▸ eingeklappt
        cx, cy, s = 14.0, h / 2, 4.5
        pen = QPen(QColor(t.accent if self._hover else t.muted), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        if self.collapsed:
            p.drawPolyline([QPointF(cx - s / 2, cy - s), QPointF(cx + s / 2, cy), QPointF(cx - s / 2, cy + s)])
        else:
            p.drawPolyline([QPointF(cx - s, cy - s / 2), QPointF(cx, cy + s / 2), QPointF(cx + s, cy - s / 2)])
        f = font({"holz": 13, "normal": 12.5}.get(t.style, 11), QFont.Bold if t.style == "normal" else QFont.DemiBold,
                 head=True)
        p.setFont(f)
        x = 30
        if t.technical:  # Kennzeichnung wie auf Geräten: kleines Quadrat in Signalgelb
            p.fillRect(QRectF(x, h / 2 - 4, 8, 8), QColor(t.accent))
            x += 16
        p.setPen(QColor(t.text))
        label = display_label(self.name)
        width = QFontMetrics(f).horizontalAdvance(label)
        p.drawText(QRectF(x, 0, width + 2, h), Qt.AlignVCenter | Qt.AlignLeft, label)
        x += width + 8
        cf = font(9)
        count = str(self.count)
        p.setFont(cf)
        p.setPen(QColor(t.muted))
        cw = QFontMetrics(cf).horizontalAdvance(count)
        p.drawText(QRectF(x, 0, cw + 2, h), Qt.AlignVCenter | Qt.AlignLeft, count)
        pill = QRectF(x, h / 2 - 9, cw, 18)
        x = pill.right() + 12
        # eingeklappt: die Kacheln als kleine farbige Symbole (bis 10)
        if self.collapsed and self.tiles:
            size = 22
            for icon_name, color in self.tiles[:10]:
                if x + size > self.width() - 40:
                    break
                bg = QColor(color or t.accent)
                bg.setAlphaF(0.2)
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(QRectF(x, h / 2 - size / 2, size, size), 4, 4)
                icons.paint(p, icon_name, QRectF(x + 4, h / 2 - size / 2 + 4, size - 8, size - 8), color or t.accent,
                            1.8)
                x += size + 5
            x += 7
        p.end()


def fade_in(widget, ms: int = 220, delay: int = 0, dy: int = 0) -> None:
    """Weiches Einblenden (optional leicht von unten hochgleitend). Der Effekt wird danach wieder entfernt –
    Grafikeffekte kosten sonst dauerhaft Leistung (und vertragen sich nicht mit Web-Ansichten)."""
    from PySide6.QtCore import QParallelAnimationGroup, QPoint, QSequentialAnimationGroup

    if widget is None or not widget.isVisible() or theme_reduced_motion():
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setDuration(ms)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.OutCubic)
    group = QParallelAnimationGroup(widget)
    group.addAnimation(fade)
    if dy and widget.parentWidget() is not None and widget.parentWidget().layout() is None:
        end = widget.pos()
        slide = QPropertyAnimation(widget, b"pos", widget)
        slide.setDuration(ms + 60)
        slide.setStartValue(end + QPoint(0, dy))
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.OutBack)
        group.addAnimation(slide)
    seq = QSequentialAnimationGroup(widget)
    if delay:
        seq.addPause(delay)
    seq.addAnimation(group)

    def done():
        try:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    seq.finished.connect(done)
    seq.start(QPropertyAnimation.DeleteWhenStopped)


def theme_reduced_motion() -> bool:
    """Animationen aus (Einstellung „Animationen“ oder Test-/Offscreen-Umgebung ohne echten Bildschirm)."""
    import os

    from .. import perf

    return os.environ.get("ALUPC_NO_ANIMATION") == "1" or perf._mode == "sparsam"


def animate_height(widget, show: bool, ms: int = 240) -> None:
    """Ein-/Ausklappen mit Animation (maximale Höhe gleitet)."""
    if theme_reduced_motion():
        widget.setVisible(show)
        return
    start = widget.height() if widget.isVisible() else 0
    target = widget.sizeHint().height() if show else 0
    widget.setMaximumHeight(start)
    widget.setVisible(True)
    anim = QPropertyAnimation(widget, b"maximumHeight", widget)
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(max(target, 1) if show else 0)
    anim.setEasingCurve(QEasingCurve.OutCubic if show else QEasingCurve.InCubic)

    def done():
        try:
            widget.setMaximumHeight(16777215)
            widget.setVisible(show)
        except RuntimeError:
            pass

    anim.finished.connect(done)
    anim.start(QPropertyAnimation.DeleteWhenStopped)


class FlowLayout(QLayout):
    """Wie eine Zeile – bricht aber um, wenn der Platz nicht reicht (Knopfreihen, Farbfelder im kleinen Fenster)."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802 (Qt-Name)
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _place(self, rect, apply: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = r.x(), r.y(), 0
        for item in self._items:
            if item.widget() is not None and item.widget().isHidden():  # nur ausdrücklich versteckte überspringen
                continue
            hint = item.sizeHint()
            if x + hint.width() > r.right() + 1 and line > 0:
                x, y, line = r.x(), y + line + self._space, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._space
            line = max(line, hint.height())
        return y + line - rect.y() + m.bottom()


def flow_row(*widgets, spacing: int = 8) -> QWidget:
    """Widget mit umbrechender Reihe aus `widgets`."""
    w = QWidget()
    lay = FlowLayout(w, spacing)
    for x in widgets:
        lay.addWidget(x)
    return w


class Telemetry(QWidget):
    """Messwerte des PCs im Live-Dock (Stil „Technisch“): Prozessor, Speicher, Temperatur – echte Werte aus
    `sysinfo.monitor()` mit Verlauf der letzten Sekunden. Misst nur, solange es zu sehen ist."""

    ROWS = (("cpu", "Prozessor", "%"), ("ram", "Speicher", "%"), ("temp", "Temperatur", "°C"))

    def __init__(self, parent=None):
        super().__init__(parent)
        from .. import sysinfo

        self._mon = sysinfo.monitor()
        self._mon.updated.connect(lambda _s: self.update())
        self.setMinimumHeight(66)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(300, 66)

    def available(self) -> bool:
        return self._mon.available

    def showEvent(self, e):
        super().showEvent(e)
        self._mon.acquire(self)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._mon.release(self)

    def values(self) -> list[tuple[str, str, list[float]]]:
        """(Name, Wert als Text, Verlauf) – „–“, solange es keinen Messwert gibt (z. B. keine Temperatur)."""
        snap, hist = self._mon.snapshot, self._mon.history
        out = []
        for key, name, unit in self.ROWS:
            if key == "temp":
                v = snap.cpu_temp if snap.cpu_temp is not None else (snap.gpu.temp if snap.gpu else None)
            else:
                v = getattr(snap, key) if snap.time else None
            text = "–" if v is None else f"{v:.0f} {unit}"
            out.append((name, text, list(hist[key])[-40:]))
        return out

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rows = self.values()
        gap = 8
        w = (self.width() - gap * (len(rows) - 1)) / len(rows)
        r = t.radii[2]
        for i, (name, text, hist) in enumerate(rows):
            cell = QRectF(i * (w + gap), 0, w, self.height() - 1)
            p.setPen(QPen(QColor(t.border), 1))
            p.setBrush(QColor(t.mix(t.bg, t.surface, 0.5)))
            p.drawRoundedRect(cell.adjusted(0.5, 0.5, -0.5, -0.5), r, r)
            inner = cell.adjusted(9, 7, -9, -7)
            p.setPen(QColor(t.muted))
            p.setFont(font(7.6))
            p.drawText(QRectF(inner.x(), inner.y(), inner.width(), 14), Qt.AlignLeft | Qt.AlignVCenter, name)
            mono = QFont(theme.MONO_FONT)
            mono.setPointSizeF(t.pt(11))
            mono.setWeight(QFont.DemiBold)
            p.setFont(mono)
            p.setPen(QColor(t.text))
            p.drawText(QRectF(inner.x(), inner.y() + 14, inner.width(), 20), Qt.AlignLeft | Qt.AlignVCenter, text)
            # LED-Kette wie ein Pegel am Pult: grün → gelb → rot
            top = 100.0
            value = hist[-1] if hist else 0.0
            area = QRectF(inner.x(), inner.bottom() - 9, inner.width(), 9)
            paint_led_bar(p, area, value / top if text != "–" else 0.0, segments=14)
        p.end()


def paint_led_bar(p: QPainter, area: QRectF, level: float, segments: int = 16, peak: float | None = None) -> None:
    """Pegel als LED-Kette (aus = dunkel): bis 60 % grün, bis 85 % gelb, darüber rot."""
    t = theme.current()
    gap = 2.0
    seg_w = (area.width() - gap * (segments - 1)) / segments
    lit = round(max(0.0, min(1.0, level)) * segments)
    peak_i = None if not peak or peak < 0.01 else min(segments - 1, int(max(0.0, min(1.0, peak)) * segments))
    p.save()
    p.setPen(Qt.NoPen)
    for i in range(segments):
        frac = (i + 1) / segments
        color = QColor("#2fd27a" if frac <= 0.6 else ("#f5c400" if frac <= 0.85 else t.live))
        if i >= lit and i != peak_i:
            color = t.mix(t.bg, color.name(), 0.18)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(area.x() + i * (seg_w + gap), area.y(), seg_w, area.height()), 1.2, 1.2)
    p.restore()


class LevelMeter(QWidget):
    """Echter Pegel des PC-Tons (Standard-Ausgang) als LED-Kette – nur gemessen, solange sichtbar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._meters = None
        self.level = 0.0
        self.peak = 0.0
        self._peak_at = 0.0
        self._timer = QTimer(self, interval=50)
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(40)
        self.setToolTip("Was gerade aus dem PC kommt")

    def showEvent(self, e):
        super().showEvent(e)
        from .. import mixer_meters

        if self._meters is None:
            self._meters = mixer_meters.Meters()
        self._meters.set_channels([{"key": "pc", "kind": "out", "dev": mixer_meters.DEFAULT_OUT,
                                    "stream": None}])
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()
        if self._meters is not None:
            self._meters.stop()

    def _tick(self):
        import time as _time

        raw = self._meters.peak("pc") if self._meters is not None else 0.0
        self.level = max(raw, self.level * 0.82)  # schnell hoch, langsam runter
        now = _time.monotonic()
        if raw >= self.peak or now - self._peak_at > 1.2:
            self.peak, self._peak_at = raw, now
        self.update()

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(t.muted))
        p.setFont(font(7.6))
        p.drawText(QRectF(0, 0, self.width(), 14), Qt.AlignLeft | Qt.AlignVCenter, "Ton vom PC")
        mono = QFont(theme.MONO_FONT)
        mono.setPointSizeF(t.pt(8))
        p.setFont(mono)
        db = "–∞ dB" if self.level < 0.001 else f"{20 * math.log10(self.level):.0f} dB"
        p.drawText(QRectF(0, 0, self.width(), 14), Qt.AlignRight | Qt.AlignVCenter, db)
        paint_led_bar(p, QRectF(0, 20, self.width(), 14), self.level, segments=28, peak=self.peak)
        p.end()


class CommandBar(QPushButton):
    """Große Befehlsleiste oben auf der Startseite: öffnet die Befehlssuche (Strg+K). Rechts die Taste als Hinweis."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("CommandBar")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("iconName", "search")
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def minimumSizeHint(self):
        return QSize(120, 46)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.width() < 360:
            return
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = font(8.2, QFont.DemiBold)
        p.setFont(f)
        x = self.width() - 14
        for key in reversed(("Strg", "K")):
            kw = QFontMetrics(f).horizontalAdvance(key) + 14
            cap = QRectF(x - kw, (self.height() - 24) / 2, kw, 24)
            p.setPen(QPen(QColor(t.border), 1))
            p.setBrush(QColor(t.surface2))
            p.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), min(6, t.radii[2]), min(6, t.radii[2]))
            p.setPen(QColor(t.muted))
            p.drawText(cap, Qt.AlignCenter, key)
            x -= kw + 5
        p.end()


def searchable(combo, placeholder: str = "Tippen zum Suchen …"):
    """Dropdown mit Suche: tippen filtert die Einträge (Teilwort, egal ob groß/klein), Auswahl wie gewohnt –
    löst „activated“ aus. Getippter Text, der zu nichts passt, springt zurück auf den gewählten Eintrag."""
    from PySide6.QtWidgets import QComboBox, QCompleter

    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    edit = combo.lineEdit()
    edit.setPlaceholderText(placeholder)
    comp = combo.completer()
    comp.setFilterMode(Qt.MatchContains)
    comp.setCaseSensitivity(Qt.CaseInsensitive)
    comp.setCompletionMode(QCompleter.PopupCompletion)
    comp.setMaxVisibleItems(14)

    def chosen(text: str) -> None:
        i = combo.findText(text)
        if i >= 0:
            combo.setCurrentIndex(i)
            combo.activated.emit(i)

    def restore() -> None:
        if combo.findText(edit.text()) < 0 or not edit.text():
            edit.setText(combo.itemText(combo.currentIndex()))

    comp.activated[str].connect(chosen)
    edit.editingFinished.connect(lambda: QTimer.singleShot(0, combo, restore))
    return combo
