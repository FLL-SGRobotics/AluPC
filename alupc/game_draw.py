"""Zeichnen der Minispiele auf Monitor 2 (mit Animationen). Jede Funktion bekommt einen Zeichen-Kontext `c`."""

from __future__ import annotations

import math
import random
from types import SimpleNamespace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

from .games_base import TEAM_COLORS, TEAM_NAMES, clock_text

TEXT = "#f1f5f9"
MUTED = "#94a3b8"
CARD = QColor(255, 255, 255, 14)
_rnd = random.Random()


# =========================================================================== Hilfen
def qc(color, alpha: float | None = None) -> QColor:
    c = QColor(color)
    if alpha is not None:
        c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def font(px: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPixelSize(max(8, int(px)))
    f.setBold(bold)
    return f


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_back(t: float) -> float:
    t = max(0.0, min(1.0, t))
    s = 1.70158
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


def text(p: QPainter, rect: QRectF, txt: str, px: float, color=TEXT, bold=False, align=Qt.AlignCenter,
         wrap=False) -> None:
    """Text, der in das Rechteck passt (wird notfalls kleiner)."""
    f = font(px, bold)
    if wrap:
        flags = int(align) | int(Qt.TextWordWrap)
        while f.pixelSize() > 10:
            br = QFontMetrics(f).boundingRect(rect.toRect(), flags, txt)
            if br.height() <= rect.height() and br.width() <= rect.width():
                break
            f.setPixelSize(int(f.pixelSize() * 0.92))
        p.setFont(f)
        p.setPen(qc(color))
        p.drawText(rect, flags, txt)
        return
    adv = QFontMetrics(f).horizontalAdvance(txt)
    if adv > rect.width() > 0:
        f.setPixelSize(max(8, int(f.pixelSize() * rect.width() / adv)))
    p.setFont(f)
    p.setPen(qc(color))
    p.drawText(rect, align, txt)


def tag(c, x, y, w, label: str, color="#a5b4fc") -> None:
    """Kleine Zeile über dem Spiel (Name, Runde …) – in der Pultschrift, normale Schreibweise."""
    from .ui.theme import HEAD_FONT

    f = QFont(HEAD_FONT)
    f.setPixelSize(max(9, int(c.h * 0.034)))
    f.setWeight(QFont.DemiBold)
    c.p.setFont(f)
    c.p.setPen(qc(color))
    c.p.drawText(QRectF(x, y, w, c.h * 0.045), Qt.AlignLeft | Qt.AlignVCenter, label)


def chip(p, rect: QRectF, name: str, color: str, extra: str = "", dim: float = 1.0, mark: str = "",
         avatar: str = "") -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, int(18 * dim)))
    r = rect.height() / 2
    p.drawRoundedRect(rect, r, r)
    d = rect.height() * (0.82 if avatar else 0.5)
    dot = QRectF(rect.x() + rect.height() * (0.1 if avatar else 0.25), rect.center().y() - d / 2, d, d)
    p.setBrush(qc(color, dim * (0.55 if avatar else 1)))
    p.drawEllipse(dot)
    if avatar:  # Emoji im farbigen Kreis
        p.setOpacity(dim)
        text(p, dot, avatar, d * 0.74)
        p.setOpacity(1.0)
    inner = rect.adjusted(rect.height() * 0.95, 0, -rect.height() * 0.35, 0)
    tail = extra or mark
    tail_w = 0.0
    if tail:  # rechts der Wert, der Name bekommt den Rest (überlappt nie)
        tail_w = min(inner.width() * 0.42, QFontMetrics(font(rect.height() * 0.45, True)).horizontalAdvance(tail) + 2)
        text(p, QRectF(inner.right() - tail_w, inner.y(), tail_w, inner.height()), tail, rect.height() * 0.45,
             qc(TEXT, dim), True, Qt.AlignRight | Qt.AlignVCenter)
    name_w = inner.width() - tail_w - (rect.height() * 0.25 if tail else 0)
    text(p, QRectF(inner.x(), inner.y(), name_w, inner.height()), name, rect.height() * 0.45,
         qc(TEXT, dim), align=Qt.AlignLeft | Qt.AlignVCenter)


def scoreboard(c, rect: QRectF, scores: dict, fmt=str, limit: int = 10, marks: dict | None = None) -> None:
    rows = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    if not rows:
        return
    row_h = min(rect.height() / len(rows), c.h * 0.065)
    for i, (pid, score) in enumerate(rows):
        pl = c.hub.players.get(pid)
        if pl is None:
            continue
        # sanft an die neue Position gleiten
        key = ("row", pid)
        y_target = rect.y() + i * row_h
        y = c.fx.vals.get(key, y_target)
        y += (y_target - y) * 0.25
        c.fx.vals[key] = y
        mark = (marks or {}).get(pid, "")
        chip(c.p, QRectF(rect.x(), y, rect.width(), row_h * 0.84), pl.name, pl.color,
             f"{mark}  {fmt(score)}" if mark else fmt(score), avatar=pl.avatar, dim=0.5 if mark in ("RAUS", "✗") else 1.0)


def time_bar(c, rect: QRectF, part: float) -> None:
    """Zeitbalken (part = 1 … 0), wird gegen Ende gelb/rot."""
    part = max(0.0, min(1.0, part))
    p = c.p
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 22))
    r = rect.height() / 2
    p.drawRoundedRect(rect, r, r)
    color = "#22c55e" if part > 0.5 else "#f59e0b" if part > 0.2 else "#ef4444"
    p.setBrush(qc(color))
    p.drawRoundedRect(QRectF(rect.x(), rect.y(), max(rect.height(), rect.width() * part), rect.height()), r, r)


def ring(c, center: QPointF, radius: float, part: float, label: str = "") -> None:
    p = c.p
    pen = QPen(QColor(255, 255, 255, 30), radius * 0.18)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(center, radius, radius)
    part = max(0.0, min(1.0, part))
    color = "#22c55e" if part > 0.5 else "#f59e0b" if part > 0.2 else "#ef4444"
    pen = QPen(qc(color), radius * 0.18)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawArc(QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius), 90 * 16, int(-360 * 16 * part))
    if label:
        text(p, QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius), label, radius * 0.7,
             TEXT, True)


# ---- Teilchen (Konfetti, Funken, Platzen)
def new_fx():
    return SimpleNamespace(parts=[], vals={}, ev=0, born={}, toasts=[])


def burst(fx, x, y, color, now, n=18, speed=320.0, size=6.0, gravity=500.0, life=0.9):
    for _ in range(n):
        a = _rnd.uniform(0, 2 * math.pi)
        v = speed * _rnd.uniform(0.35, 1.0)
        fx.parts.append([x, y, math.cos(a) * v, math.sin(a) * v - speed * 0.25, color, now,
                         life * _rnd.uniform(0.6, 1.0), size * _rnd.uniform(0.6, 1.3), gravity, _rnd.random() * 6])
    del fx.parts[:-900]


def confetti(fx, w, h, now, n=160, colors=None):
    colors = colors or ["#ef4444", "#3b82f6", "#22c55e", "#facc15", "#a855f7", "#ec4899", "#06b6d4", "#f97316"]
    for _ in range(n):
        x = _rnd.uniform(0, w)
        fx.parts.append([x, -_rnd.uniform(0, h * 0.4), _rnd.uniform(-60, 60), _rnd.uniform(60, 220),
                         _rnd.choice(colors), now, _rnd.uniform(2.5, 4.5), _rnd.uniform(6, 12) * h / 900, 120.0,
                         _rnd.random() * 6])
    del fx.parts[:-900]


def draw_particles(c) -> None:
    p, now = c.p, c.now
    keep = []
    p.setPen(Qt.NoPen)
    for part in c.fx.parts:
        x0, y0, vx, vy, color, born, life, size, g, spin = part
        t = now - born
        if t < 0 or t > life:
            if t < 0:
                keep.append(part)
            continue
        keep.append(part)
        x = x0 + vx * t
        y = y0 + vy * t + 0.5 * g * t * t
        alpha = 1.0 if t < life * 0.7 else (life - t) / (life * 0.3)
        p.setBrush(qc(color, alpha))
        p.save()
        p.translate(x, y)
        p.rotate((spin + t * 6) * 57.3)
        p.drawRect(QRectF(-size / 2, -size / 4, size, size / 2) if g < 200 else QRectF(-size / 2, -size / 2, size,
                                                                                         size))
        p.restore()
    c.fx.parts = keep


def float_text(c, x, y, label, color, born, life=1.2, px=None):
    """Kurz aufsteigender Text (+5, TOR!, PENG!)."""
    t = c.now - born
    if t < 0 or t > life:
        return
    a = 1 - max(0.0, (t - life * 0.6) / (life * 0.4))
    scale = ease_back(min(1.0, t / 0.25))
    px = (px or c.h * 0.05) * scale
    text(c.p, QRectF(x - c.w * 0.2, y - t * c.h * 0.06 - px, c.w * 0.4, px * 2), label, px, qc(color, a), True)


_BG_CACHE: dict = {}
BG_SCALE = 8  # Hintergrund in 1/8 Größe rechnen – weiche Verläufe sehen hochskaliert gleich aus
BG_EVERY = 0.12  # Sekunden: die Flecken wandern so langsam, dass öfter neu rechnen nichts bringt


def _paint_background(p: QPainter, w: float, h: float, now: float) -> None:
    from . import stage

    stage.paint_background(p, QRectF(0, 0, w, h))  # Publikumsansicht: gleich wie alle Anzeigen
    if stage.settings["look"] == "schwarz":
        return
    # langsam wandernde, sehr weiche Lichtflecken (Spiele dürfen etwas lebendiger sein)
    for i, color in enumerate((stage.ACCENT, "#ec4899", "#06b6d4")):
        cx = w * (0.5 + 0.38 * math.sin(now * 0.07 + i * 2.1))
        cy = h * (0.5 + 0.35 * math.cos(now * 0.05 + i * 1.7))
        r = max(w, h) * 0.45
        g = QRadialGradient(QPointF(cx, cy), r)
        g.setColorAt(0, qc(color, 0.10))
        g.setColorAt(1, qc(color, 0.0))
        p.fillRect(QRectF(0, 0, w, h), g)


