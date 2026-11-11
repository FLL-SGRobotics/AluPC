"""Publikumsansicht (Monitor 2): EIN Aussehen für alle Info-Anzeigen – Abstimmung, Glücksrad, WLAN/QR, System,
Musik, Spiele, AirPlay-Warten, Text/Uhr/Timer (mit Standard-Hintergrund). Unabhängig vom Design der PC-App
(Normal/Technisch/Holz), damit das Publikum immer dasselbe sieht.

„Bühne“ (Standard): dunkler, leicht blauer Verlauf mit sanftem Leuchten oben. „Schwarz“: reines Schwarz
(z. B. für Beamer mit wenig Kontrast). Einstellung: Setup → Bildschirm → Publikum.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QRadialGradient

LOOKS = {"buehne": "Bühne (dunkler Verlauf)", "schwarz": "Reines Schwarz"}
BG_TOP = "#16181e"
BG_BOTTOM = "#0b0c0f"
PANEL = "#1d2027"      # Flächen (Balken, Karten) auf der Bühne
LINE = "#2b2f38"
TEXT = "#f3f4f6"
MUTED = "#9ca3af"
ACCENT = "#5b8cff"     # AluPC-Blau – immer gleich, egal welches PC-Design
LIVE = "#ff4f45"

settings = {"look": "buehne"}  # setzt der Controller aus config["output"]["stage"]


def configure(look: str | None) -> None:
    settings["look"] = look if look in LOOKS else "buehne"


def paint_background(p: QPainter, rect: QRectF) -> None:
    """Hintergrund der Publikumsansicht: dunkle „Aurora“ in AluPC-Blau und Violett (oder reines Schwarz)."""
    rect = QRectF(rect)
    if settings["look"] == "schwarz":
        p.fillRect(rect, QColor("#000000"))
        return
    paint_aurora(p, rect, QColor(ACCENT), strength=0.4)  # ruhiger als die Vorlagen: Publikum sieht vor allem Inhalt


def _blob(p: QPainter, rect: QRectF, center: QPointF, radius: float, color: QColor, alpha: float) -> None:
    g = QRadialGradient(center, radius)
    c0, c1, c2 = QColor(color), QColor(color), QColor(color)
    c0.setAlphaF(alpha)
    c1.setAlphaF(alpha * 0.35)
    c2.setAlphaF(0.0)
    g.setColorAt(0, c0)
    g.setColorAt(0.45, c1)
    g.setColorAt(1, c2)
    p.fillRect(rect, g)


_AURORA_CACHE: dict = {}


def paint_aurora(p: QPainter, rect: QRectF, color: QColor, now: float = 0.0, strength: float = 1.0) -> None:
    """Wie `_paint_aurora`, aber einmal je Größe/Farbe gerechnet und danach nur kopiert (Animationen: 30 Bilder/s)."""
    from PySide6.QtGui import QImage

    rect = QRectF(rect)
    dpr = p.device().devicePixelRatioF() if p.device() else 1.0
    key = (QColor(color).rgb(), round(rect.width()), round(rect.height()), round(strength, 2), dpr)
    img = _AURORA_CACHE.get(key)
    if img is None:
        img = QImage(max(1, int(rect.width() * dpr)), max(1, int(rect.height() * dpr)), QImage.Format_RGB32)
        img.setDevicePixelRatio(dpr)
        q = QPainter(img)
        _paint_aurora(q, QRectF(0, 0, rect.width(), rect.height()), QColor(color), strength)
        q.end()
        if len(_AURORA_CACHE) > 16:
            _AURORA_CACHE.clear()
        _AURORA_CACHE[key] = img
    p.drawImage(rect.topLeft(), img)


def _paint_aurora(p: QPainter, rect: QRectF, color: QColor, strength: float = 1.0) -> None:
    """Gemeinsamer Hintergrund aller Vorlagen auf Monitor 2 (ab 2.2, taste-skill): ein flaches, tiefes Farbfeld
    im Ton der Vorlage – keine Lichtflecken, kein Verlauf, kein dunkler Rand. Die Farbe trägt die Seite."""
    rect = QRectF(rect)
    hue = color.hsvHueF() if color.hsvHueF() >= 0 else 0.62
    sat = color.hsvSaturationF()
    p.fillRect(rect, QColor.fromHsvF(hue, min(0.7, sat * 0.75), 0.16 if sat > 0.15 else 0.08))
    return


def font(px: float, weight: int = 700) -> QFont:
    """Schrift für Monitor 2: Inter, große Titel etwas enger gesetzt."""
    f = QFont("Inter")
    f.setPixelSize(max(6, int(px)))
    f.setWeight(QFont.Weight(weight))
    if px >= 40:
        f.setLetterSpacing(QFont.PercentageSpacing, 98)
    return f


def eyebrow(p: QPainter, x: float, y: float, text: str, color: QColor, unit: float) -> QRectF:
    """Früher ein kleines Etikett oben links. Ab 2.2 (taste-skill: „keine Etiketten über Überschriften“) zeichnet
    es nichts mehr; das Rechteck bleibt für den Aufbau."""
    return QRectF(x, y, 0, unit * 4)


HIDE_CLOCK = False  # Info-Leiste zeigt schon die Uhr (screens_bar) → oben rechts keine zweite


def clock_corner(p: QPainter, w: float, unit: float, now: float, y: float | None = None) -> None:
    """Uhrzeit oben rechts (gleiche Stelle auf allen Vorlagen)."""
    if HIDE_CLOCK:
        return
    p.save()
    p.setPen(QColor(255, 255, 255, 190))
    p.setFont(font(unit * 2.4, 600))
    top = unit * 3.5 if y is None else y
    p.drawText(QRectF(w * 0.5, top, w * 0.5 - unit * 4.5, unit * 4), Qt.AlignRight | Qt.AlignVCenter,
               time.strftime("%H:%M", time.localtime(now)))
    p.restore()


def margin(w: float, h: float) -> float:
    """Gleicher Rand für alle Anzeigen (6 % der kürzeren Seite)."""
    return min(w, h) * 0.06


def is_default_black(color: QColor) -> bool:
    """Standard-Hintergrund von Text/Uhr/Timer (Schwarz) → stattdessen die Bühne zeigen."""
    return QColor(color).rgb() == QColor("#000000").rgb()
