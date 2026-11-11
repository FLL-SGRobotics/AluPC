"""Jede Einstellung im Setup ECHT bedienen und prüfen, ob sie wirkt.

    python tests/settings_check.py [--liste]

Für jeden Bereich im Setup: jedes Häkchen, jede Auswahl, jedes Zahlenfeld, jeden Regler und jedes Textfeld
einmal ändern (wie per Maus/Tastatur) und prüfen:
  1. es gibt keinen Fehler (Ausnahme, Qt-Fehler),
  2. die Einstellung landet in der Konfiguration UND in der Datei (überlebt einen Neustart),
  3. zurückstellen stellt auch die Einstellung wieder zurück,
  4. für die wichtigsten Einstellungen: die Wirkung (Theme, Kürzel, Bildschirmschoner, Timer, Töne …).
Felder, die nichts speichern, werden aufgelistet (mit Grund, falls bekannt) – unbekannte sind Fehler.
Läuft ohne Bildschirm (Qt offscreen, zwei virtuelle Monitore).
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="alupc-settings-"))
# relativer Pfad: unter Windows würde der Doppelpunkt in „D:\…“ die Qt-Angabe zerschneiden
_cfg = os.path.relpath(ROOT / "tests" / "offscreen_two_screens.json").replace(os.sep, "/")
os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={_cfg}"
os.environ["XDG_CONFIG_HOME"] = str(TMP / "cfg")
os.environ["XDG_DATA_HOME"] = str(TMP / "data")
os.environ["APPDATA"] = str(TMP / "cfg")
os.environ["ALUPC_NO_AUTO_WIFI"] = "1"
os.environ["ALUPC_NO_ANIMATION"] = "1"
if hasattr(os, "geteuid") and os.geteuid() == 0:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401
from PySide6.QtCore import QCoreApplication, Qt, QtMsgType, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from PySide6.QtWidgets import (QAbstractButton, QAbstractSlider, QAbstractSpinBox, QApplication,  # noqa: E402
                               QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QLineEdit, QMessageBox, QRadioButton,
                               QSpinBox, QWidget)

app = QApplication([])
errors: list[str] = []
results: list[tuple[bool, str]] = []


def on_qt_message(kind, _ctx, msg):
    if (kind in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) or "Traceback" in msg) and "speechd" not in msg:
        errors.append(f"Qt: {msg}")


qInstallMessageHandler(on_qt_message)
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb))[-1500:])
# Rückfragen/Fehlerfenster nicht blockieren lassen – aber merken
dialogs: list[str] = []
QDialog.exec = lambda self: (dialogs.append(self.windowTitle()), self.reject(), 0)[2]
for name in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: (dialogs.append(f"{_n}: {a[2] if len(a) > 2 else ''}"),
                                                                        QMessageBox.No)[1]))


from PySide6.QtWidgets import QColorDialog, QFileDialog, QInputDialog  # noqa: E402

# Datei-/Farb-/Eingabe-Fenster: im Test „abgebrochen“ (sonst würden sie blockieren)
for _n in ("getOpenFileName", "getSaveFileName", "getOpenFileNames"):
    setattr(QFileDialog, _n, staticmethod(lambda *a, _n=_n, **k: (dialogs.append(_n), ("", ""))[1]))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: (dialogs.append("Ordner"), "")[1])
QColorDialog.getColor = staticmethod(lambda *a, **k: (dialogs.append("Farbe"), QColor())[1])
for _n in ("getText", "getInt", "getItem", "getDouble", "getMultiLineText"):
    setattr(QInputDialog, _n, staticmethod(lambda *a, _n=_n, **k: (dialogs.append(_n), ("", False))[1]))


def ok(cond, text: str) -> bool:
    results.append((bool(cond), text))
    print(("  ✓ " if cond else "  ✗ ") + text, flush=True)
    return bool(cond)


def pump(seconds: float = 0.15) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


# Felder, die bewusst nichts in der Konfiguration ändern (Eingaben für Knöpfe, Filter, Tests …)
NO_CONFIG = {
    "Monitore": "Auflösung/Anordnung gelten erst mit „Übernehmen“ (ändern das System, nicht AluPC)",
    "RGB & Lüfter": "steuert Geräte direkt (ohne OpenRGB-Server hier nicht prüfbar)",
}
# Bekannte, gewollte Nebenwirkungen beim Zurückstellen (Abschnitt, Feldtext, Schlüssel) → Grund
EXPECTED_BACK = {
    ("Bildschirmschoner", "Automatisch starten", "screensaver.scene"):
        "die angezeigte Szene wird mitgespeichert (vorher leer = erste Szene)",
    ("Sichern & Sync", "„An“", "sync.declined"): "„Aus“ geklickt = bewusst abgelehnt (vorher: nie gefragt)",
}
NO_CONFIG.update({"Darstellung: Swatch „AluPC“": "ist schon gewählt",
                  "Darstellung: StyleCard „Mitternacht: Tintenblau und Türkis“": "Stil Regie ist schon gewählt",
                  "Darstellung: Swatch „Eigene Farbe“": "noch keine eigene Farbe → öffnet erst den Farbdialog",
                  "Töne: QComboBox": "„Eigener Ton …“ öffnet die Dateiauswahl (hier abgebrochen)",
                  "Allgemein: QCheckBox „Autostart“": "Autostart steht im System, nicht in der Konfiguration "
                                                      "(Wirkung unten geprüft)"})
# Felder, die hier bewusst nicht bedient werden (würden echte Aktionen am System auslösen)
SKIP_NAMES = {"voice_on"}


def describe(w: QWidget) -> str:
    label = ""
    if isinstance(w, QAbstractButton):
        label = w.text() or w.toolTip()
    if not label:
        label = w.toolTip() or w.objectName() or w.accessibleName()
    parent = w.parentWidget()
    while not label and parent is not None:
        title = getattr(parent, "title", None)
        label = title() if callable(title) else ""
        parent = parent.parentWidget()
    return f"{type(w).__name__} „{(label or '?').splitlines()[0][:60]}“"


def flat(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flat(v, f"{prefix}{k}."))
    else:
        out[prefix[:-1]] = json.dumps(d, sort_keys=True, ensure_ascii=False)
    return out


def diff(a: dict, b: dict) -> list[str]:
    fa, fb = flat(a), flat(b)
    return sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))


def change(w: QWidget):
    """Wert ändern wie ein Mensch; liefert eine Funktion zum Zurückstellen (oder None, wenn nicht möglich)."""
    if isinstance(w, (QCheckBox, QRadioButton)) or (isinstance(w, QAbstractButton) and w.isCheckable()):
        if isinstance(w, QRadioButton) and w.isChecked():
            return None
        group = [b for b in w.parentWidget().findChildren(QAbstractButton) if b.isCheckable() and type(b) is type(w)] \
            if w.parentWidget() is not None else [w]
        was = next((b for b in group if b.isChecked() and b is not w), None)
        before = w.isChecked()
        w.click()

        def undo():  # wie ein Mensch: wieder klicken (bzw. die vorher gewählte Farbe)
            if was is not None and not was.isChecked():
                was.click()
            elif w.isChecked() != before:
                w.click()
        return undo
    if isinstance(w, QComboBox):
        if w.count() < 2:
            return None
        before = w.currentIndex()
        w.setCurrentIndex((before + 1) % w.count())
        w.activated.emit(w.currentIndex())
        return lambda: (w.setCurrentIndex(before), w.activated.emit(before))
    if isinstance(w, (QSpinBox, QDoubleSpinBox)):
        before = w.value()
        new = before + w.singleStep() if before + w.singleStep() <= w.maximum() else before - w.singleStep()
        if new == before:
            return None
        w.setValue(new)
        w.editingFinished.emit()
        return lambda: (w.setValue(before), w.editingFinished.emit())
    if isinstance(w, QAbstractSlider):
        before = w.value()
        step = max(1, w.singleStep())
        new = before + step if before + step <= w.maximum() else before - step
        w.setValue(new)
        w.sliderReleased.emit()
        return lambda: (w.setValue(before), w.sliderReleased.emit())
    if isinstance(w, QLineEdit) and not w.isReadOnly():
        before = w.text()
        w.setText((before + "x") if before else "Test 1")
        w.editingFinished.emit()
        w.returnPressed.emit()
        return lambda: (w.setText(before), w.editingFinished.emit(), w.returnPressed.emit())
    return None


# ---- App mit Beispiel-Daten
from alupc.config import Config  # noqa: E402
from alupc.controller import Controller  # noqa: E402
from alupc.hotkeys import HotkeyManager  # noqa: E402
from alupc.ui import theme  # noqa: E402
from alupc.ui.main_window import MainWindow  # noqa: E402
from alupc.ui.setup_page import SetupPage  # noqa: E402

cfg_path = TMP / "cfg" / "AluPC" / "config.json"
config = Config(cfg_path)
config["handy"] = {**config["handy"], "setup_done": True}
config["first_run_done"] = True
config.put_scene({"name": "Begrüßung", "layout": "vollbild", "background": "#000000",
                  "slots": [{"type": "design", "design": "willkommen"}]})
controller = Controller(config)
controller.display.available = lambda: False
hotkeys = HotkeyManager()
window = MainWindow(controller, hotkeys)
hotkeys.attach(window)
hotkeys.triggered.connect(controller.run_command)
window.resize(1400, 900)
window.show()
pump(0.8)
window._go(2)
setup = window.findChild(SetupPage)
setup.build_all()
pump(0.5)

LIST = "--liste" in sys.argv
untouched: list[str] = []
print("Jedes Feld im Setup ändern → gespeichert? → zurück?")
for i, (_icon, section, _sub) in enumerate(SetupPage.SECTIONS):
    setup.nav.setCurrentRow(i)
    pump(0.2)
    area = setup._areas[i].widget()
    widgets = [w for w in area.findChildren(QWidget)
               if (isinstance(w, QAbstractButton) and w.isCheckable())
               or isinstance(w, (QComboBox, QSpinBox, QDoubleSpinBox, QAbstractSlider, QLineEdit))]
    widgets = [w for w in widgets if w.isVisibleTo(area) and w.isEnabled()
               and not (isinstance(w, QLineEdit) and isinstance(w.parentWidget(), (QComboBox, QAbstractSpinBox)))]
    saved = changed = 0
    for w in widgets:
        name = describe(w)
        if any(getattr(setup, a, None) is w for a in SKIP_NAMES):
            continue
        before = copy.deepcopy(config.data)
        n_err = len(errors)
        try:
            undo = change(w)
        except Exception:  # noqa: BLE001
            errors.append(f"{section} {name}: {traceback.format_exc()[-800:]}")
            continue
        if undo is None:
            continue
        pump(0.12)
        keys = diff(before, config.data)
        on_disk = diff(json.loads(cfg_path.read_text(encoding="utf-8")), config.data) if keys else []
        if len(errors) > n_err:
            ok(False, f"{section}: {name} → Fehler: {errors[-1].splitlines()[-1][:160]}")
        if keys:
            changed += 1
            if on_disk:
                ok(False, f"{section}: {name} → nicht in der Datei gespeichert ({', '.join(on_disk[:3])})")
            else:
                saved += 1
            if LIST:
                print(f"     {section}: {name} → {', '.join(keys[:4])}")
        else:
            untouched.append(f"{section}: {name}")
        try:
            undo()
        except Exception:  # noqa: BLE001
            errors.append(f"{section} {name} (zurück): {traceback.format_exc()[-800:]}")
        pump(0.12)
        known = flat(before)  # neu hinzugekommene Schlüssel mit Standardwert sind kein Fehler
        back = [k for k in diff(before, config.data) if k in known
                and not k.startswith(("sync.history", "stats", "window"))]
        back = [k for k in back if not any(section == a and b in name and k == c for a, b, c in EXPECTED_BACK)]
        if keys and back:
            ok(False, f"{section}: {name} → zurückstellen stellt nicht zurück ({', '.join(back[:3])})")
    ok(saved == changed, f"{section}: {changed} Einstellungen geändert und gespeichert")

print("\nFelder ohne Wirkung auf die Konfiguration")
for u in untouched:
    section = u.split(":")[0]
    reason = next((r for k, r in NO_CONFIG.items() if u.startswith(k)), "")
    print(f"  {'·' if reason else '?'} {u}" + (f" – {reason}" if reason else ""))

# ---- Wirkung der wichtigsten Einstellungen
print("\nWirkung")
setup.nav.setCurrentRow([t for _i, t, _s in SetupPage.SECTIONS].index("Darstellung"))
pump(0.2)
setup._set_accent("gruen")
pump(0.2)
ok(theme.current().accent == theme.ACCENTS["gruen"][1], "Akzentfarbe Grün → App sofort grün")
setup._set_accent("alupc")
pump(0.2)
ok(theme.current().accent == "#2dd4bf", "AluPC-Theme (Regie) → Türkis")
setup._save_appearance(style="normal")
pump(0.2)
ok(theme.current().accent in ("#4f5bd5", "#7c8cff"), "AluPC-Theme (Normal) → Indigo")
setup._save_appearance(style="technisch")
pump(0.2)
ok(theme.current().technical, "Stil Technisch → App technisch")
setup._save_appearance(style="holz", wood="eiche")
pump(0.2)
ok(theme.current().wood and theme.current().wood_kind == "eiche" and not theme.current().dark,
   "Stil Holz + Eiche → helles Holz, dunkle Schrift")
ok(not setup._mode_combo.isEnabled(), "Holz: Hell/Dunkel bestimmt die Holzart (Auswahl gesperrt)")
setup._save_appearance(style="normal", wood="nussbaum")
pump(0.2)
ok(theme.current().style == "normal" and setup._mode_combo.isEnabled(), "Zurück zu Normal")
mode_box = next(c for c in setup._areas[2].widget().findChildren(QComboBox) if c.findData("dunkel") >= 0)
mode_box.setCurrentIndex(mode_box.findData("dunkel"))
pump(0.2)
ok(theme.current().dark and config["appearance"]["mode"] == "dunkel", "Design Dunkel → App dunkel")
mode_box.setCurrentIndex(mode_box.findData("hell"))
pump(0.2)
ok(not theme.current().dark, "Design Hell → App hell")

from alupc.config import HOTKEY_LABELS  # noqa: E402

action = next(iter(HOTKEY_LABELS))
setup._save_hotkey(action, "Ctrl+Alt+F7")
pump(0.2)
in_app = [sc for sc in hotkeys.shortcuts if sc.key().toString() == "Ctrl+Alt+F7"]
system = [i for i, a in hotkeys.win_ids.items() if a == action]  # Windows: systemweit (RegisterHotKey)
ok(config["hotkeys"][action] == "Ctrl+Alt+F7" and (in_app or system or action in hotkeys.kde_ids),
   f"Tastenkürzel „{HOTKEY_LABELS[action]}“ = Strg+Alt+F7 → aktiv "
   f"({'systemweit' if system or action in hotkeys.kde_ids else 'in AluPC'})")
got = []
hotkeys.triggered.connect(got.append)
for sc in in_app:
    sc.activated.emit()
for i in system:  # wie WM_HOTKEY von Windows
    hotkeys._on_win_hotkey(i)
pump(0.2)
ok(action in got, f"Kürzel löst „{action}“ aus")

# Autostart: steht danach wirklich im System (Linux: ~/.config/autostart, Windows: Registry „Run“)
from alupc.platform import autostart  # noqa: E402

box = next(b for b in setup._areas[-1].widget().findChildren(QCheckBox) if b.text() == "Autostart")
was = autostart.is_enabled()
box.click()
pump(0.3)
now_on = autostart.is_enabled()
box.click()
pump(0.3)
ok(now_on != was and autostart.is_enabled() == was, f"Autostart an/aus → im System {'an' if now_on else 'aus'}, "
   f"danach wieder wie vorher (vorher {'an' if was else 'aus'}, Feld {'aktiv' if box.isEnabled() else 'gesperrt'}"
   f"{', Meldungen: ' + ' | '.join(dialogs[-2:]) if dialogs else ''})")

# Im System (Rechtsklick + alupc://-Links): steht danach wirklich im System (Linux: KDE-Dienstmenü, Windows: HKCU)
from alupc.platform import system_integration  # noqa: E402

box = next(b for b in setup._areas[-1].widget().findChildren(QCheckBox) if b.text().startswith("Ins System"))
box.setChecked(True)
system_integration.set_enabled(True)
box.click()
pump(0.3)
off = system_integration.is_active()
box.click()
pump(0.3)
ok(not off and system_integration.is_active() and config["system_integration"],
   "Im System aus/an → Rechtsklick-Eintrag + alupc://-Link weg und wieder da")
system_integration.set_enabled(False)

# Neustart: alles noch da?
reloaded = Config(cfg_path)
ok(not diff(reloaded.data, config.data) or diff(reloaded.data, config.data) == [],
   "Neustart (Datei neu laden): alle Einstellungen gleich")

window.close()
controller.shutdown()
pump(0.3)
for e in errors:
    ok(False, "Fehler: " + e.strip().splitlines()[-1][:200])
unknown = [u for u in untouched if not any(u.startswith(k) for k in NO_CONFIG)]
for u in unknown:
    ok(False, f"Feld ohne Wirkung (ungeklärt): {u}")
failed = [t for good, t in results if not good]
print(f"\n{len(results) - len(failed)}/{len(results)} Prüfungen bestanden · {len(unknown)} Felder ohne Wirkung "
      f"(ungeklärt) · {len(dialogs)} Rückfragen/Hinweise")
sys.exit(1 if failed else 0)