def background(c) -> None:
    """Hintergrund aller Spiele. Früher 4 Verläufe über den ganzen Bildschirm in JEDEM Bild (Full HD: ~10 ms) –
    jetzt klein gerechnet, zwischengespeichert und nur alle 0,12 s neu."""
    from PySide6.QtGui import QImage

    p, w, h, now = c.p, c.w, c.h, c.now
    sw, sh = max(8, int(w) // BG_SCALE), max(8, int(h) // BG_SCALE)
    key = (sw, sh)
    entry = _BG_CACHE.get(key)
    if entry is None or abs(now - entry[0]) >= BG_EVERY:
        img = QImage(sw, sh, QImage.Format_RGB32)
        q = QPainter(img)
        _paint_background(q, sw, sh, now)
        q.end()
        _BG_CACHE.clear()
        _BG_CACHE[key] = entry = (now, img)
    p.save()
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawImage(QRectF(0, 0, w, h), entry[1])
    p.restore()


def qr_card(c, x, y, side, caption=True, image=None):
    p = c.p
    pad = side * 0.06
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(x - pad, y - pad, side + 2 * pad, side + 2 * pad), pad * 1.5, pad * 1.5)
    from .sources import draw_qr

    draw_qr(p, QRectF(x, y, side, side), image if image is not None else c.qr())
    if caption:
        url = c.url()
        room = min(side * 1.3, 2 * (c.w - 8 - (x + side / 2)))
        text(p, QRectF(x + side / 2 - room / 2, y + side + pad * 2, room, side * 0.12), url, side * 0.07, "#93c5fd")


def name_of(c, pid) -> str:
    pl = c.hub.players.get(pid)
    return pl.name if pl else "?"


def color_of(c, pid) -> str:
    pl = c.hub.players.get(pid)
    return pl.color if pl else "#64748b"


def avatar_of(c, pid) -> str:
    pl = c.hub.players.get(pid)
    return pl.avatar if pl else ""


# =========================================================================== Lobby, 3-2-1, Ergebnis
def lobby(c) -> None:
    p, w, h, hub = c.p, c.w, c.h, c.hub
    m = max(14, int(min(w, h) * 0.05))
    wifi = getattr(c, "wifi", None)
    link = c.link_qr() if wifi and getattr(c, "link_qr", None) else None
    if link is not None:  # zwei Codes nebeneinander: 1. WLAN, 2. Seite im Browser
        qr_side = int(min(h * 0.34, w * 0.19))
        left_w = w - 4 * m - 2 * qr_side
    else:
        qr_side = int(min(h * 0.46, w * 0.26))
        left_w = w - 3 * m - qr_side
    tag(c, m, m, left_w, "Minispiele · Handy = Controller")
    spec = hub.spec
    t = c.now - c.fx.born.setdefault(("lobby", hub.game_key), c.now)
    slide = ease_out(t / 0.6)
    text(p, QRectF(m - (1 - slide) * 60, m + h * 0.06, left_w, h * 0.13), spec.title, h * 0.1, qc("#ffffff", slide),
         True, Qt.AlignLeft | Qt.AlignVCenter)
    text(p, QRectF(m, m + h * 0.2, left_w, h * 0.09), spec.help, h * 0.032, MUTED, align=Qt.AlignLeft | Qt.AlignTop,
         wrap=True)
    players = sorted(hub.players.values(), key=lambda pl: pl.joined)
    top = m + h * 0.32
    text(p, QRectF(m, top, left_w, h * 0.055), f"{len(players)} dabei" if players else
         "Noch niemand dabei – QR-Code scannen", h * 0.036, TEXT, True, Qt.AlignLeft | Qt.AlignVCenter)
    chip_h = h * 0.064
    gap = m * 0.35
    if spec.cls.teams:
        col_w = (left_w - gap) / 2
        for team in (0, 1):
            x = m + team * (col_w + gap)
            text(p, QRectF(x, top + h * 0.065, col_w, h * 0.05), TEAM_NAMES[team], h * 0.03, TEAM_COLORS[team], True,
                 Qt.AlignLeft | Qt.AlignVCenter)
            members = [pl for pl in players if pl.team == team]
            for i, pl in enumerate(members[:8]):
                _lobby_chip(c, QRectF(x, top + h * 0.12 + i * (chip_h + gap * 0.6), col_w, chip_h), pl)
    else:
        cols = 3 if left_w > 700 else 2
        cw = (left_w - gap * (cols - 1)) / cols
        for i, pl in enumerate(players):
            r, col = divmod(i, cols)
            _lobby_chip(c, QRectF(m + col * (cw + gap), top + h * 0.075 + r * (chip_h + gap), cw, chip_h), pl)
    text(p, QRectF(m, h - m - h * 0.05, left_w, h * 0.05), "Gestartet wird am PC", h * 0.028, MUTED,
         align=Qt.AlignLeft | Qt.AlignVCenter)
    line = board_line(hub)
    if line:
        text(p, QRectF(m, h - m - h * 0.1, left_w, h * 0.05), line, h * 0.03, "#fbbf24", True,
             Qt.AlignLeft | Qt.AlignVCenter)
    if link is not None:
        qy = (h - qr_side) / 2 - h * 0.02
        for i, (step, title, img, lines) in enumerate((
                ("1", "WLAN", c.wifi_qr(), [(f"„{wifi[0]}“", "#67e8f9", True),
                                            (f"Passwort: {wifi[1]}" if wifi[1] else "", "#fbbf24", True)]),
                ("2", "Mitspielen", link, [("Name + Symbol wählen", TEXT, True),
                                           ("öffnet im Browser", MUTED, False)]))):
            qx = w - m - qr_side - (1 - i) * (qr_side + m)
            text(p, QRectF(qx - m / 2, qy - h * 0.1, qr_side + m, h * 0.06), f"{step} · {title}", h * 0.038, TEXT,
                 True)
            qr_card(c, qx, qy, qr_side, caption=False, image=img)
            for k, (line, color, bold) in enumerate(lines):
                if line:
                    text(p, QRectF(qx - m / 2, qy + qr_side + h * (0.03 + 0.045 * k), qr_side + m, h * 0.04), line,
                         h * 0.024, color, bold)
        return
    if wifi:  # AluPC-WLAN läuft, Webserver (noch) nicht: erst einmal nur der WLAN-Code
        qx, qy = w - m - qr_side, (h - qr_side) / 2 - h * 0.02
        text(p, QRectF(qx - m, qy - h * 0.11, qr_side + 2 * m, h * 0.06), "WLAN scannen", h * 0.04, TEXT, True)
        qr_card(c, qx, qy, qr_side, caption=False, image=c.wifi_qr())
        text(p, QRectF(qx - m * 2, qy + qr_side + h * 0.03, qr_side + 4 * m, h * 0.045),
             f"„{wifi[0]}“", h * 0.026, "#67e8f9", True)
        text(p, QRectF(qx - m * 2, qy + qr_side + h * 0.075, qr_side + 4 * m, h * 0.04),
             "Spiel-Code kommt gleich …", h * 0.024, MUTED)
        return
    # sonst kein Link (einziger Weg ist das AluPC-WLAN) – stattdessen sagen, was los ist
    box = QRectF(w - m - qr_side * 1.15, (h - qr_side) / 2, qr_side * 1.15, qr_side)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 14))
    p.drawRoundedRect(box, 18, 18)
    pulse = 0.55 + 0.45 * abs(math.sin(c.now * 2))
    text(p, QRectF(box.x(), box.y() + box.height() * 0.12, box.width(), box.height() * 0.3), "📶",
         box.height() * 0.18, qc("#67e8f9", pulse))
    text(p, QRectF(box.x() + m * 0.6, box.y() + box.height() * 0.42, box.width() - m * 1.2,
                   box.height() * 0.5), c.wifi_status or "AluPC-WLAN startet …", h * 0.028, TEXT, True,
         wrap=True)


def _lobby_chip(c, rect, pl):
    t = c.now - pl.joined
    s = ease_back(t / 0.45)
    if s <= 0.01:
        return
    c.p.save()
    c.p.translate(rect.center())
    c.p.scale(s, s)
    c.p.translate(-rect.center())
    chip(c.p, rect.translated(0, math.sin(c.now * 2 + pl.joined) * c.h * 0.003), pl.name, pl.color,
         avatar=pl.avatar)
    c.p.restore()


def intro(c) -> None:
    """3 – 2 – 1 vor dem Spiel: Titel, Erklärung, große Zahl."""
    p, w, h, hub = c.p, c.w, c.h, c.hub
    left = hub.intro_until - c.now
    n = max(1, math.ceil(left))
    frac = left - (n - 1)  # 1 → 0 innerhalb jeder Sekunde
    spec = hub.spec
    t = hub.INTRO - left
    slide = ease_out(t / 0.5)
    text(p, QRectF(0, h * 0.1 - (1 - slide) * 40, w, h * 0.12), spec.title, h * 0.09, qc("#ffffff", slide), True)
    text(p, QRectF(w * 0.12, h * 0.23, w * 0.76, h * 0.1), spec.help, h * 0.035, qc(MUTED, slide), wrap=True)
    scale = 0.6 + 0.6 * ease_back(1 - frac) if frac > 0.15 else 1.2 + (0.15 - frac) * 4
    alpha = min(1.0, frac / 0.15) if frac < 0.15 else 1.0
    center = QPointF(w / 2, h * 0.62)
    ring(c, center, h * 0.2, frac)
    p.save()
    p.translate(center)
    p.scale(scale, scale)
    text(p, QRectF(-h * 0.2, -h * 0.2, h * 0.4, h * 0.4), str(n), h * 0.22, qc("#ffffff", alpha), True)
    p.restore()


