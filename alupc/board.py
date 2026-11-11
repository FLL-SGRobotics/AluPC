"""Whiteboard als Notizbuch (wie GoodNotes): mehrere Seiten, Zoomen und Verschieben, Stift mit Druck, Marker,
Radierer, Lasso (auswählen, verschieben, löschen), Formen (Linie, Pfeil, Rechteck, Kreis), Text, Rückgängig/
Wiederholen. Gespeichert als Tafel (JSON im AluPC-Ordner „Tafeln“), Export als PNG (Seite) oder PDF (alle Seiten).

Koordinaten: „Welt“-Einheiten auf der Seite (16:9 = 1600 × 900, A4 hoch = 1240 × 1754 …). Die Ansicht (Zoom, Mitte)
ist für alle gleich: Monitor 2 zeigt sie, das Whiteboard-Fenster am PC zeigt genau dasselbe, das Handy steuert sie.
Zoom 1 = ganze Seite (Höhe) sichtbar.
"""

from __future__ import annotations

import copy
import json
import math
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient, QTransform

from .whiteboard import BACKGROUNDS, DEFAULT, is_dark

FORMATS = {"16:9": ("Breit (16:9)", 1600, 900), "a4": ("A4 hoch", 1240, 1754), "a4quer": ("A4 quer", 1754, 1240),
           "unendlich": ("Unendlich (ohne Rand)", 1600, 900)}
TOOLS = ("pen", "marker", "eraser", "lasso", "line", "arrow", "rect", "ellipse", "text", "laser")
SHAPES = ("line", "arrow", "rect", "ellipse")
PALETTE = ["#111827", "#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#a855f7", "#ec4899", "#ffffff"]
SETTINGS = {"background": DEFAULT, "format": "16:9", "width": 3.0, "smooth": 1, "pressure": True, "eraser": 18.0,
            "colors": PALETTE, "pencil_only": False, "autosave": True, "board": "Tafel 1", "follow": True,
            "text_size": 36.0, "marker_width": 18.0, "guests": "aus"}
ZOOM_MIN, ZOOM_MAX = 0.25, 8.0
UNIT = 900 / 24  # Kästchen: 24 Reihen auf einer 16:9-Seite
MARKER_ALPHA = 0.35


def boards_dir() -> Path:
    from .config import config_dir

    return config_dir() / "Tafeln"


def _safe_name(name: str) -> str:
    name = " ".join(str(name or "").split())[:60] or "Tafel"
    return re.sub(r'[\\/:*?"<>|]', "_", name)


SHARED: "Board | None" = None  # die eine Tafel von AluPC (setzt der Controller; Monitor 2 zeigt sie)


def shared(config=None) -> "Board":
    global SHARED
    if SHARED is None:
        SHARED = Board(config)
    return SHARED


