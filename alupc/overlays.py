"""Overlays: kleine Einblendungen über allem, was Monitor 2 gerade zeigt – Musik („Läuft gerade“), Uhr,
Bauchbinde, Laufschrift, Logo, LIVE-Zeichen, Timer, QR-Code.

Sie liegen (wie die Zeichnungen) in einem eigenen durchsichtigen Fenster über Monitor 2, das keine Klicks
abfängt – dadurch auch über dem iPhone-Bild (AirPlay-Fenster), einem Programm oder dem Desktop.

Ein Overlay ist ein Eintrag in der Einstellung „overlays“ → „items“:
    {"id", "type", "name", "on", "x", "y", "size", "style", "color", …Felder je Art}
x/y (0…1) legen die Stelle fest: 0/0 = Ecke oben links, 1/1 = unten rechts, 0.5/0.5 = Mitte – und alles
dazwischen. Die Größe richtet sich nach der Höhe von Monitor 2 (`size` = 1 → normal).
"""

from __future__ import annotations

import copy
import math
import time
import uuid

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

TYPES = {
    "music": "Musik (Läuft gerade)",
    "clock": "Uhr",
    "text": "Bauchbinde (Titel + Zeile)",
    "ticker": "Laufschrift",
    "image": "Logo / Bild",
    "live": "LIVE-Zeichen",
    "timer": "Timer",
    "qr": "QR-Code",
    "badge": "Hinweis",
    "chat": "Chat (Handys)",
}
STYLES = {"glas": "Glas (dunkel)", "hell": "Hell", "farbe": "Farbe", "schlicht": "Ohne Hintergrund"}
MUSIC_VARIANTS = {"kompakt": "Kompakt", "leiste": "Leiste (breit)", "cover": "Großes Cover"}
# Einrast-Stellen beim Verschieben (Ecken, Kanten-Mitten, Mitte)
SNAP = (0.0, 0.5, 1.0)
POSITIONS = {  # 3×3-Raster im Editor
    "oben links": (0.0, 0.0), "oben": (0.5, 0.0), "oben rechts": (1.0, 0.0),
    "links": (0.0, 0.5), "Mitte": (0.5, 0.5), "rechts": (1.0, 0.5),
    "unten links": (0.0, 1.0), "unten": (0.5, 1.0), "unten rechts": (1.0, 1.0),
}

BASE = {"on": True, "x": 1.0, "y": 1.0, "size": 1.0, "style": "glas", "color": "#1db954"}

# Vorlagen: (Bereich, Name, Beschreibung, Overlay)
TEMPLATES: list[tuple[str, str, str, dict]] = [
    ("Musik", "Musik – kompakt", "Cover, Titel, Künstler und Zeitleiste in einer Ecke",
     {"type": "music", "variant": "kompakt", "x": 0.0, "y": 1.0}),
    ("Musik", "Musik – Leiste", "Breite Leiste unten, gut für Pausen und Hintergrundmusik",
     {"type": "music", "variant": "leiste", "x": 0.5, "y": 1.0}),
    ("Musik", "Musik – großes Cover", "Großes Cover mit Titel darunter",
     {"type": "music", "variant": "cover", "x": 1.0, "y": 0.5}),
    ("Musik", "Musik – schlicht", "Nur Titel und Künstler, ohne Kasten",
     {"type": "music", "variant": "kompakt", "style": "schlicht", "x": 1.0, "y": 1.0, "cover": False}),
    ("Uhr & Zeit", "Uhr", "Uhrzeit klein in der Ecke", {"type": "clock", "x": 1.0, "y": 0.0}),
    ("Uhr & Zeit", "Uhr mit Datum", "Uhrzeit und Datum", {"type": "clock", "date": True, "x": 1.0, "y": 0.0}),
    ("Uhr & Zeit", "Timer", "Läuft mit dem AluPC-Timer mit", {"type": "timer", "x": 0.5, "y": 0.0,
                                                              "color": "#f43f5e"}),
    ("Text", "Bauchbinde", "Name und Zeile unten links – wie im Fernsehen",
     {"type": "text", "text": "Max Mustermann", "sub": "Klasse 10b", "x": 0.0, "y": 0.8, "color": "#3b82f6"}),
    ("Text", "Laufschrift", "Text läuft unten durch",
     {"type": "ticker", "text": "Willkommen! · Heute: Projekttag · Pause um 10:30", "x": 0.5, "y": 1.0,
      "color": "#f59e0b", "speed": 1.0}),
    ("Text", "Hinweis", "Kurzer Hinweis oben", {"type": "badge", "text": "Bitte leise sein", "x": 0.5, "y": 0.0,
                                               "color": "#8b5cf6"}),
    ("Text", "Überschrift", "Großer Titel oben, ohne Kasten",
     {"type": "text", "text": "Projektpräsentation", "sub": "", "style": "schlicht", "x": 0.5, "y": 0.0,
      "size": 1.6}),
    ("Bild & Logo", "Logo", "Eigenes Bild (z. B. Schullogo), halb durchsichtig",
     {"type": "image", "path": "", "opacity": 0.85, "x": 1.0, "y": 0.0}),
    ("Bild & Logo", "QR-Code", "Link als QR-Code mit Beschriftung",
     {"type": "qr", "url": "https://", "text": "Scannen", "x": 1.0, "y": 1.0, "style": "hell"}),
    ("Live", "LIVE", "Rotes LIVE-Zeichen mit pulsierendem Punkt",
     {"type": "live", "text": "LIVE", "x": 0.0, "y": 0.0, "color": "#ef4444"}),
    ("Live", "Aufnahme", "„REC“ wie bei einer Kamera", {"type": "live", "text": "REC", "x": 1.0, "y": 0.0,
                                                          "color": "#ef4444", "style": "schlicht"}),
    ("Live", "Chat", "Nachrichten der Handys (Chat übers AluPC-WLAN) mit QR-Code zum Mitschreiben",
     {"type": "chat", "count": 5, "seconds": 0, "qr": True, "x": 0.0, "y": 1.0, "color": "#6366f1"}),
]