def podium(c) -> None:
    p, w, h, hub = c.p, c.w, c.h, c.hub
    m = max(14, int(min(w, h) * 0.05))
    if not c.fx.born.get("confetti"):
        c.fx.born["confetti"] = c.now
        confetti(c.fx, w, h, c.now)
    t = c.now - hub.over_at
    tag(c, m, m, w - 2 * m, f"{hub.spec.title} · Ergebnis", "#86efac")
    if hub.spec.cls.teams:
        _team_result(c, m, t)
        return
    ranking = [(pid, s) for pid, s in hub.ranking if pid in hub.players]
    places = [hub.place_of(pid) for pid, _ in ranking]
    slots = [(0, 0.5, 0.42), (1, 0.22, 0.3), (2, 0.78, 0.22)]  # (Index, x-Mitte, Höhe)
    base_y = h * 0.78
    col_w = w * 0.22
    medal = {1: "#fbbf24", 2: "#cbd5e1", 3: "#d97706"}
    key = hub.game_key
    for idx, cx, height in slots:
        if idx >= len(ranking):
            continue
        pid, score = ranking[idx]
        pl = hub.players[pid]
        grow = ease_out((t - 0.25 * (2 - idx)) / 0.7)
        hh = h * height * grow
        rect = QRectF(w * cx - col_w / 2, base_y - hh, col_w, hh)
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, QColor(pl.color))
        g.setColorAt(1, QColor(pl.color).darker(220))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(rect, 14, 14)
        if grow < 0.98:
            continue
        place = places[idx] or idx + 1
        text(p, QRectF(rect.x(), rect.y() + h * 0.02, rect.width(), rect.width() * 0.4), str(place),
             rect.width() * 0.32, medal.get(place, TEXT), True)
        text(p, QRectF(rect.x() - col_w * 0.05, rect.y() - h * 0.08, col_w * 1.1, h * 0.07), pl.name, h * 0.055,
             "#ffffff", True)
        if pl.avatar:  # Avatar über dem Namen, hüpft ein bisschen
            hop = abs(math.sin((c.now - hub.over_at) * 4 + idx)) * h * 0.012
            text(p, QRectF(rect.x(), rect.y() - h * 0.17 - hop, rect.width(), h * 0.09), pl.avatar, h * 0.07)
        gain = hub.last_award.get(pl.name)
        a = ease_out((c.now - hub.over_at - 1.2) / 0.5) if gain else 0.0
        if gain and hh < h * 0.29:  # niedriges Podest: Punkte und Gewinn in einer Zeile (sonst überlappen sie)
            text(p, QRectF(rect.x(), rect.bottom() - h * 0.07, rect.width(), h * 0.06),
                 f"{score_label(key, score)} · +{gain}", h * 0.03, "#ffffff")
        else:
            text(p, QRectF(rect.x(), rect.bottom() - h * 0.07, rect.width(), h * 0.06), score_label(key, score),
                 h * 0.032, "#ffffff")
            if gain:
                text(p, QRectF(rect.x(), rect.bottom() - h * 0.125, rect.width(), h * 0.05), f"+{gain} gesamt",
                     h * 0.03, qc("#fbbf24", a), True)
    rest = ranking[3:10]
    if rest:
        line = "   ".join(f"{places[i + 3]}. {hub.players[pid].name}" for i, (pid, _s) in enumerate(rest))
        text(p, QRectF(m, base_y + h * 0.03, w - 2 * m, h * 0.05), line, h * 0.028, MUTED)
    _board_footer(c, m)


def board_line(hub, top: int = 3) -> str:
    rows = hub.board_ranking()[:top]
    if not rows:
        return ""
    return "Bestenliste: " + "  ·  ".join(f"{place}. {name} {pts}" for name, pts, place in rows)


def _board_footer(c, m):
    p, w, h = c.p, c.w, c.h
    line = board_line(c.hub)
    if line:
        text(p, QRectF(m, h - m - h * 0.1, w - 2 * m, h * 0.05), line, h * 0.03, "#fbbf24", True)
    text(p, QRectF(m, h - m - h * 0.05, w - 2 * m, h * 0.05), "Das nächste Spiel startet am PC", h * 0.028, MUTED)


def board(c) -> None:
    """Bestenliste über den ganzen Abend: Balken wachsen nacheinander, die ersten drei mit Medaille."""
    p, w, h, hub = c.p, c.w, c.h, c.hub
    m = max(14, int(min(w, h) * 0.05))
    t = c.now - hub.board_at
    if not c.fx.born.get("confetti") and hub.board:
        c.fx.born["confetti"] = c.now
        confetti(c.fx, w, h, c.now, n=120)
    tag(c, m, m, w - 2 * m, "Bestenliste des Abends", "#fbbf24")
    games = hub.board_games
    s = ease_back(t / 0.5)
    p.save()
    p.translate(w / 2, m + h * 0.1)
    p.scale(s, s)
    text(p, QRectF(-w * 0.45, -h * 0.05, w * 0.9, h * 0.1),
         f"nach {games} {'Spiel' if games == 1 else 'Spielen'}" if games else "Noch keine Punkte", h * 0.06,
         "#ffffff", True)
    p.restore()
    rows = hub.board_ranking()[:16]
    if not rows:
        text(p, QRectF(0, h * 0.4, w, h * 0.1), "Erst ein Spiel spielen – dann gibt es hier Punkte", h * 0.04, MUTED)
        return
    cols = 1 if len(rows) <= 8 else 2
    per_col = math.ceil(len(rows) / cols)
    top = m + h * 0.2
    row_h = min(h * 0.085, (h - top - m) / per_col)
    col_w = (w - 2 * m - (cols - 1) * m) / cols
    best = max(pts for _n, pts, _pl in rows) or 1
    medal = {1: "#fbbf24", 2: "#cbd5e1", 3: "#d97706"}
    # von unten nach oben aufbauen: Platz 1 kommt zuletzt (Spannung)
    for i, (name, pts, place) in enumerate(rows):
        col, r = divmod(i, per_col)
        x = m + col * (col_w + m)
        y = top + r * row_h
        delay = (len(rows) - 1 - i) * 0.18
        k = ease_out((t - 0.4 - delay) / 0.6)
        if k <= 0:
            continue
        circle = row_h * 0.7
        p.setPen(Qt.NoPen)
        p.setBrush(qc(medal.get(place, "#334155"), k))
        p.drawEllipse(QRectF(x, y + (row_h - circle) / 2, circle, circle))
        text(p, QRectF(x, y + (row_h - circle) / 2, circle, circle), str(place), circle * 0.5,
             qc("#0b1020" if place in medal else TEXT, k), True)
        bar_x = x + circle + row_h * 0.25
        bar_w = (col_w - circle - row_h * 0.25) * (0.35 + 0.65 * pts / best) * k
        bar = QRectF(bar_x, y + row_h * 0.14, bar_w, row_h * 0.72)
        color = QColor(hub.color_of_name(name))
        g = QLinearGradient(bar.topLeft(), bar.topRight())
        g.setColorAt(0, qc(color.darker(150).name(), 0.9 * k))
        g.setColorAt(1, qc(color.name(), 0.9 * k))
        p.setBrush(g)
        p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        inner = bar.adjusted(row_h * 0.3, 0, -row_h * 0.3, 0)
        avatar = hub.avatar_of_name(name)
        text(p, inner, f"{avatar}  {name}" if avatar else name, row_h * 0.4, qc("#ffffff", k), True,
             Qt.AlignLeft | Qt.AlignVCenter)
        text(p, inner, str(int(pts * k)), row_h * 0.42, qc("#ffffff", k), True, Qt.AlignRight | Qt.AlignVCenter)
        if place == 1 and k >= 1 and ("crown", name) not in c.fx.born:
            c.fx.born[("crown", name)] = c.now
            burst(c.fx, bar.right(), bar.center().y(), "#fbbf24", c.now, n=40, speed=380)


def score_label(key: str, score) -> str:
    if key == "rennen":
        return "Im Ziel" if score >= 1000 else f"{int(score)} Tipps"
    if key == "simon":
        return f"Runde {int(score)}"
    if key == "flappy":
        return f"{int(score)} Röhren"
    if key == "tetris":
        return clock_text(score) + " durchgehalten"
    return f"{int(score)} Punkte"


def _team_result(c, m, t):
    p, w, h, hub = c.p, c.w, c.h, c.hub
    win = hub.team_result()
    s = ease_back(t / 0.6)
    title = "Unentschieden!" if win is None else f"{TEAM_NAMES[win]} gewinnt!"
    color = "#ffffff" if win is None else TEAM_COLORS[win]
    p.save()
    p.translate(w / 2, h * 0.24)
    p.scale(s, s)
    text(p, QRectF(-w * 0.45, -h * 0.08, w * 0.9, h * 0.16), title, h * 0.1, color, True)
    p.restore()
    game = hub.game
    extra = ""
    if hasattr(game, "goals"):
        extra = f"{game.goals[0]} : {game.goals[1]}"
    if extra:
        text(p, QRectF(0, h * 0.33, w, h * 0.07), extra, h * 0.06, TEXT, True)
    col_w = w * 0.36
    scores = dict(hub.ranking)
    for team in (0, 1):
        x = w / 2 - col_w - m / 2 if team == 0 else w / 2 + m / 2
        y0 = h * 0.43
        text(p, QRectF(x, y0, col_w, h * 0.05), TEAM_NAMES[team], h * 0.036, TEAM_COLORS[team], True,
             Qt.AlignLeft | Qt.AlignVCenter)
        members = sorted((pid for pid, pl in hub.players.items() if pl.team == team and pid in scores),
                         key=lambda pid: -scores[pid])
        unit = getattr(game, "unit", "Treffer" if hasattr(game, "goals") else "Züge")
        for i, pid in enumerate(members[:7]):
            appear = ease_out((t - 0.4 - i * 0.08) / 0.4)
            if appear <= 0:
                continue
            chip(p, QRectF(x - (1 - appear) * 30, y0 + h * 0.065 + i * h * 0.062, col_w, h * 0.054),
                 hub.players[pid].name, hub.players[pid].color, f"{int(scores[pid] % 1000)} {unit}", dim=appear)
    _board_footer(c, m)


