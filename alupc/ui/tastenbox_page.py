"""Seite „Tastenbox“: 9 Tasten (3 × 3, wie auf der Box), Drehregler mit wählbarem Ziel, LED, Anschluss.

Die Tasten der Box sind unbeschriftet – hier sieht man, welche Taste was tut, und eine gedrückte Taste leuchtet kurz
auf. Der Regler hat eine Skala: Wechselt sein Ziel, startet das neue Ziel bei 0, und der Regler greift erst, wenn er
ebenfalls auf 0 gedreht wurde (Anzeige „Auf 0 drehen“).
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimeLine, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .voice_custom import action_choices
from .widgets import FlowLayout, button, font, page_header, searchable

ARDUINO = Path(__file__).resolve().parents[1] / "resources" / "arduino"
SKETCH = ARDUINO / "AluPC-Tasten" / "AluPC-Tasten.ino"
SKETCHES = {"AluPC-Tasten": "Tastenbox-Programm", "Poti-Test": "Poti-Test (welcher Draht ist die Mitte?)",
            "Buzzer-Test": "Buzzer-Test (Taste drücken = Lied, Buzzer an A2)",
            "Pin-Finder": "Pin-Finder (an welchem Pin hängt welche Taste?)"}
LED_MODES = {"live": "Nur wenn Bildschirm 2 etwas zeigt (live)", "an": "Immer an", "aus": "Aus"}
LED_EFFECTS = {"ruhig": "Ruhig (gleichmäßig)", "atmen": "Atmen (langsam heller und dunkler)", "blinken": "Blinken",
               "regenbogen": "Farbwechsel (alle Farben nacheinander)"}
# Besondere Einträge in den Tasten-Listen: öffnen eine Dateiauswahl
FILE_CHOICES = [("__klang__", "Klang-Datei am PC abspielen … (Datei wählen)"),
                ("__midi__", "MIDI-Datei auf dem Buzzer … (Datei wählen)")]
LED_HELP = (
    "<b>Wann:</b> wann die LEDs überhaupt leuchten. <b>Effekt:</b> ruhig = gleichmäßig, atmen = langsam heller und "
    "dunkler, blinken, Farbwechsel = läuft durch alle Farben. <b>Farbe:</b> mischt deine roten, grünen und blauen LEDs "
    "in jeder Ecke – Rot = nur die roten (Pin 10), Grün = nur die grünen (Pin 9), Blau = nur die blauen (Pin 16), "
    "Weiß = alle drei. <b>Farbschema</b> gilt nur für RGB-LED-Streifen (WS2812), nicht für einzelne LEDs.")
QUICK_COLORS = [("Rot", "#ff0000"), ("Grün", "#00ff00"), ("Blau", "#0000ff"), ("Weiß", "#ffffff"),
                ("Lila", "#ff00ff"), ("Gelb", "#ffff00"), ("Türkis", "#00ffff")]
CELL = 132  # 3 × 132 passt auch ins kleinste Fenster (600 px)


class KeyCap(QWidget):
    """Eine Taste der Box: zeigt, was sie tut; leuchtet auf, wenn sie gedrückt wurde."""

    clicked = Signal()

    def __init__(self, number: int, parent=None):
        super().__init__(parent)
        self.number = number
        self.text = ""
        self.long_text = ""  # Befehl bei langem Druck (klein unten)
        self.selected = False
        self.down = False  # Fehlersuche: Taste ist gerade unten (Rohwerte der Box)
        self.glow = 0.0
        self.setFixedSize(CELL, 92)
        self.setCursor(Qt.PointingHandCursor)
        # Aufleuchten: 25 Bilder/s reichen (vorher bis 60 – jedes Bild zeichnet die Taste neu, das bremste beim Tippen)
        self._anim = QTimeLine(700, self)
        self._anim.setUpdateInterval(40)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_glow)

    def _set_glow(self, v) -> None:
        self.glow = 1.0 - float(v)
        self.update()

    def flash(self) -> None:
        self._anim.stop()
        self._anim.setCurrentTime(0)  # QTimeLine liefe sonst an der alten Stelle weiter
        self.glow = 1.0
        self.update()
        self._anim.start()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        rad = {"normal": 14, "holz": 6}.get(t.style, 4) * t.radius_scale
        accent = QColor(t.accent)
        # Tastenkappe: etwas heller oben (wie eine echte Taste), Rand, beim Drücken Leuchten in Akzentfarbe
        from PySide6.QtGui import QLinearGradient

        shade = QLinearGradient(r.topLeft(), r.bottomLeft())  # weicher Verlauf statt hartem hellen Block
        shade.setColorAt(0, t.mix(t.surface2, "#ffffff", 0.07 if t.dark else 0.6))
        shade.setColorAt(1, QColor(t.surface2))
        p.setPen(QPen(QColor(t.border), 1.2))
        p.setBrush(shade)
        p.drawRoundedRect(r, rad, rad)
        p.setPen(Qt.NoPen)
        if self.selected:
            p.setPen(QPen(accent, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad, rad)
            p.setPen(Qt.NoPen)
        if self.glow > 0 or self.down:
            g = QColor(accent)
            g.setAlphaF(0.55 * (1.0 if self.down else self.glow))
            p.setBrush(g)
            p.drawRoundedRect(r, rad, rad)
            p.setPen(QPen(accent, 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad, rad)
        p.setPen(QColor(t.muted))
        p.setFont(font(8, QFont.DemiBold))
        p.drawText(r.adjusted(10, 6, -10, -6), Qt.AlignLeft | Qt.AlignTop, str(self.number))
        p.setPen(QColor(t.text if self.text else t.muted))
        p.setFont(font(9 if self.long_text else 10, QFont.DemiBold))
        body = r.adjusted(8, 16, -8, -20 if self.long_text else -8)
        p.drawText(body, Qt.AlignCenter | Qt.TextWordWrap, self.text or "– frei –")
        if self.long_text:
            p.setPen(QColor(t.muted))
            p.setFont(font(7.5))
            p.drawText(r.adjusted(8, 0, -8, -6), Qt.AlignHCenter | Qt.AlignBottom,
                       p.fontMetrics().elidedText(f"lang: {self.long_text}", Qt.ElideRight, int(r.width() - 16)))
        p.end()


class KnobDial(QWidget):
    """Der Drehregler als Skala (wie auf der Box): Zeiger = Reglerstellung, darunter das Ziel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value: int | None = None
        self.waiting = False
        self.target = ""
        self.setFixedSize(220, 200)

    def set_state(self, value: int | None, waiting: bool, target: str) -> None:
        self.value, self.waiting, self.target = value, waiting, target
        self.update()

    @staticmethod
    def _angle(v: float) -> float:
        return math.radians(225 - 270 * v / 100)  # 0 % links unten, 100 % rechts unten

    def paintEvent(self, _e):
        t = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, 96)
        radius = 78
        accent = QColor(t.warning if self.waiting else t.accent)
        # Skala: Striche alle 10 %, lange bei 0/50/100
        for i in range(0, 101, 5):
            a = self._angle(i)
            long = i % 50 == 0
            inner = radius - (12 if long else (8 if i % 10 == 0 else 4))
            p.setPen(QPen(QColor(t.text if long else t.muted), 2 if long else 1))
            p.drawLine(QPointF(c.x() + math.cos(a) * inner, c.y() - math.sin(a) * inner),
                       QPointF(c.x() + math.cos(a) * radius, c.y() - math.sin(a) * radius))
        p.setFont(font(7.5))
        p.setPen(QColor(t.muted))
        for v, align in ((0, Qt.AlignLeft), (100, Qt.AlignRight)):
            a = self._angle(v)
            pt = QPointF(c.x() + math.cos(a) * (radius + 4), c.y() - math.sin(a) * (radius + 4))
            p.drawText(QRectF(pt.x() - 20, pt.y() + 2, 40, 14), Qt.AlignHCenter | align, str(v))
        # Bogen bis zur Stellung
        arc = QRectF(c.x() - radius + 18, c.y() - radius + 18, (radius - 18) * 2, (radius - 18) * 2)
        p.setPen(QPen(QColor(t.border), 6, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(arc, 225 * 16, -270 * 16)
        if self.value is not None:
            p.setPen(QPen(accent, 6, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc, 225 * 16, round(-270 * 16 * self.value / 100))
            a = self._angle(self.value)
            p.setPen(QPen(accent, 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c, QPointF(c.x() + math.cos(a) * (radius - 26), c.y() - math.sin(a) * (radius - 26)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.surface2))
        p.drawEllipse(c, 26, 26)
        p.setPen(QColor(t.text))
        p.setFont(font(13, QFont.Bold))
        p.drawText(QRectF(c.x() - 30, c.y() - 14, 60, 28), Qt.AlignCenter,
                   "–" if self.value is None else f"{self.value}")
        p.setFont(font(9, QFont.DemiBold))
        p.setPen(accent if self.waiting else QColor(t.muted))
        text = "↺ Auf 0 drehen" if self.waiting else (self.target or "")
        p.drawText(QRectF(0, self.height() - 26, self.width(), 22), Qt.AlignCenter, text)
        p.end()


class TastenboxPage(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = c = controller
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(page_header("Tastenbox", "9 Tasten und ein Drehregler am USB (Arduino Pro Micro oder Nano)", "grid"))
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        intro = QLabel(
            "<b>So geht's:</b> ① Eine Taste unten antippen (oder die echte Taste drücken) und wählen, was sie tun soll "
            "– kurz drücken und lang drücken (über 0,6 s) gehen getrennt. ② Beim Drehregler wählen, was er regelt. "
            "③ Unter <b>Wenn → Dann</b> gehen Tastenfolgen (z. B. 7‑1‑2 schnell hintereinander), Codes und Zahlen am "
            "Regler. In jeder Auswahlliste kann man <b>tippen, um zu suchen</b>.")
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        root.addWidget(intro)
        osd_row = QWidget()
        ol = FlowLayout(spacing=12)
        osd_row.setLayout(ol)
        cfg = c.config["tasten"]
        osd = QCheckBox("Eingaben unten links am Monitor anzeigen")
        osd.setToolTip("Bei jedem Tastendruck kurz unten links am Hauptmonitor: welche Taste (mit Lage auf der Box), "
                       "was sie tut, laufende Tastenfolge oder Code, ausgelöste Wenn → Dann-Regel")
        osd.setChecked(bool(cfg.get("anzeige", False)))
        osd_knob = QCheckBox("auch den Regler")
        osd_knob.setChecked(bool(cfg.get("anzeige_regler", True)))
        osd_knob.setEnabled(osd.isChecked())
        osd.toggled.connect(lambda v: (self._save(anzeige=v), osd_knob.setEnabled(v)))
        osd_knob.toggled.connect(lambda v: self._save(anzeige_regler=v))
        ol.addWidget(osd)
        ol.addWidget(osd_knob)
        root.addWidget(osd_row)

        cols = FlowLayout(spacing=14)  # breit: Tasten links, Regler rechts – schmal: untereinander
        holder = QWidget()
        holder.setLayout(cols)
        root.addWidget(holder)

        # ---- Tasten 3 × 3, wie auf der Box
        keys_box = QGroupBox("Tasten")
        kl = QVBoxLayout(keys_box)
        hint = QLabel("Wie auf der Box angeordnet. Taste antippen, unten wählen, was sie tut.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        kl.addWidget(hint)
        grid_box = QWidget()
        grid = QGridLayout(grid_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.choices = [("", "– nichts –")] + action_choices(c.config)
        self.caps: dict[int, KeyCap] = {}
        for n in range(1, 10):
            cap = KeyCap(n)
            cap.clicked.connect(lambda n=n: self.select(n))
            grid.addWidget(cap, (n - 1) // 3, (n - 1) % 3)
            self.caps[n] = cap
        kl.addWidget(grid_box, 0, Qt.AlignLeft)
        row = QHBoxLayout()
        self.key_label = QLabel("Taste 9:")
        self.key_label.setObjectName("Muted")
        self.key_label.setFixedWidth(self.key_label.sizeHint().width() + 4)  # Textwechsel → kein neues Layout
        self.action = searchable(QComboBox())
        self.action.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.action.setMinimumContentsLength(16)
        for key, label in self.choices + FILE_CHOICES:
            self.action.addItem(label, key)
        self.action.activated.connect(lambda _i: self._set_key(self.action.currentData() or ""))
        row.addWidget(self.key_label)
        row.addWidget(self.action, 1)
        kl.addLayout(row)
        long_row = QHBoxLayout()
        long_label = QLabel("Lang:")
        long_label.setObjectName("Muted")
        long_label.setToolTip("Taste länger als 0,6 s halten (Arduino-Programm ab Version 2)")
        self.long_action = searchable(QComboBox())
        self.long_action.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.long_action.setMinimumContentsLength(16)
        self.long_action.addItem("– wie kurz –", "")
        for key, label in self.choices[1:] + FILE_CHOICES:
            self.long_action.addItem(label, key)
        self.long_action.activated.connect(lambda _i: self._set_long(self.long_action.currentData() or ""))
        long_row.addWidget(long_label)
        long_row.addWidget(self.long_action, 1)
        kl.addLayout(long_row)
        cols.addWidget(keys_box)

        # ---- Regler
        knob_box = QGroupBox("Drehregler")
        rl = QVBoxLayout(knob_box)
        self.dial = KnobDial()
        rl.addWidget(self.dial, 0, Qt.AlignHCenter)
        form = QFormLayout()
        self.target = QComboBox()
        self.target.setToolTip("Wechselt das Ziel, startet es bei 0 – den Regler dann auch auf 0 drehen.")
        self._fill_targets()
        self.target.activated.connect(lambda _i: c.set_knob_target(self.target.currentData()))
        self.preset = QComboBox()
        self.preset.addItem("– Voreinstellung wählen –", -1)
        for i, (name, _t, _s) in enumerate(c.KNOB_PRESETS):
            self.preset.addItem(name, i)
        self.preset.setToolTip("Ziel und Skala in einem Schritt (Zählwerk = 10-Gang-Poti, Arduino-Programm 5)")
        self.preset.activated.connect(self._apply_preset)
        form.addRow("Vorlage:", self.preset)
        form.addRow("Regelt:", self.target)
        self.scale = QComboBox()
        for key, label in c.KNOB_SCALES.items():
            self.scale.addItem(label, key)
        self.scale.setCurrentIndex(max(0, self.scale.findData(c.knob_scale())))
        self.scale.setToolTip("Mit Zählwerk-Drehknopf: die Zahl darauf bestimmt die Lautstärke")
        self.scale.activated.connect(lambda _i: (self._save(skala=self.scale.currentData()), self.refresh()))
        form.addRow("Skala:", self.scale)
        for combo in (self.preset, self.scale):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        self.only = QCheckBox("PC-Ton nur am Regler")
        self.only.setToolTip("Andere Wege (Windows, Tastatur, Handy, Sprache) werden zurückgestellt. "
                             "Programme einzeln (Seite Audio) gehen weiter.")
        self.only.setChecked(bool(c.config["tasten"].get("nur_regler", True)))
        self.only.toggled.connect(lambda v: self._save(nur_regler=v))
        cfg = c.config["tasten"]
        self.invert = QCheckBox("Richtung umkehren")
        self.invert.setChecked(bool(cfg.get("umkehren")))
        self.invert.toggled.connect(lambda v: self._save(umkehren=v))
        self.pickup = QCheckBox("Bei Wechsel erst auf 0")
        self.pickup.setToolTip("Aus: das neue Ziel springt sofort auf die Reglerstellung")
        self.pickup.setChecked(bool(cfg.get("abgleich", True)))
        self.pickup.toggled.connect(lambda v: self._save(abgleich=v))
        self.top = QSpinBox()
        self.top.setRange(10, 100)
        self.top.setSingleStep(5)
        self.top.setSuffix(" %")
        self.top.setToolTip("Ganz aufgedreht = dieser Wert (z. B. nie lauter als 80 %)")
        self.top.setValue(int(cfg.get("max", 100) or 100))
        self.top.valueChanged.connect(lambda v: self._save(max=v))
        form.addRow("Höchstens:", self.top)
        rl.addLayout(form)
        for box in (self.only, self.invert, self.pickup):
            rl.addWidget(box)
        knob_box.setFixedWidth(320)
        cols.addWidget(knob_box)

        root.addWidget(self._led_box())
        root.addWidget(self._buzzer_box())
        root.addWidget(self._rules_box())
        root.addWidget(self._device_box())
        root.addWidget(self._debug_box())
        root.addStretch(1)

        bb = c.buttonbox
        bb.state_changed.connect(self.refresh)
        bb.state_changed.connect(self._fill_ports)
        self._select_later = 1
        self._select_timer = QTimer(self, singleShot=True, interval=400,
                                    timeout=lambda: self.select(self._select_later))
        bb.key.connect(self._pressed)
        bb.long_key.connect(self._pressed)
        # Regler: viele Werte pro Sekunde → gebündelt neu zeichnen
        self._live = QTimer(self, singleShot=True, interval=100, timeout=lambda: (self.refresh(), self._rules_live()))
        bb.volume.connect(lambda _v: self._live.isActive() or self._live.start())
        bb.fine.connect(lambda _v: self._live.isActive() or self._live.start())
        bb.raw.connect(self._raw)
        bb.log_line.connect(self._log)
        bb.state_changed.connect(self._debug_info)
        c.changed.connect(self.refresh)
        self.select(1)
        self.refresh()

    # ------------------------------------------------------------ Speichern
    def _save(self, **changes) -> None:
        c = self.controller
        c.config["tasten"] = {**c.config["tasten"], **changes}
        c.apply_buttonbox()

    def _set_key(self, action: str) -> None:
        action = self._file_action(action)
        if action is None:  # Dateiauswahl abgebrochen
            return self.select(self.selected)
        keys = self.controller.box_keys()
        keys[str(self.selected)] = action
        self._save(keys=keys)
        self.select(self.selected)
        self.refresh()

    def _set_long(self, action: str) -> None:
        action = self._file_action(action)
        if action is None:
            return self.select(self.selected)
        cfg = self.controller.config["tasten"]
        lang = {**(cfg.get("lang") or {}), str(self.selected): action}
        self._save(lang={k: v for k, v in lang.items() if v})
        self.select(self.selected)
        self.refresh()

    def _file_action(self, action: str) -> str | None:
        """„Klang-/MIDI-Datei wählen …“ → Datei aussuchen, in den AluPC-Ordner kopieren → „klang:…“ bzw.
        „buzzer_midi:…“. Andere Aktionen bleiben, wie sie sind; None = abgebrochen."""
        if action not in ("__klang__", "__midi__"):
            return action
        from .. import buzzer, sounds

        midi = action == "__midi__"
        path, _ = QFileDialog.getOpenFileName(self, "MIDI-Datei wählen" if midi else "Klang wählen", "",
                                              "MIDI (*.mid *.midi)" if midi else
                                              "Töne (*.wav *.mp3 *.ogg *.flac *.m4a)")
        if not path:
            return None
        if midi:
            try:
                buzzer.midi_notes(path)
            except (OSError, ValueError) as exc:
                self.controller.message.emit(f"Diese MIDI-Datei geht nicht: {exc}")
                return None
            command = "buzzer_midi:" + buzzer.upload_midi(path)
        else:
            command = "klang:" + sounds.upload(path)
        self._refill_choices()
        return command

    def _refill_choices(self) -> None:
        """Aktionslisten neu (neue Klang-/MIDI-Datei, neue Regel)."""
        self.choices = [("", "– nichts –")] + action_choices(self.controller.config)
        for combo, items in ((self.action, self.choices), (self.long_action, [("", "– wie kurz –")] + self.choices[1:])):
            combo.blockSignals(True)
            combo.clear()
            for key, label in items + FILE_CHOICES:
                combo.addItem(label, key)
            combo.blockSignals(False)

    # ------------------------------------------------------------ LEDs, Buzzer, Box
    def _led_box(self) -> QGroupBox:
        c = self.controller
        box = QGroupBox("LEDs")
        lay = QVBoxLayout(box)
        help_text = QLabel(LED_HELP)
        help_text.setObjectName("Muted")
        help_text.setWordWrap(True)
        lay.addWidget(help_text)
        df = QFormLayout()
        led = QComboBox()
        for key, label in LED_MODES.items():
            led.addItem(label, key)
        led.setCurrentIndex(max(0, led.findData(c.config["tasten"].get("led", "live"))))
        led.currentIndexChanged.connect(lambda _i: self._save(led=led.currentData()))
        df.addRow("Wann:", led)
        effect = QComboBox()
        for key, label in LED_EFFECTS.items():
            effect.addItem(label, key)
        effect.setCurrentIndex(max(0, effect.findData(c.config["tasten"].get("led_effekt", "ruhig"))))
        effect.currentIndexChanged.connect(lambda _i: self._save(led_effekt=effect.currentData()))
        df.addRow("Effekt:", effect)
        self.color_btn = QPushButton()
        self.color_btn.setToolTip("Eigene Mischfarbe wählen")
        self.color_btn.clicked.connect(self._pick_color)
        self._show_color()
        quick = QWidget()
        ql = FlowLayout(spacing=4)
        quick.setLayout(ql)
        ql.addWidget(self.color_btn)
        from .icons import dot_icon

        for name, col in QUICK_COLORS:
            b = QPushButton(name)
            b.setIcon(dot_icon(col, 12))
            b.clicked.connect(lambda _c=False, col=col: (self._save(led_farbe=col), self._show_color()))
            ql.addWidget(b)
        df.addRow("Farbe:", quick)
        bright = QSpinBox()
        bright.setRange(5, 100)
        bright.setSingleStep(5)
        bright.setSuffix(" %")
        bright.setValue(c.box_led_brightness())
        bright.valueChanged.connect(lambda v: self._save(led_hell=v))
        df.addRow("Helligkeit:", bright)
        from ..tastenbox import LED_SCHEMES

        scheme = QComboBox()
        for key, label in LED_SCHEMES.items():
            scheme.addItem(label, key)
        scheme.setCurrentIndex(max(0, scheme.findData(c.config["tasten"].get("led_schema", "einfarbig"))))
        scheme.setToolTip("Nur für RGB-LED-Streifen (WS2812): Ecken in eigenen Farben, Zustand (Rot = live, "
                          "Gelb = Schwarz/Standbild) oder Regenbogen – siehe LED-Plan")
        scheme.currentIndexChanged.connect(lambda _i: self._save(led_schema=scheme.currentData()))
        df.addRow("Farbschema:", scheme)
        blink = QCheckBox("LEDs blitzen bei jedem Tastendruck kurz auf")
        blink.setChecked(bool(c.config["tasten"].get("led_blink", False)))
        blink.toggled.connect(lambda v: self._save(led_blink=v))
        df.addRow("", blink)
        lay.addLayout(df)
        test = button("LED-Test: Rot, Grün, Blau einzeln", "sun")
        test.setToolTip("Zeigt jede Farbe 1,2 s allein – so sieht man, welche LEDs nicht leuchten")
        test.clicked.connect(self._test_led)
        lay.addWidget(test, 0, Qt.AlignLeft)
        return box

    def _buzzer_box(self) -> QGroupBox:
        from .. import buzzer

        c = self.controller
        box = QGroupBox("Buzzer")
        lay = QVBoxLayout(box)
        hint = QLabel("Ein kleiner Lautsprecher in der Box: <b>+</b> an <b>A2</b>, <b>−</b> an Minus. Passiver Buzzer "
                      "(unten grüne Platine) spielt Melodien und MIDI-Dateien, aktiver (schwarz vergossen) piept nur. "
                      f"Braucht das Tastenbox-Programm ab Version {buzzer.MIN_VERSION}. Melodien kann man Tasten "
                      "und Wenn → Dann-Regeln geben.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        click = QCheckBox("Kurzer Klick bei jedem Tastendruck")
        click.setChecked(bool(c.config["tasten"].get("buzzer_klick", False)))
        click.toggled.connect(lambda v: self._save(buzzer_klick=v))
        lay.addWidget(click)
        row = QHBoxLayout()
        self.song = searchable(QComboBox())
        for key, (label, _t, _n) in buzzer.SONGS.items():
            self.song.addItem(label, key)
        self.song.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.song.setMinimumContentsLength(14)
        play = button("Abspielen", "play")
        play.clicked.connect(lambda: c.play_buzzer_song(self.song.currentData()))
        midi = button("MIDI-Datei …", "download")
        midi.setToolTip("MIDI-Datei aussuchen und einmal auf dem Buzzer abspielen (der Buzzer spielt immer nur "
                        "die höchste Stimme)")
        midi.clicked.connect(self._try_midi)
        stop = button("Stopp", "x")
        stop.clicked.connect(c.buzzer.stop)
        row.addWidget(self.song, 1)
        lay.addLayout(row)
        buttons = QWidget()
        bl = FlowLayout(spacing=6)  # Knöpfe brechen im schmalen Fenster um
        buttons.setLayout(bl)
        for b in (play, midi, stop):
            bl.addWidget(b)
        lay.addWidget(buttons)
        return box

    def _try_midi(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "MIDI-Datei abspielen", "", "MIDI (*.mid *.midi)")
        if path:
            self.controller.play_buzzer_midi(path)

    def _device_box(self) -> QGroupBox:
        c = self.controller
        dev = QGroupBox("Box und Arduino-Programme")
        dl = QVBoxLayout(dev)
        df = QFormLayout()
        on = QCheckBox("Tastenbox suchen und benutzen")
        on.setChecked(bool(c.config["tasten"].get("on", True)))
        on.toggled.connect(lambda v: self._save(on=v))
        df.addRow("", on)
        self.port = QComboBox()
        self.port.addItem("Automatisch", "")
        self._fill_ports()
        self.port.currentIndexChanged.connect(lambda _i: self._save(port=self.port.currentData()))
        df.addRow("Anschluss:", self.port)
        dl.addLayout(df)
        explain = QLabel("Programme für die Arduino-IDE. Speichern → in der IDE öffnen → Board „Arduino Leonardo“ "
                         "(Pro Micro) bzw. „Arduino Nano“ → Anschluss → Hochladen. Danach für AluPC wieder das "
                         "<b>Tastenbox-Programm</b> hochladen – die Test-Programme reden nicht mit AluPC.")
        explain.setObjectName("Muted")
        explain.setWordWrap(True)
        dl.addWidget(explain)
        for name, label in SKETCHES.items():  # untereinander: passt auch ins kleinste Fenster
            b = button(label.split(" (")[0] + " speichern …", "download")
            b.setToolTip(label + " – speichert einen fertigen Ordner für die Arduino-IDE (Ordner + .ino gleichen Namens)")
            b.clicked.connect(lambda _c=False, n=name: self._save_sketch(n))
            row = QHBoxLayout()
            row.addWidget(b)
            what = QLabel(label.split(" (", 1)[1].rstrip(")") if " (" in label else "für AluPC – immer drauf lassen")
            what.setObjectName("Muted")
            what.setWordWrap(True)
            row.addWidget(what, 1)
            dl.addLayout(row)
        wiring = QLabel("Anschlüsse Pro Micro: Tasten 2–8, 14, 15 · Regler-Mitte A0 · LEDs rot 10, grün 9, blau 16 · "
                        "Buzzer A2.  Nano: Tasten 2–10 · A0 · LEDs 11, 12, 13 · Buzzer A2.  Je Farbe ein "
                        "220-Ω-Widerstand für 4 LEDs · das andere Tasten-Bein und die kurzen LED-Beine an Minus (GND).")
        wiring.setObjectName("Muted")
        wiring.setWordWrap(True)
        dl.addWidget(wiring)
        return dev

    def select(self, n: int) -> None:
        """Taste n bearbeiten (Klick auf die Kappe oder Druck auf die echte Taste)."""
        if n != getattr(self, "selected", None):
            for k in (getattr(self, "selected", None), n):  # nur die zwei betroffenen Kappen neu zeichnen
                cap = self.caps.get(k)
                if cap is not None:
                    cap.selected = k == n
                    cap.update()
        self.selected = n
        current = self.controller.box_keys().get(str(n), "")
        if current and self.action.findData(current) < 0:
            self.action.addItem(current, current)  # z. B. gelöschte Szene: nicht still verlieren
        self.action.setCurrentIndex(max(0, self.action.findData(current)))
        self.key_label.setText(f"Taste {n}:")
        long = (self.controller.config["tasten"].get("lang") or {}).get(str(n), "")
        if long and self.long_action.findData(long) < 0:
            self.long_action.addItem(long, long)
        self.long_action.setCurrentIndex(max(0, self.long_action.findData(long)))

    # ------------------------------------------------------------ LED
    def _show_color(self) -> None:
        col = self.controller.config["tasten"].get("led_farbe", "#00ffa0")
        from .icons import dot_icon

        self.color_btn.setText(col.upper())
        self.color_btn.setIcon(dot_icon(col, 14))

    def _pick_color(self) -> None:
        col = QColorDialog.getColor(QColor(self.controller.config["tasten"].get("led_farbe", "#00ffa0")), self,
                                    "Farbe der LEDs")
        if col.isValid():
            self._save(led_farbe=col.name())
            self._show_color()

    # LED-Test: jede Farbe einzeln (rote LEDs an Pin 10, grüne an 9, blaue an 16), dann alle – so sieht man,
    # welche Farbgruppe nicht leuchtet. Mit der eingestellten Farbe allein ginge das nicht (Standard #00FFA0 hat 0 Rot).
    LED_TEST_STEPS = [("Rot", "#ff0000"), ("Grün", "#00ff00"), ("Blau", "#0000ff"), ("Alle (weiß gemischt)", "#ffffff")]

    def _test_led(self) -> None:
        """Rot, Grün, Blau je 1,2 s einzeln, dann alle; danach wieder wie eingestellt."""
        c = self.controller
        if not c.buttonbox.connected:
            c.message.emit("Keine Box verbunden.")
            return
        bb = c.buttonbox
        bb.set_effect("ruhig")
        bb.set_led(100)
        step_ms = 1200

        def show(name, color):
            bb.set_color(color)
            bb.set_led(100)
            c.message.emit(f"LED-Test: jetzt {name} – leuchten alle 4 Ecken?")

        for i, (name, color) in enumerate(self.LED_TEST_STEPS):
            QTimer.singleShot(i * step_ms, lambda n=name, col=color: show(n, col))
        QTimer.singleShot(len(self.LED_TEST_STEPS) * step_ms,
                          lambda: (setattr(c, "_led_level", None), setattr(c, "_led_extra", None),
                                   c.update_box_led(), c.message.emit("LED-Test fertig.")))

    # ------------------------------------------------------------ Fehlersuche
    def _apply_preset(self, _i: int) -> None:
        index = self.preset.currentData()
        self.preset.setCurrentIndex(0)
        if index is None or index < 0:
            return
        self.controller.apply_knob_preset(index)
        self.controller.message.emit(f"Regler: {self.controller.KNOB_PRESETS[index][0]}")
        self.refresh()

    # ------------------------------------------------------------ Wenn → Dann
    def _rules_box(self) -> QGroupBox:
        box = QGroupBox("Wenn → Dann")
        lay = QVBoxLayout(box)
        hint = QLabel("<b>Wenn</b> an der Box etwas passiert, <b>dann</b> macht AluPC etwas (Programm, Webseite, Video, "
                      "Klang, Buzzer-Melodie, eigener Befehl …). Drei Arten von „Wenn“:<br>"
                      "• <b>Tastenfolge</b> – mehrere Tasten schnell hintereinander, z. B. 7‑1‑2 = unten links, oben "
                      "links, oben Mitte. Keine Extra-Taste nötig.<br>"
                      "• <b>Code</b> – erst die Code-Taste <i>lang</i> drücken (LEDs blinken), dann Ziffern tippen, "
                      "1,5 s warten. Gut für „geheime“ Aktionen.<br>"
                      "• <b>Reglerzahl</b> – Regler auf „Zahl 0–999“ stellen und auf eine Zahl drehen.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.rules_live = QLabel()
        self.rules_live.setWordWrap(True)
        lay.addWidget(self.rules_live)
        self.rules_list = QListWidget()
        self.rules_list.setMinimumHeight(110)
        self.rules_list.itemChanged.connect(self._rule_toggled)
        self.rules_list.itemDoubleClicked.connect(lambda _i: self._edit_rule())
        lay.addWidget(self.rules_list)
        row = QHBoxLayout()
        for text, icon, slot in (("Neu", "plus", self._new_rule), ("Bearbeiten", "edit", self._edit_rule),
                                 ("Testen", "play", self._test_rule), ("Löschen", "trash", self._delete_rule)):
            b = button(text, icon, primary=text == "Neu")
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()  # eigene Zeile: passt auch ins kleinste Fenster
        row.addWidget(QLabel("Code-Taste:"))
        self.code_key = QSpinBox()
        self.code_key.setRange(1, 9)
        self.code_key.setValue(self.controller.code_key())
        self.code_key.setToolTip("Diese Taste lang drücken = Code-Eingabe (nur wenn eine Regel einen Code hat)")
        self.code_key.valueChanged.connect(lambda v: (self._save(code_taste=v), self._rules_live()))
        row.addWidget(self.code_key)
        row.addStretch(1)
        lay.addLayout(row)
        cfg = self.controller.config["tasten"]
        wait_row = QWidget()
        wl = FlowLayout(spacing=10)  # bricht im schmalen Fenster um
        wait_row.setLayout(wl)
        wait = QCheckBox("Jede Taste wartet kurz (Tastenfolgen bleiben geheim)")
        wait.setToolTip("An: jede Taste führt ihre Funktion erst nach der Wartezeit aus – man sieht nicht, mit welchen "
                        "Tasten eine Folge anfängt. Aus: nur Tasten, mit denen eine Folge anfängt, warten.")
        wait.setChecked(bool(cfg.get("folge_warten", True)))
        wait.toggled.connect(lambda v: self._save(folge_warten=v))
        gap = QSpinBox()
        gap.setRange(300, 2000)
        gap.setSingleStep(100)
        gap.setSuffix(" ms")
        gap.setPrefix("Wartezeit ")
        gap.setToolTip("So lange wartet AluPC auf die nächste Taste einer Folge (nur wenn es Tastenfolgen gibt)")
        gap.setValue(self.controller.seq_gap_ms())
        gap.valueChanged.connect(lambda v: self._save(folge_ms=v))
        wl.addWidget(wait)
        wl.addWidget(gap)
        lay.addWidget(wait_row)
        self._fill_rules()
        return box

    def _rules(self) -> list[dict]:
        return self.controller.box_rules()

    def _store_rules(self, rules: list[dict]) -> None:
        self._save(regeln=rules)
        self._fill_rules()
        self._refill_choices()  # „Wenn → Dann: Name“ in den Tasten-Listen
        self.select(self.selected)

    def _fill_rules(self) -> None:
        from ..automations import describe

        self.rules_list.blockSignals(True)
        self.rules_list.clear()
        for rule in self._rules():
            item = QListWidgetItem(f"{rule['name']}  ·  {describe(rule)}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if rule["an"] else Qt.Unchecked)
            item.setData(Qt.UserRole, rule["name"])
            self.rules_list.addItem(item)
        if not self._rules():
            item = QListWidgetItem("Noch keine Regel – „Neu“ drücken.")
            item.setFlags(Qt.NoItemFlags)
            self.rules_list.addItem(item)
        self.rules_list.blockSignals(False)
        self._rules_live()

    def _rules_live(self) -> None:
        c = self.controller
        value = c.box_number()
        parts = [f"Regler jetzt: <b>{value:03d}</b>" if value is not None else "Regler: –"]
        if c.knob_target() != "wert":
            parts.append("Reglerzahl-Regeln ruhen (Regler „Regelt:“ auf „Zahl 0–999“ stellen)")
        if any(r["wenn"]["folge"] for r in self._rules()):
            parts.append("Tastenfolgen: Tasten schnell hintereinander drücken")
        if any(r["wenn"]["code"] for r in self._rules()):
            parts.append(f"Code: Taste {c.code_key()} lang drücken, Ziffern tippen, 1,5 s warten")
        if c.box_code is not None:
            parts.append("Eingabe läuft: " + ("-".join(c.box_code) or "…"))
        self.rules_live.setText(" · ".join(parts))

    def _current_rule(self) -> dict | None:
        item = self.rules_list.currentItem()
        name = item.data(Qt.UserRole) if item else None
        return next((r for r in self._rules() if r["name"] == name), None)

    def _new_rule(self) -> None:
        self._open_rule(None)

    def _edit_rule(self) -> None:
        rule = self._current_rule()
        if rule is not None:
            self._open_rule(rule)

    def _open_rule(self, rule: dict | None) -> None:
        from .rules_dialog import RuleDialog

        rules = self._rules()
        taken = {r["name"] for r in rules} - ({rule["name"]} if rule else set())
        dlg = RuleDialog(self.controller, rule, taken, self)
        if not dlg.exec():
            return
        new = dlg.rule()
        if rule is not None:
            new["an"] = rule["an"]
            rules = [new if r["name"] == rule["name"] else r for r in rules]
        else:
            rules.append(new)
        self._store_rules(rules)

    def _rule_toggled(self, item) -> None:
        name = item.data(Qt.UserRole)
        on = item.checkState() == Qt.Checked
        self._store_rules([{**r, "an": on} if r["name"] == name else r for r in self._rules()])

    def _test_rule(self) -> None:
        rule = self._current_rule()
        if rule is not None:
            self.controller.run_rule(rule)

    def _delete_rule(self) -> None:
        rule = self._current_rule()
        if rule is not None:
            self._store_rules([r for r in self._rules() if r["name"] != rule["name"]])

    def _debug_box(self) -> QGroupBox:
        box = QGroupBox("Fehlersuche")
        lay = QVBoxLayout(box)
        top = QHBoxLayout()
        self.debug_toggle = QCheckBox("Anzeigen")
        top.addWidget(self.debug_toggle)
        self.debug_summary = QLabel()
        self.debug_summary.setObjectName("Muted")
        top.addWidget(self.debug_summary, 1)
        lay.addLayout(top)
        self.debug_body = QWidget()
        bl = QVBoxLayout(self.debug_body)
        bl.setContentsMargins(0, 0, 0, 0)
        self.debug_raw = QCheckBox("Rohwerte live (Tasten leuchten oben, solange sie unten sind)")
        self.debug_raw.toggled.connect(self._set_raw)
        bl.addWidget(self.debug_raw)
        self.raw_label = QLabel("–")
        self.raw_label.setWordWrap(True)
        bl.addWidget(self.raw_label)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(300)
        self.log_view.setMinimumHeight(150)
        self.log_view.setPlaceholderText("Hier erscheint jede Zeile zwischen AluPC und der Box.")
        bl.addWidget(self.log_view)
        row = QHBoxLayout()
        for text, icon, slot in (("LED testen", "sun", self._test_led), ("Neu verbinden", "refresh", self._reconnect),
                                 ("Protokoll kopieren", "copy", self._copy_log)):
            b = button(text, icon)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        bl.addLayout(row)
        self.debug_body.setVisible(False)
        self.debug_toggle.toggled.connect(self._show_debug)
        lay.addWidget(self.debug_body)
        self._debug_info()
        return box

    def _show_debug(self, on: bool) -> None:
        self.debug_body.setVisible(on)
        if on:
            self.log_view.setPlainText("\n".join(self.controller.buttonbox.log))
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        elif self.debug_raw.isChecked():
            self.debug_raw.setChecked(False)

    def _set_raw(self, on: bool) -> None:
        bb = self.controller.buttonbox
        bb.set_debug(on)
        if on and bb.connected and bb.version < 3:
            self.raw_label.setText(f"Die Box hat Programm {bb.version} – Rohwerte gibt es ab Version 3. "
                                   "„Arduino-Programm speichern …“ und neu aufspielen.")
        elif not on:
            self.raw_label.setText("–")
            for cap in self.caps.values():
                cap.down = False
                cap.update()

    def _raw(self, keys: str, knob: int) -> None:
        down = [str(i + 1) for i, ch in enumerate(keys) if ch == "1"]
        self.raw_label.setText(f"Tasten unten: {', '.join(down) or 'keine'}   ·   Regler A0: {knob} von 1023 "
                               f"({round(knob * 100 / 1023)} %)")
        for i, cap in enumerate(self.caps.values()):
            d = i < len(keys) and keys[i] == "1"
            if cap.down != d:
                cap.down = d
                cap.update()

    def _log(self, line: str) -> None:
        if self.debug_body.isVisible():
            self.log_view.appendPlainText(line)

    def _debug_info(self) -> None:
        bb = self.controller.buttonbox
        if bb.connected:
            board = f" · {bb.board}" if bb.board else ""
            old = "  (älter – neu aufspielen für Effekte/Rohwerte)" if bb.version < 3 else ""
            self.debug_summary.setText(f"{bb.port} · Programm {bb.version}{board}{old}")
        else:
            self.debug_summary.setText("Keine Box verbunden")

    def _reconnect(self) -> None:
        c = self.controller
        c.buttonbox.stop()
        c.buttonbox._silent.clear()
        c.apply_buttonbox()
        c.message.emit("Tastenbox: suche neu …")

    def _copy_log(self) -> None:
        bb = self.controller.buttonbox
        QGuiApplication.clipboard().setText("\n".join(bb.log))
        self.controller.message.emit("Protokoll kopiert.")

    def _save_sketch(self, name: str = "AluPC-Tasten") -> Path | None:
        """Sketch als Ordner speichern – die Arduino-IDE will „Name/Name.ino“."""
        base = QFileDialog.getExistingDirectory(self, f"{SKETCHES[name]}: Ordner wählen", str(Path.home()))
        if not base:
            return None
        target = Path(base) / name
        try:
            target.mkdir(parents=True, exist_ok=True)
            out = target / f"{name}.ino"
            out.write_text((ARDUINO / name / f"{name}.ino").read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            self.controller.message.emit(f"Speichern ging nicht: {exc}")
            return None
        self.controller.message.emit(f"Gespeichert: {out} – in der Arduino-IDE öffnen, Board wählen, hochladen.")
        return out

    # ------------------------------------------------------------ Listen
    def _fill_targets(self) -> None:
        c = self.controller
        current = c.knob_target()
        items = list(c.KNOB_TARGETS.items())
        try:
            from .. import mixer

            apps = sorted({s.get("app", "") for s in mixer.streams("out") if s.get("app")})
        except Exception:  # noqa: BLE001
            apps = []
        if current.startswith("app:") and current[4:] not in apps:
            apps.append(current[4:])  # Programm läuft gerade nicht: Auswahl trotzdem behalten
        items += [(f"app:{a}", f"Programm: {a}") for a in apps]
        self.target.blockSignals(True)
        self.target.clear()
        for key, label in items:
            self.target.addItem(label, key)
        self.target.setCurrentIndex(max(0, self.target.findData(current)))
        self.target.blockSignals(False)

    def showEvent(self, e):
        super().showEvent(e)
        self._fill_targets()  # Programme mit Ton ändern sich

    def _fill_ports(self) -> None:
        from ..tastenbox import candidate_ports

        current = self.controller.config["tasten"].get("port", "")
        ports = candidate_ports()
        self.port.blockSignals(True)
        while self.port.count() > 1:
            self.port.removeItem(1)
        for dev in ports + ([current] if current and current not in ports else []):
            self.port.addItem(dev, dev)
        self.port.setCurrentIndex(max(0, self.port.findData(current)))
        self.port.blockSignals(False)

    # ------------------------------------------------------------ Anzeige
    def _pressed(self, n: int) -> None:
        cap = self.caps.get(n)
        if cap is not None:
            cap.flash()
            # echte Taste drücken = diese Taste bearbeiten – aber erst, wenn kurz Ruhe ist: Auswahllisten umstellen
            # kostet ~20 ms (Layout), beim schnellen Tippen ruckelte es sonst
            self._select_later = n
            self._select_timer.start()

    def refresh(self) -> None:
        c = self.controller
        bb = c.buttonbox
        if not c.config["tasten"].get("on", True):
            self.status.setText("Aus.")
        elif bb.connected:
            lock = " · PC-Lautstärke nur am Regler" if c.volume_locked() else ""
            self.status.setText(f"✓ Verbunden ({bb.port}){lock}")
        else:
            self.status.setText("Nicht gefunden – Box einstecken, AluPC sucht alle paar Sekunden.")
        labels = dict(self.choices)
        keys = c.box_keys()
        longs = c.config["tasten"].get("lang") or {}
        for n, cap in self.caps.items():
            action = keys.get(str(n), "")
            cap.text = labels.get(action, action)
            long = longs.get(str(n), "")
            cap.long_text = labels.get(long, long) if long else ""
            cap.update()
        self.dial.set_state(c.knob_level() if bb.connected else None, c.knob_wait, c.knob_target_label())
        if self.scale.currentData() != c.knob_scale():
            self.scale.setCurrentIndex(max(0, self.scale.findData(c.knob_scale())))
        self.scale.setEnabled(c.knob_target() != "wert")
        self.only.setEnabled(c.knob_target() == "pc")  # Sperre gilt nur, wenn der Regler den PC-Ton regelt
        if self.target.currentData() != c.knob_target():
            self._fill_targets()
        self._rules_live()