def from_template(item: dict, name: str = "") -> dict:
    new = {**BASE, **copy.deepcopy(item)}
    new["id"] = uuid.uuid4().hex[:10]
    new["name"] = name or TYPES.get(new.get("type"), "Overlay")
    return new


def clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 1.0


def snap(v: float, tolerance: float = 0.04) -> float:
    """Nahe an einer Ecke/Kante/Mitte → genau dorthin (Einrasten beim Ziehen)."""
    for s in SNAP:
        if abs(v - s) <= tolerance:
            return s
    return clamp01(v)


def position_name(x: float, y: float) -> str:
    for name, (px, py) in POSITIONS.items():
        if abs(px - x) < 0.001 and abs(py - y) < 0.001:
            return name
    return "frei"


def margin(w: float, h: float) -> float:
    return min(w, h) * 0.03


def place(item: dict, w: float, h: float, size: QSizeF) -> QRectF:
    """Rechteck des Overlays auf einer Fläche w×h: x/y 0…1 → zwischen den Rändern (mit Abstand)."""
    m = margin(w, h)
    bw, bh = min(size.width(), w - 2 * m), min(size.height(), h - 2 * m)
    x = m + clamp01(item.get("x", 1)) * max(0.0, w - 2 * m - bw)
    y = m + clamp01(item.get("y", 1)) * max(0.0, h - 2 * m - bh)
    return QRectF(x, y, bw, bh)


def xy_for(item: dict, rect: QRectF, w: float, h: float) -> tuple[float, float]:
    """Umgekehrt: Overlay wurde nach `rect` gezogen → x/y 0…1."""
    m = margin(w, h)
    free_w, free_h = max(1.0, w - 2 * m - rect.width()), max(1.0, h - 2 * m - rect.height())
    return clamp01((rect.x() - m) / free_w), clamp01((rect.y() - m) / free_h)


# --------------------------------------------------------------------------- Daten zum Zeichnen
class OverlayData:
    """Was die Overlays anzeigen (aktueller Titel, Bilder …) – gemeinsam für Fenster und Vorschauen."""

    def __init__(self):
        self.track = None  # now_playing.Track
        self._art_key = None
        self.art = QImage()
        self._images: dict[str, QImage] = {}
        self._qr: dict[str, QImage] = {}
        self.chat = None  # Funktion → laufender Chat (chat.Chat) oder None

    def chat_messages(self, item: dict) -> list[dict]:
        chat = self.chat() if callable(self.chat) else None
        if chat is None:
            return []
        n = max(1, min(12, int(item.get("count", 5) or 5)))
        if chat_popup(item):  # Pop-up: nur die neuesten, nur kurz
            return chat.latest(min(n, 4), popup_seconds(item))
        return chat.latest(n, float(item.get("seconds", 0) or 0))

    def chat_link(self) -> str:
        from .cast_server import wlan_url

        return wlan_url("/chat")

    def chat_fresh(self, seconds: float = 0.6) -> bool:
        """Kam gerade eine Nachricht? (dann kurz animieren)"""
        chat = self.chat() if callable(self.chat) else None
        last = chat.latest(1) if chat is not None else []
        return bool(last) and time.time() - last[0]["t"] < seconds

    def set_track(self, track) -> None:
        self.track = track
        key = track.art_key if track is not None else None
        if key != self._art_key:
            self._art_key = key
            img = QImage()
            if track is not None and track.art:
                img.loadFromData(track.art)
            self.art = img

    def image(self, path: str) -> QImage:
        if path not in self._images:
            from .platform.shared_paths import resolve
            from .sources import load_image

            if len(self._images) > 16:
                self._images.clear()
            self._images[path] = load_image(resolve(path, mount=False)) if path else QImage()
        return self._images[path]

    def qr(self, url: str) -> QImage:
        if url not in self._qr:
            img = QImage()
            try:
                import io

                import segno

                buf = io.BytesIO()
                segno.make(url or " ", error="m").save(buf, kind="png", scale=8, border=2)
                img.loadFromData(buf.getvalue())
            except Exception:  # noqa: BLE001
                pass
            self._qr = {url: img}
            return img
        return self._qr[url]