# =========================================================================== Simon sagt
def simon(c, g, events) -> None:
    from .games_party import SIMON_PADS

    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(14, int(min(w, h) * 0.045))
    for _n, t, kind, data in events:
        if kind == "out":
            c.fx.born[("out", data["pid"])] = t
        elif kind == "go":
            c.fx.born["go"] = t
    tag(c, m, m, w * 0.6, f"Simon sagt · Runde {g.round} · {len(g.seq)} Farben · noch {len(g.alive)} im Spiel")
    size = min(h * 0.56, w * 0.42)
    center = QPointF(w * 0.3, h * 0.45)
    lit = g.lit(now)
    outer, inner = size / 2, size * 0.2
    for i, (_name, color) in enumerate(SIMON_PADS):
        start = [90, 0, 180, 270][i]  # grün oben links, rot oben rechts, gelb unten links, blau unten rechts
        path = QPainterPath()
        path.arcMoveTo(QRectF(center.x() - outer, center.y() - outer, 2 * outer, 2 * outer), start + 2)
        path.arcTo(QRectF(center.x() - outer, center.y() - outer, 2 * outer, 2 * outer), start + 2, 86)
        path.arcTo(QRectF(center.x() - inner, center.y() - inner, 2 * inner, 2 * inner), start + 88, -86)
        path.closeSubpath()
        on = lit == i
        col = QColor(color)
        if on:
            glow = QRadialGradient(center, outer * 1.3)
            glow.setColorAt(0.5, qc(color, 0.55))
            glow.setColorAt(1, qc(color, 0.0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(center, outer * 1.3, outer * 1.3)
        p.setPen(Qt.NoPen)
        p.setBrush(col.lighter(135) if on else col.darker(260))
        p.drawPath(path)
    p.setBrush(QColor("#0f172a"))
    p.drawEllipse(center, inner * 0.92, inner * 0.92)
    label = {"zeigen": "Merken!", "eingabe": "Jetzt ihr!", "pause": "…"}[g.phase]
    text(p, QRectF(center.x() - inner, center.y() - inner * 0.6, 2 * inner, inner * 0.7), label, inner * 0.32,
         "#ffffff", True)
    if g.phase == "eingabe":
        part = (g.until - now) / (3.0 + 0.9 * len(g.seq))
        ring(c, QPointF(center.x(), center.y() + inner * 0.35), inner * 0.32, part)
    elif g.phase == "pause" and g.all_failed:
        text(p, QRectF(center.x() - outer, center.y() + outer + h * 0.01, 2 * outer, h * 0.05),
             "Alle falsch – keiner fliegt raus!", h * 0.035, "#fbbf24", True)
    # Folge als Punkte (beim Eingeben)
    if g.phase != "zeigen":
        dot = min(h * 0.022, (outer * 2) / max(1, len(g.seq)) / 2.6)
        total = len(g.seq) * dot * 2.6
        p.setPen(Qt.NoPen)
        for k, pad in enumerate(g.seq):
            # beim Eingeben nur graue Punkte (sonst könnte man die Folge ablesen)
            p.setBrush(qc(SIMON_PADS[pad][1], 0.85) if g.phase == "pause" else QColor(255, 255, 255, 50))
            p.drawEllipse(QPointF(center.x() - total / 2 + dot * 1.3 + k * dot * 2.6, center.y() + outer + h * 0.06),
                          dot, dot)
    # Spieler rechts
    side = QRectF(w * 0.6, h * 0.12, w * 0.4 - m, h * 0.8)
    pids = sorted(g.players, key=lambda pid: (pid not in g.alive, name_of(c, pid)))
    row_h = min(side.height() / max(1, len(pids)), h * 0.07)
    for i, pid in enumerate(pids):
        rect = QRectF(side.x(), side.y() + i * row_h, side.width(), row_h * 0.84)
        out = pid not in g.alive
        born = c.fx.born.get(("out", pid))
        shake = 0.0
        if born is not None and now - born < 0.6:
            shake = math.sin((now - born) * 60) * h * 0.008
            if ("outfx", pid) not in c.fx.born:
                c.fx.born[("outfx", pid)] = now
                burst(c.fx, rect.center().x(), rect.center().y(), "#ef4444", now, n=22, speed=260)
        if out:
            mark = "RAUS"
        elif g.phase == "eingabe":
            mark = "✓" if pid in g.done else "✗" if pid in g.failed else f"{g.progress.get(pid, 0)}/{len(g.seq)}"
        else:
            mark = ""
        chip(p, rect.translated(shake, 0), name_of(c, pid), color_of(c, pid), avatar=avatar_of(c, pid), mark=mark, dim=0.35 if out else 1)
        if g.phase == "eingabe" and not out and pid not in g.done:  # Fortschritt
            part = g.progress.get(pid, 0) / max(1, len(g.seq))
            p.setPen(Qt.NoPen)
            p.setBrush(qc(color_of(c, pid), 0.35))
            p.drawRoundedRect(QRectF(rect.x(), rect.bottom() - 4, rect.width() * part, 4), 2, 2)


# =========================================================================== Pong
def pong(c, g, events) -> None:
    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(10, int(min(w, h) * 0.03))
    top = m + h * 0.1
    scale = min((w - 2 * m) / g.W, (h - top - m) / g.H)
    fw, fh = g.W * scale, g.H * scale
    fx0, fy0 = (w - fw) / 2, top + (h - top - m - fh) / 2
    field = QRectF(fx0, fy0, fw, fh)

    def pt(x, y):
        return QPointF(fx0 + x * scale, fy0 + y * scale)

    for _n, t, kind, data in events:
        if kind == "hit":
            q = pt(data["x"], data["y"])
            burst(c.fx, q.x(), q.y(), color_of(c, data["pid"]), now, n=14, speed=260, size=5, gravity=0)
        elif kind == "goal":
            c.fx.born["goal"] = t
            c.fx.vals["goal_team"] = data["team"]
            q = pt(0 if data["team"] == 1 else g.W, g.H / 2)
            burst(c.fx, q.x(), q.y(), TEAM_COLORS[data["team"]], now, n=50, speed=420)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 10))
    p.drawRoundedRect(field.adjusted(-6, -6, 6, 6), 16, 16)
    for team, rect in ((0, QRectF(fx0, fy0, fw / 2, fh)), (1, QRectF(fx0 + fw / 2, fy0, fw / 2, fh))):
        grad = QLinearGradient(rect.topLeft() if team == 0 else rect.topRight(), rect.center())
        grad.setColorAt(0, qc(TEAM_COLORS[team], 0.16))
        grad.setColorAt(1, qc(TEAM_COLORS[team], 0.0))
        p.fillRect(rect, grad)
    goal_born = c.fx.born.get("goal")
    if goal_born is not None and now - goal_born < 0.8:
        a = 1 - (now - goal_born) / 0.8
        team = c.fx.vals.get("goal_team", 0)
        p.fillRect(QRectF(fx0 + (0 if team == 1 else fw / 2), fy0, fw / 2, fh), qc(TEAM_COLORS[team], 0.3 * a))
    pen = QPen(QColor(255, 255, 255, 50), max(2, scale * 0.06), Qt.DashLine)
    p.setPen(pen)
    p.drawLine(pt(g.W / 2, 0), pt(g.W / 2, g.H))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(pt(g.W / 2, g.H / 2), scale * 1.2, scale * 1.2)
    # Stand
    pop = 1.0
    if goal_born is not None and now - goal_born < 0.5:
        pop = 1 + 0.4 * (1 - (now - goal_born) / 0.5)
    p.save()
    p.translate(w / 2, m + h * 0.045)
    p.scale(pop, pop)
    text(p, QRectF(-w * 0.1, -h * 0.045, w * 0.2, h * 0.09), f"{g.goals[0]} : {g.goals[1]}", h * 0.07, TEXT, True)
    p.restore()
    text(p, QRectF(m, m, w * 0.35, h * 0.08), TEAM_NAMES[0], h * 0.04, TEAM_COLORS[0], True,
         Qt.AlignLeft | Qt.AlignVCenter)
    text(p, QRectF(w - m - w * 0.35, m, w * 0.35, h * 0.08), TEAM_NAMES[1], h * 0.04, TEAM_COLORS[1], True,
         Qt.AlignRight | Qt.AlignVCenter)
    text(p, QRectF(w * 0.6, m + h * 0.05, w * 0.4 - m, h * 0.04), f"bis {g.to_win}", h * 0.024, MUTED,
         align=Qt.AlignLeft | Qt.AlignVCenter)
    # Schläger
    for pid, pad in g.paddles.items():
        center = pt(pad["x"], pad["y"])
        rect = QRectF(center.x() - g.PW * scale / 2, center.y() - g.PH * scale / 2, g.PW * scale, g.PH * scale)
        glow = QRadialGradient(center, g.PH * scale * 0.7)
        glow.setColorAt(0, qc(TEAM_COLORS[pad["team"]], 0.35))
        glow.setColorAt(1, qc(TEAM_COLORS[pad["team"]], 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(center, g.PH * scale * 0.7, g.PH * scale * 0.7)
        p.setBrush(qc(color_of(c, pid)))
        p.drawRoundedRect(rect, rect.width() / 2, rect.width() / 2)
        lx = rect.right() + 6 if pad["team"] == 0 else rect.left() - 6 - w * 0.1
        text(p, QRectF(lx, rect.center().y() - h * 0.016, w * 0.1, h * 0.032), name_of(c, pid), h * 0.022,
             qc(TEXT, 0.8), align=(Qt.AlignLeft if pad["team"] == 0 else Qt.AlignRight) | Qt.AlignVCenter)
    # Ball mit Schweif
    for i, (x, y) in enumerate(g.trail):
        a = (i + 1) / max(1, len(g.trail)) * 0.35
        p.setBrush(qc("#ffffff", a))
        p.drawEllipse(pt(x, y), g.R * scale * (0.4 + 0.6 * a / 0.35), g.R * scale * (0.4 + 0.6 * a / 0.35))
    waiting = now < g.serve_at
    if not waiting or int(now * 6) % 2 == 0:
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(pt(g.bx, g.by), g.R * scale, g.R * scale)
    if goal_born is not None and now - goal_born < 1.1:
        float_text(c, w / 2, h * 0.45, "TOR!", TEAM_COLORS[c.fx.vals.get("goal_team", 0)], goal_born, 1.1, h * 0.12)


# =========================================================================== Tetris
def _block(p, rect: QRectF, color: str, ghost: bool = False) -> None:
    if ghost:
        p.setPen(QPen(qc(color, 0.55), max(1.0, rect.width() * 0.08)))
        p.setBrush(qc(color, 0.12))
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), rect.width() * 0.18, rect.width() * 0.18)
        return
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor(color).lighter(135))
    grad.setColorAt(1, QColor(color).darker(115))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), rect.width() * 0.18, rect.width() * 0.18)


