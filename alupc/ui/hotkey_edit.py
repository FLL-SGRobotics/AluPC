"""Tastenkürzel selbst auswählen: Knopf → Dialog „Tasten jetzt drücken“.

Während der Dialog offen ist, sind alle AluPC-Kürzel pausiert – sonst würde z. B. unter
Windows ein bereits belegtes Kürzel sofort ausgelöst statt aufgenommen.
"""

from __future__ import annotations

from PySide6.QtCore import QKeyCombination, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from .widgets import button, font

MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr}

# Das Programm setzt hier seinen HotkeyManager ein (zum Pausieren während der Aufnahme)
manager = None


def pretty(seq: str) -> str:
    if not seq:
        return "– keins –"
    text = QKeySequence(seq, QKeySequence.PortableText).toString(QKeySequence.NativeText)
    return (text.replace("Ctrl", "Strg").replace("PgDown", "Bild↓").replace("PgUp", "Bild↑")
            .replace("Meta", "Win").replace("Del", "Entf").replace("Ins", "Einfg"))


class CaptureDialog(QDialog):
    def __init__(self, title: str, current: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tastenkürzel festlegen")
        self.setMinimumWidth(420)
        self.sequence = current
        head = QLabel(title)
        head.setObjectName("SectionTitle")
        hint = QLabel("Tastenkombination drücken\nz. B. Strg + Alt + S")
        hint.setObjectName("Muted")
        hint.setAlignment(Qt.AlignCenter)
        self.shown = QLabel(pretty(current))
        self.shown.setAlignment(Qt.AlignCenter)
        self.shown.setFont(font(20, font().Weight.Bold))
        self.shown.setMinimumHeight(64)
        self.warn = QLabel("")
        self.warn.setObjectName("Muted")
        self.warn.setAlignment(Qt.AlignCenter)
        self.warn.setWordWrap(True)
        buttons = QDialogButtonBox()
        ok = button("Übernehmen", "check", primary=True)
        clear = button("Kein Kürzel", "x")
        cancel = button("Abbrechen")
        for b in (ok, clear, cancel):
            b.setFocusPolicy(Qt.NoFocus)  # Tasten sollen im Dialog landen, nicht auf Knöpfen
        buttons.addButton(ok, QDialogButtonBox.AcceptRole)
        buttons.addButton(clear, QDialogButtonBox.ResetRole)
        buttons.addButton(cancel, QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        clear.clicked.connect(self._clear)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)
        lay.addWidget(head)
        lay.addWidget(hint)
        lay.addWidget(self.shown)
        lay.addWidget(self.warn)
        lay.addWidget(buttons)
        self.setFocusPolicy(Qt.StrongFocus)

    def _clear(self):
        self.sequence = ""
        self.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Escape,) and not event.modifiers():
            self.reject()
            return
        if key in MODIFIER_KEYS or key == Qt.Key_unknown:
            return
        mods = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
        if not mods and not (Qt.Key_F1 <= key <= Qt.Key_F24):
            self.warn.setText("Mit Strg, Alt, Shift oder Win (F-Tasten auch allein)")
            return
        seq = QKeySequence(QKeyCombination(mods, Qt.Key(key))).toString(QKeySequence.PortableText)
        self.sequence = seq
        self.shown.setText(pretty(seq))
        self.warn.setText("")

    def exec(self):
        if manager is not None:
            manager.pause()
        try:
            return super().exec()
        finally:
            if manager is not None:
                manager.resume()


class HotkeyButton(QPushButton):
    """Zeigt das aktuelle Kürzel; Klick öffnet den Aufnahme-Dialog."""

    changed = Signal(str)

    def __init__(self, sequence: str = "", title: str = "Tastenkürzel", parent=None):
        super().__init__(parent)
        self.title = title
        self._seq = sequence or ""
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(180)
        self.clicked.connect(self._capture)
        self._update()

    def sequence(self) -> str:
        return self._seq


    def _update(self):
        self.setText(pretty(self._seq) + "   ✎")
        self.setToolTip("Klicken und neue Tastenkombination drücken")

    def _capture(self):
        dlg = CaptureDialog(self.title, self._seq, self.window())
        if dlg.exec() == QDialog.Accepted and dlg.sequence != self._seq:
            self._seq = dlg.sequence
            self._update()
            self.changed.emit(self._seq)