# --------------------------------------------------------------------------- Zeichnen
def _font(px: float, bold: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setPixelSize(max(5, int(px)))
    f.setWeight(QFont.Bold if bold else QFont.Normal)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def _tw(text: str, px: float, bold: bool = False) -> float:
    return QFontMetricsF(_font(px, bold)).horizontalAdvance(text)


def colors(item: dict) -> tuple[QColor | None, QColor, QColor, QColor]:
    """(Hintergrund oder None, Text, Nebentext, Akzent) je Stil."""
    accent = QColor(item.get("color") or "#1db954")
    style = item.get("style", "glas")
    if style == "hell":
        return QColor(255, 255, 255, 235), QColor("#0f172a"), QColor(15, 23, 42, 160), accent
    if style == "farbe":
        bg = QColor(accent)
        bg.setAlpha(235)
        return bg, QColor("#ffffff"), QColor(255, 255, 255, 200), QColor("#ffffff")
    if style == "schlicht":
        return None, QColor("#ffffff"), QColor(255, 255, 255, 210), accent
    return QColor(12, 16, 28, 190), QColor("#ffffff"), QColor(255, 255, 255, 170), accent


def unit(item: dict, h: float) -> float:
    return max(2.0, h * 0.01 * max(0.3, min(4.0, float(item.get("size", 1.0) or 1.0))))


def clock_text(item: dict) -> tuple[str, str]:
    t = time.localtime()
    main = time.strftime("%H:%M:%S" if item.get("seconds") else "%H:%M", t)
    if item.get("date"):
        from .sources import format_date_de

        return main, format_date_de(t)
    return main, ""


def timer_text() -> tuple[str, str]:
    from .timer import clock

    return clock.text(), ("" if clock.running or clock.finished() else "Pause")


def measure(item: dict, w: float, h: float, data: OverlayData) -> QSizeF:
    u = unit(item, h)
    t = item.get("type")
    pad = u * 1.6
    if t == "music":
        variant = item.get("variant", "kompakt")
        if variant == "leiste":
            return QSizeF(min(w * 0.7, u * 110), u * 11)
        if variant == "cover":
            return QSizeF(u * 36, u * 47)
        tr = data.track
        text_w = max(_tw(tr.title if tr else "Gerade läuft nichts", u * 3.4, True),
                     _tw((tr.artist or tr.player) if tr else "", u * 2.6))
        cover = u * 9 if item.get("cover", True) else 0
        return QSizeF(min(w * 0.6, max(u * 34, cover + text_w + pad * 2.6)), u * 12)
    if t in ("clock", "timer"):
        main, sub = clock_text(item) if t == "clock" else timer_text()
        tw = max(_tw(main, u * 6, True), _tw(sub, u * 2.4) if sub else 0)
        return QSizeF(tw + pad * 2, u * 6 * 1.25 + (u * 3.2 if sub else 0) + pad)
    if t == "text":
        title, sub = item.get("text", ""), item.get("sub", "")
        tw = max(_tw(title, u * 4.6, True), _tw(sub, u * 3) if sub else 0)
        bar = u * 1.2 if item.get("style", "glas") != "schlicht" else 0
        return QSizeF(min(w * 0.9, tw + pad * 2 + bar), u * 4.6 * 1.3 + (u * 3 * 1.3 if sub else 0) + pad * 1.2)
    if t == "ticker":
        return QSizeF(w * float(item.get("width", 1.0) or 1.0), u * 6)
    if t == "image":
        img = data.image(item.get("path", ""))
        hh = u * 14
        if img.isNull():
            return QSizeF(hh * 1.6, hh)
        return QSizeF(hh * img.width() / max(1, img.height()), hh)
    if t == "live":
        return QSizeF(_tw(item.get("text", "LIVE"), u * 3.2, True) + u * 7, u * 5.4)
    if t == "qr":
        side = u * 16
        return QSizeF(side + pad, side + pad + (u * 3.4 if item.get("text") else 0))
    if t == "badge":
        return QSizeF(_tw(item.get("text", ""), u * 3, True) + pad * 2.4, u * 6)
    if t == "chat":
        width = min(w * 0.46, u * 62)
        return QSizeF(width, sum(hh for _m, hh in _chat_rows(item, width, u, data)) + _chat_head(item, u, data))
    return QSizeF(u * 20, u * 6)


def chat_popup(item: dict) -> bool:
    """Chat als Pop-up: neue Nachrichten ploppen kurz auf und verschwinden wieder – ohne QR-Code, ohne Liste."""
    return item.get("mode") == "popup"


def popup_seconds(item: dict) -> float:
    """So lange bleibt ein Pop-up stehen (Einstellung „sichtbar“, sonst 8 s)."""
    return float(item.get("seconds", 0) or 0) or 8.0


def _chat_head(item: dict, u: float, data: OverlayData) -> float:
    """Höhe des Kopfes (QR-Code + „Chat: scannen“), 0 ohne QR (und immer 0 als Pop-up)."""
    return u * 13 if item.get("qr", True) and not chat_popup(item) and data.chat_link() else 0.0


def _chat_rows(item: dict, width: float, u: float, data: OverlayData) -> list[tuple[dict, float]]:
    """Nachrichten mit ihrer Höhe (Name + umbrochener Text)."""
    msgs = data.chat_messages(item)
    if not msgs and chat_popup(item):
        return []  # Pop-up: ohne Nachricht ist nichts zu sehen
    if not msgs:
        return [({"id": 0, "name": "", "text": "Noch keine Nachrichten – QR-Code scannen und schreiben",
                  "avatar": "💬", "t": 0, "host": True}, u * 7.4)]
    fm = QFontMetricsF(_font(u * 3))
    inner = width - u * 4
    rows = []
    for m in msgs:
        lines = fm.boundingRect(QRectF(0, 0, inner, 10_000), int(Qt.TextWordWrap), m["text"]).height()
        rows.append((m, u * 2.6 * 1.25 + min(lines, u * 3 * 1.35 * 4) + u * 2.6 + u * 0.8))
    return rows


def _panel(p: QPainter, r: QRectF, bg: QColor | None, radius: float) -> None:
    if bg is None:
        return
    p.setPen(Qt.NoPen)
    shadow = QColor(0, 0, 0, 60)
    p.setBrush(shadow)
    p.drawRoundedRect(r.translated(0, radius * 0.25), radius, radius)
    p.setBrush(bg)
    p.drawRoundedRect(r, radius, radius)
    if bg.lightness() < 128:
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def _text(p: QPainter, r: QRectF, text: str, px: float, color: QColor, bold=False, align=Qt.AlignLeft | Qt.AlignVCenter,
          shadow: bool = False, spacing: float = 0.0) -> None:
    font = _font(px, bold, spacing)
    p.setFont(font)
    text = QFontMetricsF(font).elidedText(text, Qt.ElideRight, max(1.0, r.width()))
    if shadow:  # ohne Kasten lesbar auf hellem und dunklem Grund
        p.setPen(QColor(0, 0, 0, 170))
        p.drawText(r.translated(max(1.0, px * 0.06), max(1.0, px * 0.06)), int(align), text)
    p.setPen(color)
    p.drawText(r, int(align), text)


def _note(p: QPainter, r: QRectF, color: QColor) -> None:
    from .ui.icons import draw_note

    draw_note(p, r, color)


def _cover(p: QPainter, r: QRectF, data: OverlayData, accent: QColor, radius: float) -> None:
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    p.save()
    p.setClipPath(path)
    if not data.art.isNull():
        img = data.art.scaled(int(r.width()), int(r.height()), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawImage(QPointF(r.x() - (img.width() - r.width()) / 2, r.y() - (img.height() - r.height()) / 2), img)
    else:
        p.fillRect(r, accent.darker(170))
        s = r.width() * 0.42
        _note(p, QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s), QColor(255, 255, 255, 190))
    p.restore()


def _progress(p: QPainter, r: QRectF, frac: float, accent: QColor, track: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(track)
    p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
    p.setBrush(accent)
    p.drawRoundedRect(QRectF(r.x(), r.y(), max(r.height(), r.width() * max(0.0, min(1.0, frac))), r.height()),
                      r.height() / 2, r.height() / 2)


def paint(p: QPainter, item: dict, r: QRectF, h: float, data: OverlayData, now: float | None = None) -> None:
    """Ein Overlay ins Rechteck `r` malen (h = Höhe der ganzen Fläche, für die Größe)."""
    now = time.monotonic() if now is None else now
    u = unit(item, h)
    bg, fg, sub_c, accent = colors(item)
    plain = bg is None
    radius = u * 1.6
    pad = u * 1.6
    t = item.get("type")
    p.save()
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    if t == "music":
        tr = data.track
        title = tr.title if tr else "Gerade läuft nichts"
        artist = (tr.artist or tr.player) if tr else ""
        frac = (tr.position_now() / tr.length) if tr and tr.length > 0 else None
        variant = item.get("variant", "kompakt")
        _panel(p, r, bg, radius)
        if variant == "cover":
            side = r.width() - pad * 2
            cover = QRectF(r.x() + pad, r.y() + pad, side, side)
            _cover(p, cover, data, accent, radius * 0.7)
            y = cover.bottom() + u * 1.4
            px = u * 3.2  # langer Titel: erst kleiner, dann erst „…“
            while px > u * 2.3 and _tw(title, px, True) > side:
                px *= 0.92
            _text(p, QRectF(r.x() + pad, y, side, u * 4.2), title, px, fg, True, shadow=plain)
            _text(p, QRectF(r.x() + pad, y + u * 4, side, u * 3.4), artist, u * 2.4, sub_c, shadow=plain)
            if frac is not None:
                _progress(p, QRectF(r.x() + pad, r.bottom() - pad - u * 0.6, side, u * 0.6), frac, accent,
                          QColor(128, 128, 128, 90))
        else:
            show_cover = item.get("cover", True)
            cs = r.height() - pad * 1.5 if show_cover else 0
            cover = QRectF(r.x() + pad * 0.75, r.y() + pad * 0.75, cs, cs)
            if show_cover:
                _cover(p, cover, data, accent, radius * 0.6)
            tx = cover.right() + pad * 0.9 if show_cover else r.x() + pad
            tw = r.right() - pad - tx
            if tr is not None and not tr.playing:
                title = "❚❚  " + title
            # Titel, Künstler, Zeitleiste untereinander – als Block senkrecht mittig
            t1, t2 = u * 3.3, u * 2.4
            h1, h2 = t1 * 1.3, t2 * 1.35
            bar_h = max(2.0, u * 0.55) if frac is not None else 0.0
            gap = u * 1.1 if frac is not None else 0.0
            y = r.y() + (r.height() - (h1 + h2 + gap + bar_h)) / 2
            _text(p, QRectF(tx, y, tw, h1), title, t1, fg, True, shadow=plain)
            _text(p, QRectF(tx, y + h1, tw, h2), artist, t2, sub_c, shadow=plain)
            if frac is not None:
                _progress(p, QRectF(tx, y + h1 + h2 + gap, tw, bar_h), frac, accent, QColor(128, 128, 128, 90))
    elif t in ("clock", "timer"):
        main, sub = clock_text(item) if t == "clock" else timer_text()
        _panel(p, r, bg, radius)
        mh = u * 6 * 1.2
        color = fg
        if t == "timer":
            from .timer import clock

            if clock.urgency() in ("gleich", "ende"):  # letzte 10 s / abgelaufen: Akzentfarbe
                color = accent if item.get("style") != "farbe" else fg
        _text(p, QRectF(r.x(), r.y() + pad * 0.5, r.width(), mh), main, u * 6, color, True, Qt.AlignCenter, plain)
        if sub:
            _text(p, QRectF(r.x(), r.y() + pad * 0.5 + mh * 0.95, r.width(), u * 3), sub, u * 2.4, sub_c, False,
                  Qt.AlignCenter, plain)
    elif t == "text":
        title, sub = item.get("text", ""), item.get("sub", "")
        _panel(p, r, bg, radius * 0.6)
        x = r.x() + pad
        if not plain:  # Akzentbalken links
            p.setPen(Qt.NoPen)
            p.setBrush(accent if item.get("style") != "farbe" else QColor(255, 255, 255, 220))
            p.drawRoundedRect(QRectF(r.x() + pad * 0.55, r.y() + pad * 0.6, u * 0.8, r.height() - pad * 1.2),
                              u * 0.4, u * 0.4)
            x += u * 1.2
        align = Qt.AlignLeft | Qt.AlignVCenter if not plain else Qt.AlignCenter
        if plain:
            x = r.x()
        width = r.right() - x - (pad if not plain else 0)
        th = u * 4.6 * 1.3
        top = r.y() + pad * 0.6
        _text(p, QRectF(x, top, width, th), title, u * 4.6, fg, True, align, plain)
        if sub:
            _text(p, QRectF(x, top + th * 0.92, width, u * 3 * 1.3), sub, u * 3, sub_c, False, align, plain)
    elif t == "ticker":
        _panel(p, r, bg, radius * 0.5)
        text = (item.get("text", "") or " ") + "     •     "
        px = u * 3.2
        tw = max(1.0, _tw(text, px, True))
        speed = u * 9 * max(0.2, min(4.0, float(item.get("speed", 1.0) or 1.0)))  # Pixel pro Sekunde
        offset = (now * speed) % tw
        inner = r.adjusted(pad * 0.6, 0, -pad * 0.6, 0)
        p.setClipRect(inner)
        x = inner.x() - offset
        while x < inner.right():
            _text(p, QRectF(x, r.y(), tw + 2, r.height()), text, px, fg, True, Qt.AlignLeft | Qt.AlignVCenter, plain)
            x += tw
    elif t == "image":
        img = data.image(item.get("path", ""))
        p.setOpacity(max(0.1, min(1.0, float(item.get("opacity", 0.85) or 0.85))))
        if img.isNull():
            p.setPen(QPen(QColor(255, 255, 255, 160), max(1.0, u * 0.25), Qt.DashLine))
            p.setBrush(QColor(0, 0, 0, 60))
            p.drawRoundedRect(r, radius, radius)
            _text(p, r, "Bild wählen", u * 2.6, QColor(255, 255, 255, 200), False, Qt.AlignCenter)
        else:
            p.drawImage(r, img)
    elif t == "live":
        _panel(p, r, bg if bg is not None else None, r.height() / 2)
        dot_r = u * 1.1
        pulse = 0.55 + 0.45 * abs(((now * 1.2) % 2) - 1)  # sanftes Pulsieren
        c = QColor(accent if item.get("style") != "farbe" else QColor("#ffffff"))
        c.setAlphaF(pulse)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        center = QPointF(r.x() + u * 2.8, r.center().y())
        p.drawEllipse(center, dot_r, dot_r)
        _text(p, QRectF(center.x() + u * 2, r.y(), r.width() - u * 5, r.height()), item.get("text", "LIVE"),
              u * 3.2, fg, True, Qt.AlignLeft | Qt.AlignVCenter, plain, spacing=u * 0.3)
    elif t == "qr":
        _panel(p, r, bg, radius)
        side = u * 16
        img = data.qr(item.get("url", ""))
        q = QRectF(r.center().x() - side / 2, r.y() + pad / 2, side, side)
        if not img.isNull():
            p.drawImage(q, img)
        if item.get("text"):
            _text(p, QRectF(r.x(), q.bottom(), r.width(), u * 3.4), item["text"], u * 2.4, fg, True, Qt.AlignCenter,
                  plain)
    elif t == "badge":
        _panel(p, r, bg, r.height() / 2)
        _text(p, r, item.get("text", ""), u * 3, fg, True, Qt.AlignCenter, plain)
    elif t == "chat":
        _paint_chat(p, item, r, u, data, bg, fg, accent, now)
    p.restore()


def _paint_chat(p: QPainter, item: dict, r: QRectF, u: float, data: OverlayData, bg, fg, accent, now: float) -> None:
    from .chat import name_color

    head = _chat_head(item, u, data)
    if head:  # QR-Code zum Mitschreiben + Hinweis
        side = head - u * 2
        q = QRectF(r.x(), r.y(), side + u * 1.6, side + u * 1.6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 240))
        p.drawRoundedRect(q, u, u)
        img = data.qr(data.chat_link())
        if not img.isNull():
            p.drawImage(q.adjusted(u * 0.8, u * 0.8, -u * 0.8, -u * 0.8), img)
        tx = q.right() + u * 1.6
        _text(p, QRectF(tx, r.y() + u * 1.5, r.right() - tx, u * 4.4), "💬 Chat", u * 3.6, fg, True,
              shadow=True)
        _text(p, QRectF(tx, r.y() + u * 6, r.right() - tx, u * 3.4), "QR-Code scannen · mitschreiben", u * 2.4,
              QColor(255, 255, 255, 215), shadow=True)
    y = r.y() + head
    wall = time.time()
    popup = chat_popup(item)
    for m, hh in _chat_rows(item, r.width(), u, data):
        age = wall - m["t"] if m["t"] else 9.0
        appear = max(0.0, min(1.0, age / 0.35))  # neue Nachricht gleitet herein
        box = QRectF(r.x() + (1 - appear) * u * 8, y, r.width(), hh - u * 0.8)
        p.save()
        if popup:  # aufploppen (kurz größer als nötig), am Ende ausblenden
            k = min(1.0, age / 0.45)
            scale = 0.6 + 0.4 * k + 0.08 * math.sin(math.pi * k)
            box = QRectF(r.x(), y, r.width(), hh - u * 0.8)
            c = box.center()
            p.translate(c)
            p.scale(scale, scale)
            p.translate(-c)
            vanish = max(0.0, min(1.0, (popup_seconds(item) - age) / 0.8))
            p.setOpacity(p.opacity() * min(k * 1.6, 1.0) * vanish)
        else:
            p.setOpacity(p.opacity() * (0.3 + 0.7 * appear))
        fill = QColor(bg) if bg is not None else QColor(12, 16, 28, 175)
        if m.get("host"):
            fill = QColor(accent)
            fill.setAlpha(225)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 55))
        p.drawRoundedRect(box.translated(0, u * 0.3), u * 1.8, u * 1.8)
        p.setBrush(fill)
        p.drawRoundedRect(box, u * 1.8, u * 1.8)
        if popup and not m.get("host"):  # Pop-up: leuchtender Rand in der Farbe des Namens
            ring = QColor(name_color(m.get("name", "")))
            ring.setAlpha(200)
            p.setPen(QPen(ring, max(1.5, u * 0.3)))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box.adjusted(u * 0.15, u * 0.15, -u * 0.15, -u * 0.15), u * 1.7, u * 1.7)
        name = (m.get("avatar", "") + " " if m.get("avatar") else "") + m.get("name", "")
        if name.strip():
            _text(p, QRectF(box.x() + u * 2, box.y() + u * 1.1, box.width() - u * 4, u * 2.6 * 1.25), name,
                  u * 2.6, QColor("#ffffff") if m.get("host") else QColor(name_color(m.get("name", ""))), True)
        top = box.y() + (u * 1.1 + u * 2.6 * 1.25 if name.strip() else u * 1.4)
        p.setFont(_font(u * 3))
        p.setPen(fg if not m.get("host") else QColor("#ffffff"))
        p.drawText(QRectF(box.x() + u * 2, top, box.width() - u * 4, box.bottom() - top - u * 0.9),
                   int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop), m["text"])
        p.restore()
        y += hh