def tetris(c, g, events) -> None:
    from .games_tetris import COLORS, H as TH, SHAPES, W as TW

    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(12, int(min(w, h) * 0.03))
    for _n, t, kind, data in events:
        if kind in ("lines", "out", "win"):
            c.fx.born[(kind, data["pid"], t)] = t
    alive = len(g.alive())
    tag(c, m, m, w * 0.7, f"Tetris · alle dieselben Teile · noch {alive} von {len(g.boards)} im Spiel")
    text(p, QRectF(w - m - w * 0.25, m, w * 0.25, h * 0.045),
         f"Stufe {g.level(now) + 1} · {clock_text(now - g.start)}", h * 0.034, TEXT, True,
         Qt.AlignRight | Qt.AlignVCenter)
    pids = list(g.boards)
    n = max(1, len(pids))
    top = m + h * 0.07
    area_w, area_h = w - 2 * m, h - top - m
    # Raster so wählen, dass die Felder (10 × 20 + Kopfzeile) möglichst groß werden
    best = (0, 1, 1)
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cw, ch = area_w / cols, area_h / rows
        cell = min((cw * 0.92) / (TW + 4.5), (ch * 0.86) / TH)
        if cell > best[0]:
            best = (cell, cols, rows)
    cell, cols, rows = best
    cw, ch = area_w / cols, area_h / rows
    for i, pid in enumerate(pids):
        b = g.boards[pid]
        r, col = divmod(i, cols)
        bw, bh = cell * TW, cell * TH
        x0 = m + col * cw + (cw - bw - cell * 4.5) / 2
        y0 = top + r * ch + ch * 0.1
        color = color_of(c, pid)
        # Kopf: Name + Reihen
        text(p, QRectF(x0, y0 - ch * 0.09, bw, ch * 0.08), name_of(c, pid), min(h * 0.03, ch * 0.06), TEXT, True,
             Qt.AlignLeft | Qt.AlignVCenter)
        field = QRectF(x0, y0, bw, bh)
        p.setPen(QPen(qc(color, 0.9 if b["alive"] else 0.3), max(2.0, cell * 0.12)))
        p.setBrush(QColor(10, 12, 28, 230))
        p.drawRoundedRect(field.adjusted(-cell * 0.15, -cell * 0.15, cell * 0.15, cell * 0.15), cell * 0.3, cell * 0.3)
        grid = g.view(pid)
        for yy in range(TH):
            for xx in range(TW):
                v = grid[yy][xx]
                if not v:
                    continue
                rect = QRectF(x0 + xx * cell, y0 + yy * cell, cell, cell)
                _block(p, rect, COLORS.get(v.upper(), "#64748b") if b["alive"] else "#475569", ghost=v.islower())
        # nächstes Teil (für alle gleich – nur je nach Tempo an anderer Stelle der Folge)
        nx, ny, small = x0 + bw + cell * 0.8, y0, cell * 0.8
        text(p, QRectF(nx, ny, cell * 3.6, cell), "NÄCHSTES", cell * 0.55, MUTED, True, Qt.AlignLeft)
        if b["alive"]:
            nk = g.piece_at(b["index"])
            for px, py in SHAPES[nk][0]:
                _block(p, QRectF(nx + px * small, ny + cell * 1.3 + py * small, small, small), COLORS[nk])
        text(p, QRectF(nx, ny + cell * 4.4, cell * 3.6, cell * 1.2), str(b["lines"]), cell * 1.0, TEXT, True,
             Qt.AlignLeft)
        text(p, QRectF(nx, ny + cell * 5.5, cell * 3.6, cell * 0.9), "Reihen", cell * 0.5, MUTED, False,
             Qt.AlignLeft)
        text(p, QRectF(nx, ny + cell * 6.8, cell * 3.6, cell * 1.0), clock_text(g.survived(pid, now)),
             cell * 0.75, TEXT, True, Qt.AlignLeft)
        # Effekte
        for key, t in list(c.fx.born.items()):
            if not (isinstance(key, tuple) and len(key) == 3 and key[1] == pid):
                continue
            if key[0] == "lines" and now - t < 1.0:
                float_text(c, x0 + bw / 2, y0 + bh * 0.5, "REIHE!", "#4ade80", t, 1.0, cell * 1.6)
            elif key[0] == "out":
                if ("outfx", pid) not in c.fx.born:
                    c.fx.born[("outfx", pid)] = now
                    burst(c.fx, x0 + bw / 2, y0 + bh * 0.2, color, now, n=30, speed=360, size=7)
        if not b["alive"]:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 120))
            p.drawRect(field)
            place = len(g.boards) - g.order_out.index(pid) if pid in g.order_out else 1
            text(p, QRectF(x0, y0 + bh * 0.4, bw, cell * 2.2), f"Platz {place}", cell * 1.5, "#f87171", True)
        elif g.end_at is not None:
            text(p, QRectF(x0, y0 + bh * 0.4, bw, cell * 2.2), "🏆", cell * 2.0, "#fbbf24", True)


# =========================================================================== Snake, Tipp-Rennen
def schlangen(c, g, events) -> None:
    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(10, int(min(w, h) * 0.03))
    side_w = w * 0.22
    area = QRectF(m, m + h * 0.06, w - 3 * m - side_w, h - 2 * m - h * 0.06)
    cell = min(area.width() / g.W, area.height() / g.H)
    bx = area.x() + (area.width() - cell * g.W) / 2
    by = area.y() + (area.height() - cell * g.H) / 2
    board = QRectF(bx, by, cell * g.W, cell * g.H)
    for _n, t, kind, data in events:
        x, y = data["cell"]
        q = (bx + (x + 0.5) * cell, by + (y + 0.5) * cell)
        if kind == "eat":
            burst(c.fx, q[0], q[1], "#fbbf24", now, n=10, speed=160, size=4, gravity=0, life=0.5)
        elif kind == "crash":
            burst(c.fx, q[0], q[1], color_of(c, data["pid"]), now, n=30, speed=300, size=6)
            if data.get("wall"):
                c.fx.born["wall"] = t
    tag(c, m, m, area.width(), "Snake")
    left = g.remaining(now)
    text(p, QRectF(m, m, area.width(), h * 0.05), clock_text(left), h * 0.04, "#fbbf24" if left < 10 else TEXT, True,
         Qt.AlignRight | Qt.AlignVCenter)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 10))
    p.drawRoundedRect(board.adjusted(-4, -4, 4, 4), 10, 10)
    # Wand: der Rand ist tödlich – rot leuchtend (blitzt kurz auf, wenn jemand dagegen fährt)
    hit = c.fx.born.get("wall")
    flash = 1 - (now - hit) / 0.5 if hit is not None and now - hit < 0.5 else 0.0
    wall = max(3.0, cell * 0.22)
    p.setPen(QPen(qc("#ef4444", 0.55 + 0.45 * flash), wall))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(board.adjusted(-wall / 2 - 2, -wall / 2 - 2, wall / 2 + 2, wall / 2 + 2), 10, 10)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#fbbf24"))
    pulse = 0.32 + 0.06 * math.sin(now * 6)
    for fx_, fy in g.food:
        p.drawEllipse(QPointF(bx + (fx_ + 0.5) * cell, by + (fy + 0.5) * cell), cell * pulse, cell * pulse)
    for pid, s in g.snakes.items():
        if not s["alive"]:
            continue
        color = QColor(color_of(c, pid))
        for k, (x, y) in enumerate(s["body"]):
            p.setBrush(color.lighter(125) if k == 0 else color)
            inset = cell * (0.06 if k == 0 else 0.12)
            p.drawRoundedRect(QRectF(bx + x * cell + inset, by + y * cell + inset, cell - 2 * inset,
                                     cell - 2 * inset), cell * 0.3, cell * 0.3)
        hx, hy = s["body"][0]
        label = QRectF(bx + hx * cell - cell * 3, by + hy * cell - cell * 1.05, cell * 7, cell * 0.9)
        text(p, label.translated(1, 1), name_of(c, pid), cell * 0.62, qc("#000000", 0.6))
        text(p, label, name_of(c, pid), cell * 0.62, "#ffffff")
        p.setPen(Qt.NoPen)
    scoreboard(c, QRectF(w - m - side_w, m + h * 0.06, side_w, h * 0.62), g.scores(), limit=8)
    q = min(side_w * 0.5, h * 0.18)
    x, y = w - m - side_w / 2 - q / 2, h - m - q - q * 0.06 - h * 0.04
    if c.wifi:  # einsteigen nur übers AluPC-WLAN: WLAN-Code
        qr_card(c, x, y, q, caption=False, image=c.wifi_qr())
        text(p, QRectF(w - m - side_w, h - m - h * 0.035, side_w, h * 0.035), "Einsteigen", h * 0.022, MUTED)


def rennen(c, g, events) -> None:
    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(14, int(min(w, h) * 0.04))
    tag(c, m, m, w - 2 * m, f"Tipp-Rennen · {g.goal} Tipps bis zum Ziel")
    pids = list(g.progress)
    top = m + h * 0.08
    lane_h = min((h - top - m) / max(1, len(pids)), h * 0.14)
    name_w = w * 0.18
    track_x = m + name_w + m * 0.5 + lane_h * 0.36  # Platz für die Kugel am Start (sonst verdeckt sie den Namen)
    track_w = w - track_x - m - h * 0.06
    for _n, t, kind, data in events:
        if kind == "finish" and data["pid"] in pids:
            i = pids.index(data["pid"])
            burst(c.fx, track_x + track_w, top + i * lane_h + lane_h / 2, color_of(c, data["pid"]), now, n=40)
    for i, pid in enumerate(pids):
        y = top + i * lane_h
        cy = y + lane_h / 2
        text(p, QRectF(m, y, name_w, lane_h), name_of(c, pid), lane_h * 0.32, TEXT, align=Qt.AlignRight |
             Qt.AlignVCenter)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 16))
        p.drawRoundedRect(QRectF(track_x, cy - lane_h * 0.14, track_w, lane_h * 0.28), lane_h * 0.14, lane_h * 0.14)
        key = ("share", pid)
        target = min(1.0, g.progress[pid] / g.goal)
        share = c.fx.vals.get(key, 0.0)
        share += (target - share) * 0.35
        c.fx.vals[key] = share
        color = QColor(color_of(c, pid))
        p.setBrush(color.darker(140))
        p.drawRoundedRect(QRectF(track_x, cy - lane_h * 0.14, track_w * share, lane_h * 0.28), lane_h * 0.14,
                          lane_h * 0.14)
        r = lane_h * 0.3
        recent = now - g.last_tap.get(pid, -9) < 0.12
        p.setBrush(color.lighter(130) if recent else color)
        p.drawEllipse(QPointF(track_x + track_w * share, cy), r * (1.1 if recent else 1), r * (1.1 if recent else 1))
        if pid in g.finish:
            text(p, QRectF(track_x + track_w * share - r, cy - r, 2 * r, 2 * r), str(g.finish.index(pid) + 1), r * 1.1,
                 "#ffffff", True)
    fx = track_x + track_w + h * 0.02
    sq = h * 0.015
    for k in range(int((h - top - m) / sq)):
        for j in range(2):
            p.fillRect(QRectF(fx + j * sq, top + k * sq, sq, sq), QColor("#ffffff") if (k + j) % 2 else
                       QColor("#111827"))


