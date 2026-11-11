"""Auswahl eines Tons: kein Ton, eingebaute Klänge, hochgeladene Dateien, „Datei hochladen …“."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QMessageBox, QWidget

from .. import sounds
from .widgets import button

UPLOAD = "__upload__"
AUDIO_FILTER = "Töne (*.wav *.mp3 *.ogg *.oga *.flac *.m4a *.aac *.opus *.wma)"


class SoundPicker(QWidget):
    changed = Signal(str)

    def __init__(self, player, spec: str = "", parent=None):
        super().__init__(parent)
        self.player = player
        self.combo = QComboBox()
        self.combo.setMinimumWidth(170)
        self.play_btn = button("", "play")
        self.play_btn.setToolTip("Probehören")
        self.play_btn.clicked.connect(lambda: self.player.play(self.spec()))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.combo, 1)
        lay.addWidget(self.play_btn)
        self._fill(spec)
        self.combo.activated.connect(self._chosen)

    def _fill(self, select: str):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("Kein Ton", "")
        for key, label in sounds.BUILTIN.items():
            self.combo.addItem("♪ " + label, f"builtin:{key}")
        for path in sounds.uploaded_files():
            self.combo.addItem("♫ " + path.name, str(path))
        if select and self.combo.findData(select) < 0:
            self.combo.addItem(sounds.describe(select), select)  # z. B. Datei außerhalb des Ordners
        self.combo.addItem("Eigene Datei hochladen …", UPLOAD)
        self.combo.setCurrentIndex(max(0, self.combo.findData(select)))
        self._current = select
        self.combo.blockSignals(False)
        self.play_btn.setEnabled(bool(select))

    def spec(self) -> str:
        return self._current


    def _chosen(self, _index):
        data = self.combo.currentData()
        if data == UPLOAD:
            path, _ = QFileDialog.getOpenFileName(self, "Ton hochladen", "", AUDIO_FILTER)
            if not path:
                self._fill(self._current)
                return
            try:
                data = sounds.upload(path)
            except OSError as exc:
                QMessageBox.warning(self, "Ton", f"Datei konnte nicht übernommen werden: {exc}")
                self._fill(self._current)
                return
        self._fill(data)
        self.changed.emit(data)
        if data:
            self.player.play(data)