def layout(items: list[dict], w: float, h: float, data: OverlayData) -> list[tuple[dict, QRectF]]:
    return [(it, place(it, w, h, measure(it, w, h, data))) for it in items if it.get("on", True)]


def paint_all(p: QPainter, items: list[dict], w: float, h: float, data: OverlayData) -> None:
    now = time.monotonic()
    for it, r in layout(items, w, h, data):
        paint(p, it, r, h, data, now)


def animated(item: dict) -> bool:
    return item.get("type") in ("ticker", "live", "chat")


# --------------------------------------------------------------------------- Fenster über Monitor 2
class OverlayWindow(QWidget):
    """Overlays über Monitor 2. Zeigt Monitor 2 ein AluPC-Bild, sind sie eine Ebene IN diesem Fenster (gleiche
    Skalierung, sicher obendrauf – unter Wayland liegt ein eigenes Fenster sonst oft dahinter oder auf dem falschen
    Monitor). Nur bei „Erweitern“ (kein AluPC-Fenster auf Monitor 2) sind sie ein eigenes, durchsichtiges Fenster."""
    TITLE = "AluPC – Overlays"
    FLOAT_FLAGS = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                   | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)

    def __init__(self, controller):
        super().__init__(None, self.FLOAT_FLAGS)
        self.controller = controller
        self.setWindowTitle(self.TITLE)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.data = OverlayData()
        self.data.chat = lambda: getattr(getattr(controller, "cast", None), "chat", None)
        self._kde_done = False
        self._music = False
        self._rects: list[tuple[dict, QRectF]] = []
        self._tick = QTimer(self, interval=1000)  # Uhr, Timer, Musik-Zeitleiste
        self._tick.timeout.connect(self._second)
        self._anim = QTimer(self)  # Laufschrift, LIVE-Punkt – nur deren Bereich neu zeichnen
        self._anim.timeout.connect(self._animate)

    # ---- Einstellungen
    def cfg(self) -> dict:
        return self.controller.config["overlays"]

    def items(self) -> list[dict]:
        """Alle sichtbaren Overlays: die aus dem Editor (wenn Overlays an) + die von Kacheln der Startseite."""
        own = [it for it in self.cfg().get("items", []) if it.get("on", True)] if self.cfg().get("on") else []
        return own + list(getattr(self.controller, "tile_overlays", {}).values()) + \
            list(getattr(self.controller, "live_overlays", {}).values())

    def needed(self) -> bool:
        c = self.controller
        saver = getattr(c, "screensaver", None)
        return bool(self.items() and c.output_screen() is not None and not c.privacy
                    and not (saver is not None and saver.active))

    def reload(self) -> None:
        """Nach jeder Änderung (Editor, Kachel, Handy): Datenquellen an/aus, neu zeichnen, zeigen/verstecken."""
        items = self.items() if self.needed() else []
        want_music = any(it.get("type") == "music" for it in items)
        from .now_playing_view import feed

        if want_music and not self._music:
            feed().changed.connect(self._track)
            feed().acquire()
            self._track(feed().track)
        elif not want_music and self._music:
            try:
                feed().changed.disconnect(self._track)
            except (RuntimeError, TypeError):
                pass
            feed().release()
        self._music = want_music
        if items:
            self._tick.start()
        else:
            self._tick.stop()
        if any(animated(it) for it in items):
            from . import perf

            self._anim.setInterval(perf.interval(33))
            self._anim.start()
        else:
            self._anim.stop()
        self.place()
        self._relayout()
        self.update()

    def _track(self, track) -> None:
        self.data.set_track(track)
        self._relayout()
        self.update()

    # ---- Platz auf Monitor 2
    def _host(self):
        """Das AluPC-Fenster auf Monitor 2, wenn es gerade etwas zeigt – sonst None."""
        out = getattr(self.controller, "output", None)
        return out if out is not None and out.isVisible() and out.needed() else None

    def embedded(self) -> bool:
        return self.parentWidget() is not None

    def place(self) -> None:
        screen = self.controller.output_screen()
        if screen is None or not self.needed():
            if self.isVisible():
                self.hide()
            self._kde_done = False
            return
        host = self._host()
        if host is not None:
            self._embed(host)
            return
        self._float(screen)

    def _embed(self, host) -> None:
        if self.parentWidget() is not host:
            self.hide()
            self.setParent(host, Qt.Widget)  # Ebene im Monitor-2-Fenster
            host.overlay_layer = self
            self._kde_done = False
        if self.geometry() != host.rect():
            self.setGeometry(host.rect())
        if not self.isVisible():
            self.show()
        self.raise_()
        host.raise_layers()

    def _float(self, screen) -> None:
        old = self.parentWidget()
        if old is not None:
            if getattr(old, "overlay_layer", None) is self:
                old.overlay_layer = None
            self.hide()
            self.setParent(None, self.FLOAT_FLAGS)
            self.setWindowTitle(self.TITLE)
            self._kde_done = False
        from .platform.linux_display import is_wayland

        geo = screen.geometry()
        if is_wayland():
            # Wayland: Programme dürfen ihr Fenster nicht selbst setzen – KWin legt es auf Monitor 2 (und oben drauf)
            if self.size() != geo.size():
                self.resize(geo.size())
            if not self.isVisible():
                self.show()
        else:
            if self.geometry() != geo:
                self.setGeometry(geo)
            if not self.isVisible():
                self.show()
        if not self._kde_done and self.isVisible():
            self._kde_done = True
            self._kde_pin(geo)
            QTimer.singleShot(800, lambda: self._kde_pin(geo) if self.isVisible() and not self.embedded() else None)
        self._raise_float()

    def _kde_pin(self, geo) -> None:
        """KDE: oben halten, aus Taskleiste/Alt+Tab nehmen, genau auf Monitor 2 legen."""
        from .platform import linux_windows
        from .ui.util import run_async

        rect = (geo.x(), geo.y(), geo.width(), geo.height())
        run_async(lambda: linux_windows.kwin_window_props(self.TITLE, above=True, geo=rect), None, lambda _e: None)

    def raise_above(self) -> None:
        """Nach jedem Hochholen des Monitor-2-Fensters: Overlays wieder obendrauf (bzw. hinein, falls es jetzt etwas
        zeigt)."""
        if self.embedded() or self._host() is not None:
            if self.needed():
                self.place()
            return
        self._raise_float()

    def _raise_float(self) -> None:
        if not self.isVisible():
            return
        from .platform.window_tools import keep_on_top

        self.raise_()
        keep_on_top(self)

    # ---- Neu zeichnen, sparsam
    def _relayout(self) -> None:
        old = [r for _it, r in self._rects]
        self._rects = layout(self.items(), self.width(), self.height(), self.data) if self.isVisible() else []
        for r in old + [r for _it, r in self._rects]:
            self.update(r.toAlignedRect().adjusted(-4, -4, 4, 8))

    def _second(self) -> None:
        if not self.isVisible():
            return
        self._relayout()  # Uhr/Timer/Titel können breiter werden

    def _animate(self) -> None:
        fresh = None
        for it, r in self._rects:
            if it.get("type") == "chat":  # nur kurz nach einer neuen Nachricht (hereingleiten / Pop-up läuft)
                if chat_popup(it):
                    if not self.data.chat_fresh(popup_seconds(it) + 0.2):
                        continue
                else:
                    fresh = self.data.chat_fresh() if fresh is None else fresh
                    if not fresh:
                        continue
            if animated(it):
                pad = 40 if it.get("type") == "chat" else 2  # Pop-up ploppt kurz über seinen Platz hinaus
                self.update(r.toAlignedRect().adjusted(-pad, -pad, pad, pad))

    def chat_changed(self) -> None:
        """Neue/gelöschte Chat-Nachricht: Größe neu berechnen und neu zeichnen."""
        if any(it.get("type") == "chat" for it, _r in self._rects):
            self._relayout()

    def resizeEvent(self, _event):
        self._relayout()

    def showEvent(self, _event):
        self._relayout()

    def paintEvent(self, event):
        if self.controller.privacy:
            return
        p = QPainter(self)
        now = time.monotonic()
        clip = QRectF(event.rect())
        for it, r in self._rects:
            if r.adjusted(-8, -8, 8, 8).intersects(clip):
                paint(p, it, r, self.height(), self.data, now)
        p.end()

    def shutdown(self) -> None:
        self._tick.stop()
        self._anim.stop()
        if self._music:
            from .now_playing_view import feed

            try:
                feed().changed.disconnect(self._track)
            except (RuntimeError, TypeError):
                pass
            feed().release()
            self._music = False
        self.close()


def draw_into(controller, image: QImage) -> None:
    """Overlays in ein Vorschaubild malen (Bild-in-Bild, Live-Vorschau, Handy)."""
    win = getattr(controller, "overlay_window", None)
    if win is None or image is None or image.isNull() or not win.needed():
        return
    w, h = image.width() / image.devicePixelRatio(), image.height() / image.devicePixelRatio()
    p = QPainter(image)
    paint_all(p, win.items(), w, h, win.data)
    p.end()


