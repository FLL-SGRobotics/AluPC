"""Schiffe versenken zeichnen – dieselben Spielfelder im PC-Fenster (Spieler 1) und auf Monitor 2 (Spieler 2)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen

from .duel import LETTERS, SIZE
from .game_draw import qc, text

SEA = "#123456"
SEA_LINE = "#1f4f7a"
SHIP = "#94a3b8"
HIT = "#ef4444"
SUNK = "#7f1d1d"
MISS = "#38bdf8"
CURSOR = "#facc15"
OK = "#22c55e"
BAD = "#ef4444"


def board_rect(area: QRectF) -> tuple[QRectF, float]:
    """Quadratisches Feld (mit Rand für A–J / 1–10) in der Fläche – (Feld ohne Beschriftung, Zellgröße)."""
    side = min(area.width(), area.height())
    cell = side / (SIZE + 1)
    x = area.x() + (area.width() - side) / 2 + cell
    y = area.y() + (area.height() - side) / 2 + cell
    return QRectF(x, y, cell * SIZE, cell * SIZE), cell


def cell_at(area: QRectF, pos: QPointF) -> tuple[int, int] | None:
    rect, cell = board_rect(area)
    if not rect.contains(pos):
        return None
    x, y = int((pos.x() - rect.x()) // cell), int((pos.y() - rect.y()) // cell)
    return (x, y) if 0 <= x < SIZE and 0 <= y < SIZE else None


def _has(cells, x: int, y: int) -> bool:
    return [x, y] in cells or (x, y) in cells


def board(p: QPainter, area: QRectF, *, ships=(), hits=(), misses=(), sunk=(), cursor=None, ghost=(),
          ghost_ok=True, active=False, dim=False) -> None:
    """Ein 10×10-Feld. ships = Liste von Zellen (eigene Flotte), hits/misses = Schüsse, sunk = versenkte
    Gegner-Schiffe (aufgedeckt), cursor = (x, y) Tastatur-Auswahl, ghost = Vorschau des nächsten Schiffs."""
    rect, cell = board_rect(area)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    for i in range(SIZE):  # Beschriftung A–J oben, 1–10 links
        text(p, QRectF(rect.x() + i * cell, rect.y() - cell, cell, cell), LETTERS[i], cell * 0.42, "#cbd5e1", True)
        text(p, QRectF(rect.x() - cell, rect.y() + i * cell, cell, cell), str(i + 1), cell * 0.42, "#cbd5e1", True)
    p.setPen(Qt.NoPen)
    p.setBrush(qc(SEA, 0.55 if dim else 1.0))
    p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), cell * 0.18, cell * 0.18)
    ship_cells = {tuple(c) for s in ships for c in s}
    pad = max(1.0, cell * 0.06)
    for y in range(SIZE):
        for x in range(SIZE):
            r = QRectF(rect.x() + x * cell + pad, rect.y() + y * cell + pad, cell - 2 * pad, cell - 2 * pad)
            if _has(sunk, x, y):
                p.setBrush(qc(SUNK))
                p.drawRoundedRect(r, cell * 0.15, cell * 0.15)
            elif (x, y) in ship_cells:
                p.setBrush(qc(SHIP))
                p.drawRoundedRect(r, cell * 0.22, cell * 0.22)
            else:
                p.setBrush(qc(SEA_LINE, 0.45))
                p.drawRoundedRect(r, cell * 0.12, cell * 0.12)
            if _has(hits, x, y):  # Treffer: rotes Kreuz
                p.setPen(QPen(qc(HIT), max(2.0, cell * 0.12), Qt.SolidLine, Qt.RoundCap))
                k = cell * 0.26
                c = r.center()
                p.drawLine(QPointF(c.x() - k, c.y() - k), QPointF(c.x() + k, c.y() + k))
                p.drawLine(QPointF(c.x() - k, c.y() + k), QPointF(c.x() + k, c.y() - k))
                p.setPen(Qt.NoPen)
            elif _has(misses, x, y):  # Wasser: kleiner Punkt
                p.setBrush(qc(MISS))
                p.drawEllipse(r.center(), cell * 0.12, cell * 0.12)
    for gx, gy in ghost:
        if 0 <= gx < SIZE and 0 <= gy < SIZE:
            p.setBrush(qc(OK if ghost_ok else BAD, 0.55))
            p.drawRoundedRect(QRectF(rect.x() + gx * cell + pad, rect.y() + gy * cell + pad,
                                     cell - 2 * pad, cell - 2 * pad), cell * 0.22, cell * 0.22)
    if cursor is not None:
        cx, cy = cursor
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(qc(CURSOR), max(2.0, cell * 0.1)))
        p.drawRoundedRect(QRectF(rect.x() + cx * cell + 1, rect.y() + cy * cell + 1, cell - 2, cell - 2),
                          cell * 0.2, cell * 0.2)
    if active:  # Dieses Feld ist gerade dran
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(qc(CURSOR, 0.9), max(2.0, cell * 0.08)))
        p.drawRoundedRect(rect.adjusted(-cell * 0.12, -cell * 0.12, cell * 0.12, cell * 0.12), cell * 0.25,
                          cell * 0.25)
    p.restore()


def own_board(p: QPainter, area: QRectF, view: dict, **extra) -> None:
    own = view["own"]
    board(p, area, ships=own["ships"], hits=own["hits"], misses=own["misses"], **extra)


def target_board(p: QPainter, area: QRectF, view: dict, **extra) -> None:
    t = view["target"]
    board(p, area, hits=t["hits"], misses=t["misses"], sunk=t["sunk"], **extra)


def headline(view: dict) -> tuple[str, str]:
    """(große Zeile, Farbe) für Spieler view["me"]."""
    me, phase = view["me"], view["phase"]
    if phase == "ende":
        return ("🏆 Gewonnen!", "#facc15") if view["winner"] == me else (f"{view['other']} gewinnt", "#fca5a5")
    if phase == "setzen":
        if view["ready"]:
            return (f"Bereit – warte auf {view['other']}", "#fcd34d")
        if view["next"]:
            return (f"Schiff setzen: Länge {view['next']}", "#e2e8f0")
        return ("Alle Schiffe gesetzt – „Bereit“", "#86efac")
    return ("Du bist dran!", "#86efac") if view["turn"] == me else (f"{view['other']} ist dran", "#fcd34d")