# --------------------------------------------------------------------------- Modell
class Board(QObject):
    changed = Signal()  # Inhalt oder Ansicht geändert → neu zeichnen

    def __init__(self, config=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.aspect = 16 / 9  # Breite/Höhe von Monitor 2 (setzt die Anzeige)
        self.zoom, self.cx, self.cy = 1.0, None, None
        self.sel: list[int] = []  # ausgewählte Striche (Lasso)
        self.live = None  # gerade entstehender Strich/Form/Lasso
        self.laser: QPointF | None = None
        self._undo: list[list] = []
        self._redo: list[list] = []
        self._move_from: QPointF | None = None
        self._last: QPointF | None = None
        self._dirty = False
        self._save_timer = QTimer(self, singleShot=True, interval=1200)
        self._save_timer.timeout.connect(self.save)
        self.doc = {"name": self.settings()["board"], "pages": [self._new_page()], "page": 0}
        self.load(self.settings()["board"], quiet=True)

    # ---- Einstellungen
    def settings(self) -> dict:
        raw = dict((self.config.data.get("whiteboard") if self.config is not None else None) or {})
        cfg = {**SETTINGS, **raw}
        if cfg.get("background") not in BACKGROUNDS:
            cfg["background"] = DEFAULT
        if cfg.get("format") not in FORMATS:
            cfg["format"] = "16:9"
        colors = [c for c in (cfg.get("colors") or []) if re.fullmatch(r"#[0-9a-fA-F]{6}", str(c))]
        cfg["colors"] = (colors + PALETTE)[:8]
        if cfg.get("guests") not in ("aus", "alle", "frage"):
            cfg["guests"] = "aus"
        return cfg

    def set_settings(self, **values) -> None:
        if self.config is not None:
            self.config["whiteboard"] = {**self.settings(), **values}
        self.changed.emit()

    # ---- Seiten
    def _new_page(self, bg: str | None = None) -> dict:
        return {"bg": bg or self.settings()["background"], "strokes": []}

    @property
    def page(self) -> dict:
        i = max(0, min(len(self.doc["pages"]) - 1, int(self.doc.get("page", 0))))
        self.doc["page"] = i
        return self.doc["pages"][i]

    @property
    def strokes(self) -> list[dict]:
        return self.page["strokes"]

    def page_size(self) -> tuple[float, float]:
        _label, w, h = FORMATS[self.settings()["format"]]
        return float(w), float(h)

    def infinite(self) -> bool:
        return self.settings()["format"] == "unendlich"

    def goto(self, i: int) -> None:
        self.doc["page"] = max(0, min(len(self.doc["pages"]) - 1, i))
        self._undo.clear()
        self._redo.clear()
        self.sel = []
        self.fit(emit=False)
        self._changed()

    def new_page(self) -> None:
        i = self.doc["page"] + 1
        self.doc["pages"].insert(i, self._new_page(self.page["bg"]))
        self.goto(i)

    def delete_page(self) -> None:
        if len(self.doc["pages"]) <= 1:
            self.clear()
            return
        del self.doc["pages"][self.doc["page"]]
        self.goto(min(self.doc["page"], len(self.doc["pages"]) - 1))

    def set_background(self, bg: str) -> None:
        if bg in BACKGROUNDS:
            self.page["bg"] = bg
            self._changed()

    def clear(self) -> None:
        if self.strokes:
            self._snapshot()
            self.page["strokes"] = []
            self.sel = []
            self._changed()

    # ---- Ansicht
    def center(self) -> QPointF:
        w, h = self.page_size()
        return QPointF(w / 2 if self.cx is None else self.cx, h / 2 if self.cy is None else self.cy)

    def visible(self) -> QRectF:
        """Sichtbarer Ausschnitt der Seite (Welt-Koordinaten)."""
        _w, ph = self.page_size()
        vh = ph / self.zoom
        vw = vh * self.aspect
        c = self.center()
        return QRectF(c.x() - vw / 2, c.y() - vh / 2, vw, vh)

    def transform(self, rect: QRectF) -> QTransform:
        """Welt → Bildschirm für das Rechteck `rect` (Monitor 2, Fenster, Export)."""
        vis = self.visible()
        s = rect.height() / vis.height()
        tr = QTransform()
        tr.translate(rect.center().x(), rect.center().y())
        tr.scale(s, s)
        c = vis.center()
        tr.translate(-c.x(), -c.y())
        return tr

    def to_world(self, nx: float, ny: float) -> QPointF:
        """Stelle auf dem Bildschirm (0…1 von Monitor 2) → Welt."""
        vis = self.visible()
        return QPointF(vis.left() + nx * vis.width(), vis.top() + ny * vis.height())

    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        """Zoomen; `anchor` (Welt) bleibt dabei an derselben Stelle auf dem Bildschirm."""
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        c = self.center()
        if anchor is not None:
            k = self.zoom / zoom
            c = QPointF(anchor.x() + (c.x() - anchor.x()) * k, anchor.y() + (c.y() - anchor.y()) * k)
        self.zoom = zoom
        self.cx, self.cy = c.x(), c.y()
        self._clamp()
        self.changed.emit()

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        self.set_zoom(self.zoom * factor, anchor)

    def pan(self, dx: float, dy: float) -> None:
        """Verschieben um (dx, dy) Welt-Einheiten (positiv = Inhalt wandert nach rechts/unten)."""
        c = self.center()
        self.cx, self.cy = c.x() - dx, c.y() - dy
        self._clamp()
        self.changed.emit()

    def pan_screen(self, nx: float, ny: float) -> None:
        """Verschieben um einen Anteil des sichtbaren Ausschnitts (z. B. Wischen auf dem Handy)."""
        vis = self.visible()
        self.pan(nx * vis.width(), ny * vis.height())

    def fit(self, emit: bool = True) -> None:
        self.zoom, self.cx, self.cy = 1.0, None, None
        if emit:
            self.changed.emit()

    def _clamp(self) -> None:
        if self.infinite():
            return
        w, h = self.page_size()
        vis = self.visible()
        mx, my = max(w * 0.25, vis.width() * 0.4), max(h * 0.25, vis.height() * 0.4)
        self.cx = max(-mx, min(w + mx, self.cx if self.cx is not None else w / 2))
        self.cy = max(-my, min(h + my, self.cy if self.cy is not None else h / 2))

    # ---- Rückgängig
    def _snapshot(self) -> None:
        self._undo.append(copy.deepcopy(self.strokes))
        del self._undo[:-80]
        self._redo.clear()

    def undo(self) -> None:
        if self._undo:
            self._redo.append(copy.deepcopy(self.strokes))
            self.page["strokes"] = self._undo.pop()
            self.sel = []
            self._changed()

    def redo(self) -> None:
        if self._redo:
            self._undo.append(copy.deepcopy(self.strokes))
            self.page["strokes"] = self._redo.pop()
            self.sel = []
            self._changed()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    # ---- Eingabe (Welt-Koordinaten)
    def input(self, phase: str, x: float, y: float, pressure: float = 0.5, tool: str = "pen",
              color: str = "#111827", width: float | None = None) -> None:
        """Stift/Finger/Maus: phase down → move … → up. Werkzeuge siehe TOOLS."""
        cfg = self.settings()
        p = QPointF(x, y)
        pressure = max(0.05, min(1.0, float(pressure))) if cfg["pressure"] else 0.5
        if tool == "laser":
            self.laser = None if phase == "up" else p
            self.changed.emit()
            return
        if tool == "eraser":
            if phase == "down":
                self._snapshot()
                self._erased = False
            if phase != "up":
                self._erase_at(p, float(cfg["eraser"]) / max(0.4, self.zoom))
            elif not getattr(self, "_erased", True) and self._undo:
                self._undo.pop()  # nichts getroffen: kein leerer Rückgängig-Schritt
            return
        if tool == "lasso":
            self._lasso(phase, p)
            return
        if phase == "down":
            self.sel = []
            if tool in ("pen", "marker"):
                w = float(width if width is not None else cfg["marker_width" if tool == "marker" else "width"])
                self.live = {"t": tool, "c": color, "w": w, "p": [[round(x, 1), round(y, 1), round(pressure, 2)]]}
            elif tool in SHAPES:
                self.live = {"t": tool, "c": color, "w": float(width if width is not None else cfg["width"]),
                             "p": [[round(x, 1), round(y, 1)], [round(x, 1), round(y, 1)]]}
            self._last = p
            self.changed.emit()
            return
        if self.live is None:
            return
        if phase == "move":
            if self.live["t"] in SHAPES:
                self.live["p"][1] = [round(x, 1), round(y, 1)]
            else:
                k = {0: 1.0, 1: 0.55, 2: 0.33}.get(int(cfg["smooth"]), 0.55)  # Glätten: zum neuen Punkt hin
                self._raw = [round(x, 1), round(y, 1), round(pressure, 2)]  # wo der Stift wirklich ist
                last = self._last or p
                q = QPointF(last.x() + (p.x() - last.x()) * k, last.y() + (p.y() - last.y()) * k)
                if math.hypot(q.x() - last.x(), q.y() - last.y()) < 0.6 / max(0.5, self.zoom):
                    return
                self.live["p"].append([round(q.x(), 1), round(q.y(), 1), round(pressure, 2)])
                self._last = q
            self.changed.emit()
            return
        if phase == "up":
            stroke, self.live = self.live, None
            raw, self._raw = getattr(self, "_raw", None), None
            if stroke["t"] not in SHAPES and raw and stroke["p"][-1][:2] != raw[:2]:
                stroke["p"].append(raw)  # Glätten hinkt hinterher: Strich endet genau, wo der Stift aufhört
            if stroke["t"] in SHAPES and stroke["p"][0] == stroke["p"][1]:
                self.changed.emit()
                return
            self._snapshot()
            self.strokes.append(stroke)
            self._changed()

    def add_stroke(self, tool: str, color: str, width: float, points: list[tuple[float, float, float]]) -> None:
        """Fertiger Strich auf einmal (Gäste im AluPC-WLAN, board_guests.py) – stört keinen laufenden Strich."""
        if tool not in ("pen", "marker") or not points:
            return
        pts = [[round(x, 1), round(y, 1), round(pr, 2)] for x, y, pr in points]
        if len(pts) == 1:
            pts.append(list(pts[0]))
        self._snapshot()
        self.strokes.append({"t": tool, "c": color, "w": float(width), "p": pts})
        self._changed()

    def add_text(self, x: float, y: float, text: str, color: str = "#111827", size: float | None = None) -> None:
        text = str(text or "").strip()[:500]
        if not text:
            return
        self._snapshot()
        self.strokes.append({"t": "text", "c": color, "s": float(size or self.settings()["text_size"]),
                             "p": [[round(x, 1), round(y, 1)]], "text": text})
        self._changed()

    def _erase_at(self, p: QPointF, r: float) -> None:
        keep = [s for s in self.strokes if not _hits(s, p, r)]
        if len(keep) != len(self.strokes):
            self.page["strokes"] = keep
            self._erased = True
            self.sel = []
            self._changed()

    # ---- Lasso
    def selection_rect(self) -> QRectF:
        r = QRectF()
        for i in self.sel:
            if i < len(self.strokes):
                r = r.united(_bounds(self.strokes[i]))
        return r

    def _lasso(self, phase: str, p: QPointF) -> None:
        if phase == "down":
            if self.sel and self.selection_rect().adjusted(-12, -12, 12, 12).contains(p):
                self._snapshot()
                self._move_from = p  # in die Auswahl getippt → verschieben
            else:
                self.sel = []
                self._move_from = None
                self.live = {"t": "lasso", "p": [[p.x(), p.y()]]}
        elif phase == "move":
            if self._move_from is not None:
                dx, dy = p.x() - self._move_from.x(), p.y() - self._move_from.y()
                for i in self.sel:
                    for pt in self.strokes[i]["p"]:
                        pt[0], pt[1] = round(pt[0] + dx, 1), round(pt[1] + dy, 1)
                self._move_from = p
            elif self.live is not None:
                self.live["p"].append([p.x(), p.y()])
        else:
            if self._move_from is None and self.live is not None:
                poly = QPolygonF([QPointF(a, b) for a, b in self.live["p"]])
                path = QPainterPath()
                path.addPolygon(poly)
                path.closeSubpath()
                self.sel = [i for i, s in enumerate(self.strokes)
                            if all(path.contains(QPointF(pt[0], pt[1])) for pt in s["p"])]
            self.live = None
            if self._move_from is not None:
                self._move_from = None
                self._changed()
        self.changed.emit()

    def delete_selection(self) -> None:
        if self.sel:
            self._snapshot()
            self.page["strokes"] = [s for i, s in enumerate(self.strokes) if i not in set(self.sel)]
            self.sel = []
            self._changed()

    def recolor_selection(self, color: str) -> None:
        if self.sel:
            self._snapshot()
            for i in self.sel:
                self.strokes[i]["c"] = color
            self._changed()

    # ---- Speichern
    def _changed(self) -> None:
        self._dirty = True
        if self.settings()["autosave"]:
            self._save_timer.start()
        self.changed.emit()

    def path_for(self, name: str) -> Path:
        return boards_dir() / f"{_safe_name(name)}.json"

    def save(self) -> Path:
        path = self.path_for(self.doc["name"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.doc, "saved": time.time()}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        self._dirty = False
        return path

    def load(self, name: str, quiet: bool = False) -> bool:
        path = self.path_for(name)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            pages = [{"bg": pg.get("bg") if pg.get("bg") in BACKGROUNDS else DEFAULT,
                      "strokes": [s for s in pg.get("strokes") or [] if isinstance(s, dict) and s.get("p")]}
                     for pg in doc.get("pages") or [] if isinstance(pg, dict)]
        except (OSError, ValueError, AttributeError, TypeError):
            if not quiet:
                return False
            pages = None
        self.doc = {"name": _safe_name(name), "pages": pages or [self._new_page()],
                    "page": int(doc.get("page", 0)) if pages else 0}
        self._undo.clear()
        self._redo.clear()
        self.sel, self.live = [], None
        self.fit(emit=False)
        self._remember(self.doc["name"])
        self.changed.emit()
        return pages is not None

    def new_board(self, name: str = "") -> str:
        if self._dirty:
            self.save()
        names = set(self.boards()) | {self.doc["name"]}
        base = _safe_name(name) if name else "Tafel"
        new, n = base, 2
        while new in names:
            new, n = f"{base} {n}", n + 1
        self.doc = {"name": new, "pages": [self._new_page()], "page": 0}
        self._undo.clear()
        self._redo.clear()
        self.sel = []
        self.fit(emit=False)
        self._remember(new)
        self.save()
        self.changed.emit()
        return new

    def rename(self, name: str) -> None:
        old = self.path_for(self.doc["name"])
        self.doc["name"] = _safe_name(name)
        self.save()
        if old != self.path_for(self.doc["name"]):
            old.unlink(missing_ok=True)
        self._remember(self.doc["name"])
        self.changed.emit()

    def _remember(self, name: str) -> None:
        """Nur den Namen der offenen Tafel merken – und nur, wenn er sich ändert (sonst gälte jeder Start als
        Änderung, und der Dual-Boot-Abgleich schriebe sofort zurück)."""
        if self.config is None:
            return
        raw = dict(self.config.data.get("whiteboard") or {})
        if raw.get("board") != name:
            self.config["whiteboard"] = {**raw, "board": name}

    @staticmethod
    def boards() -> list[str]:
        try:
            files = sorted(boards_dir().glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        except OSError:
            return []
        return [f.stem for f in files]

    # ---- Export
    def render_page(self, index: int, width: int = 1920):
        """Eine ganze Seite als Bild (für PNG/PDF und Vorschaubilder)."""
        from PySide6.QtGui import QImage

        w, h = self.page_size()
        img = QImage(width, round(width * h / w), QImage.Format_RGB32)
        img.fill(QColor("#ffffff"))
        p = QPainter(img)
        paint_page(p, QRectF(0, 0, img.width(), img.height()), self.doc["pages"][index], (w, h))
        p.end()
        return img

    def export_png(self, path: str) -> bool:
        return self.render_page(self.doc["page"]).save(path, "PNG")

    def export_pdf(self, path: str) -> bool:
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

        w, h = self.page_size()
        pdf = QPdfWriter(path)
        pdf.setResolution(150)
        size = QPageSize(QSizeF(w / 150 * 25.4 * 1.0, h / 150 * 25.4), QPageSize.Millimeter, "Tafel")
        pdf.setPageLayout(QPageLayout(size, QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
        p = QPainter(pdf)
        if not p.isActive():
            return False
        for i in range(len(self.doc["pages"])):
            if i:
                pdf.newPage()
            vp = p.viewport()
            img = self.render_page(i, 2000)
            p.drawImage(QRectF(vp), img)
        p.end()
        return Path(path).is_file()


# --------------------------------------------------------------------------- Geometrie
def _pts(s: dict) -> list[QPointF]:
    return [QPointF(pt[0], pt[1]) for pt in s.get("p") or []]


def _bounds(s: dict) -> QRectF:
    if s.get("t") == "text":
        x, y = s["p"][0]
        size = float(s.get("s", 36))
        lines = str(s.get("text", "")).split("\n")
        return QRectF(x, y, max(len(ln) for ln in lines) * size * 0.6, len(lines) * size * 1.25)
    pts = _pts(s)
    if not pts:
        return QRectF()
    xs, ys = [q.x() for q in pts], [q.y() for q in pts]
    pad = float(s.get("w", 3))
    return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)


def _seg_dist(p: QPointF, a: QPointF, b: QPointF) -> float:
    dx, dy = b.x() - a.x(), b.y() - a.y()
    if dx == dy == 0:
        return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = max(0.0, min(1.0, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p.x() - (a.x() + t * dx), p.y() - (a.y() + t * dy))


def _hits(s: dict, p: QPointF, r: float) -> bool:
    t = s.get("t")
    if t == "text":
        return _bounds(s).adjusted(-r, -r, r, r).contains(p)
    pts = _pts(s)
    reach = r + float(s.get("w", 3)) / 2
    if t in ("rect", "ellipse") and len(pts) == 2:
        box = QRectF(pts[0], pts[1]).normalized()
        if t == "rect":
            corners = [box.topLeft(), box.topRight(), box.bottomRight(), box.bottomLeft(), box.topLeft()]
            return any(_seg_dist(p, a, b) <= reach for a, b in zip(corners, corners[1:]))
        rx, ry = box.width() / 2 or 1, box.height() / 2 or 1
        d = math.hypot((p.x() - box.center().x()) / rx, (p.y() - box.center().y()) / ry)
        return abs(d - 1) * min(rx, ry) <= reach
    if len(pts) == 1:
        return math.hypot(p.x() - pts[0].x(), p.y() - pts[0].y()) <= reach
    return any(_seg_dist(p, a, b) <= reach for a, b in zip(pts, pts[1:]))


# --------------------------------------------------------------------------- Zeichnen
def _cosmetic(color, width: float = 1.0) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCosmetic(True)
    return pen


def paint_pattern(p: QPainter, tr: QTransform, area: QRectF, bg: str) -> None:
    """Hintergrund-Muster in Welt-Koordinaten (`area`), mit dünnen Linien, die beim Zoomen scharf bleiben."""
    s = tr.m11()

    def lines(step, color, width, vertical=True, horizontal=True, x0=0.0, y0=0.0):
        if step * s < 5:  # zu eng (weit herausgezoomt) → weglassen
            return
        p.setPen(_cosmetic(color, width))
        if vertical:
            x = math.floor((area.left() - x0) / step) * step + x0
            while x <= area.right():
                p.drawLine(tr.map(QPointF(x, area.top())), tr.map(QPointF(x, area.bottom())))
                x += step
        if horizontal:
            y = math.floor((area.top() - y0) / step) * step + y0
            while y <= area.bottom():
                p.drawLine(tr.map(QPointF(area.left(), y)), tr.map(QPointF(area.right(), y)))
                y += step

    u = UNIT
    if bg == "kariert":
        lines(u, "#b9c6dc", 1)
    elif bg == "millimeter":
        lines(u / 5, QColor(229, 120, 60, 45), 0.6)
        lines(u, QColor(229, 120, 60, 110), 0.9)
        lines(u * 5, QColor(229, 120, 60, 170), 1.3)
    elif bg == "liniert":
        lines(u * 1.25, "#a9c1e6", 1, vertical=False, y0=u * 2.2)
    elif bg == "punkte" and u * s >= 6:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#9aa7bd"))
        r = max(1.0, u * s / 16)
        y = math.floor(area.top() / u) * u
        while y <= area.bottom():
            x = math.floor(area.left() / u) * u
            while x <= area.right():
                p.drawEllipse(tr.map(QPointF(x, y)), r, r)
                x += u
            y += u
    elif bg == "koordinaten":
        lines(u, "#d3dbe8", 1)
    elif bg == "noten":
        gap = u * 0.55
        top = math.floor(area.top() / (gap * 4 + u * 1.6)) * (gap * 4 + u * 1.6) + u * 2
        p.setPen(_cosmetic("#6b7280", 1))
        while top < area.bottom():
            for i in range(5):
                y = top + i * gap
                p.drawLine(tr.map(QPointF(area.left(), y)), tr.map(QPointF(area.right(), y)))
            top += gap * 4 + u * 1.6
    elif bg == "blaupause":
        lines(u / 2, QColor(255, 255, 255, 26), 1)
        lines(u * 2, QColor(255, 255, 255, 60), 1.2)


def _paper(bg: str) -> QColor:
    return {"tafel": QColor("#264d3a"), "schwarz": QColor("#1f2227"), "blaupause": QColor("#123a73")}.get(
        bg, QColor("#fdfdfb"))


def paint_stroke(p: QPainter, tr: QTransform, s: dict) -> None:
    t, color = s.get("t"), QColor(s.get("c", "#111827"))
    scale = tr.m11()
    pts = [tr.map(q) for q in _pts(s)]
    if not pts:
        return
    if t == "text":
        f = QFont()
        f.setPixelSize(max(1, int(float(s.get("s", 36)) * scale)))
        f.setWeight(QFont.Medium)
        p.setFont(f)
        p.setPen(color)
        x, y = pts[0].x(), pts[0].y()
        for i, ln in enumerate(str(s.get("text", "")).split("\n")):
            p.drawText(QPointF(x, y + f.pixelSize() * (1 + i * 1.25)), ln)
        return
    w = max(0.8, float(s.get("w", 3)) * scale)
    if t == "marker":
        color.setAlphaF(MARKER_ALPHA)
        pen = QPen(color, w, Qt.SolidLine, Qt.FlatCap, Qt.RoundJoin)
    else:
        pen = QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setBrush(Qt.NoBrush)
    if t in SHAPES and len(pts) >= 2:
        p.setPen(pen)
        a, b = pts[0], pts[1]
        if t in ("line", "arrow"):
            p.drawLine(a, b)
            if t == "arrow":
                ang = math.atan2(b.y() - a.y(), b.x() - a.x())
                head = max(10.0, w * 4)
                for side in (1, -1):
                    a2 = ang + math.pi - side * math.radians(28)
                    p.drawLine(b, QPointF(b.x() + head * math.cos(a2), b.y() + head * math.sin(a2)))
        elif t == "rect":
            p.drawRect(QRectF(a, b).normalized())
        else:
            p.drawEllipse(QRectF(a, b).normalized())
        return
    raw = s.get("p") or []
    if len(pts) == 1:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        r = w * (0.35 + 0.65 * (raw[0][2] if len(raw[0]) > 2 else 0.5)) / 2
        p.drawEllipse(pts[0], r, r)
        return
    pressures = [pt[2] if len(pt) > 2 else 0.5 for pt in raw]
    if t == "marker" or max(pressures) - min(pressures) < 0.05:  # gleichmäßig: ein weicher Pfad
        path = QPainterPath(pts[0])
        for i in range(1, len(pts) - 1):  # durch die Mittelpunkte → runde Kurven statt Ecken
            mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2, (pts[i].y() + pts[i + 1].y()) / 2)
            path.quadTo(pts[i], mid)
        path.lineTo(pts[-1])
        if t != "marker":
            pen.setWidthF(w * (0.35 + 0.65 * pressures[0]))
        p.setPen(pen)
        p.drawPath(path)
        return
    for i in range(1, len(pts)):  # Druck: Strichstärke ändert sich unterwegs
        pen.setWidthF(w * (0.35 + 0.65 * (pressures[i - 1] + pressures[i]) / 2))
        p.setPen(pen)
        p.drawLine(pts[i - 1], pts[i])


def paint_page(p: QPainter, rect: QRectF, page: dict, size: tuple[float, float]) -> None:
    """Eine ganze Seite in `rect` (Export, Vorschaubilder)."""
    w, h = size
    tr = QTransform()
    tr.translate(rect.left(), rect.top())
    tr.scale(rect.width() / w, rect.height() / h)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(rect, _paper(page["bg"]))
    paint_pattern(p, tr, QRectF(0, 0, w, h), page["bg"])
    for s in page["strokes"]:
        paint_stroke(p, tr, s)
    p.restore()


def paint_board(p: QPainter, rect: QRectF, board: Board, live: bool = True) -> None:
    """Die aktuelle Ansicht der Tafel in `rect` (Monitor 2 und Whiteboard-Fenster – genau gleich)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setClipRect(rect)
    page = board.page
    bg = page["bg"]
    tr = board.transform(rect)
    inv, _ok = tr.inverted()
    vis = inv.mapRect(rect)
    w, h = board.page_size()
    if board.infinite():
        p.fillRect(rect, _paper(bg))
        paint_pattern(p, tr, vis, bg)
    else:
        desk = QColor("#c7ccd6") if not is_dark(bg) else QColor("#0c0e12")
        p.fillRect(rect, desk)
        page_r = tr.mapRect(QRectF(0, 0, w, h))
        shadow = QRadialGradient(page_r.center(), max(page_r.width(), page_r.height()) * 0.75)
        shadow.setColorAt(0.7, QColor(0, 0, 0, 60))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(page_r.adjusted(-30, -20, 30, 40), shadow)
        p.fillRect(page_r, _paper(bg))
        if bg == "tafel":
            g = QRadialGradient(page_r.center(), max(page_r.width(), page_r.height()) * 0.75)
            g.setColorAt(0, QColor("#2f5e46"))
            g.setColorAt(1, QColor("#1d3b2c"))
            p.fillRect(page_r, g)
        p.save()
        p.setClipRect(page_r.intersected(rect))
        paint_pattern(p, tr, QRectF(0, 0, w, h).intersected(vis.adjusted(-UNIT, -UNIT, UNIT, UNIT)), bg)
        p.restore()
    if bg == "koordinaten":  # Achsen durch die Seitenmitte
        p.setPen(_cosmetic("#1f2937", 2))
        cx, cy = round(w / 2 / UNIT) * UNIT, round(h / 2 / UNIT) * UNIT
        p.drawLine(tr.map(QPointF(vis.left(), cy)), tr.map(QPointF(vis.right(), cy)))
        p.drawLine(tr.map(QPointF(cx, vis.top())), tr.map(QPointF(cx, vis.bottom())))
    for s in page["strokes"]:
        paint_stroke(p, tr, s)
    if live and board.live is not None:
        if board.live["t"] == "lasso":
            pen = _cosmetic("#6366f1", 2)
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(99, 102, 241, 30))
            p.drawPolygon(QPolygonF([tr.map(QPointF(a, b)) for a, b in board.live["p"]]))
        else:
            paint_stroke(p, tr, board.live)
    if board.sel:
        r = tr.mapRect(board.selection_rect()).adjusted(-6, -6, 6, 6)
        pen = _cosmetic("#6366f1", 2)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(99, 102, 241, 22))
        p.drawRoundedRect(r, 8, 8)
    if board.laser is not None:
        from .laser import paint_dot

        paint_dot(p, tr.map(board.laser), max(8.0, rect.height() / 90), QColor("#ef4444"))
    p.restore()