# =========================================================================== Schere, Stein, Papier
def ssp(c, g, events) -> None:
    from .games_retro import RPS

    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(14, int(min(w, h) * 0.045))
    for _n, t, kind, data in events:
        if kind == "reveal":
            c.fx.born["rps_reveal"] = t
        elif kind == "pick":
            c.fx.born[("pick", data["pid"])] = t
    side_w = w * 0.24
    main = QRectF(m, m, w - 3 * m - side_w, h - 2 * m)
    tag(c, m, m, main.width(), f"Schere, Stein, Papier · Runde {g.round} / {g.rounds}", "#fda4af")
    col_w = main.width() / 3
    if g.phase == "waehlen":
        text(p, QRectF(main.x(), main.y() + h * 0.08, main.width(), h * 0.09), "Geheim wählen!", h * 0.07, TEXT, True)
        time_bar(c, QRectF(main.x() + main.width() * 0.2, main.y() + h * 0.19, main.width() * 0.6, h * 0.018),
                 (g.until - now) / g.CHOOSE)
        for i, (_k, label, emoji) in enumerate(RPS):  # die drei Zeichen wippen im Takt
            cx = main.x() + col_w * (i + 0.5)
            bob = math.sin(now * 5 + i) * h * 0.02
            text(p, QRectF(cx - col_w / 2, main.y() + h * 0.27 + bob, col_w, h * 0.22), emoji, h * 0.16)
            text(p, QRectF(cx - col_w / 2, main.y() + h * 0.5, col_w, h * 0.06), label, h * 0.04, MUTED, True)
        players = [pid for pid in g.score]
        chip_w = min(main.width() / 4, w * 0.16)
        for i, pid in enumerate(players[:16]):
            r_, col = divmod(i, 4)
            rect = QRectF(main.x() + col * (chip_w + m * 0.3), main.y() + h * 0.62 + r_ * h * 0.075, chip_w, h * 0.06)
            done = pid in g.picks
            chip(p, rect, name_of(c, pid), color_of(c, pid), "✓" if done else "…", dim=1.0 if done else 0.5,
                 avatar=avatar_of(c, pid))
    else:
        t = now - c.fx.born.get("rps_reveal", now)
        counts = g.counts()
        for i, (key, label, emoji) in enumerate(RPS):
            k = ease_back(min(1.0, max(0.0, (t - i * 0.15) / 0.4)))
            cx = main.x() + col_w * (i + 0.5)
            p.save()
            p.translate(cx, main.y() + h * 0.24)
            p.scale(k, k)
            text(p, QRectF(-col_w / 2, -h * 0.11, col_w, h * 0.22), emoji, h * 0.15)
            p.restore()
            text(p, QRectF(cx - col_w / 2, main.y() + h * 0.37, col_w, h * 0.06), f"{label} · {counts[key]}×",
                 h * 0.038, TEXT, True)
            pids = [pid for pid, v in g.picks.items() if v == key]
            for j, pid in enumerate(pids[:7]):
                gained = g.gained.get(pid, 0)
                chip(p, QRectF(cx - col_w * 0.45, main.y() + h * 0.45 + j * h * 0.068, col_w * 0.9, h * 0.058),
                     name_of(c, pid), color_of(c, pid), f"+{gained}" if gained else "0", dim=k,
                     avatar=avatar_of(c, pid))
    scoreboard(c, QRectF(w - m - side_w, m + h * 0.07, side_w, h * 0.8), g.scores())


# =========================================================================== Tic-Tac-Toe
def _mark(p, rect: QRectF, mark: str, grow: float = 1.0, alpha: float = 1.0) -> None:
    r = rect.adjusted(rect.width() * 0.2, rect.height() * 0.2, -rect.width() * 0.2, -rect.height() * 0.2)
    if grow < 1:
        k = (1 - grow) * r.width() / 2
        r = r.adjusted(k, k, -k, -k)
    color = TEAM_COLORS[0] if mark == "X" else TEAM_COLORS[1]
    pen = QPen(qc(color, alpha), max(4.0, rect.width() * 0.11))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if mark == "X":
        p.drawLine(r.topLeft(), r.bottomRight())
        p.drawLine(r.topRight(), r.bottomLeft())
    else:
        p.drawEllipse(r)


def tictactoe(c, g, events) -> None:
    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(10, int(min(w, h) * 0.03))
    for _n, t, kind, data in events:
        if kind == "place":
            c.fx.born[f"ttt{data['cell']}"] = t
        elif kind == "line":
            c.fx.born["tttline"] = t
            confetti(c.fx, w, h, now, n=90, colors=[TEAM_COLORS[data["team"]], "#ffffff"])
    side = min(h * 0.72, w * 0.5)
    board = QRectF((w - side) / 2, h * 0.17, side, side)
    cs = side / 3
    # Kopf: Teams, Stand, wer dran ist
    text(p, QRectF(m, m, w * 0.3, h * 0.07), f"✕  {TEAM_NAMES[0]}", h * 0.042, TEAM_COLORS[0], True,
         Qt.AlignLeft | Qt.AlignVCenter)
    text(p, QRectF(w - m - w * 0.3, m, w * 0.3, h * 0.07), f"{TEAM_NAMES[1]}  ◯", h * 0.042, TEAM_COLORS[1], True,
         Qt.AlignRight | Qt.AlignVCenter)
    text(p, QRectF(w * 0.35, m, w * 0.3, h * 0.07), f"{g.wins[0]} : {g.wins[1]}", h * 0.06, TEXT, True)
    text(p, QRectF(w * 0.35, m + h * 0.065, w * 0.3, h * 0.04), f"Runde {g.round} / {g.rounds}", h * 0.026, MUTED)
    if g.result == "draw":
        status, color = "Unentschieden!", TEXT
    elif g.result:
        team = 0 if g.result == "X" else 1
        status, color = f"{TEAM_NAMES[team]} gewinnt die Runde!", TEAM_COLORS[team]
    else:
        status, color = f"{TEAM_NAMES[g.turn]} ist dran", TEAM_COLORS[g.turn]
    text(p, QRectF(0, board.bottom() + h * 0.02, w, h * 0.06), status, h * 0.045, color, True)
    if not g.result:  # Bedenkzeit
        part = g.time_left(now) / max(0.1, g.think)
        time_bar(c, QRectF(board.x(), board.bottom() + h * 0.085, board.width(), h * 0.012), part)
        # leuchtender Rand in der Farbe des Teams, das dran ist
        glow = 0.25 + 0.15 * math.sin(now * 4)
        p.setPen(QPen(qc(TEAM_COLORS[g.turn], glow), max(4, side * 0.02)))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(board.adjusted(-side * 0.03, -side * 0.03, side * 0.03, side * 0.03), 18, 18)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 12))
    p.drawRoundedRect(board, 16, 16)
    grid = QPen(QColor(255, 255, 255, 70), max(3, side * 0.012))
    grid.setCapStyle(Qt.RoundCap)
    p.setPen(grid)
    for k in (1, 2):
        p.drawLine(QPointF(board.x() + k * cs, board.y() + cs * 0.12), QPointF(board.x() + k * cs, board.bottom() - cs * 0.12))
        p.drawLine(QPointF(board.x() + cs * 0.12, board.y() + k * cs), QPointF(board.right() - cs * 0.12, board.y() + k * cs))
    # Stimmen des Teams, das gerade dran ist (kleine Punkte im Feld)
    counts: dict[int, list] = {}
    for pid, cell in g.votes.items():
        counts.setdefault(cell, []).append(pid)
    for i in range(9):
        cell = QRectF(board.x() + (i % 3) * cs, board.y() + (i // 3) * cs, cs, cs)
        mark = g.cells[i]
        if mark:
            born = c.fx.born.get(f"ttt{i}")
            grow = ease_back((now - born) / 0.35) if born is not None and now - born < 0.35 else 1.0
            _mark(p, cell, mark, max(0.05, grow))
        elif i in counts and not g.result:
            for k, pid in enumerate(counts[i][:6]):
                p.setPen(Qt.NoPen)
                p.setBrush(qc(color_of(c, pid)))
                r = cs * 0.07
                p.drawEllipse(QPointF(cell.x() + cs * 0.2 + k * r * 2.6, cell.bottom() - cs * 0.16), r, r)
            _mark(p, cell, "X" if g.turn == 0 else "O", 0.6, 0.18 + 0.1 * len(counts[i]))
    if g.line:  # Gewinnlinie
        born = c.fx.born.get("tttline", now)
        part = min(1.0, (now - born) / 0.4)
        a, b = g.line[0], g.line[2]

        def center(i):
            return QPointF(board.x() + (i % 3 + 0.5) * cs, board.y() + (i // 3 + 0.5) * cs)

        pa, pb = center(a), center(b)
        end = QPointF(pa.x() + (pb.x() - pa.x()) * part, pa.y() + (pb.y() - pa.y()) * part)
        pen = QPen(QColor("#ffffff"), max(6, side * 0.03))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(pa, end)
    # Teams am Rand
    for team in (0, 1):
        members = [pid for pid in g.players if g.team_of(pid) == team]
        x = m if team == 0 else w - m - w * 0.2
        for k, pid in enumerate(members[:8]):
            voted = pid in g.votes and g.turn == team and not g.result
            chip(p, QRectF(x, h * 0.2 + k * h * 0.07, w * 0.2, h * 0.058), name_of(c, pid), color_of(c, pid),
                 "✓" if voted else "")
        if not members:
            text(p, QRectF(x, h * 0.2, w * 0.2, h * 0.06), "PC spielt", h * 0.03, MUTED)


# =========================================================================== Space Invaders
def _alien(p, center: QPointF, size: float, row: int, now: float) -> None:
    """Pixel-Alien (zwei Bilder im Wechsel – wie im Original)."""
    shapes = (["..X.....X..", "...X...X...", "..XXXXXXX..", ".XX.XXX.XX.", "XXXXXXXXXXX", "X.XXXXXXX.X",
               "X.X.....X.X", "...XX.XX..."],
              ["..X.....X..", "X..X...X..X", "X.XXXXXXX.X", "XXX.XXX.XXX", "XXXXXXXXXXX", ".XXXXXXXXX.",
               "..X.....X..", ".X.......X."])
    frame = shapes[int(now * 2) % 2]
    colors = ("#f472b6", "#a78bfa", "#a78bfa", "#34d399")
    px = size / 11
    x0, y0 = center.x() - size / 2, center.y() - px * 4
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(colors[row % 4]))
    for j, line in enumerate(frame):
        for i, ch in enumerate(line):
            if ch == "X":
                p.drawRect(QRectF(x0 + i * px, y0 + j * px, px + 0.5, px + 0.5))


def invaders(c, g, events) -> None:
    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(10, int(min(w, h) * 0.03))
    side_w = w * 0.2
    top = m + h * 0.07
    scale = min((w - 3 * m - side_w) / g.W, (h - top - m) / g.H)
    fx0, fy0 = m + (w - 3 * m - side_w - g.W * scale) / 2, top
    field = QRectF(fx0, fy0, g.W * scale, g.H * scale)

    def pt(x, y):
        return QPointF(fx0 + x * scale, fy0 + y * scale)

    for _n, t, kind, data in events:
        if kind == "hit":
            q = pt(data["x"], data["y"])
            burst(c.fx, q.x(), q.y(), color_of(c, data["pid"]), now, n=16, speed=260, size=5, gravity=0, life=0.6)
        elif kind == "crash" and "pid" in data:
            q = pt(data["x"], data["y"])
            burst(c.fx, q.x(), q.y(), "#f87171", now, n=34, speed=320, size=6)
        elif kind == "go":
            c.fx.born["wave"] = t
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawRoundedRect(field.adjusted(-6, -6, 6, 6), 14, 14)
    # Sterne
    rng = random.Random(7)
    for _ in range(70):
        sx, sy = rng.uniform(0, g.W), rng.uniform(0, g.H)
        a = 0.25 + 0.25 * math.sin(now * 2 + sx * 3)
        p.setBrush(qc("#ffffff", a))
        p.drawEllipse(pt(sx, sy), 1.4, 1.4)
    tag(c, m, m, field.width(), f"Space Invaders · Welle {g.wave}")
    left = g.remaining(now)
    text(p, QRectF(m, m, field.width() + (fx0 - m), h * 0.05), clock_text(left), h * 0.04,
         "#fbbf24" if left < 15 else TEXT, True, Qt.AlignRight | Qt.AlignVCenter)
    # Boden-Linie (kommen die Aliens hier an, ist es vorbei)
    p.setPen(QPen(qc("#ef4444", 0.5), max(2, scale * 0.05)))
    p.drawLine(pt(0, g.SHIP_Y - 0.6), pt(g.W, g.SHIP_Y - 0.6))
    for a in g.aliens:
        if a["alive"]:
            _alien(p, pt(a["x"], a["y"]), scale * 0.85, a["row"], now)
    p.setPen(Qt.NoPen)
    for sh in g.shots:
        p.setBrush(qc(color_of(c, sh["pid"])))
        p.drawRoundedRect(QRectF(pt(sh["x"], sh["y"]).x() - scale * 0.05, pt(sh["x"], sh["y"]).y() - scale * 0.25,
                                 scale * 0.1, scale * 0.5), 2, 2)
    p.setBrush(QColor("#fde047"))
    for b in g.bombs:
        q = pt(b["x"], b["y"])
        zig = scale * 0.08 * (1 if int(now * 10 + b["x"]) % 2 else -1)
        p.drawRect(QRectF(q.x() - scale * 0.05 + zig, q.y() - scale * 0.2, scale * 0.1, scale * 0.4))
    label_rows: list[float] = []
    for pid, s in g.ships.items():
        if s["lives"] <= 0:
            continue
        near = sum(1 for x in label_rows if abs(x - s["x"]) < 1.6)  # Namen nicht übereinander schreiben
        label_rows.append(s["x"])
        if now < s["safe_until"] and int(now * 10) % 2:
            continue  # blinkt nach einem Treffer
        q = pt(s["x"], g.SHIP_Y)
        sw = g.SHIP_W * scale
        path = QPainterPath()
        path.moveTo(q.x(), q.y() - sw * 0.45)
        path.lineTo(q.x() + sw / 2, q.y() + sw * 0.25)
        path.lineTo(q.x() - sw / 2, q.y() + sw * 0.25)
        path.closeSubpath()
        p.setBrush(qc(color_of(c, pid)))
        p.drawPath(path)
        text(p, QRectF(q.x() - scale * 2, q.y() + sw * 0.28 + near * scale * 0.38, scale * 4, scale * 0.5),
             name_of(c, pid), scale * 0.34, qc(color_of(c, pid)).lighter(140))
    if g.lost:
        text(p, field, "DIE ALIENS SIND GELANDET!", h * 0.06, "#f87171", True)
    born = c.fx.born.get("wave")
    if born is not None and now - born < 1.4:
        float_text(c, field.center().x(), field.center().y(), f"WELLE {g.wave}", "#a78bfa", born, 1.4, h * 0.1)
    marks = {pid: "♥" * s["lives"] if s["lives"] > 0 else "RAUS" for pid, s in g.ships.items()}
    scoreboard(c, QRectF(w - m - side_w, top, side_w, h * 0.62), g.scores(), limit=8, marks=marks)
    q = min(side_w * 0.5, h * 0.18)
    if c.wifi:
        qr_card(c, w - m - side_w / 2 - q / 2, h - m - q - q * 0.06 - h * 0.04, q, caption=False, image=c.wifi_qr())
        text(p, QRectF(w - m - side_w, h - m - h * 0.035, side_w, h * 0.035), "Einsteigen", h * 0.022, MUTED)


# =========================================================================== Flappy Bird
def flappy(c, g, events) -> None:
    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(10, int(min(w, h) * 0.03))
    side_w = w * 0.2
    top = m + h * 0.07
    scale = min((w - 3 * m - side_w) / g.W, (h - top - m) / g.H)
    fx0, fy0 = m, top
    field = QRectF(fx0, fy0, g.W * scale, g.H * scale)

    def pt(x, y):
        return QPointF(fx0 + x * scale, fy0 + y * scale)

    for _n, t, kind, data in events:
        if kind == "crash":
            q = pt(g.BIRD_X, data.get("y", g.H / 2))
            burst(c.fx, q.x(), q.y(), color_of(c, data["pid"]), now, n=26, speed=280, size=6)
    sky = QLinearGradient(field.topLeft(), field.bottomLeft())
    sky.setColorAt(0, QColor("#38bdf8"))
    sky.setColorAt(1, QColor("#bae6fd"))
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(field, 16, 16)
    p.setClipPath(clip)
    p.fillRect(field, sky)
    # Wolken (wandern langsamer als die Röhren)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 170))
    for k in range(5):
        cx = (k * 4.1 - g.dist * 0.3) % (g.W + 4) - 2
        cy = 1.2 + (k % 3) * 0.9
        for dx, r in ((0, 0.6), (0.6, 0.45), (-0.6, 0.45)):
            p.drawEllipse(pt(cx + dx, cy), r * scale, r * scale * 0.7)
    # Röhren
    for pipe in g.pipes:
        x = pipe["x"] - g.dist
        if x < -g.PIPE_W or x > g.W + g.PIPE_W:
            continue
        gx0 = fx0 + (x - g.PIPE_W / 2) * scale
        top_h = (pipe["gap_y"] - g.gap / 2) * scale
        bot_y = fy0 + (pipe["gap_y"] + g.gap / 2) * scale
        grad = QLinearGradient(QPointF(gx0, 0), QPointF(gx0 + g.PIPE_W * scale, 0))
        grad.setColorAt(0, QColor("#15803d"))
        grad.setColorAt(0.4, QColor("#4ade80"))
        grad.setColorAt(1, QColor("#166534"))
        p.setBrush(grad)
        p.drawRect(QRectF(gx0, fy0, g.PIPE_W * scale, top_h))
        p.drawRect(QRectF(gx0, bot_y, g.PIPE_W * scale, field.bottom() - bot_y))
        lip = scale * 0.35
        p.drawRect(QRectF(gx0 - scale * 0.12, fy0 + top_h - lip, g.PIPE_W * scale + scale * 0.24, lip))
        p.drawRect(QRectF(gx0 - scale * 0.12, bot_y, g.PIPE_W * scale + scale * 0.24, lip))
    # Boden
    p.setBrush(QColor("#d97706"))
    p.drawRect(QRectF(fx0, field.bottom() - scale * 0.25, field.width(), scale * 0.25))
    # Vögel (alle an derselben Stelle – leicht durchsichtig, damit man alle sieht)
    alive = [(pid, b) for pid, b in g.birds.items() if b["alive"]]
    for k, (pid, b) in enumerate(alive):
        q = pt(g.BIRD_X, b["y"])
        r = g.R * scale
        tilt = max(-30.0, min(70.0, b["vy"] * 6))
        p.save()
        p.translate(q)
        p.rotate(tilt)
        p.setBrush(qc(color_of(c, pid), 0.92))
        p.drawEllipse(QPointF(0, 0), r * 1.25, r)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(r * 0.55, -r * 0.3), r * 0.38, r * 0.38)
        p.setBrush(QColor("#0f172a"))
        p.drawEllipse(QPointF(r * 0.65, -r * 0.3), r * 0.16, r * 0.16)
        p.setBrush(QColor("#f97316"))
        beak = QPainterPath()
        beak.moveTo(r * 1.1, -r * 0.05)
        beak.lineTo(r * 1.7, r * 0.15)
        beak.lineTo(r * 1.1, r * 0.35)
        beak.closeSubpath()
        p.drawPath(beak)
        flap = math.sin(now * 18 + k) * r * 0.35
        p.setBrush(qc(color_of(c, pid)).lighter(130))
        p.drawEllipse(QPointF(-r * 0.3, flap * 0.5), r * 0.55, r * 0.32)
        p.restore()
        near = sum(1 for _p, other in alive[:k] if abs(other["y"] - b["y"]) < 0.6)
        text(p, QRectF(q.x() - scale * 2, q.y() - r * 2.6 - near * scale * 0.4, scale * 4, scale * 0.45),
             name_of(c, pid), scale * 0.32, qc("#0f172a", 0.8), True)
    p.restore()
    tag(c, m, m, field.width(), "Flappy Bird")
    best = max((b["passed"] for b in g.birds.values()), default=0)
    text(p, QRectF(m, m, field.width(), h * 0.05), f"{best}", h * 0.05, TEXT, True, Qt.AlignRight | Qt.AlignVCenter)
    if now < g.go_at:
        k = int(g.go_at - now) + 1
        text(p, field, f"{k}", h * 0.2, "#ffffff", True)
        text(p, field.adjusted(0, h * 0.2, 0, 0), "Tippen = flattern!", h * 0.045, "#ffffff", True)
    marks = {pid: ("" if b["alive"] else "✗") for pid, b in g.birds.items()}
    scoreboard(c, QRectF(w - m - side_w, top, side_w, h * 0.62), g.scores(), limit=8, marks=marks)


# =========================================================================== Vier gewinnt
def vierg(c, g, events) -> None:
    from .games_arcade import COLS4, ROWS4

    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(10, int(min(w, h) * 0.03))
    for _n, t, kind, data in events:
        if kind == "place":
            c.fx.born[f"c4{data['cell']}"] = t
        elif kind == "line":
            confetti(c.fx, w, h, now, n=90, colors=[TEAM_COLORS[data["team"]], "#ffffff"])
    cs = min(h * 0.6 / ROWS4, w * 0.5 / COLS4)
    board = QRectF((w - cs * COLS4) / 2, h * 0.19, cs * COLS4, cs * ROWS4)
    text(p, QRectF(m, m, w * 0.3, h * 0.07), f"●  {TEAM_NAMES[0]}", h * 0.042, TEAM_COLORS[0], True,
         Qt.AlignLeft | Qt.AlignVCenter)
    text(p, QRectF(w - m - w * 0.3, m, w * 0.3, h * 0.07), f"{TEAM_NAMES[1]}  ●", h * 0.042, TEAM_COLORS[1], True,
         Qt.AlignRight | Qt.AlignVCenter)
    text(p, QRectF(w * 0.35, m, w * 0.3, h * 0.07), f"{g.wins[0]} : {g.wins[1]}", h * 0.06, TEXT, True)
    text(p, QRectF(w * 0.35, m + h * 0.065, w * 0.3, h * 0.04), f"Runde {g.round} / {g.rounds}", h * 0.026, MUTED)
    if g.result == "draw":
        status, color = "Unentschieden!", TEXT
    elif g.result:
        team = 0 if g.result == "X" else 1
        status, color = f"{TEAM_NAMES[team]} hat vier in einer Reihe!", TEAM_COLORS[team]
    else:
        status, color = f"{TEAM_NAMES[g.turn]} ist dran", TEAM_COLORS[g.turn]
    text(p, QRectF(0, board.bottom() + h * 0.02, w, h * 0.06), status, h * 0.045, color, True)
    if not g.result:
        time_bar(c, QRectF(board.x(), board.bottom() + h * 0.085, board.width(), h * 0.012),
                 g.time_left(now) / max(0.1, g.think))
    # Stimmen über den Spalten
    counts: dict[int, list] = {}
    for pid, col in g.votes.items():
        counts.setdefault(col, []).append(pid)
    for col, pids in counts.items():
        if g.result:
            break
        for k, pid in enumerate(pids[:6]):
            p.setPen(Qt.NoPen)
            p.setBrush(qc(color_of(c, pid)))
            r = cs * 0.08
            p.drawEllipse(QPointF(board.x() + (col + 0.5) * cs + (k - (len(pids[:6]) - 1) / 2) * r * 2.4,
                                  board.y() - cs * 0.25), r, r)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#334155"))
    p.drawRoundedRect(board.adjusted(-cs * 0.12, -cs * 0.12, cs * 0.12, cs * 0.12), cs * 0.25, cs * 0.25)
    line = set(g.line or [])
    for i, mark in enumerate(g.cells):
        r, col = divmod(i, COLS4)
        center = QPointF(board.x() + (col + 0.5) * cs, board.y() + (r + 0.5) * cs)
        p.setBrush(QColor("#0b1020"))
        p.drawEllipse(center, cs * 0.4, cs * 0.4)
        if not mark:
            continue
        born = c.fx.born.get(f"c4{i}")
        y = center.y()
        if born is not None and now - born < 0.45:  # Stein fällt herunter
            k = ease_out((now - born) / 0.45)
            y = board.y() - cs * 0.5 + (center.y() - board.y() + cs * 0.5) * k
        color = TEAM_COLORS[0] if mark == "X" else TEAM_COLORS[1]
        if i in line and int(now * 4) % 2 == 0:
            p.setBrush(QColor("#facc15"))
            p.drawEllipse(QPointF(center.x(), y), cs * 0.44, cs * 0.44)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(center.x(), y), cs * 0.38, cs * 0.38)
    for team in (0, 1):
        members = [pid for pid in g.players if g.team_of(pid) == team]
        x = m if team == 0 else w - m - w * 0.18
        for k, pid in enumerate(members[:8]):
            voted = pid in g.votes and g.turn == team and not g.result
            chip(p, QRectF(x, h * 0.2 + k * h * 0.07, w * 0.18, h * 0.058), name_of(c, pid), color_of(c, pid),
                 "✓" if voted else "")
        if not members:
            text(p, QRectF(x, h * 0.2, w * 0.18, h * 0.06), "PC spielt", h * 0.03, MUTED)


def malen(c, g, events) -> None:
    """Montagsmaler: links das Bild (live vom Handy des Malers), rechts Wort-Lücken, Zeit, wer es schon hat."""
    p, w, h, now = c.p, c.w, c.h, c.now
    m = max(14, int(min(w, h) * 0.04))
    for _n, t, kind, data in events:
        if kind == "correct":
            c.fx.born[("correct", data["pid"])] = t
        elif kind == "reveal":
            c.fx.born["paint_reveal"] = t
    side_w = w * 0.27
    tag(c, m, m, w - 2 * m, f"Montagsmaler · Runde {g.round} / {g.total}", "#f9a8d4")
    # Leinwand 4:3, so groß wie möglich
    avail = QRectF(m, m + h * 0.07, w - 3 * m - side_w, h - 2 * m - h * 0.07)
    cw = min(avail.width(), avail.height() * 4 / 3)
    canvas = QRectF(avail.x() + (avail.width() - cw) / 2, avail.y() + (avail.height() - cw * 3 / 4) / 2, cw, cw * 3 / 4)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(canvas, 18, 18)
    p.save()
    p.setClipRect(canvas)
    for st in g.strokes:
        pts = st["pts"]
        pen = QPen(QColor("#ffffff" if st.get("erase") else st["c"]),
                   cw * (0.03 if st.get("erase") else 0.005 * st["w"] + 0.002))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        if len(pts) == 1:
            x, y = pts[0]
            p.drawPoint(QPointF(canvas.x() + x * cw, canvas.y() + y * canvas.height()))
            continue
        path = QPainterPath(QPointF(canvas.x() + pts[0][0] * cw, canvas.y() + pts[0][1] * canvas.height()))
        for x, y in pts[1:]:
            path.lineTo(canvas.x() + x * cw, canvas.y() + y * canvas.height())
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    p.restore()
    side = QRectF(w - m - side_w, m + h * 0.07, side_w, h - 2 * m - h * 0.07)
    drawer = name_of(c, g.drawer) if getattr(g, "drawer", None) else ""
    text(p, QRectF(side.x(), side.y(), side.width(), h * 0.06), f"🎨 {drawer} malt", h * 0.04, TEXT, True)
    if g.phase == "malen":
        text(p, QRectF(side.x(), side.y() + h * 0.08, side.width(), h * 0.1), g.hint(now), h * 0.05, "#fde68a", True,
             wrap=True)
        time_bar(c, QRectF(side.x(), side.y() + h * 0.2, side.width(), h * 0.018), (g.until - now) / g.seconds)
        text(p, QRectF(side.x(), side.y() + h * 0.23, side.width(), h * 0.05), clock_text(g.until - now), h * 0.035,
             MUTED, True)
    y = side.y() + h * 0.3
    others = g.guessers() if hasattr(g, "guessers") else []
    for pid in others[:9]:
        ok = pid in getattr(g, "guessed", {})
        pop = now - c.fx.born.get(("correct", pid), -9)
        k = 1 + 0.15 * max(0.0, 1 - pop / 0.4)
        rect = QRectF(side.x(), y, side.width() * k, h * 0.055)
        chip(p, rect, name_of(c, pid), color_of(c, pid), "✓" if ok else "…", dim=1.0 if ok else 0.5,
             avatar=avatar_of(c, pid))
        y += h * 0.065
    if g.phase == "malen" and g.guesses:  # falsche Tipps – ohne die richtigen zu verraten
        lines = " · ".join(t for _pid, t in g.guesses[-5:])
        text(p, QRectF(side.x(), side.bottom() - h * 0.1, side.width(), h * 0.1), lines, h * 0.026, MUTED,
             wrap=True)
    if g.phase == "aufloesen":
        t = now - c.fx.born.get("paint_reveal", now)
        k = min(1.0, t / 0.35)
        band = QRectF(canvas.x(), canvas.center().y() - h * 0.09, canvas.width(), h * 0.18)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(15, 23, 42, int(220 * k)))
        p.drawRoundedRect(band, 18, 18)
        text(p, band, f"Es war: {g.word}", h * 0.08 * (0.7 + 0.3 * k), "#fde68a", True)


DRAW = {"malen": malen, "simon": simon, "pong": pong, "tetris": tetris, "schlangen": schlangen, "rennen": rennen, "ssp": ssp,
        "tictactoe": tictactoe, "invaders": invaders, "flappy": flappy, "vierg": vierg}
