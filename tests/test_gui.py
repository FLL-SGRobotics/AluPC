"""Oberflächen-Test mit zwei virtuellen Monitoren (Qt „offscreen“), ohne echte Hardware."""

import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _qpa_path(path: Path) -> str:
    """Pfad ohne Doppelpunkt: Qt trennt Plattform-Optionen am „:“ (Windows: „D:\\…“)."""
    try:
        rel = os.path.relpath(path)
    except ValueError:  # anderes Laufwerk
        rel = str(path)
    rel = rel.replace("\\", "/")
    if ":" in rel:
        rel = rel.split(":", 1)[1]  # Laufwerksbuchstabe weg → gilt für das aktuelle Laufwerk
    return rel


os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={_qpa_path(HERE / 'offscreen_two_screens.json')}"

pytest.importorskip("PySide6.QtWidgets")

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401  (vor QApplication laden)
from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
APP = QApplication.instance() or QApplication([])


def pump(n=5):
    for _ in range(n):
        APP.processEvents()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from alupc.config import Config
    from alupc.controller import Controller
    from alupc.hotkeys import HotkeyManager
    from alupc.ui.main_window import MainWindow
    from alupc.ui.pip_window import PipWindow

    before = set(map(id, APP.topLevelWidgets()))
    config = Config(tmp_path / "config.json")
    config["handy"] = {**config["handy"], "setup_done": True}  # im Test nichts installieren
    controller = Controller(config)
    # Keine echten Monitor-Befehle im Test ausführen
    controller.display.available = lambda: False
    hotkeys = HotkeyManager()
    window = MainWindow(controller, hotkeys)
    hotkeys.attach(window)
    hotkeys.triggered.connect(controller.run_command)
    hotkeys.apply(config["hotkeys"])
    controller.pip = PipWindow(controller)
    window.show()
    pump()
    yield controller, window, tmp_path
    controller.pip.hide()
    presenter = getattr(window, "presenter", None)
    if presenter is not None:  # „Zeigen & Zeichnen“ nimmt Monitor 2 live auf – beim Aufräumen stoppen
        presenter.hide()
    controller.shutdown()
    window.tray.hide()
    window._sys_timer.stop()  # sonst färbt das alte (unsichtbare) Fenster die nächsten Tests um
    window.hide()
    window.deleteLater()
    # Alle Fenster dieses Tests wirklich löschen (Monitor-2-Fenster, Bild-in-Bild, Dialoge …): vorher blieben je
    # Test ~14 übrig – nach ~100 Tests brach Qt unter Windows ab („Fatal Python error: Aborted“ in pump)
    for widget in APP.topLevelWidgets():
        if id(widget) not in before:
            widget.hide()
            widget.deleteLater()
    pump()
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    pump()


def test_screens(env):
    controller, _window, _ = env
    assert controller.output_screen().name() == "Zweit"
    assert controller.main_screen().name() == "Haupt"
    assert not controller.screens_overlap()


def test_simple_sources_and_freeze(env, tmp_path):
    controller, window, _ = env
    img = QImage(64, 36, QImage.Format_RGB32)
    img.fill(QColor("#ff0000"))
    img_path = str(tmp_path / "rot.png")
    img.save(img_path)
    for cfg in [
        {"type": "text", "text": "Hallo"},
        {"type": "clock"},
        {"type": "countdown", "minutes": 1},
        {"type": "color", "color": "#00ff00"},
        {"type": "image", "path": img_path},
        {"type": "slideshow", "folder": str(tmp_path)},
    ]:
        controller.show_source(cfg)
        pump()
        assert controller.mode == "content"
        assert controller.output.isVisible()
        assert controller.output.geometry().topLeft().x() == 1920  # auf Monitor 2

    controller.show_source({"type": "color", "color": "#00ff00"})
    pump()
    controller.toggle_freeze()
    assert controller.frozen and controller.output.freeze_layer.isVisible()
    frozen = controller.output.freeze_layer.image()
    assert frozen is not None and QColor(frozen.pixel(10, 10)) == QColor("#00ff00")
    # Inhalt wechselt → Standbild bleibt bis zum Ausschalten nicht hängen
    controller.toggle_freeze()
    assert not controller.frozen and not controller.output.freeze_layer.isVisible()
    assert "Farbe" in window.status_card.title.text()


def test_privacy_and_pip(env):
    controller, window, _ = env
    controller.show_source({"type": "text", "text": "Geheim"})
    controller.toggle_privacy()
    pump()
    assert controller.privacy and controller.output.privacy_layer.isVisible()
    assert "SCHWARZ" in window.status_card.pill_texts()
    controller.toggle_pip()
    pump()
    assert controller.pip.isVisible()
    controller.pip.refresh()
    assert controller.pip.view.image() is not None
    controller.toggle_privacy()
    controller.toggle_pip()
    assert not controller.pip.isVisible()


def test_extend_hides_output(env):
    controller, _window, _ = env
    controller.show_source({"type": "text", "text": "x"})
    controller.extend()
    pump()
    assert controller.mode == "desktop"
    assert not controller.output.isVisible()


def test_scene_with_nested_scene(env):
    controller, _window, _ = env
    cfg = controller.config
    cfg.put_scene({"name": "Innen", "layout": "nebeneinander", "background": "#000000",
                   "slots": [{"type": "text", "text": "L"}, {"type": "clock"}]})
    cfg.put_scene({"name": "Außen", "layout": "ecke_unten_rechts", "background": "#101010",
                   "slots": [{"type": "color", "color": "#0000ff"}, {"type": "scene", "scene": "Innen"}]})
    controller.run_command("szene:Außen")
    pump()
    content = controller.output.content
    assert type(content).__name__ == "SceneSource"
    assert len(content.children_sources) == 2
    inner = content.children_sources[1][1]
    assert type(inner).__name__ == "SceneSource"
    # Kleines Feld unten rechts: 26 % von 1280 px Breite
    assert abs(inner.width() - round(0.26 * 1280)) <= 1


def test_scene_cycle_is_limited(env):
    controller, _window, _ = env
    cfg = controller.config
    cfg.put_scene({"name": "A", "layout": "vollbild", "slots": [{"type": "scene", "scene": "A"}]})
    controller.show_source({"type": "scene", "scene": "A"})  # darf nicht endlos rekursiv werden
    pump()
    assert controller.output.content is not None


def test_missing_things_do_not_crash(env):
    controller, _window, _ = env
    for cfg in [
        {"type": "image", "path": "/gibt/es/nicht.png"},
        {"type": "camera", "device_id": "nicht-da", "name": "Weg"},
        {"type": "scene", "scene": "Unbekannt"},
        {"type": "unsinn"},
    ]:
        controller.show_source(cfg)
        pump()
        assert controller.output.content is not None


def test_commands_and_computer_lock(env, monkeypatch):
    controller, _window, _ = env
    controller.run_command("standbild")
    pump()
    assert controller.frozen
    calls = []
    import alupc.platform.window_tools as wt

    monkeypatch.setattr(wt, "lock_computer", lambda: calls.append(1))
    controller.run_command("sperren")
    assert calls == [1]  # „Sperren“ sperrt jetzt den Computer (wie Win+L)
    controller.run_command("schwarz")
    assert controller.privacy  # AluPC selbst bleibt bedienbar
    controller.run_command("schwarz")


def test_dialogs_build(env):
    controller, window, _ = env
    from alupc.ui.program_dialog import ProgramDialog
    from alupc.ui.scene_editor import SceneEditor
    from alupc.ui.source_picker import SOURCE_TYPES, SourcePicker

    picker = SourcePicker(controller.config, window)
    for key, _label in SOURCE_TYPES:
        picker.select_type(key)
        assert picker.current_type() == key
        picker.result_config()  # darf nicht abstürzen, auch wenn Felder leer sind
    picker.select_type("text")
    page = picker.stack.currentWidget()
    from PySide6.QtWidgets import QPlainTextEdit

    page.findChild(QPlainTextEdit).setPlainText("Willkommen")
    assert picker.result_config()["text"] == "Willkommen"

    editor = SceneEditor(controller.config, None, window)
    editor.layout_list.setCurrentRow(6)  # 2 × 2 Raster
    assert len(editor.scene["slots"]) == 4
    editor.scene["slots"][0] = {"type": "text", "text": "x"}
    editor._save()
    assert controller.config.get_scene(editor.scene["name"]) is not None

    dialog = ProgramDialog(controller, window)
    from PySide6.QtWidgets import QPushButton, QTabWidget

    # „Verschieben“ gibt es im Programm-Dialog nicht mehr – nur noch Anzeigen (Kopie auf Monitor 2)
    assert not dialog.findChildren(QTabWidget) and not hasattr(dialog, "move_list")
    assert not [b for b in dialog.findChildren(QPushButton) if "Verschieben" in b.text() or "Anklicken" in b.text()]
    for i in range(window.stack.count()):
        window._go(i)
        pump()
    # Szenen-Seite zeigt eine Karte pro Szene
    window._reload_scenes()
    assert len(window.scene_cards) == len(controller.config["scenes"])


def test_theme_switch(env):
    controller, window, _ = env
    from alupc.ui import theme

    for mode, accent in (("hell", "violett"), ("dunkel", "gruen"), ("dunkel", "blau")):
        controller.config["appearance"] = {"mode": mode, "accent": accent, "fade": True}
        window.apply_theme()
        pump()
        assert theme.current().dark == (mode == "dunkel")
        assert theme.current().accent == theme.ACCENTS[accent][1]


def test_crossfade_layer_is_removed(env):
    controller, _window, _ = env
    controller.show_source({"type": "color", "color": "#ff0000"})
    pump()
    controller.show_source({"type": "color", "color": "#0000ff"})
    assert len(controller.output._fades) == 1
    import time

    end = time.time() + 2
    while controller.output._fades and time.time() < end:
        pump()
    assert controller.output._fades == []


def test_website_source(env):
    controller, _window, _ = env
    controller.show_source({"type": "website", "url": "about:blank"})
    pump()
    assert type(controller.output.content).__name__ == "WebsiteSource"


def test_screensaver_styles_and_layers(env, tmp_path):
    controller, window, _ = env
    img = QImage(40, 30, QImage.Format_RGB32)
    img.fill(QColor("#ff8800"))
    img.save(str(tmp_path / "a.png"))
    controller.config.put_scene({"name": "S", "layout": "vollbild", "slots": [{"type": "clock"}]})
    for style in ("uhr", "schweben", "diashow", "farben", "szene"):
        cfg = dict(controller.config["screensaver"])
        cfg.update({"style": style, "folder": str(tmp_path), "scene": "S", "text": "Hallo"})
        controller.config["screensaver"] = cfg
        controller.toggle_screensaver()
        pump()
        assert controller.screensaver.active
        assert controller.output.isVisible() and controller.output.screensaver is not None
        controller.output.screensaver.grab()  # Zeichnen darf nicht abstürzen
        controller.toggle_screensaver()
        pump()
        assert not controller.screensaver.active and controller.output.screensaver is None
    # Sichtschutz liegt immer über dem Bildschirmschoner
    controller.toggle_screensaver()
    controller.toggle_privacy()
    kids = controller.output.children()
    assert kids.index(controller.output.privacy_layer) > kids.index(controller.output.screensaver)
    controller.toggle_privacy()
    # Etwas anzeigen beendet den Bildschirmschoner
    controller.show_source({"type": "clock"})
    assert not controller.screensaver.active


def test_screensaver_auto_start_and_stop(env):
    controller, _window, _ = env
    saver = controller.screensaver
    controller.extend()
    controller.config["screensaver"] = {**controller.config["screensaver"], "enabled": True, "minutes": 1}
    saver.idle_seconds = lambda: 120  # niemand hat seit 2 Minuten etwas gemacht
    saver.check()
    assert saver.active and not saver.manual
    saver.idle_seconds = lambda: 0.5  # Maus bewegt
    saver.check()
    assert not saver.active
    # Modus „nur wenn nichts gezeigt wird“: bei laufendem Inhalt kein Start
    controller.show_source({"type": "clock"})
    saver.idle_seconds = lambda: 999
    saver.check()
    assert not saver.active


def test_custom_tile_and_scene_steps(env):
    controller, window, _ = env
    cfg = controller.config
    cfg.put_scene({"name": "Eins", "layout": "vollbild", "slots": [{"type": "clock"}]})
    cfg.put_scene({"name": "Zwei", "layout": "vollbild", "slots": [{"type": "text", "text": "2"}]})
    controller.run_command("naechste_szene")
    assert controller.content == {"type": "scene", "scene": "Eins"}
    controller.run_command("naechste_szene")
    assert controller.content["scene"] == "Zwei"
    controller.run_command("vorherige_szene")
    assert controller.content["scene"] == "Eins"

    start = dict(cfg["start_page"])
    start["custom"] = [
        {"id": "t1", "title": "Uhr", "icon": "clock", "color": "#10b981", "section": "anzeigen",
         "action": {"kind": "source", "source": {"type": "clock"}}},
        {"id": "t2", "title": "Freeze", "icon": "snowflake", "color": "#0ea5e9", "section": "schnell",
         "action": {"kind": "command", "command": "standbild"}},
    ]
    start["tiles"] = ["camera", "custom:t1", "freeze", "custom:t2"]
    from alupc.startpage import BUILTIN_TILES

    start["seen"] = list(BUILTIN_TILES)  # wie beim Speichern im Dialog: alle anderen bewusst ausgeblendet
    cfg["start_page"] = start
    window.rebuild_start()
    pump()
    assert window.section_grids["anzeigen"].items == [window.tiles["camera"], window.custom_tiles["custom:t1"]]
    assert window.section_grids["schnell"].items == [window.tiles["freeze"], window.custom_tiles["custom:t2"]]
    assert window.tiles["mirror"].isHidden()
    window.custom_tiles["custom:t1"].click()
    assert controller.content == {"type": "clock"}
    assert window.custom_tiles["custom:t1"].active
    controller.run_command("kachel:t2")
    assert controller.frozen
    controller.run_command("kachel:t2")
    assert not controller.frozen


def test_several_screensaver_tiles(env):
    """Mehrere Bildschirmschoner als eigene Kacheln auf die Startseite – jeder mit eigenem Stil."""
    controller, window, _ = env
    from alupc.ui.start_page_dialog import StartPageDialog

    dlg = StartPageDialog(controller.config, window, controller)
    dlg.add_saver("aurora")
    dlg.add_saver("matrix")
    dlg.add_saver("flipuhr")
    dlg._save()
    tiles = [t for t in controller.config["start_page"]["custom"]
             if (t.get("action") or {}).get("kind") == "screensaver"]
    assert [t["action"]["screensaver"]["style"] for t in tiles[-3:]] == ["aurora", "matrix", "flipuhr"]
    for t in tiles[-3:]:
        assert f"custom:{t['id']}" in controller.config["start_page"]["tiles"]  # sichtbar
    window.rebuild_start()
    assert all(f"custom:{t['id']}" in window.custom_tiles for t in tiles[-3:])
    if controller.output_screen() is not None:
        controller.run_tile(tiles[-3]["id"])
        assert controller.screensaver.active and controller.screensaver.override_id == tiles[-3]["id"]
        controller.run_tile(tiles[-2]["id"])  # anderer Schoner → wechselt statt zu beenden
        assert controller.screensaver.active and controller.screensaver.override_id == tiles[-2]["id"]
        controller.run_tile(tiles[-2]["id"])  # gleicher nochmal → aus
        assert not controller.screensaver.active


def test_custom_start_sections(env):
    """Eigene Bereiche: anlegen, Standard- und eigene Kacheln verschieben, umbenennen, sortieren, löschen,
    einklappen (bleibt gespeichert)."""
    from alupc.startpage import delete_section, section_of, sections
    from alupc.ui.start_page_dialog import StartPageDialog

    controller, window, _ = env
    dlg = StartPageDialog(controller.config, window, controller)
    party = dlg.add_section("Party")
    dlg.move_tile("camera", party)
    dlg.move_tile("airplay", party)
    dlg.add_saver("aurora")
    saver_key = next(k for k in dlg._current_keys()[0] if k.startswith("custom:"))
    dlg.move_tile(saver_key, party)
    dlg.rename_section(party, "Party & Gäste")
    dlg.sec_list.setCurrentRow(len(sections(dlg.cfg)) - 1)
    dlg._move_section(-1)  # eins nach oben
    dlg._save()
    cfg = controller.config["start_page"]
    names = [s["name"] for s in sections(cfg)]
    assert "Party & Gäste" in names and names.index("Party & Gäste") == len(names) - 2
    assert section_of("camera", cfg) == party and section_of("airplay", cfg) == party
    assert section_of(saver_key, cfg) == party
    window.rebuild_start()
    pump()
    grid = window.section_grids[party]
    assert window.tiles["camera"] in grid.items and window.tiles["airplay"] in grid.items
    assert window.tiles["camera"] not in window.section_grids["anzeigen"].items
    head = window.section_labels[party]
    assert head.name == "Party & Gäste" and head.count == len(grid.items)
    # einklappen: Kacheln weg, Zustand gespeichert und nach Neuaufbau noch da
    window._fold_section(party, True)
    assert not window.tiles["camera"].isVisibleTo(window)
    window.rebuild_start()
    assert window.section_labels[party].collapsed and not window.section_grids[party].isVisibleTo(window)
    window._fold_section(party, False)
    window.rebuild_start()
    pump()
    assert window.tiles["camera"].isVisibleTo(window)
    # Bereich löschen → Kacheln landen im ersten Bereich, nichts geht verloren
    start = dict(controller.config["start_page"])
    delete_section(start, party)
    controller.config["start_page"] = start
    window.rebuild_start()
    first = sections(start)[0]["id"]
    assert window.tiles["camera"] in window.section_grids[first].items
    assert party not in window.section_grids


def test_start_page_dialog_roundtrip(env):
    controller, window, _ = env
    from alupc.ui.start_page_dialog import StartPageDialog
    from PySide6.QtCore import Qt as _Qt

    dlg = StartPageDialog(controller.config, window)
    dlg.title.setText("Mein Start")
    dlg.list.item(0).setCheckState(_Qt.Unchecked)  # erste Kachel (Spiegeln) ausblenden
    dlg.list.setCurrentRow(1)
    dlg._move(-1)  # Erweitern nach oben
    dlg._save()
    start = controller.config["start_page"]
    assert start["title"] == "Mein Start"
    assert start["tiles"][0] == "extend" and "mirror" not in start["tiles"]
    window.rebuild_start()
    assert window.start_title.text() == "Mein Start"


def test_duplicate_hotkeys_are_reported(env):
    controller, window, _ = env
    problems = window.hotkeys.apply({"standbild": "Ctrl+Alt+S", "schwarz": "Ctrl+Alt+S"})
    assert any("doppelt" in p for p in problems)
    window.hotkeys.apply(controller.config["hotkeys"])


def test_windows_hotkey_mapping():
    from alupc.hotkeys import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, to_windows_hotkey

    assert to_windows_hotkey("Ctrl+Alt+S") == (MOD_NOREPEAT | MOD_CONTROL | MOD_ALT, ord("S"))
    assert to_windows_hotkey("Ctrl+Alt+PgDown") == (MOD_NOREPEAT | MOD_CONTROL | MOD_ALT, 0x22)
    assert to_windows_hotkey("Shift+F5") == (MOD_NOREPEAT | MOD_SHIFT, 0x74)
    assert to_windows_hotkey("Ctrl+Alt+1")[1] == ord("1")
    assert to_windows_hotkey("") is None


def test_timer_keeps_running_when_view_is_rebuilt(env):
    controller, window, _ = env
    from alupc.timer import clock

    clock.set(120)
    controller.config.put_scene({"name": "T", "layout": "vollbild",
                                 "slots": [{"type": "countdown", "minutes": 2}]})
    controller.run_command("szene:T")  # startet den Timer
    assert clock.running
    clock._accumulated = 30  # 30 s sind schon vergangen
    controller.run_command("szene:T")  # Szene neu aufbauen …
    assert clock.running and clock.remaining() <= 90.5  # … Timer läuft weiter statt neu zu beginnen
    controller.timer_action("toggle")
    assert not clock.running
    rem = clock.remaining()
    controller.show_timer()  # Timer-Kachel zeigt den pausierten Timer, ohne ihn zurückzusetzen
    assert abs(clock.remaining() - rem) < 0.5 and not clock.running
    assert controller.timer_visible()
    controller.timer_action("plus")
    assert clock.remaining() > rem + 59
    controller.timer_action("restart")
    assert clock.running and clock.remaining() > 170
    window._update_timer_ui()
    assert window.t_timer.badge.startswith("0")
    clock.reset()


def test_timer_clock_logic():
    from alupc.timer import TimerClock, format_time

    t = TimerClock()
    t.set(65)
    assert t.text() == "01:05" and t.fresh()
    t._accumulated = 64.2
    assert t.text() == "00:01" and t.urgency() == "gleich"
    t._accumulated = 70
    assert t.finished() and t.text() == "Zeit ist um!" and t.urgency() == "ende"
    t.toggle()  # abgelaufen + Start → beginnt neu
    assert t.running and not t.finished()
    t.set(30, "stoppuhr")
    t._accumulated = 75
    assert t.text() == "01:15" and not t.finished()
    assert format_time(3725) == "1:02:05"


def test_freeze_badge_and_watchdog(env):
    controller, _window, _ = env
    controller.show_source({"type": "color", "color": "#00ff00"})
    pump()
    controller.toggle_freeze()
    img = controller.output.freeze_layer.grab().toImage()
    w = img.width()
    # oben rechts ist das hellblaue Standbild-Symbol, links oben die grüne Fläche
    corner = QColor(img.pixel(w - 26, 26))
    assert corner.blue() > 150 and corner != QColor("#00ff00")
    assert QColor(img.pixel(20, 20)) == QColor("#00ff00")
    controller.config["output"] = {"hide_taskbar": True, "freeze_badge": False}
    controller.apply_output_settings()
    img = controller.output.freeze_layer.grab().toImage()
    assert QColor(img.pixel(w - 26, 26)) == QColor("#00ff00")
    controller.toggle_freeze()
    # Wächter: versteckt jemand das Fenster (z. B. Win+D), kommt es zurück
    controller.output.hide()
    controller.output._watch()
    assert controller.output.isVisible()


def test_hotkey_capture_dialog(env):
    controller, window, _ = env
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from alupc.ui.hotkey_edit import CaptureDialog, pretty

    dlg = CaptureDialog("Test", "")
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_K, Qt.ControlModifier | Qt.AltModifier)
    dlg.keyPressEvent(ev)
    assert dlg.sequence == "Ctrl+Alt+K"
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_K, Qt.NoModifier)
    dlg.keyPressEvent(ev)
    assert dlg.sequence == "Ctrl+Alt+K" and dlg.warn.text()  # ohne Zusatztaste nicht erlaubt
    assert pretty("Ctrl+Alt+PgDown") == "Strg+Alt+Bild↓"
    # Pausieren schaltet alle Kürzel ab und wieder an
    window.hotkeys.pause()
    assert all(not sc.isEnabled() for sc in window.hotkeys.shortcuts)
    window.hotkeys.resume()
    assert window.hotkeys.shortcuts and all(sc.isEnabled() for sc in window.hotkeys.shortcuts)


def test_split_tile_menu_and_action(env):
    controller, window, _ = env
    from PySide6.QtCore import QPointF

    tile = window.t_saver
    tile.resize(260, 132)
    hits = []
    tile.activated.connect(lambda: hits.append("action"))
    tile._press_pos = QPointF(40, 100)  # Klick auf die Kachel → Aktion
    tile._show_menu()
    assert hits == ["action"]
    controller.screensaver.stop()
    tile._press_pos = QPointF(250, 30)  # Klick auf den Pfeil → Menü, keine Aktion
    tile._show_menu()
    assert hits == ["action"] and tile.menu.isVisible()
    tile.menu.hide()


def test_program_window_matching():
    from alupc.sources import _app_part, find_window

    class W:
        def __init__(self, d):
            self.d = d

        def description(self):
            return self.d

    wins = [W("Folien.pptx - PowerPoint"), W("Neuer Tab — Mozilla Firefox")]
    assert find_window(wins, "Folien.pptx - PowerPoint").d == "Folien.pptx - PowerPoint"
    # Browser hat den Tab gewechselt → gleiches Programm wird trotzdem gefunden
    assert find_window(wins, "Startseite — Mozilla Firefox").d == "Neuer Tab — Mozilla Firefox"
    assert find_window(wins, "Gibt es nicht") is None
    assert _app_part("a - b - Word") == "Word"


def test_sounds_builtin_and_events(env, tmp_path):
    controller, _window, _ = env
    from alupc import sounds

    for name in sounds.BUILTIN:
        path = sounds.builtin_path(name)
        assert path.exists() and path.stat().st_size > 1000
    src = tmp_path / "mein ton.wav"
    src.write_bytes(sounds.builtin_path("ding").read_bytes())
    stored = sounds.upload(str(src))
    assert stored != str(src) and sounds.resolve(stored) is not None
    src.unlink()  # Original weg – hochgeladene Kopie bleibt
    assert sounds.resolve(stored) is not None
    cfg = controller.config
    events = dict(cfg["sounds"]["events"])
    events.update({"standbild_an": "builtin:ding", "schwarz_an": stored, "inhalt": "builtin:klick"})
    cfg["sounds"] = {**cfg["sounds"], "events": events}
    player = controller.sounds
    player.last_played = None
    controller.show_source({"type": "clock"})
    assert player.last_played == "builtin:klick"
    controller.toggle_freeze()
    assert player.last_played == "builtin:ding"
    controller.toggle_freeze()
    controller.toggle_privacy()
    assert player.last_played == stored
    controller.toggle_privacy()
    cfg["sounds"] = {**cfg["sounds"], "enabled": False}
    player.last_played = None
    controller.toggle_freeze()
    assert player.last_played is None  # Töne aus → still
    controller.toggle_freeze()


def test_timer_end_plays_sound(env):
    controller, _window, _ = env
    from alupc.timer import clock

    controller.config["sounds"] = {**controller.config["sounds"], "enabled": True}
    clock.set(30)
    clock.start()
    controller._watch_timer()
    clock._accumulated = 31  # abgelaufen
    controller.sounds.last_played = None
    controller._watch_timer()
    assert controller.sounds.last_played == "builtin:alarm"
    clock.reset()


def test_tile_with_own_screensaver_and_timer(env):
    controller, window, _ = env
    from alupc.timer import clock

    start = dict(controller.config["start_page"])
    start["custom"] = [
        {"id": "pause", "title": "Pause", "icon": "moon", "color": "#6366f1", "section": "schnell",
         "sound": "builtin:gong",
         "action": {"kind": "screensaver", "screensaver": {"style": "nachricht", "text": "Pause – 10 Minuten"}}},
        {"id": "t10", "title": "10 Minuten", "icon": "timer", "color": "#f43f5e", "section": "schnell",
         "action": {"kind": "timer", "timer": {"minutes": 10, "seconds": 0, "mode": "countdown",
                                               "autostart": True}}},
    ]
    controller.config["start_page"] = start
    window.rebuild_start()
    controller.sounds.last_played = None
    controller.run_tile("pause")
    assert controller.screensaver.active and controller.screensaver.override_id == "pause"
    assert controller.output.screensaver.cfg["text"] == "Pause – 10 Minuten"
    assert controller.sounds.last_played == "builtin:gong"  # eigener Ton der Kachel
    window.refresh()
    assert window.custom_tiles["custom:pause"].active
    controller.run_tile("pause")  # nochmal = beenden
    assert not controller.screensaver.active
    controller.run_tile("t10")
    assert clock.running and 590 < clock.remaining() <= 600
    assert controller.timer_visible()
    clock.reset()


def test_custom_tile_dialog_kinds(env):
    controller, window, _ = env
    from alupc.startpage import new_custom_tile
    from alupc.ui.start_page_dialog import CustomTileDialog

    dlg = CustomTileDialog(controller.config, new_custom_tile(), "", window, controller)
    dlg.title.setText("Meine Pause")
    dlg.kind.setCurrentIndex(dlg.kind.findData("screensaver"))
    dlg.saver.style_combo.setCurrentIndex(dlg.saver.style_combo.findData("uhr"))
    dlg._save()
    assert dlg.tile["action"]["kind"] == "screensaver"
    assert dlg.tile["action"]["screensaver"]["style"] == "uhr"
    dlg2 = CustomTileDialog(controller.config, new_custom_tile(), "", window, controller)
    dlg2.kind.setCurrentIndex(dlg2.kind.findData("timer"))
    dlg2.t_min.setValue(3)
    dlg2._save()
    assert dlg2.tile["action"] == {"kind": "timer", "timer": {"mode": "countdown", "minutes": 3, "seconds": 0,
                                                             "finished_text": "Zeit ist um!", "autostart": True}}


def test_website_favorites_and_browser_control(env):
    controller, window, _ = env
    import time

    from alupc.sources import fit_rect

    html = ("data:text/html,<html><head><title>Start</title></head><body style='margin:0'>"
            "<button id=b style='width:100vw;height:100vh' onclick=\"document.title='Geklickt'\">X</button>"
            "</body></html>")
    controller.show_source({"type": "website", "url": html})
    view = controller.current_web_view()
    assert view is not None
    loaded = []
    view.loadFinished.connect(loaded.append)
    end = time.time() + 20
    while not loaded and time.time() < end:
        pump()
    assert loaded and loaded[0]
    controller.save_website("Testseite", html)
    assert controller.config["websites"]["favorites"][0] == {"title": "Testseite", "url": html}
    controller.save_website("Testseite 2", html)  # gleiche Adresse → ersetzt, nicht doppelt
    assert len(controller.config["websites"]["favorites"]) == 1

    window.open_browser_control()
    bc = window.browser_control
    pump()
    assert bc.view is view and bc.save_btn.isEnabled()
    bc.refresh()
    assert bc.preview.image() is not None
    # Klick in die Vorschau landet auf der Website (Knopf füllt die ganze Seite)
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    bc.preview.resize(640, 360)
    center = QPointF(bc.preview.width() / 2, bc.preview.height() / 2)

    def click():
        for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            ev = QMouseEvent(kind, center, center, Qt.LeftButton,
                             Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton, Qt.NoModifier)
            (bc.preview.mousePressEvent if kind == QEvent.MouseButtonPress else bc.preview.mouseReleaseEvent)(ev)

    # Chromium nimmt Eingaben erst an, wenn die Seite fertig gezeichnet ist – auf langsamen Rechnern
    # (GitHub) kann das nach „geladen“ noch etwas dauern, darum ggf. erneut klicken
    end = time.time() + 20
    next_click = 0.0
    while view.title() != "Geklickt" and time.time() < end:
        if time.time() >= next_click:
            click()
            next_click = time.time() + 1.5
        pump()
    assert view.title() == "Geklickt"
    bc._zoom(0.1)
    assert abs(view.zoomFactor() - 1.1) < 0.01
    # Zeichnen direkt auf der Website: Strich landet auf Monitor 2 und in der Vorschau
    controller.laser.clear_strokes()
    bc.refresh()
    img = bc.preview.image()
    r = fit_rect(img.width(), img.height(), bc.preview.width(), bc.preview.height(), "contain")
    a, b = QPointF(r.left() + r.width() * 0.2, r.center().y()), QPointF(r.left() + r.width() * 0.6, r.center().y())
    bc.set_color("#22c55e")
    assert bc.draw_tool == "stift"
    for kind, pos, handler in ((QEvent.MouseButtonPress, a, bc.preview.mousePressEvent),
                               (QEvent.MouseMove, b, bc.preview.mouseMoveEvent),
                               (QEvent.MouseButtonRelease, b, bc.preview.mouseReleaseEvent)):
        handler(QMouseEvent(kind, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert len(controller.laser.strokes) == 1 and view.title() == "Geklickt"  # kein Klick an die Seite
    (x0, y0), (x1, _y1) = controller.laser.strokes[0]["points"][0], controller.laser.strokes[0]["points"][-1]
    assert abs(x0 - 0.2) < 0.03 and abs(x1 - 0.6) < 0.03 and abs(y0 - 0.5) < 0.03
    bc.refresh()
    px = bc.preview.image().pixelColor(int(bc.preview.image().width() * 0.4), bc.preview.image().height() // 2)
    assert px.green() > 150 and px.red() < 120, px.name()  # grüner Strich in der Vorschau
    bc.set_tool(None)
    # Standbild: Vorschau zeigt die echte Seite dahinter, Hinweis und „Standbild aus“ sind da
    controller.toggle_freeze()
    pump()
    bc.refresh()
    assert controller.frozen and bc.freeze_banner.isVisible() and bc.unfreeze_btn.isVisible()
    bc.unfreeze_btn.click()
    pump()
    assert not controller.frozen and not bc.freeze_banner.isVisible()
    # Keine Website mehr → Fenster zeigt Hinweis
    controller.show_source({"type": "clock"})
    pump()
    assert bc.view is None and not bc.save_btn.isEnabled()
    bc.close()


# ---------------------------------------------------------------- 0.5: Übergänge, Lautstärke, Programm
def test_transitions_all_kinds_finish(env):
    import time

    from alupc.transitions import TRANSITIONS

    controller, _window, _ = env
    for kind in TRANSITIONS:
        controller.config["transition"] = {"type": kind, "ms": 120}
        controller.show_source({"type": "color", "color": "#ff0000"})
        pump()
        controller.show_source({"type": "color", "color": "#00ff00"})
        expected = 0 if kind == "schnitt" else 1
        assert len(controller.output._fades) == expected, kind
        end = time.time() + 2
        while controller.output._fades and time.time() < end:
            pump()
            controller.output.grab()  # Zeichnen jeder Übergangsart muss klappen
        assert controller.output._fades == []


def test_scene_transition_overrides_setup(env):
    controller, _window, _ = env
    controller.config["transition"] = {"type": "blende", "ms": 400}
    controller.config.put_scene({"name": "S", "layout": "vollbild", "slots": [{"type": "color"}],
                                 "transition": {"type": "zoom", "ms": 900}})
    assert controller.transition_for({"type": "scene", "scene": "S"}) == ("zoom", 900)
    assert controller.transition_for({"type": "color"}) == ("blende", 400)
    controller.config["appearance"] = {**controller.config["appearance"], "fade": False}
    assert controller.transition_for({"type": "color"})[0] == "schnitt"  # Setup: harter Schnitt
    assert controller.transition_for({"type": "scene", "scene": "S"}) == ("zoom", 900)  # Szene gilt trotzdem


def test_media_volume_live(env, tmp_path):
    controller, window, _ = env
    controller.show_source({"type": "color"})
    pump()
    assert controller.media_state() is None
    assert window.volume_box.isHidden()
    controller.show_source({"type": "video", "path": str(tmp_path / "fehlt.mp4"), "volume": 40})
    pump()
    assert controller.media_state() == {"volume": 40, "muted": False}
    assert abs(controller.output.content.audio.volume() - 0.4) < 0.01
    controller.set_media_volume(volume=70)
    controller.set_media_volume(muted=True)
    assert controller.content["volume"] == 70 and controller.content["muted"] is True
    assert controller.config["last_content"]["volume"] == 70
    assert controller.output.content.audio.isMuted()
    pump()
    assert not window.volume_box.isHidden()
    assert window.volume_box.mute_btn.isChecked()

    # In einer Szene: gilt für alle Medien darin, auch für Websites
    controller.config.put_scene({"name": "M", "layout": "nebeneinander", "slots": [
        {"type": "website", "url": "about:blank", "volume": 30},
        {"type": "video", "path": str(tmp_path / "fehlt.mp4")}]})
    controller.show_source({"type": "scene", "scene": "M"})
    pump()
    from alupc.sources import media_sources

    assert len(media_sources(controller.output.content)) == 2
    controller.set_media_volume(volume=55)
    web = media_sources(controller.output.content)[0]
    assert web.volume == 55
    assert controller.media_state()["volume"] == 55


def test_capture_programs_filters_system_windows(monkeypatch):
    from alupc.platform.base import WindowBackend, WindowInfo
    from alupc.ui import program_dialog

    class Fake:
        def __init__(self, text):
            self.text = text

        def description(self):
            return self.text

    monkeypatch.setattr(program_dialog, "capturable_windows",
                        lambda: [Fake("Program Manager"), Fake("Editor"), Fake("Editor"), Fake("Film")])

    class Backend(WindowBackend):
        can_list = True

        def list_windows(self):
            return [WindowInfo("1", "Editor", "notepad"), WindowInfo("2", "Film", "vlc", minimized=True)]

    programs = program_dialog.capture_programs(Backend())
    assert [(p.title, p.app, p.minimized) for p in programs] == [("Editor", "notepad", False),
                                                                  ("Film", "vlc", True)]
    assert "minimiert" in programs[1].label()
    # Ohne Fensterliste des Systems: alles zeigen, was Qt aufnehmen kann
    assert len(program_dialog.capture_programs(WindowBackend())) == 3


def test_program_dialog_list_keeps_selection(env, monkeypatch):
    controller, window, _ = env
    from alupc.ui import program_dialog

    progs = [program_dialog.Program("A"), program_dialog.Program("B", "app")]
    monkeypatch.setattr(program_dialog, "capture_programs", lambda _b: list(progs))
    dialog = program_dialog.ProgramDialog(controller, window)
    lst = dialog.capture_list
    assert lst.list.count() == 2
    lst.list.setCurrentRow(1)
    progs.insert(0, program_dialog.Program("Neu"))
    lst.reload()
    assert lst.selected().title == "B"
    lst.search.setText("neu")
    assert lst.selected() is None or lst.selected().title != "B"
    lst.search.setText("")
    lst.list.setCurrentRow(2)
    dialog._do_capture()
    assert controller.content == {"type": "window", "title": "B"}
    dialog.deleteLater()


def test_window_source_wakes_minimized(env, monkeypatch):
    from alupc import sources
    from alupc.platform.base import WindowBackend

    calls = []

    class Backend(WindowBackend):
        can_restore_background = True

        def is_minimized(self, title):
            return True

        def restore_in_background(self, title):
            calls.append(title)
            return True

    monkeypatch.setattr(sources, "_window_backend", Backend())

    class Win:
        def description(self):
            return "Film"

    monkeypatch.setattr(sources, "capturable_windows", lambda: [Win()])
    src = sources.WindowSource.__new__(sources.WindowSource)
    sources.SinkView.__init__(src, "contain")
    src.title = "Film"
    src.restore_minimized = True
    src._image = None
    src.state = "start"
    assert src._wake_if_minimized() is True
    assert calls == ["Film"]
    src.restore_minimized = False
    assert src._wake_if_minimized() is False
    assert src.state == "minimiert"
    assert "minimiert" in src._message


# ---------------------------------------------------------------- 0.6: Linux
class FakeFingerprint:
    name = "fprintd"
    can_enroll = True
    login_toggle = True
    can_auto_install = True

    def __init__(self, missing=("libpam-fprintd",), sensors=True, match=True):
        self.missing = list(missing)
        self.sensors = sensors
        self.match = match
        self.log = []
        self.login = False

    def missing_packages(self):
        return list(self.missing)

    def install_packages(self, pkgs):
        self.log.append(("install", tuple(pkgs)))
        self.missing = []

    def availability(self):
        return (True, "") if self.sensors else (False, "Kein Sensor")

    def list_sensors(self):
        from alupc.platform.base import Sensor

        return [Sensor("/dev/0", "Testsensor")] if self.sensors else []

    def detect_hardware(self):
        return [("Goodix (USB 27c6:538c)", "Zusatztreiber nötig")]

    def list_enrolled(self, _sid):
        return []

    def is_enrolled(self, sid, finger):
        return finger in self.list_enrolled(sid)

    def enroll(self, sid, finger, status):
        for i in range(1, 4):
            status("auflegen", i, 3)
        self.log.append(("enroll", finger))

    def verify(self, sid, status):
        status("auflegen", 0, 0)
        return (self.match, "Erkannt" if self.match else "Passt nicht")

    def login_enabled(self):
        return self.login

    def set_login_enabled(self, on):
        self.login = on
        self.log.append(("login", on))

    def cancel(self):
        self.log.append(("cancel",))

    def open_system_settings(self):
        self.log.append(("hello",))


def _wait(cond, seconds=5):
    import time

    end = time.time() + seconds
    while not cond() and time.time() < end:
        pump()
        time.sleep(0.01)
    return cond()


def test_fingerprint_wizard_runs_all_steps(env):
    from alupc.ui.fingerprint_wizard import FingerprintWizard

    backend = FakeFingerprint()
    wiz = FingerprintWizard(backend, None, "left-thumb")
    wiz.start()
    assert _wait(lambda: not wiz.running)
    assert [s.state for s in wiz.steps.values()] == ["ok"] * 5, [s.detail.text() for s in wiz.steps.values()]
    assert backend.log == [("install", ("libpam-fprintd",)), ("enroll", "left-thumb"), ("login", True)]
    assert "Fertig" in wiz.message.text()
    wiz.deleteLater()


def test_fingerprint_wizard_explains_missing_driver(env):
    from alupc.ui.fingerprint_wizard import FingerprintWizard

    backend = FakeFingerprint(missing=(), sensors=False)
    wiz = FingerprintWizard(backend)
    wiz.start()
    assert _wait(lambda: not wiz.running)
    assert wiz.steps["sensor"].state == "fehler"
    assert "27c6:538c" in wiz.steps["sensor"].detail.text()
    assert wiz.start_btn.isEnabled()
    # Test-Scan passt nicht → anhalten, Anmeldung NICHT einschalten
    backend = FakeFingerprint(missing=(), match=False)
    wiz2 = FingerprintWizard(backend)
    wiz2.start()
    assert _wait(lambda: not wiz2.running)
    assert wiz2.steps["test"].state == "fehler"
    assert ("login", True) not in backend.log
    wiz.deleteLater()
    wiz2.deleteLater()


def test_fingerprint_wizard_windows_flow(env):
    from alupc.ui.fingerprint_wizard import FingerprintWizard

    backend = FakeFingerprint(missing=())
    backend.name, backend.can_enroll, backend.login_toggle = "winbio", False, False
    wiz = FingerprintWizard(backend)
    wiz.start()
    assert _wait(lambda: not wiz.next_btn.isHidden())
    assert ("hello",) in backend.log and "pakete" not in wiz.steps
    wiz.next_btn.click()
    assert _wait(lambda: not wiz.running)
    assert wiz.steps["test"].state == "ok"
    wiz.deleteLater()


def test_fingerprint_wizard_windows_module_login(env, monkeypatch):
    """Windows + Modul (ZW101): der Assistent fragt vor dem Einschalten nach dem Windows-Passwort (0.52 rief es
    ohne auf → „Für die Windows-Anmeldung wird dein Windows-Passwort gebraucht“); falsches Passwort → nochmal."""
    from PySide6.QtWidgets import QInputDialog

    from alupc.ui.fingerprint_wizard import FingerprintWizard

    class ModuleBackend(FakeFingerprint):
        name, login_needs_password = "Modul am seriellen Anschluss", True

        def set_login_enabled(self, on, allow_multi=False, password=None):
            if on and password != "richtig":
                raise RuntimeError("Das Windows-Passwort stimmt nicht")
            self.login = on
            self.log.append(("login", on, password))

    answers = [("falsch", True), ("richtig", True)]
    asked = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (asked.append(a[2]), answers.pop(0))[1])
    backend = ModuleBackend(missing=())
    wiz = FingerprintWizard(backend)
    wiz.login_box.setChecked(True)
    wiz.start()
    assert _wait(lambda: not wiz.running, 10)
    assert wiz.steps["anmeldung"].state == "ok", {k: s.detail.text() for k, s in wiz.steps.items()}
    assert ("login", True, "richtig") in backend.log and len(asked) == 2
    assert "stimmt nicht" in asked[1]  # zweite Frage erklärt, warum nochmal
    wiz.deleteLater()


def test_screen_source_uses_kwin_without_asking(env, monkeypatch):
    from PySide6.QtCore import QObject, Signal

    from alupc import sources
    from alupc.platform import kwin_capture

    class FakeFeed(QObject):
        frame = Signal(QImage)
        failed = Signal(str)

        def __init__(self, name, fps, cursor=True, parent=None):
            super().__init__(parent)
            self.name = name
            self.cursor = cursor
            self.stopped = False

        def start(self):
            img = QImage(40, 20, QImage.Format_RGB32)
            img.fill(QColor("#00ff00"))
            self.frame.emit(img)

        def frame_taken(self):
            pass

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(sources, "_kwin_allowed", lambda _name: True)
    monkeypatch.setattr(kwin_capture, "KWinScreenFeed", FakeFeed)
    controller, _window, _ = env
    controller.mirror()
    pump()
    src = controller.output.content
    assert src.method == "kwin" and src.feed.name == "Haupt"
    assert src.image().pixelColor(1, 1) == QColor("#00ff00")
    feed = src.feed
    controller.show_source({"type": "color"})
    assert feed.stopped
    # KWin verweigert später → normale Aufnahme
    controller.mirror()
    src = controller.output.content
    src.feed.failed.emit("NoAuthorized")
    assert src.method == "qt" and src.capture is not None


def test_tray_badge_icon_has_all_sizes(env):
    controller, window, _ = env
    from alupc.ui import icons

    ic = icons.app_icon_with_badge("snowflake", "#0ea5e9")
    sizes = {s.width() for s in ic.availableSizes()}
    assert {16, 22, 32, 48, 256} <= sizes
    controller.toggle_privacy()
    pump()
    assert window._tray_key == "eye_off"
    controller.toggle_privacy()


# ---------------------------------------------------------------- 0.7: Maus, Laserpointer
def test_mirror_draws_mouse_pointer(env, monkeypatch):
    from PySide6.QtCore import QPoint

    from alupc import cursor as cursor_mod

    controller, _window, _ = env
    controller.mirror()
    pump()
    src = controller.output.content
    assert src.method == "qt" and src._cursor_on
    img = QImage(1920, 1080, QImage.Format_RGB32)
    img.fill(QColor("#336699"))
    src._pending = None
    src._image = img
    src.resize(960, 540)
    main = controller.main_screen().geometry()
    t = cursor_mod.tracker()
    t._set(QPoint(main.x() + main.width() // 2, main.y() + main.height() // 2))
    shot = src.grab().toImage()
    # Pfeilspitze in der Bildmitte: dort (knapp daneben) ist der Pfeil weiß/schwarz statt blau
    colors = {shot.pixelColor(480 + dx, 270 + dy).name() for dx in range(1, 6) for dy in range(3, 12)}
    assert "#ffffff" in colors or "#000000" in colors, colors
    # Ausschalten im Setup → kein Zeiger mehr
    controller.config["output"] = {**controller.config["output"], "mirror_cursor": False}
    controller.apply_output_settings()
    shot = src.grab().toImage()
    colors = {shot.pixelColor(480 + dx, 270 + dy).name() for dx in range(1, 6) for dy in range(3, 12)}
    assert colors == {"#336699"}
    controller.config["output"] = {**controller.config["output"], "mirror_cursor": True}
    controller.apply_output_settings()
    users = t.users
    controller.show_source({"type": "color"})
    assert t.users == users - 1  # Aufnahme beendet → Abfrage wird freigegeben


def test_cursor_stays_home_except_extend(env):
    from PySide6.QtGui import QCursor

    controller, _window, _ = env
    controller.show_source({"type": "color"})
    pump()
    assert controller.cursor_should_stay_home()
    guard = controller.cursor_guard
    assert guard.active
    out = controller.output_screen().geometry()
    QCursor.setPos(out.center())
    guard._enforce()
    assert controller.main_screen().geometry().contains(QCursor.pos())
    controller.extend()
    pump()
    assert not controller.cursor_should_stay_home() and not guard.active
    controller.show_source({"type": "color"})
    controller.config["output"] = {**controller.config["output"], "confine_cursor": False}
    controller.apply_output_settings()
    assert not guard.active
    controller.config["output"] = {**controller.config["output"], "confine_cursor": True}
    controller.apply_output_settings()




# ---------------------------------------------------------------- 0.8: Zeigen & Zeichnen
def _mouse(widget, kind, pos, button=None):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    button = button if button is not None else Qt.LeftButton
    buttons = Qt.LeftButton if kind in (QEvent.MouseButtonPress, QEvent.MouseMove) and button == Qt.LeftButton \
        else Qt.NoButton
    ev = QMouseEvent(kind, QPointF(pos), QPointF(pos), button if kind != QEvent.MouseMove else Qt.NoButton,
                     buttons, Qt.NoModifier)
    {QEvent.MouseButtonPress: widget.mousePressEvent, QEvent.MouseMove: widget.mouseMoveEvent,
     QEvent.MouseButtonRelease: widget.mouseReleaseEvent}[kind](ev)


def test_presenter_laser_and_drawing(env):
    from PySide6.QtCore import QEvent, QPointF

    controller, window, _ = env
    controller.show_source({"type": "color", "color": "#000000"})
    pump()
    controller.run_command("zeichnen")
    pump()
    win = window.presenter
    laser = controller.laser
    assert win.isVisible() and laser.remote and laser.isVisible()
    assert laser.geometry() == controller.output_screen().geometry()
    canvas = win.canvas
    canvas.resize(800, 450)
    a = canvas.area()
    center = QPointF(a.x() + a.width() / 2, a.y() + a.height() / 2)

    # Laser: Maus in der Mitte der Vorschau → Punkt in der Mitte von Monitor 2
    win.set_tool("laser")
    _mouse(canvas, QEvent.MouseMove, center)
    out = controller.output_screen().geometry()
    assert abs(laser.point.x() - out.width() / 2) < 2 and abs(laser.point.y() - out.height() / 2) < 2
    canvas.leaveEvent(None)
    assert laser.point is None

    # Stift: Strich von links oben zur Mitte
    win.set_tool("pen")
    win.set_color("#22c55e")
    start = QPointF(a.x() + a.width() * 0.25, a.y() + a.height() * 0.25)
    _mouse(canvas, QEvent.MouseButtonPress, start)
    for i in range(1, 11):
        _mouse(canvas, QEvent.MouseMove, start + (center - start) * (i / 10))
    _mouse(canvas, QEvent.MouseButtonRelease, center)
    assert len(laser.strokes) == 1 and laser.strokes[0]["color"] == "#22c55e"
    assert len(laser.strokes[0]["points"]) == 11
    img = laser.grab().toImage()
    mid = QPointF(out.width() * 0.375, out.height() * 0.375).toPoint()
    assert img.pixelColor(mid).green() > 150  # grüner Strich auf Monitor 2
    prev = canvas.grab().toImage()
    pm = QPointF(a.x() + a.width() * 0.375, a.y() + a.height() * 0.375).toPoint()
    assert prev.pixelColor(pm).green() > 150  # … und in der Vorschau

    # Textmarker, Rückgängig, Radierer
    win.set_tool("marker")
    _mouse(canvas, QEvent.MouseButtonPress, center)
    _mouse(canvas, QEvent.MouseMove, center + QPointF(60, 0))
    _mouse(canvas, QEvent.MouseButtonRelease, center + QPointF(60, 0))
    assert laser.strokes[-1]["tool"] == "marker"
    controller.laser.undo()
    assert len(laser.strokes) == 1
    win.set_tool("eraser")
    _mouse(canvas, QEvent.MouseButtonPress, start)
    _mouse(canvas, QEvent.MouseButtonRelease, start)
    assert laser.strokes == []

    # Neuer Inhalt → Zeichnungen weg (Standard)
    laser.begin_stroke("pen", "#ff0000", 0.004, QPointF(0.5, 0.5))
    controller.show_source({"type": "color", "color": "#111111"})
    assert laser.strokes == []
    # Schwarz: nichts vom Laser/Zeichnen zu sehen
    laser.begin_stroke("pen", "#ff0000", 0.02, QPointF(0.5, 0.5))
    laser.extend_stroke(QPointF(0.6, 0.5))
    controller.toggle_privacy()
    img = laser.grab().toImage()
    assert img.pixelColor(QPointF(out.width() * 0.55, out.height() * 0.5).toPoint()).red() < 50
    controller.toggle_privacy()
    # Schließen → Zeichnung bleibt (erst Szenenwechsel/Löschen entfernt sie)
    win.close()
    pump()
    assert laser.strokes and not laser.remote and laser.isVisible()
    controller.laser.clear_strokes()
    assert not laser.isVisible()
    assert controller.config["draw"]["tool"] == "eraser"  # zuletzt gewähltes Werkzeug gemerkt


def test_drawings_stay_until_deleted_or_scene_change(env, tmp_path):
    from PySide6.QtCore import QPointF

    from alupc.config import Config
    from alupc.controller import Controller

    controller, window, _ = env
    controller.show_source({"type": "color", "color": "#123456"})
    window.open_presenter()
    controller.laser.begin_stroke("pen", "#ff0000", 0.004, QPointF(0.2, 0.2))
    controller.laser.extend_stroke(QPointF(0.6, 0.6))
    controller.laser.end_stroke()
    window.presenter.close()
    pump()
    # Fenster zu → Zeichnung bleibt auf Monitor 2 und ist gespeichert
    assert len(controller.laser.strokes) == 1 and controller.laser.isVisible()
    assert len(controller.config["draw"]["strokes"]) == 1
    # Neustart von AluPC (gleicher Inhalt) → Zeichnung ist wieder da
    controller.config.save()
    again = Controller(Config(controller.config.path))
    again.display.available = lambda: False
    again.restore_last()
    assert len(again.laser.strokes) == 1 and again.laser.strokes[0]["points"][0] == (0.2, 0.2)
    again.shutdown()
    # Szenenwechsel → weg (auch aus dem Speicher)
    controller.show_source({"type": "color", "color": "#654321"})
    assert controller.laser.strokes == [] and controller.config["draw"]["strokes"] == []
    # von Hand löschen
    controller.laser.begin_stroke("pen", "#ff0000", 0.004, QPointF(0.5, 0.5))
    controller.laser.end_stroke()
    controller.run_command("zeichnungen_loeschen")
    assert controller.laser.strokes == [] and controller.config["draw"]["strokes"] == []
    # „laserpointer“ (alter Befehl/Tastenkürzel) öffnet jetzt Zeigen & Zeichnen
    window.presenter.close()
    controller.run_command("laserpointer")
    pump()
    assert window.presenter.isVisible()
    window.presenter.close()


def test_preview_frame_rate_selectable(env):

    from PySide6.QtCore import QSize

    from alupc.output_window import grab_scaled

    controller, window, _ = env
    controller.show_source({"type": "color", "color": "#ff0000"})
    pump()
    window.open_presenter()
    win = window.presenter
    assert win.fps() == 30 and win.timer.interval() == 33  # Standard jetzt 30 statt 10
    win.fps_combo.setCurrentIndex(win.fps_combo.findData(60))
    assert win.timer.interval() == 17 and controller.config["draw"]["fps"] == 60
    # Vorschau wird direkt verkleinert gezeichnet – richtiger Inhalt, richtige Größe
    img = grab_scaled(controller.output, QSize(320, 180))
    assert img.width() <= 320 and img.height() <= 180 and img.pixelColor(10, 10).name() == "#ff0000"
    # Es kommen wirklich viele Bilder pro Sekunde an (nicht mehr nur 10)
    from PySide6.QtTest import QTest

    counted = []
    win.timer.timeout.connect(lambda: counted.append(1))  # eigener Zähler (die Anzeige setzt ihren zurück)
    QTest.qWait(1000)
    assert len(counted) >= 15, len(counted)  # großzügig: GitHub-Rechner sind langsam
    win.close()
    pump()


def test_cursor_tracker_cleans_up_failed_kwin(monkeypatch):
    from alupc import cursor as cursor_mod
    from alupc.platform import kwin_cursor

    stopped = []

    class Broken(kwin_cursor.CursorReceiver):
        def start(self, load_script=True):
            raise RuntimeError("KWin-Skript ließ sich nicht laden")

        def stop(self):
            stopped.append(1)

    monkeypatch.setattr(cursor_mod, "is_wayland", lambda: True)
    monkeypatch.setattr(cursor_mod, "wayland_kde", lambda: True)
    monkeypatch.setattr(kwin_cursor, "CursorReceiver", Broken)
    t = cursor_mod.CursorTracker()
    t.acquire()
    assert t.method == "keine" and t._kwin is None and stopped == [1]
    t.release()


# ---------------------------------------------------------------- 0.9: Fingerabdruckmodul am seriellen Anschluss
@pytest.mark.skipif(not __import__("sys").platform.startswith("linux"), reason="virtueller Anschluss nur Linux")
def test_serial_module_page_and_wizard(env, monkeypatch):
    pytest.importorskip("serial")
    import time

    from fake_zw101 import FakeZW101

    from alupc.platform import zw_fingerprint as zw
    from alupc.ui.fingerprint_wizard import FingerprintWizard

    controller, window, tmp = env
    fake = FakeZW101()
    try:
        monkeypatch.setattr(zw, "candidate_ports", lambda: [fake.port])
        backend = controller.fingerprint
        assert backend.availability() == (True, "") and backend.is_serial
        assert backend.can_enroll and backend.can_delete

        # Seite: passt sich an (Finger-Auswahl, Löschen sichtbar, kein Windows-Hello-Hinweis)
        window._go(3)
        page = window.pages[3].findChild(__import__("alupc.ui.fingerprint_page", fromlist=["x"]).FingerprintPage)
        end = time.time() + 5
        while page.sensor_combo.count() == 0 and time.time() < end:
            pump()
        assert fake.port in page.sensor_combo.currentData()
        assert not page.finger_combo.isHidden() and not page.hello_note.isVisible()

        # Assistent: Sensor → anlernen → testen (Anmeldung hier aus: dafür braucht es das .deb)
        fake.finger = "zeigefinger"
        fake.auto_lift = True
        wiz = FingerprintWizard(backend, None, "right-index-finger")
        wiz.login_box.setChecked(False)
        wiz.start()
        end = time.time() + 15
        while wiz.running and time.time() < end:
            pump()
            time.sleep(0.01)
        states = {k: s.state for k, s in wiz.steps.items()}
        assert states["sensor"] == "ok" and states["anlernen"] == "ok" and states["test"] == "ok", \
            {k: s.detail.text() for k, s in wiz.steps.items()}
        assert list(fake.library.values()) == ["zeigefinger"]
        wiz.deleteLater()

        # Mehrere Personen: Name eintippen → Finger gehören dieser Person, Liste + Belegung zeigen es
        assert not page.person_row.isHidden()
        page.person_combo.setEditText("Lena")
        assert zw.get_person() == "Lena"
        fake.finger = "lena"
        backend.enroll(page.sensor_id(), "right-index-finger", lambda *_: None)
        page._changed()  # wie nach dem Anlernen: Belegung sofort neu, ohne auf die Liste zu warten
        assert page.usage_label.text() == "2 belegt · 48 frei"
        end = time.time() + 5
        while page.enrolled.count() < 2 and time.time() < end:
            pump()
        assert page.usage_label.text() == "2 belegt · 48 frei"
        names = [page.person_combo.itemText(i) for i in range(page.person_combo.count())]
        assert names[0] == "Lena" and len(names) == 2
        assert any("Lena · " in page.enrolled.item(i).text() for i in range(page.enrolled.count()))
        assert page.hands.enrolled == {"right-index-finger"}
    finally:
        zw.set_person("")
        fake.close()


# ---------------------------------------------------------------- 0.10: Mediathek
def test_media_library_dialog(env, tmp_path):
    import time

    from alupc import media_library as lib
    from alupc.ui.media_library import MediaLibraryDialog

    controller, window, _ = env
    paths = []
    for name, color in (("rot", "#ff0000"), ("blau", "#0000ff")):
        img = QImage(800, 450, QImage.Format_RGB32)
        img.fill(QColor(color))
        path = tmp_path / f"{name}.png"
        img.save(str(path))
        paths.append(str(path))
    lib.save(controller.config, {"type": "image", "path": paths[0]}, "Rotes Bild")
    lib.save(controller.config, {"type": "video", "path": str(tmp_path / "fehlt.mp4")})
    controller.show_source({"type": "image", "path": paths[1]})  # → automatisch unter „Zuletzt“
    pump()
    dialog = MediaLibraryDialog(controller, window)
    assert dialog.grid.count() == 3
    titles = [dialog.grid.item(i).text() for i in range(3)]
    assert titles == ["fehlt", "Rotes Bild", "blau"]
    # Filter und Suche
    dialog._set_filter("video")
    assert dialog.grid.count() == 1
    dialog._set_filter("all")
    dialog.search.setText("rot")
    assert dialog.grid.count() == 1
    dialog.search.setText("")
    # Vorschaubild wird im Hintergrund erzeugt und zwischengespeichert
    end = time.time() + 5
    while f"image:{paths[0]}" not in dialog.thumbs.memory and time.time() < end:
        pump()
    assert f"image:{paths[0]}" in dialog.thumbs.memory
    # „Zuletzt“ speichern, dann anzeigen
    dialog.grid.item(2).setSelected(True)
    assert dialog.save_btn.isEnabled()
    dialog.save_selected()
    assert lib.is_saved(controller.config, {"type": "image", "path": paths[1]})
    dialog.grid.clearSelection()
    item = next(dialog.grid.item(i) for i in range(dialog.grid.count()) if dialog.grid.item(i).text() == "Rotes Bild")
    item.setSelected(True)
    dialog.show_selected()
    assert controller.content == {"type": "image", "path": paths[0]}
    # fehlende Datei lässt sich nicht zeigen
    dialog = MediaLibraryDialog(controller, window)
    missing = next(dialog.grid.item(i) for i in range(dialog.grid.count()) if dialog.grid.item(i).text() == "fehlt")
    missing.setSelected(True)
    assert not dialog.show_btn.isEnabled()
    # Menü an der Kachel
    window._fill_media_menu(window.media_menu)
    texts = [a.text() for a in window.media_menu.actions()]
    assert "Rotes Bild" in texts and "Mediathek öffnen …" in texts


def test_cursor_rule_depends_on_mode_not_visibility(env):
    controller, _window, _ = env
    controller.show_source({"type": "color"})
    pump()
    controller.output.hide()  # z. B. kurz beim Umschalten des Monitors
    assert controller.cursor_should_stay_home()
    controller.program_moved("Editor")  # Programm direkt auf Monitor 2 = Erweitern → Maus frei
    assert not controller.cursor_should_stay_home()
    controller.toggle_privacy()  # Schwarz über dem Desktop → Maus bleibt auf Monitor 1
    assert controller.cursor_should_stay_home()
    controller.toggle_privacy()
    controller.extend()
    assert not controller.cursor_should_stay_home()


# ---------------------------------------------------------------- 0.10: Mediensteuerung
@pytest.fixture(scope="module")
def test_video(tmp_path_factory):
    import subprocess
    import sys

    path = tmp_path_factory.mktemp("video") / "test.mp4"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.run([sys.executable, str(HERE / "make_video.py"), str(path)], env=env,
                          capture_output=True, timeout=120)
    if proc.returncode != 0 or not path.exists() or path.stat().st_size < 1000:
        pytest.skip("Testvideo ließ sich hier nicht erzeugen")
    return str(path)


def _until(cond, seconds=8):
    import time

    end = time.time() + seconds
    while not cond() and time.time() < end:
        pump()
        time.sleep(0.02)
    return cond()


def test_media_bar_controls_video(env, test_video):
    controller, window, _ = env
    bar = window.media_bar
    controller.show_source({"type": "color"})
    pump()
    assert bar.isHidden()
    controller.show_source({"type": "video", "path": test_video, "loop": True, "muted": True})
    pump()
    assert not bar.isHidden() and bar.title.text() == "test"
    video = bar.current()
    assert _until(lambda: video.duration() > 0), "Video lädt nicht"
    bar.refresh()
    assert bar.dur_label.text() == "0:04"
    bar._toggle()  # Pause
    assert _until(lambda: not video.playing())
    bar._seek(2500)  # auf der Zeitleiste springen
    assert _until(lambda: abs(video.position() - 2500) < 400), video.position()
    bar._skip(-10_000)  # 10 s zurück → nicht vor den Anfang
    assert _until(lambda: video.position() < 300), video.position()
    bar._toggle()
    assert _until(lambda: video.playing())


def test_media_bar_in_scene_with_two_videos(env, test_video):
    controller, window, _ = env
    controller.config.put_scene({"name": "Zwei Videos", "layout": "nebeneinander", "slots": [
        {"type": "video", "path": test_video, "muted": True},
        {"type": "video", "path": test_video, "muted": True}]})
    controller.show_source({"type": "scene", "scene": "Zwei Videos"})
    pump()
    bar = window.media_bar
    assert not bar.isHidden() and bar.which.count() == 2 and not bar.which.isHidden()
    bar.which.setCurrentIndex(1)
    second = bar.current()
    assert second is bar.videos[1]
    assert _until(lambda: second.duration() > 0)
    bar._toggle()
    assert _until(lambda: not second.playing())
    assert bar.videos[0].playing()  # nur das gewählte Video pausiert


def test_coding_screensavers(env):
    import time

    from alupc.screensaver import ScreensaverView
    from alupc.screensaver_code import SNIPPETS, highlight

    controller, _window, _ = env
    for style in ("matrix", "code", "terminal", "netz", "sterne"):
        view = ScreensaverView({"style": style}, controller.config.get_scene)
        view.resize(640, 360)
        for _ in range(40):  # ~1,5 s Animation
            view._last_tick -= 0.04
            view._tick()
        img = view.grab().toImage()
        colors = {img.pixelColor(x, y).name() for x in range(0, 640, 16) for y in range(0, 360, 12)}
        assert len(colors) > 3, (style, colors)  # es ist wirklich etwas zu sehen
        view.stop()
    # Syntaxfarben: Schlüsselwort, Text, Zeichenkette, Kommentar
    parts = dict((t, c) for t, c in highlight('def zeigen(x):  # "Hallo"', "python"))
    assert parts["def"] == "keyword" and parts["zeigen"] == "function"
    assert highlight('print("Hi")  # Kommentar', "python")[-1] == ("# Kommentar", "comment")
    assert all(len(text) > 100 for _name, _lang, text in SNIPPETS)
    time.sleep(0)


def test_hand_picker(env):
    from PySide6.QtCore import QEvent

    from alupc.ui.hand_picker import HandPicker

    hands = HandPicker()
    hands.resize(420, 200)
    picked = []
    hands.fingerClicked.connect(picked.append)
    fingers = {k: tip for k, _path, tip in hands._fingers()}
    assert len(fingers) == 10
    for key in ("left-thumb", "right-index-finger", "right-little-finger"):
        _mouse(hands, QEvent.MouseButtonPress, fingers[key])
    assert picked == ["left-thumb", "right-index-finger", "right-little-finger"]
    assert hands.selected == "right-little-finger"
    hands.set_enrolled({"right-thumb"})
    img = hands.grab().toImage()
    tip = fingers["right-thumb"].toPoint()
    c = img.pixelColor(tip.x(), tip.y() + 10)
    assert c.green() > c.red()  # angelernter Finger leuchtet grün


# ---------------------------------------------------------------- 0.11: Handy → Monitor 2
FAKE_UXPLAY = '''#!%(python)s
import sys, time
args = sys.argv[1:]
if args == ["-h"]:
    print("UxPlay 1.73 usage: ... %(vrtp)s")
    print(%(pipeline_help)r)
    sys.exit(0)
open(%(log)r, "a").write(" ".join(args) + "\\n")
print("Initialized server socket(s)", flush=True)
if "-pin" in args:
    print("Pin code: 4711", flush=True)
sink = args[args.index("-vs") + 1] if "-vs" in args else ""
if "-vrtp" in args or "udpsink" in sink:  # 1.73: -vrtp · 1.68: -vs "rtph264pay … ! udpsink … port=N"
    port = int((args[args.index("-vrtp") + 1] if "-vrtp" in args else sink).rsplit("port=", 1)[1])
    time.sleep(1.0)
    sys.path.insert(0, %(tests)r)
    import rtp_sender
    rtp_sender.main(port, 6.0)
time.sleep(30)
'''


def _fake_uxplay(tmp_path, vrtp: bool, pipeline_opts: bool = False) -> tuple[str, Path]:
    """vrtp: wie UxPlay ≥ 1.73 · pipeline_opts: wie 1.68 aus Kubuntu (-vd/-vc/-vs, kein -vrtp)."""
    import sys

    log = tmp_path / "uxplay_args.txt"
    script = tmp_path / ("uxplay_neu" if vrtp else "uxplay_168" if pipeline_opts else "uxplay_alt")
    help_text = ("-vd ...   Choose the GStreamer h264 decoder\n-vc ...   Choose the GStreamer videoconverter\n"
                 "-vs ...   Choose the GStreamer videosink\n-s wxh   Set display resolution") if pipeline_opts else ""
    script.write_text(FAKE_UXPLAY % {"python": sys.executable, "vrtp": "-vrtp pipeline" if vrtp else "",
                                     "log": str(log), "tests": str(HERE), "pipeline_help": help_text})
    script.chmod(0o755)
    return str(script), log


def test_handy_helpers(tmp_path):
    import sys

    from alupc import handy

    if sys.platform.startswith("win"):
        pytest.skip("Fake-Programm ist ein Shell-Skript")
    new, _ = _fake_uxplay(tmp_path, True)
    old, _ = _fake_uxplay(tmp_path, False)
    assert handy.supports_vrtp(new) and not handy.supports_vrtp(old)
    assert handy.supports_vrtp(str(tmp_path / "gibtsnicht")) is False
    assert handy.find_program("uxplay", new) == new
    assert "m=video 5004 RTP/AVP 96" in handy.sdp_text(5004)
    args = handy.uxplay_args("Klasse 7b", "1234", 5004)
    assert args[:4] == ["-n", "Klasse 7b", "-nh", "-p"] and args[4:6] == ["-pin", "1234"]
    assert args[-1].endswith("udpsink host=127.0.0.1 port=5004")
    assert handy.uxplay_args("", "zufall", None)[4] == "-pin" and "-fs" in handy.uxplay_args("", "", None)
    assert len(handy.random_pin()) == 4


def test_airplay_source_shows_stream(env, tmp_path):
    """UxPlay ≥ 1.73 (simuliert): Bild kommt per RTP und erscheint als normale Quelle auf Monitor 2."""
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("Fake-Programm ist ein Shell-Skript")
    pytest.importorskip("av")
    controller, window, _ = env
    uxplay, log = _fake_uxplay(tmp_path, True)
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": uxplay, "pin": "zufall",
                                  "airplay_name": "Beamer"}
    controller.start_airplay()
    pump()
    view = controller.output.content
    assert controller.content == {"type": "airplay"} and view.mode == "stream"
    assert "Beamer" in view._message
    assert _until(lambda: log.exists() and "-pin" in log.read_text(), 8)  # Datei kann kurz leer sein
    assert "-vrtp" in log.read_text() and "-pin" in log.read_text()
    assert _until(lambda: view._had_frames, 20), "Kein Bild vom (simulierten) iPhone"
    assert _until(lambda: view._image is not None and not view._image.isNull(), 5)
    c = view._image.pixelColor(view._image.width() - 5, view._image.height() // 2)
    assert c.red() > 150 and c.green() < 90, c.name()  # rotes Testbild
    assert controller.airplay.pin_code == "4711"
    pump()
    assert window.t_airplay.active and not window.t_extend.active
    controller.extend()  # etwas anderes → UxPlay wird (verzögert) beendet
    assert _until(lambda: not controller.airplay.running(), 5)


def test_airplay_stream_with_uxplay_168(env, tmp_path):
    """Linux mit UxPlay 1.68 (Kubuntu): kein -vrtp, aber das Bild geht trotzdem direkt in AluPC – über
    -vd identity -vc identity -vs "rtph264pay … ! udpsink …" (kein UxPlay-Fenster, das verschwinden kann)."""
    import sys

    if not sys.platform.startswith("linux"):
        pytest.skip("Nur Linux (Pipeline-Weg)")
    pytest.importorskip("av")
    controller, window, _ = env
    uxplay, log = _fake_uxplay(tmp_path, False, pipeline_opts=True)
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": uxplay, "pin": ""}
    controller.start_airplay()
    pump()
    view = controller.output.content
    assert view.mode == "stream"
    assert _until(log.exists, 5)
    started = log.read_text()
    assert "-vd identity -vc identity -vs rtph264pay config-interval=1 pt=96 ! udpsink host=127.0.0.1 port=" \
        in started and "-vrtp" not in started and "-fs" not in started and "-avdec" not in started
    # iPhone bekommt die Größe von Monitor 2 → schickt nicht mehr Pixel als nötig (flüssiger)
    out = controller.output_screen()
    size = f"{int(out.geometry().width() * out.devicePixelRatio())}x{int(out.geometry().height() * out.devicePixelRatio())}"
    assert f"-s {size}" in started, started
    assert _until(lambda: view._had_frames, 20), "Kein Bild über den 1.68-Weg"
    assert _until(lambda: view._image is not None and not view._image.isNull(), 5)
    c = view._image.pixelColor(view._image.width() - 5, view._image.height() // 2)
    assert c.red() > 150 and c.green() < 90, c.name()
    controller.extend()
    assert _until(lambda: not controller.airplay.running(), 5)


def test_handy_windows_mode(env, tmp_path, monkeypatch):
    """Ältere UxPlay-Version: eigenes Fenster, das auf Monitor 2 geschoben wird."""
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("Fake-Programm ist ein Shell-Skript")
    controller, window, _ = env
    uxplay, log = _fake_uxplay(tmp_path, False)
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": uxplay, "pin": ""}
    from alupc.platform.base import WindowInfo

    moved, open_windows = [], []
    monkeypatch.setattr(controller.windows, "follow_windows", lambda *a: None)  # wie Windows/X11: selbst suchen
    monkeypatch.setattr(controller.windows, "list_windows", lambda: list(open_windows))
    monkeypatch.setattr(controller.windows, "move_window",
                        lambda wid, out, rect, full=False: moved.append((wid, out, rect, full)))
    controller.start_airplay()
    pump()
    # Monitor 2 zeigt den Warte-Bildschirm (AluPC-Inhalt) – NICHT „Erweitert“
    assert controller.mode == "content" and controller.content == {"type": "airplay"}
    from alupc.sources import AirPlaySource

    assert isinstance(controller.output.content, AirPlaySource) and controller.output.content._waiting
    assert "AluPC" in controller.output.content._message or controller.airplay.settings()["airplay_name"] in \
        controller.output.content._message
    assert _until(lambda: controller.airplay.running() and log.exists(), 5)
    assert "-fs" in log.read_text() and "-vrtp" not in log.read_text()
    # UxPlay öffnet sein Fenster erst, wenn sich das iPhone verbindet – auch viel später
    controller._follow_timer.timeout.emit()
    assert moved == []
    assert controller.output.yield_top and not controller.output.suspended  # AluPC drängelt sich nicht vor
    assert controller.output.isVisible()  # Warte-Bildschirm sichtbar
    open_windows.append(WindowInfo(id="0x1", title="AluPC", app="uxplay"))
    controller._follow_timer.timeout.emit()
    assert moved == [("0x1", "Zweit", (1920, 0, 1280, 720), True)]
    # iPhone-Bild da → Warte-Bildschirm aus (verdeckt es nicht); iPhone weg → wieder da
    assert controller.output.suspended and not controller.output.isVisible()
    open_windows.clear()
    controller._follow_timer.timeout.emit()
    assert not controller.output.suspended and controller.output.isVisible()
    open_windows.append(WindowInfo(id="0x1", title="AluPC", app="uxplay"))  # neu verbunden → wieder platzieren
    controller._follow_timer.timeout.emit()
    assert len(moved) == 2
    controller._follow_timer.timeout.emit()
    assert len(moved) == 2  # nicht dauernd neu schieben
    # UxPlay setzt sein Fenster auf Videogröße zurück (Bild kommt / dreht sich) → wieder über ganz Monitor 2
    monkeypatch.setattr(controller.windows, "window_fits", lambda wid, out, rect: False, raising=False)
    controller._follow_timer.timeout.emit()
    assert len(moved) == 3
    monkeypatch.setattr(controller.windows, "window_fits", lambda wid, out, rect: True, raising=False)
    controller._follow_timer.timeout.emit()
    assert len(moved) == 3  # passt wieder → in Ruhe lassen
    open_windows[:] = [WindowInfo(id="0x2", title="AluPC", app="uxplay")]  # neue Verbindung → neues Fenster
    controller._follow_timer.timeout.emit()
    assert moved[-1][0] == "0x2"
    # uxplay-windows: Videofenster am Programm erkennen (Titel je nach Version) – nicht dessen Einstellungsfenster
    open_windows[:] = [WindowInfo(id="0x3", title="uxplay-windows", app="uxplay-windows"),
                       WindowInfo(id="0x4", title="Direct3D11 renderer", app="uxplay-windows")]
    controller._follow_timer.timeout.emit()
    assert [m[0] for m in moved[-1:]] == ["0x4"] and "0x3" not in [m[0] for m in moved]
    assert controller.cursor_should_stay_home()  # kein Erweitern: Maus bleibt auf Monitor 1
    pump()
    assert window.t_airplay.active and not window.t_extend.active and not window.t_program.active
    # Name geändert, während es läuft → UxPlay startet mit dem neuen Namen neu
    controller.config["handy"] = {**controller.config["handy"], "airplay_name": "Neuer Name"}
    assert controller.airplay.restart_if_changed()
    assert _until(lambda: controller.airplay.running() and "Neuer Name" in log.read_text(), 5)
    controller.extend()
    assert _until(lambda: not controller.airplay.running(), 5)  # AirPlay beendet
    assert controller._handy_window == "" and not controller.output.yield_top


def test_handy_page_airplay_simple(env, tmp_path):
    """AirPlay ohne Einstellungen: einmalig Name „AluPC“, kein Code – keine Name-/Code-Felder mehr."""
    from alupc.config import Config
    from alupc.controller import Controller

    controller, window, _ = env
    assert controller.config["handy"]["airplay_name"] == "AluPC" and controller.config["handy"]["pin"] == ""
    assert controller.config["handy"]["airplay_simple"] is True
    controller.airplay.ensure_unique_name()  # benennt nicht mehr in „AluPC (Rechner)“ um
    assert controller.config["handy"]["airplay_name"] == "AluPC"
    # alte Einstellungen (eigener Name, Code) werden einmalig ersetzt
    old = Config(tmp_path / "alt.json")
    old["handy"] = {**old["handy"], "airplay_name": "Mein Name", "pin": "1234", "setup_done": True}
    c2 = Controller(old)
    assert old["handy"]["airplay_name"] == "AluPC" and old["handy"]["pin"] == ""
    c2.shutdown()
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": ""}
    window.open_handy_window()
    pump()
    page = window.handy_page
    assert not hasattr(page, "pin_mode") and not hasattr(page, "name")
    assert {"airplay", "handy_remote"} <= set(window.tiles)
    assert "handy_stream" not in set(window.tiles)  # Android-Stream gibt es nicht mehr
    assert "miracast" in window.tiles  # Miracast seit 0.141 wieder (empfangen Windows, senden Windows/Linux)
    controller.start_airplay()  # UxPlay fehlt → nur Hinweis, nichts kaputt
    pump()


# ---------------------------------------------------------------- 0.12: Kamera-Optionen
def _quadrants(w=200, h=100):
    from PySide6.QtGui import QPainter

    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.fillRect(0, 0, w // 2, h // 2, QColor("#ff0000"))       # oben links rot
    p.fillRect(w // 2, 0, w // 2, h // 2, QColor("#00ff00"))  # oben rechts grün
    p.fillRect(0, h // 2, w // 2, h // 2, QColor("#0000ff"))  # unten links blau
    p.fillRect(w // 2, h // 2, w // 2, h // 2, QColor("#ffff00"))  # unten rechts gelb
    p.end()
    return img


def test_camera_options_and_bar(env, monkeypatch):
    from PySide6.QtMultimedia import QVideoFrame

    from alupc.sources import CameraSource

    controller, window, _ = env
    cam = CameraSource({"device_id": "gibt-es-nicht", "name": "Test"})
    cam.device_id, cam.name = "testcam", "Testkamera"
    monkeypatch.setattr(controller, "cameras_on_output", lambda: [cam])
    cam.sink.setVideoFrame(QVideoFrame(_quadrants()))
    img = cam.image()
    assert img is not None and img.size().width() == 200

    def color(x, y):
        return cam.image().pixelColor(x, y).name()

    controller.set_camera_option("testcam", zoom=2.0, x=0.0, y=0.0)  # Ausschnitt oben links
    assert cam.image().width() == 100 and color(50, 25) == "#ff0000"
    opts = controller.config["camera"]["testcam"]
    assert opts["x"] == 0.25 and opts["y"] == 0.25  # am Rand begrenzt und gespeichert
    controller.set_camera_option("testcam", zoom=1.0, mirror=True)
    assert color(10, 10) == "#00ff00"  # gespiegelt: grün jetzt links
    controller.set_camera_option("testcam", mirror=False, rotate=90)
    assert cam.image().width() == 100 and cam.image().height() == 200
    assert color(90, 10) == "#ff0000"  # 90° gedreht: oben links wandert nach oben rechts

    # Leiste im Hauptfenster
    controller.reset_camera("testcam")
    bar = window.camera_bar
    bar.sync()
    assert not bar.isHidden() and bar.title.text() == "Testkamera"
    assert not bar.left.isEnabled()  # ohne Zoom nichts zu verschieben
    bar._zoom_by(2.0)
    assert cam.options()["zoom"] == 2.0 and bar.zoom.value() == 200 and bar.zoom_label.text() == "2,0×"
    assert bar.left.isEnabled()
    bar._pan(1, 0)
    assert cam.options()["x"] > 0.5
    bar._flip()
    assert cam.options()["mirror"] and bar.flip.isChecked()
    x = cam.options()["x"]
    bar._pan(1, 0)  # gespiegelt: „rechts“ im Bild ist links in der Kamera
    assert cam.options()["x"] < x
    bar._rotate()
    assert cam.options()["rotate"] == 90
    controller.camera_zoom(1.25)
    assert cam.options()["zoom"] == 2.5
    controller.run_command("kamera_zoom_aus")
    assert cam.options()["zoom"] == 1.0
    bar._reset()
    assert cam.options()["rotate"] == 0 and not cam.options()["mirror"]
    monkeypatch.undo()
    bar.sync()
    assert bar.isHidden()
    cam.deleteLater()


# ---------------------------------------------------------------- 0.12: AluCast (Handy per Browser)
def _free_tcp_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(method, url, body=None, headers=None, timeout=10):
    """Anfrage in einem Thread schicken und dabei Qt weiterlaufen lassen (Server meldet sich per Signal)."""
    import threading
    import urllib.error
    import urllib.request

    result = {}

    def run():
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                result["status"], result["body"] = r.status, r.read()
        except urllib.error.HTTPError as e:
            result["status"], result["body"] = e.code, e.read()
        except Exception as e:  # noqa: BLE001
            result["status"], result["body"] = 0, str(e).encode()

    t = threading.Thread(target=run)
    t.start()
    assert _until(lambda: not t.is_alive(), timeout + 2)
    pump(10)
    return result["status"], result["body"]


def test_alucast_end_to_end(env, tmp_path, monkeypatch):
    import json

    controller, window, _ = env
    port = _free_tcp_port()
    controller.config["cast"] = {**controller.config["cast"], "port": port, "code": "123456"}
    from alupc import hotspot as hs_mod

    hs = hs_mod.hotspot  # Handy-Steuerung zeigt den WLAN-Code des AluPC-WLANs (hier: so getan, als liefe es)
    monkeypatch.setattr(hs, "running", True)
    monkeypatch.setattr(hs, "ssid", "AluPC")
    monkeypatch.setattr(hs, "password", "abcdefgh23")
    controller.start_cast()
    pump()
    assert controller.cast.running() and controller.content == {"type": "cast"}
    assert controller.config["cast"]["autostart"] is False  # startet nicht ungefragt beim nächsten Mal
    view = controller.output.content
    assert view._qr is not None and view.ssid == "AluPC" and view._qr.width() > 20
    img = view.grab().toImage()
    colors = {img.pixelColor(x, y).name() for x in range(0, img.width(), 8) for y in range(0, img.height(), 8)}
    assert "#ffffff" in colors and "#000000" in colors  # QR-Code ist zu sehen
    pump()
    assert window.t_remote.active and window.t_remote.badge == "LÄUFT"
    base = f"http://127.0.0.1:{port}"
    ok = {"X-AluPC-Code": "123456"}

    status, body = _http("GET", base + "/")
    assert status == 200 and b"AluCast" in body
    assert _http("GET", base + "/api/status")[0] == 403
    status, body = _http("GET", base + "/api/status", headers=ok)
    assert status == 200 and "Handy-Empfang" in json.loads(body)["now"]

    # Foto senden → erscheint auf Monitor 2 und liegt im Ordner „Vom Handy“
    png = tmp_path / "rot.png"
    q = QImage(40, 30, QImage.Format_RGB32)
    q.fill(QColor("#ff0000"))
    q.save(str(png))
    status, body = _http("POST", base + "/api/upload?name=Tafelbild.png", png.read_bytes(),
                         {**ok, "Content-Type": "image/png"})
    assert status == 200, body
    assert _until(lambda: (controller.content or {}).get("type") == "image")
    assert "Vom Handy" in controller.content["path"] and controller.content["path"].endswith("_Tafelbild.png")
    assert _http("POST", base + "/api/upload?name=virus.exe", b"MZ", {**ok, "Content-Type": "x/y"})[0] == 415

    # Text, Befehle
    assert _http("POST", base + "/api/text", json.dumps({"text": "Hallo Klasse"}).encode(), ok)[0] == 200
    assert _until(lambda: (controller.content or {}).get("text") == "Hallo Klasse")
    # Live vom Handy: Buchstabe für Buchstabe, auch leer (alles gelöscht)
    for part in ("H", "Hi", ""):
        assert _http("POST", base + "/api/text", json.dumps({"text": part, "live": True}).encode(), ok)[0] == 200
        assert _until(lambda p=part: (controller.content or {}).get("text") == p)
    assert _http("POST", base + "/api/text", json.dumps({"text": "  "}).encode(), ok)[0] == 400  # ohne live: leer
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "schwarz"}).encode(), ok)[0] == 200
    assert _until(lambda: controller.privacy)
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "sperren"}).encode(), ok)[0] == 400
    assert _http("POST", base + "/api/link", json.dumps({"url": "kein link"}).encode(), ok)[0] == 400

    # Zeichnen mit dem Finger: Strich landet genau dort auf Monitor 2
    controller.laser.clear_strokes()
    for body in ({"phase": "down", "x": 0.25, "y": 0.5, "tool": "stift", "color": "#22c55e"},
                 {"phase": "move", "x": 0.5, "y": 0.5, "tool": "stift", "color": "#22c55e"},
                 {"phase": "up"}):
        assert _http("POST", base + "/api/draw", json.dumps(body).encode(), ok)[0] == 200
    assert _until(lambda: len(controller.laser.strokes) == 1)
    stroke = controller.laser.strokes[0]
    assert stroke["color"] == "#22c55e" and stroke["points"][0] == (0.25, 0.5) and stroke["points"][-1] == (0.5, 0.5)
    assert _http("POST", base + "/api/draw", json.dumps({"phase": "down", "x": 2, "y": 0}).encode(), ok)[0] == 400
    assert _http("POST", base + "/api/draw", json.dumps({"phase": "down", "x": 0.1, "y": 0.1, "tool": "stift",
                                                         "color": "red;x"}).encode(), ok)[0] == 400
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "zeichnung_zurueck"}).encode(), ok)[0] == 200
    assert _until(lambda: not controller.laser.strokes)
    # Touchpad: Zeiger bewegen, Klick (Klick selbst simuliert – im Test keine echte Maus)
    from alupc.platform import keys as keys_mod
    from PySide6.QtGui import QCursor

    clicks = []
    monkeypatch.setattr(keys_mod, "click", lambda b="links": clicks.append(b) or True)
    QCursor.setPos(100, 100)
    start = QCursor.pos()
    assert _http("POST", base + "/api/mouse", json.dumps({"dx": 30, "dy": -10}).encode(), ok)[0] == 200
    assert _http("POST", base + "/api/mouse", json.dumps({"click": "rechts"}).encode(), ok)[0] == 200
    assert _http("POST", base + "/api/mouse", json.dumps({"click": "mitte; rm"}).encode(), ok)[0] == 400
    assert _until(lambda: clicks == ["rechts"])
    moved = QCursor.pos() - start
    assert (moved.x(), moved.y()) in ((30, -10), (0, 0))  # offscreen-Plattform kann den Zeiger evtl. nicht setzen
    # Timer-Vorgabe vom Handy: zeigen und starten
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "timer:180"}).encode(), ok)[0] == 200
    from alupc.timer import clock

    assert _until(lambda: clock.running and 170 < clock.remaining() <= 180)
    assert (controller.content or {}).get("type") == "countdown"
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "timer_stopp"}).encode(), ok)[0] == 200
    assert _until(lambda: not clock.running)
    # „Zum Home-Bildschirm“: Manifest und Symbol ohne Code
    status, body = _http("GET", base + "/manifest.json")
    assert status == 200 and json.loads(body)["display"] == "standalone"
    status, body = _http("GET", base + "/icon.png")
    assert status == 200 and body[:4] == b"\x89PNG"

    # Rechte (Setup → Handy & Kamera): Ausgeschaltetes wird abgelehnt und dem Handy gemeldet
    controller.config["cast"] = {**controller.config["cast"], "allow": {"senden": False, "steuern": False,
                                                                         "live": False, "laser": True}}
    assert _http("POST", base + "/api/text", json.dumps({"text": "x"}).encode(), ok)[0] == 423
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "schwarz"}).encode(), ok)[0] == 423
    assert _http("GET", base + "/api/preview", headers=ok)[0] == 423
    controller._cast_snapshot()
    status, body = _http("GET", base + "/api/status", headers=ok)
    assert status == 200 and json.loads(body)["allow"] == {"senden": False, "steuern": False, "live": False,
                                                            "laser": True}
    controller.config["cast"] = {**controller.config["cast"], "allow": {}}
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "schwarz"}).encode(), ok)[0] == 200

    # Falsche Codes: nach 10 Versuchen 1 Minute gesperrt (auch für den richtigen Code)
    bad = {"X-AluPC-Code": "000000"}
    codes = [_http("GET", base + "/api/status", headers=bad)[0] for _ in range(11)]
    assert codes[:10] == [403] * 10 and codes[10] == 429
    assert _http("GET", base + "/api/status", headers=ok)[0] == 429

    controller.stop_cast()
    assert not controller.cast.running() and controller.config["cast"]["autostart"] is False
    assert _http("GET", base + "/", timeout=2)[0] == 0


def test_handy_page_cards_and_auto_setup(env, tmp_path, monkeypatch):
    from alupc import handy

    controller, window, _ = env
    controller.config["cast"] = {**controller.config["cast"], "port": _free_tcp_port()}
    # Pfeil-Menü jeder Handy-Kachel führt zur Handy-Seite
    window._fill_handy_menu(window.handy_menus["airplay"], "airplay")
    window.handy_menus["airplay"].actions()[-1].trigger()
    pump()
    assert window.handy_window.isVisible()  # eigenes Fenster statt eigener Seite
    page = window.handy_page
    assert list(page.cards) == ["cast", "airplay"]
    assert page.cards["cast"].pill.text_ == "BEREIT" and page.cast_toggle.text() == "Starten"
    from alupc import hotspot as hs_mod

    monkeypatch.setattr(hs_mod.hotspot, "running", True)  # nur mit AluPC-WLAN gibt es einen (WLAN-)Code
    monkeypatch.setattr(hs_mod.hotspot, "ssid", "AluPC")
    monkeypatch.setattr(hs_mod.hotspot, "password", "abcdefgh23")
    page._toggle_cast()
    assert controller.cast.running() and page.cards["cast"].pill.text_ == "LÄUFT"
    assert page.qr.pixmap().width() >= 80
    page._toggle_cast()
    assert not controller.cast.running()
    # Automatisch einrichten: Plan wird ausgeführt, Name wird eindeutig
    ran = []
    monkeypatch.setattr(handy, "setup_plan", lambda cfg: [("UxPlay installieren", ["true"])])
    monkeypatch.setattr(handy, "run_plan", lambda plan, status=None: ran.append(plan) or [])
    controller.config["handy"] = {**controller.config["handy"], "airplay_name": "AluPC", "setup_done": False}
    page.run_setup()
    assert _until(lambda: ran and not page._setup_running, 5)
    assert controller.config["handy"]["airplay_name"] == "AluPC"  # fester Name, nicht mehr „AluPC (Rechner)“
    assert controller.config["handy"]["setup_done"] is True
    assert page.setup_btn.isVisible()  # Plan (nachgestellt) meldet weiter etwas → Knopf bleibt
    menu = window.handy_menus["handy_remote"]
    window._fill_handy_menu(menu, "handy_remote")
    texts = [a.text() for a in menu.actions()]
    assert texts[0] == "QR-Code zeigen" and texts[-1].startswith("Einrichten")
    assert not hasattr(controller, "start_android")


# ---------------------------------------------------------------- 0.12.1: Statuskarte mit Vorschau, Bedienung
def test_status_card_preview_and_stop(env):
    controller, window, _ = env
    window._go(0)
    controller.show_source({"type": "color", "color": "#00ff00"})
    pump()
    assert window.stop_btn.isVisible()
    window._update_preview()
    img = window.status_card.preview.image
    assert img is not None and img.pixelColor(img.width() // 2, img.height() // 2).green() > 200
    window.stop_btn.click()
    pump()
    assert controller.mode == "desktop" and not window.stop_btn.isVisible()
    window._update_preview()
    assert window.status_card.preview.image is None  # Erweitern: Symbol statt Bild
    shown = []
    controller.toggle_pip = lambda: shown.append(True)
    window.status_card.preview.clicked.disconnect()
    window.status_card.preview.clicked.connect(controller.toggle_pip)
    window.status_card.preview.clicked.emit()
    assert shown
    window.t_scenes.activated.emit()  # Kachel „Meine Szenen“ → Szenen-Seite
    assert window.stack.currentIndex() == 1


# ---------------------------------------------------------------- 0.13: Sichern & Sync
def test_sync_setup_and_autosave(env, tmp_path):
    import json

    from alupc import settings_sync as ss

    controller, window, _ = env
    shared = tmp_path / "Windows-C"
    shared.mkdir()
    window._go(2)
    pump()
    controller.config.data["sync"] = {**controller.config.data["sync"], "enabled": True,
                                      "folder": str(ss.folder_for(shared))}
    ss.folder_for(shared).mkdir()
    controller.config["start_page"] = {**controller.config["start_page"], "title": "Hallo Sync"}
    assert controller._sync_timer.isActive()  # Änderung → gleich abgleichen
    controller._sync_timer.stop()
    controller.run_sync()
    data = json.loads(ss.sync_file(ss.folder_for(shared)).read_text())
    assert data["data"]["start_page"]["title"] == "Hallo Sync" and data["rev"] == 1
    # Datei verändert, Prüfsumme passt nicht (halb geschrieben) → wird NIE übernommen, sondern neu geschrieben
    data["data"]["start_page"]["title"] = "Kaputt"
    ss.sync_file(ss.folder_for(shared)).write_text(json.dumps(data))
    controller.run_sync()
    assert controller.config["start_page"]["title"] == "Hallo Sync"
    assert (ss.folder_for(shared) / "alupc-sync-defekt.json").is_file()
    data = json.loads(ss.sync_file(ss.folder_for(shared)).read_text())
    # Das „andere System“ schreibt einen neuen Stand (eigene Kennung, richtige Prüfsumme) → übernommen
    data.update(rev=5, id="anderes-system-1")
    data["data"]["start_page"]["title"] = "Von Windows"
    data["hash"] = ss.payload_hash(data["data"])
    ss.sync_file(ss.folder_for(shared)).write_text(json.dumps(data))
    rebuilt = []
    window.rebuild_start = lambda: rebuilt.append(True)
    controller.run_sync()
    assert controller.config["start_page"]["title"] == "Von Windows" and rebuilt
    assert not controller._sync_timer.isActive()  # Übernehmen löst keinen neuen Abgleich aus


def test_sync_page_fresh_start_and_old_folder_warning(env, tmp_path, monkeypatch):
    """Sync-Seite: zweiter (alter) Sync-Ordner → Warnung; „Neu anfangen …“ fragt nach, löscht alle alten Stände
    und zeigt das Prüf-Ergebnis; danach zeigt die Karte „geprüft ✓, dieses System ist aktuell“."""
    import json
    import time

    from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

    from alupc import settings_sync as ss
    from alupc.ui.sync_page import sync_group

    controller, window, _ = env
    c_drive, d_drive = tmp_path / "C", tmp_path / "D"
    folder, old = c_drive / ss.FOLDER_NAME, d_drive / ss.FOLDER_NAME
    folder.mkdir(parents=True)
    old.mkdir(parents=True)
    (old / ss.FILE_NAME).write_text(json.dumps({"app": "AluPC", "rev": 9, "data": {}}), encoding="utf-8")
    monkeypatch.setattr(ss, "drives", lambda: [str(c_drive), str(d_drive)])
    controller.config.data["sync"] = {**controller.config.data["sync"], "enabled": True, "folder": str(folder)}
    controller._sync_timer.stop()
    controller.run_sync()

    from PySide6.QtWidgets import QWidget

    page = QWidget()  # wie die Setup-Seite: echtes Widget mit config/controller
    page.config, page.controller = controller.config, controller
    box = sync_group(page)
    box.show()
    labels = lambda: [lab.text() for lab in box.findChildren(QLabel) if lab.isVisible()]  # noqa: E731
    end = time.time() + 5
    while time.time() < end and not any("Weitere alte Sync-Ordner" in t for t in labels()):
        pump()
    assert any("Weitere alte Sync-Ordner" in t and str(old) in t for t in labels())
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append("WARN " + a[2]))
    asked = []

    def answer(self):  # Nachfrage: „Neu anfangen“ anklicken
        asked.append(self.text())
        next(b for b in self.buttons() if self.buttonRole(b) == QMessageBox.DestructiveRole).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", answer)
    fresh = next(b for b in box.findChildren(QPushButton) if b.text().startswith("Neu anfangen"))
    fresh.click()
    assert asked and str(old) in asked[0] and "DIESEM System" in asked[0]
    assert shown and shown[0].startswith("Aufgeräumt: 2 Ordner") and "✗" not in shown[0]
    assert not old.exists() and ss.sync_file(folder).is_file()
    end = time.time() + 5
    while time.time() < end and any("Weitere alte Sync-Ordner" in t for t in labels()):
        pump()
    assert not any("Weitere alte Sync-Ordner" in t for t in labels())
    assert any("dieses System ist aktuell" in t for t in labels())
    box.close()


# ---------------------------------------------------------------- 0.13: RGB & Lüfter
def test_hardware_page_rgb_and_fans(env, tmp_path, monkeypatch):
    import sys
    import time

    sys.path.insert(0, str(HERE))
    from fake_openrgb import FakeOpenRGB
    from test_logic import _fake_hwmon

    from alupc.platform import fans

    controller, window, _ = env
    fake = FakeOpenRGB(4)
    hw = _fake_hwmon(tmp_path / "hwmon")
    monkeypatch.setattr(fans, "HWMON", tmp_path / "hwmon")
    controller.config["rgb"] = {**controller.config["rgb"], "port": fake.port, "start_openrgb": False}
    window._go(2)  # RGB & Lüfter steckt jetzt im Setup
    from alupc.ui.setup_page import SetupPage

    setup = window.findChild(SetupPage)
    setup.nav.setCurrentRow([t for _i, t, _s in SetupPage.SECTIONS].index("RGB & Lüfter"))
    pump()
    page = window.findChild(__import__("alupc.ui.hardware_page", fromlist=["HardwarePage"]).HardwarePage)
    assert page is not None
    if sys.platform.startswith("win"):  # Windows: keine Lüfter-Schnittstelle → ehrlicher Hinweis
        assert "FanControl" in page.fan_status.label.text() and not page.pwm_rows
    else:
        assert "nct6798/SYSTIN" in page.temp_bars and page.temp_bars["nct6798/SYSTIN"].value == 41.5
        assert page.fan_labels["nct6798/Lüfter 1"].text() == "812 U/min"
        assert controller.config["fans"]["original"] == {"nct6798/pwm1": 5}  # Automatik-Modus gemerkt
        key, slider, auto = page.pwm_rows["hwmon3/pwm2"]
        slider.setValue(60)
        assert page.fan_request() == "hwmon3/pwm1=auto:5,hwmon3/pwm2=153"
        (hw / "temp1_input").write_text("83000\n")
        page._update_sensors()
        assert page.temp_bars["nct6798/SYSTIN"].value == 83.0
    # RGB: verbinden, Farbe wählen, Gerät abwählen, aus
    controller.rgb.connect_async()
    assert _until(lambda: controller.rgb.connected, 5)
    pump()
    assert "2 Geräte" in page.rgb_status.label.text() and page.devices_box.isVisibleTo(page)
    page._pick_color("#22c55e")
    time.sleep(0.2)
    assert fake.leds(0) == [(0x22, 0xC5, 0x5E)] * 6
    controller.rgb.update_settings(skip=["Tastatur K70"], brightness=50)
    time.sleep(0.2)
    assert fake.leds(0) == [(0x11, 0x62, 0x2F)] * 6 and fake.leds(1) == [(0x22, 0xC5, 0x5E)] * 6
    controller.run_command("rgb_aus")
    time.sleep(0.2)
    assert fake.leds(0) == [(0, 0, 0)] * 6 and page.mode_btns["aus"].isChecked()
    # Farbe folgt Monitor 2
    controller.show_source({"type": "color", "color": "#ff0000"})
    pump()
    controller.rgb.update_settings(mode="monitor2", brightness=100)
    for _ in range(12):
        controller.rgb._ambient_tick()
    time.sleep(0.2)
    r, g, b = fake.leds(0)[0]
    assert r > 200 and g < 40 and b < 40, (r, g, b)
    controller.rgb.shutdown()
    fake.close()


# ---------------------------------------------------------------- 0.14: bessere Handysteuerung
def test_phone_remote_preview_laser_and_flags(env):
    import json

    controller, window, _ = env
    port = _free_tcp_port()
    controller.config["cast"] = {**controller.config["cast"], "port": port, "code": "654321"}
    controller.config.put_scene({"name": "Mathe", "layout": "vollbild", "slots": [{"type": "color", "color": "#00ff00"}]})
    controller.start_cast()
    controller.show_source({"type": "scene", "scene": "Mathe"})
    pump()
    base, ok = f"http://127.0.0.1:{port}", {"X-AluPC-Code": "654321"}
    # Live-Bild: erst nach Abruf erzeugt (sonst keine Rechenzeit verschwenden)
    assert controller.cast.preview == b""
    assert _http("GET", base + "/api/preview", headers=ok)[0] == 204
    def green_preview():  # nach dem Übergang (Einblenden) ist die Szene grün
        controller._cast_tick()
        img = QImage.fromData(controller.cast.preview)
        return not img.isNull() and img.pixelColor(img.width() // 2, img.height() // 2).green() > 200

    assert _until(green_preview, 5)
    status, jpg = _http("GET", base + "/api/preview", headers=ok)
    assert status == 200 and jpg[:2] == b"\xff\xd8"
    assert _http("GET", base + "/api/preview")[0] == 403
    # Status mit aktiver Szene und Zuständen
    s = json.loads(_http("GET", base + "/api/status", headers=ok)[1])
    assert s["scene"] == "Mathe" and s["flags"]["schwarz"] is False and s["timer"]
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "schwarz"}).encode(), ok)[0] == 200
    assert _until(lambda: controller.privacy)
    s = json.loads(_http("GET", base + "/api/status", headers=ok)[1])
    assert s["flags"]["schwarz"] is True
    assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "timer_plus"}).encode(), ok)[0] == 200
    # Laserpointer per Finger
    assert _http("POST", base + "/api/laser", json.dumps({"x": 0.25, "y": 0.5}).encode(), ok)[0] == 200
    assert _until(lambda: controller.laser.point is not None and controller.laser.remote)
    assert abs(controller.laser.point.x() - 0.25 * controller.laser.width()) < 2
    assert _http("POST", base + "/api/laser", json.dumps({"up": True}).encode(), ok)[0] == 200
    assert _until(lambda: controller.laser.point is None and not controller.laser.remote)
    assert _http("POST", base + "/api/laser", json.dumps({"x": 3, "y": 0}).encode(), ok)[0] == 400
    sent = []
    import alupc.platform.keys as keys_mod

    orig = keys_mod.send
    keys_mod.send = lambda name: sent.append(name) or True
    try:
        assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "taste:weiter"}).encode(), ok)[0] == 200
        assert _until(lambda: sent == ["weiter"])
        assert _http("POST", base + "/api/cmd", json.dumps({"cmd": "taste:alt+f4"}).encode(), ok)[0] == 400
    finally:
        keys_mod.send = orig
    assert _http("POST", base + "/api/laser", json.dumps({"x": "a"}).encode(), ok)[0] == 400
    controller.stop_cast()


# ---------------------------------------------------------------- 0.14.1: eigene Kacheln je Handy-Weg
def test_handy_tiles_start_each_way(env, monkeypatch):
    from alupc.startpage import section_of

    controller, window, _ = env
    controller.config["cast"] = {**controller.config["cast"], "port": _free_tcp_port()}
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": ""}
    assert all(section_of(k, {}) == "handy" for k in ("airplay", "handy_remote"))
    messages = []
    controller.message.connect(messages.append)
    window.t_remote.activated.emit()  # Handy-Steuerung: QR-Code auf Monitor 2
    pump()
    assert controller.cast.running() and controller.content == {"type": "cast"}
    assert window.t_remote.badge == "LÄUFT"
    monkeypatch.setattr(controller.airplay, "binary", lambda: None)  # kein Empfänger installiert
    window.t_airplay.activated.emit()
    assert any("Empfänger fehlt" in m for m in messages)
    controller.stop_cast()
    pump()
    assert window.t_remote.badge == ""


# ---------------------------------------------------------------- 0.15: Spiegeln mit Rückfall, Diagnose
def test_mirror_falls_back_to_system_mirror(env, monkeypatch):
    controller, window, _ = env
    calls, msgs = [], []
    controller.message.connect(msgs.append)
    monkeypatch.setattr(controller.display, "available", lambda: True)
    monkeypatch.setattr(controller.display, "mirror", lambda main, out: calls.append((main, out)))
    monkeypatch.setattr(controller, "screens_overlap", lambda: False)
    controller.mirror()
    pump()
    content = controller.output.content
    if content is not None and hasattr(content, "no_signal"):  # Aufnahme läuft (noch) → 4 s ohne Bild nachstellen
        content.frames = 0
        content._no_signal("Die Aufnahme liefert kein Bild.")
    # sonst: Aufnahme ist schon beim Start gescheitert (z. B. Windows im Test) → Rückfall ist schon passiert
    assert _until(lambda: calls, 5)
    assert calls[0] == ("Haupt", "Zweit")
    assert any("spiegelt jetzt über" in m for m in msgs)
    assert controller.mode == "desktop" and controller.desktop_note.startswith("System-Spiegeln")
    controller.mirror()  # kommen Bilder, passiert nichts
    content2 = controller.output.content
    if content2 is not None and hasattr(content2, "no_signal"):
        content2.frames = 3
        content2._no_signal("x")
        assert len(calls) == 1


def test_diagnose_report(env):
    from alupc import diagnose

    controller, window, _ = env
    text = diagnose.report(controller, probe=False)
    assert "Android" not in text
    for part in ("== Monitore ==", "Bildschirm = Zweit", "== AirPlay",
                 "== RGB und Lüfter =="):
        assert part in text, part


# ---------------------------------------------------------------- 0.15: Ersteinrichtung
def test_first_run_wizard(env, monkeypatch):
    from alupc import handy
    from alupc.ui.first_run import FirstRunDialog

    controller, window, _ = env
    ran = []
    monkeypatch.setattr(handy, "setup_plan", lambda cfg: [("UxPlay installieren", ["x"])])
    monkeypatch.setattr(handy, "run_plan", lambda plan, status=None: ran.append(plan) or [])
    monkeypatch.setattr("alupc.diagnose.capture_probe", lambda c, s=3.0: "Methode qt, 0 Bilder in 3 s, KEIN Bild")
    from alupc.platform import autostart

    monkeypatch.setattr(autostart, "set_enabled", lambda on: ran.append(("autostart", on)))
    controller.config["handy"] = {**controller.config["handy"], "airplay_name": "AluPC"}
    controller.config["first_run_done"] = False
    dlg = FirstRunDialog(controller, window)
    dlg.show()
    dlg.start()
    assert _until(lambda: dlg.finished_all, 10)
    states = [row.icon.state for row, _ in dlg.steps]
    assert "run" not in states and "wait" not in states
    assert controller.config["first_run_done"] is True
    # Bildaufnahme liefert nichts → künftig über das Betriebssystem spiegeln
    assert controller.config["output"]["mirror_method"] == "system"
    assert controller.config["handy"]["airplay_name"] == "AluPC"  # fester Name, nicht mehr „AluPC (Rechner)“
    assert ran[0] == [("UxPlay installieren", ["x"])] and ("autostart", True) in ran
    assert dlg.go.text() == "Fertig"
    # Spiegeln nutzt jetzt direkt das System-Spiegeln
    calls = []
    monkeypatch.setattr(controller.display, "available", lambda: True)
    monkeypatch.setattr(controller, "system_mirror", lambda: calls.append(True))
    controller.mirror()
    assert calls
    dlg.close()


# ---------------------------------------------------------------- 0.15.2: Zeichnungen in Bild-in-Bild
def test_drawings_visible_in_pip_and_previews(env):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QImage

    controller, window, _ = env
    controller.show_source({"type": "color", "color": "#00ff00"})
    pump(30)
    laser = controller.laser
    laser.begin_stroke("pen", "#ff0000", 0.05, QPointF(0.1, 0.5))  # dicker roter Strich quer durch die Mitte
    laser.extend_stroke(QPointF(0.9, 0.5))
    laser.end_stroke()
    pump()

    def red_in_middle(img):
        c = img.pixelColor(img.width() // 2, img.height() // 2)
        return c.red() > 180 and c.green() < 120, c.name()

    controller.toggle_pip()
    pump(10)
    controller.pip.refresh()
    ok, col = red_in_middle(controller.pip.view.image())
    assert ok, f"Bild-in-Bild zeigt die Zeichnung nicht ({col})"
    window._go(0)
    window._update_preview()
    ok, col = red_in_middle(window.status_card.preview.image)
    assert ok, f"Vorschau zeigt die Zeichnung nicht ({col})"
    ok, col = red_in_middle(QImage.fromData(controller._preview_jpeg()))
    assert ok, f"Handy-Bild zeigt die Zeichnung nicht ({col})"
    controller.toggle_privacy()  # bei „Schwarz“ keine Zeichnung zeigen
    pump(10)
    controller.pip.refresh()
    assert not red_in_middle(controller.pip.view.image())[0]
    controller.toggle_privacy()
    controller.toggle_pip()
    laser.clear_strokes()


def test_start_content_and_default_camera(env, monkeypatch):
    """Setup: was Monitor 2 beim Start zeigt (auch eine Szene) und welche Kamera die Kachel sofort startet."""
    controller, window, _ = env
    from alupc.scenes import new_scene

    scene = new_scene("Begrüßung")
    controller.config["scenes"] = [*controller.config["scenes"], scene]
    controller.config["start_content"] = "scene:Begrüßung"
    controller.restore_last()
    pump()
    assert controller.content == {"type": "scene", "scene": "Begrüßung"}
    controller.config["start_content"] = "cast"
    controller.restore_last()
    pump()
    assert controller.content == {"type": "cast"}
    controller.extend()
    controller.config["start_content"] = "none"
    controller.restore_last()
    assert controller.mode == "desktop"
    # alte Einstellung „letzten Inhalt nicht wiederherstellen“ wird als „Nichts“ verstanden
    controller.config["start_content"] = "last"
    controller.config["restore_last_content"] = False
    assert controller.start_content_setting() == "none"
    controller.config["restore_last_content"] = True
    # Einstellungen: Auswahl enthält Szenen; Wechsel speichert
    window._go(2)
    window.setup._fill_start_combo()
    combo = window.setup.start_combo
    idx = combo.findData("scene:Begrüßung")
    assert idx >= 0
    combo.setCurrentIndex(idx)
    assert controller.config["start_content"] == "scene:Begrüßung"
    # Kamera: vorausgewählt = gespeicherte, sonst erste; Kachel-Klick startet sofort
    class Dev:
        def __init__(self, i, name):
            self._i, self._n = i, name

        def id(self):
            return self._i

        def description(self):
            return self._n

    import alupc.sources as sources

    devs = [Dev(b"cam-a", "Webcam A"), Dev(b"cam-b", "Dokumentenkamera")]
    monkeypatch.setattr("PySide6.QtMultimedia.QMediaDevices.videoInputs", staticmethod(lambda: devs))
    monkeypatch.setattr(sources, "camera_id", lambda d: bytes(d.id()).decode())
    shown = []
    monkeypatch.setattr(controller, "show_source", lambda cfg, *a, **k: shown.append(cfg))
    assert controller.default_camera()["name"] == "Webcam A"  # nichts gewählt → erste
    controller.config["default_camera"] = "cam-b"
    window._camera_clicked()
    assert shown[-1]["name"] == "Dokumentenkamera"
    controller.config["default_camera"] = "gibt-es-nicht"
    assert controller.default_camera()["name"] == "Webcam A"


# ---------------------------------------------------------------- 0.19: Schoner, Seiten, Vorlagen
def test_new_screensavers_and_design_pages(env):
    """Alle neuen Schoner und gestalteten Seiten laufen und zeichnen etwas (nicht nur Schwarz)."""
    from alupc import screensaver_art
    from alupc.screens import DESIGNS, SCENE_TEMPLATES, build_template, design_defaults, render_preview, wifi_payload
    from alupc.screensaver import STYLES, ScreensaverView

    controller, window, _ = env
    from alupc.screensaver import STYLE_GROUPS
    grouped = [k for keys in STYLE_GROUPS.values() for k in keys]
    assert sorted(grouped) == sorted(STYLES)  # jeder Stil genau einmal in einer Gruppe
    for style in screensaver_art.CLASSES:
        assert style in STYLES
        v = ScreensaverView({"style": style, "text": "A|B"}, controller.config.get_scene)
        v.resize(320, 180)
        colors: set = set()
        for _ in range(6):  # zufällige Stile (z. B. Feuerwerk): über mehrere Momente sammeln, nicht nur einen
            for _ in range(20):
                v._last_tick -= 0.05
                v._tick()
            img = v.grab().toImage()
            colors |= {img.pixelColor(x, y).name() for x in range(0, 320, 8) for y in range(0, 180, 6)}
            if len(colors) > 3:
                break
        assert len(colors) > 3, style
        v.stop()
    for key in DESIGNS:
        img = render_preview(design_defaults(key), 320, 180)
        # dicht abtasten: „glitch“ ist zufällig und hat manchmal fast nur Hintergrund + Schrift (Windows-CI 0.128)
        colors = {img.pixelColor(x, y).name() for x in range(0, 320, 2) for y in range(0, 180, 2)}
        assert len(colors) >= 3, key  # flache Felder: Grund + Zahl/Schrift + Akzent reichen (Windows: wenig Kantenglättung)
    assert wifi_payload("Mein;Netz", "p:w") == r"WIFI:T:WPA;S:Mein\;Netz;P:p\:w;;"
    assert wifi_payload("Offen", "") == "WIFI:T:nopass;S:Offen;P:;;"
    for key in SCENE_TEMPLATES:
        scene = build_template(key, {"name": f"T {key}", "title": "Hallo", "text": "Welt", "minutes": 3})
        assert scene["name"] == f"T {key}" and scene["slots"] and all(scene["slots"])
    # Seite direkt auf Monitor 2
    controller.show_source({**design_defaults("pause"), "minutes": 1})
    pump()
    assert controller.describe().startswith("Pause")


def test_new_scene_from_template(env):
    """Vorlagen gibt es nur beim Anlegen einer Szene: Vorlage wählen → Editor → erst Speichern legt sie an."""
    controller, window, _ = env
    assert "vorlagen" not in window.tiles  # keine Vorlagen-Kachel mehr auf der Startseite
    before = controller.config.scene_names()
    window.new_scene()
    dlg = window.templates_dialog
    pump()
    assert dlg.list.item(0).data(Qt.UserRole) == ("leer", "")  # „Leer“ steht vorne
    assert not hasattr(dlg, "show_btn")  # kein „Jetzt zeigen“ – Vorlagen landen nicht direkt auf Monitor 2
    row = next(i for i in range(dlg.list.count()) if dlg.list.item(i).data(Qt.UserRole) == ("scene", "pause"))
    dlg.list.setCurrentRow(row)
    dlg.name.setText("Kaffeepause")
    dlg.minutes.setValue(15)
    editor = dlg.save_scene()
    pump()
    assert editor.name_edit.text() == "Kaffeepause" and controller.config.scene_names() == before
    editor._save()
    pump()
    scene = controller.config.get_scene("Kaffeepause")
    assert scene["slots"][0]["design"] == "pause" and scene["slots"][0]["minutes"] == 15
    # Karte als Szene; Name schon vergeben → eindeutig
    window.new_scene()
    dlg = window.templates_dialog
    pump()
    row = next(i for i in range(dlg.list.count()) if dlg.list.item(i).data(Qt.UserRole) == ("design", "neon"))
    dlg.list.setCurrentRow(row)
    dlg.name.setText("Kaffeepause")
    editor2 = dlg.save_scene()
    assert editor2.name_edit.text() == "Kaffeepause 2" and editor2.scene["slots"][0]["design"] == "neon"
    editor2.reject()
    # Leer → normaler Editor
    dlg.list.setCurrentRow(0)
    editor3 = dlg.save_scene()
    assert editor3.windowTitle() == "Neue Szene" and not any(editor3.scene["slots"])
    editor3.reject()
    dlg.close()

def test_agenda_step(env):
    from alupc.screens import build_template, design_defaults

    controller, window, _ = env
    controller.show_source({**design_defaults("ablauf"), "text": "A\nB\nC", "current": 1})
    pump()
    controller.run_command("ablauf_weiter")
    controller.run_command("ablauf_weiter")
    assert controller.agenda_sources()[0].cfg["current"] == 3 and controller.content["current"] == 3
    for _ in range(5):
        controller.run_command("ablauf_weiter")
    assert controller.agenda_sources()[0].cfg["current"] == 4  # alles erledigt, nicht weiter
    controller._cast_snapshot()
    assert controller.cast.snapshot["ablauf"] is True
    # auch in einer Szene (Ablauf + Uhr + Hinweis)
    controller.config.put_scene(build_template("ablauf_uhr", {"name": "Plan", "text": "X\nY"}))
    controller.show_source({"type": "scene", "scene": "Plan"})
    pump()
    controller.run_command("ablauf_zurueck")
    controller.run_command("ablauf_weiter")
    assert controller.agenda_sources()[0].cfg["current"] == 2
    messages = []
    controller.message.connect(messages.append)
    controller.show_source({"type": "clock"})
    controller.run_command("ablauf_weiter")
    assert any("nichts zum Weiterschalten" in m for m in messages)


def test_step_pages_and_animation(env):
    """Tabelle, Termine, Quiz, Siegerehrung, Pro & Contra, Gäste: weiterschalten mit Animation und Anzeige."""
    import time as _time

    from alupc.screens import (DesignSource, design_defaults, render_preview, step_design, step_label,
                               step_range, step_value)
    from alupc.screens_more import count_up

    controller, window, _ = env
    cases = {"tabelle": (0, 4), "termine": (0, 3), "abstimmung": (0, 4), "sieger": (3, 3),
             "zweispaltig": (3, 3), "willkommen_gast": (1, 3), "ablauf": (1, 6)}
    for design, (start, hi) in cases.items():
        cfg = design_defaults(design)
        assert step_range(cfg)[1] == hi, design
        assert step_value(cfg) == start, design
        assert step_design(cfg, 1) == (start < hi)
        assert step_label(cfg)
    assert step_range(design_defaults("willkommen")) is None
    # Siegerehrung: zurück = Platz 1 wieder verdeckt, bis 0; mehr als 3 geht nicht
    cfg = design_defaults("sieger")
    for _ in range(5):
        step_design(cfg, -1)
    assert step_value(cfg) == 0 and step_label(cfg) == "0/3 aufgedeckt"
    # auf Monitor 2: Tabelle weiterschalten, Seitenleiste zeigt den Stand
    controller.show_source(design_defaults("tabelle"))
    pump()
    controller.step_page(1)
    controller.step_page(1)
    src = controller.step_sources()[0]
    assert isinstance(src, DesignSource) and src.cfg["current"] == 2 and src.anim.isActive()
    assert controller.step_label() == "Punkt 2/4"
    assert window.step_row.isVisibleTo(window) and window.step_text.text() == "Punkt 2/4"
    window.step_prev.click()
    assert src.cfg["current"] == 1
    controller._cast_snapshot()
    assert controller.cast.snapshot["punkt"] == "Punkt 1/4"
    # Animation: kurz nach dem Start noch unsichtbar, danach fertig; Vorschau immer fertig gezeichnet
    for design in cases:
        cfg = {**design_defaults(design), "_intro": _time.time()}
        early = render_preview(cfg, 320, 180)
        cfg["_intro"] = _time.time() - 5
        late = render_preview(cfg, 320, 180)
        assert early != late, design
        assert render_preview(design_defaults(design), 320, 180) == late or design in ("sieger",), design
    assert count_up("1.250 €", 0.4) == "500 €" and count_up("1.250 €", 1) == "1.250 €"
    assert count_up("3,5 km", 0.5) == "1,8 km" and count_up("Platz 7", 0.1) == "Platz 7"
    controller.show_source({"type": "clock"})
    pump()
    assert not window.step_row.isVisibleTo(window)


def test_many_templates_render_and_filter(env):
    """Alle Seiten und Szenen-Vorlagen: zeichnen etwas, haben Kategorie; Filter und Suche im Dialog."""
    import time as _time

    from alupc.screens import (CATEGORIES, DESIGNS, SCENE_TEMPLATES, TEMPLATE_CATEGORIES, build_template,
                               design_defaults, render_preview)
    from alupc.screens_more import _parse_target

    controller, window, _ = env
    assert len(DESIGNS) >= 30 and len(SCENE_TEMPLATES) >= 22
    # keine Lehrer-/Schulvorlagen mehr; alte Szenen mit entfernten Vorlagen zeigen trotzdem Titel und Text
    for removed in ("aufgabe", "regeln", "gruppen", "hausaufgaben", "stimmung", "tuerschild", "speiseplan", "ruhe"):
        assert removed not in DESIGNS
        img = render_preview({"type": "design", "design": removed, "title": "Alt", "text": "Szene"}, 320, 180)
        # flaches Design (keine Verläufe) + Windows-Schrift ohne viel Kantenglättung: Grund + mind. 2 Farben = gezeichnet
        assert len({img.pixelColor(x, y).name() for x in range(0, 320, 2) for y in range(0, 180, 2)}) >= 3
    for key in ("neon", "glitch", "synthwave", "poster", "nowplaying", "live", "versus", "linkkarte", "glas"):
        assert key in DESIGNS
    for key in DESIGNS:
        assert CATEGORIES[key]
        img = render_preview(design_defaults(key), 320, 180)
        # fein abtasten: auf Windows (andere Schrift, weniger Kantenglättung) trifft ein 8-px-Raster sonst nur Flächen
        assert len({img.pixelColor(x, y).name() for x in range(0, 320, 2) for y in range(0, 180, 2)}) >= 3, key
    for key in SCENE_TEMPLATES:
        assert TEMPLATE_CATEGORIES[key]
        scene = build_template(key, {})
        controller.config.put_scene(scene)
        controller.show_source({"type": "scene", "scene": scene["name"]})
        pump(5)
    now = _time.mktime((2026, 5, 4, 10, 0, 0, 0, 0, -1))
    assert _parse_target("18:00", now) - now == 8 * 3600
    assert _parse_target("09:00", now) - now == 23 * 3600  # schon vorbei → morgen
    assert _parse_target("24.12.2026 18:00", now) > now
    assert _parse_target("Quatsch", now) is None
    window.new_scene()
    dlg = window.templates_dialog
    pump()
    from alupc.screens import CATEGORY_NAMES
    from alupc.ui.templates_dialog import HEADER
    total = sum(not dlg.list.item(i).isHidden() for i in range(dlg.list.count()))
    heads = [dlg.list.item(i).data(Qt.UserRole + 1) for i in range(dlg.list.count())
             if dlg.list.item(i).data(Qt.UserRole) == HEADER]
    assert heads == CATEGORY_NAMES  # bei „Alle“: nach Kategorie geordnet, je eine Überschrift
    assert total == len(DESIGNS) + len(SCENE_TEMPLATES) + 1 + len(heads)  # + „Leer“
    dlg.search.setText("pause")
    visible = [dlg.list.item(i).data(Qt.UserRole) for i in range(dlg.list.count()) if not dlg.list.item(i).isHidden()]
    assert ("design", "pause") in visible and ("scene", "pause") in visible and len(visible) < total
    assert HEADER not in visible  # Überschriften nur ohne Suche
    dlg.search.clear()
    dlg.cat_group.button(1).click()  # erste Kategorie
    visible = [dlg.list.item(i) for i in range(dlg.list.count()) if not dlg.list.item(i).isHidden()]
    assert 1 < len(visible) < total and not dlg.list.currentItem().isHidden()
    assert all(v.data(Qt.UserRole) != HEADER for v in visible)
    dlg.close()


def test_small_window_and_same_font(env):
    """Kleines Fenster: schmale Seitenleiste, Kacheln einspaltig; Schrift „Inter“ ist überall dieselbe."""
    from PySide6.QtWidgets import QApplication

    from alupc.ui import theme

    controller, window, _ = env
    assert theme.load_fonts(QApplication.instance())  # wie beim Start von AluPC (app.py → theme.apply)
    assert QApplication.font().family() == "Inter"  # mitgeliefert – gleich unter Windows und Linux
    window.show()
    window.resize(1200, 800)
    pump()
    assert window.sidebar.width() == 224 and not window.compact
    window.resize(560, 640)
    pump(20)
    assert window.compact and window.sidebar.width() == 76
    assert all(b.compact for b in window.nav_group.buttons())
    grid = window.section_grids["anzeigen"]
    assert grid._cols <= 2 and grid.width() <= window.width()  # nichts ragt rechts heraus (Karten sind schmal)
    window._go(2)
    pump()
    assert window.setup.nav.width() == 64
    window.resize(1200, 800)
    pump(20)
    assert not window.compact and window.setup.nav.width() == 230
    window._go(0)


# ---------------------------------------------------------------- 0.25: AirPlay ohne iPhone
def test_airplay_idle_styles(env):
    """Ohne iPhone: „AirPlay bereit“ (fast nur Schwarz), ganz schwarz oder die ausführliche Anleitung."""
    from PySide6.QtWidgets import QWidget

    from alupc.sources import paint_airplay_waiting

    class View(QWidget):
        style = "bereit"

        def paintEvent(self, _e):
            paint_airplay_waiting(self, "AluPC", "1234", self.style)

    from alupc import stage

    v = View()
    v.resize(640, 360)

    def share(style, limit):
        v.style = style
        img = v.grab().toImage()
        px = [img.pixelColor(x, y) for x in range(0, 640, 4) for y in range(0, 360, 4)]
        return sum(c.lightness() < limit for c in px) / len(px)

    assert share("schwarz", 8) == 1.0
    # „bereit“: ruhige, dunkle Bühne (wie alle Publikums-Anzeigen) mit etwas Text und Symbol
    assert 0.9 < share("bereit", 40) < 1.0
    assert share("anleitung", 8) < 0.5
    stage.configure("schwarz")  # Publikum: reines Schwarz → auch „bereit“ fast nur Schwarz
    try:
        assert 0.9 < share("bereit", 8) < 1.0
    finally:
        stage.configure("buehne")


def test_airplay_rename_while_running(env, tmp_path, monkeypatch):
    """Name ändern, während AirPlay läuft: UxPlay startet mit dem neuen Namen neu, AluPC sucht das
    iPhone-Fenster unter dem neuen Titel, „Randlos“ aus → normal verschieben; danach stoppt UxPlay wieder."""
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("Fake-Programm ist ein Shell-Skript")
    controller, window, _ = env
    uxplay, log = _fake_uxplay(tmp_path, False)
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": uxplay, "pin": "",
                                  "airplay_name": "Alt"}
    from alupc.platform.base import WindowInfo

    moved, open_windows, follow_calls = [], [], []
    monkeypatch.setattr(controller.windows, "follow_windows", lambda *a: follow_calls.append(a))
    monkeypatch.setattr(controller.windows, "list_windows", lambda: list(open_windows))
    monkeypatch.setattr(controller.windows, "move_window",
                        lambda wid, out, rect, full=False: moved.append((wid, full)))
    controller.start_airplay()
    pump()
    assert _until(lambda: controller.airplay.running() and log.exists() and "Alt" in log.read_text(), 5)
    assert controller.airplay.running_settings()["airplay_name"] == "Alt"
    log.unlink()
    assert controller.airplay.update_settings(airplay_name="Neu Name")  # läuft → Neustart
    assert _until(lambda: log.exists() and "Neu Name" in log.read_text(), 5)
    assert controller.airplay.running_settings()["airplay_name"] == "Neu Name"
    assert "Neu Name" in follow_calls[-1][0]  # sucht das Fenster jetzt unter dem neuen Titel
    open_windows.append(WindowInfo(id="0x9", title="Neu Name", app="irgendwas"))
    controller._follow_timer.timeout.emit()
    assert moved and moved[-1][0] == "0x9"
    # Randlos aus: KDE-Skript bekommt fullscreen=False
    controller.airplay.update_settings(airplay_borderless=False)
    assert follow_calls[-1][3] is False
    # Quelle weg → UxPlay beendet sich (Nutzerzahl stimmt nach dem Neustart)
    controller.show_source({"type": "clock"})
    pump()
    assert _until(lambda: not controller.airplay.running(), 5)


def test_text_on_pc_like_phone(env):
    """Kachel „Text“: wie am Handy – eintippen, Anzeigen, steht auf Monitor 2; letzte Texte gemerkt."""
    controller, window, _ = env
    from alupc.startpage import ordered_keys

    assert "text" in ordered_keys(controller.config["start_page"])
    window.t_text.activated.emit()
    pump()
    dlg = window.text_dialog
    dlg.text.setPlainText("  Gleich geht's los!  ")
    dlg.show_btn.click()
    pump()
    assert controller.content == {"type": "text", "text": "Gleich geht's los!"} and controller.mode == "content"
    assert controller.config["recent_texts"][0] == "Gleich geht's los!"
    assert window.t_text.active
    # vom Handy gesendet → landet auch in „Zuletzt“, doppelte nur einmal
    controller._cast_request({"kind": "text", "text": "Pause 10 min"})
    controller.show_text("Gleich geht's los!")
    assert controller.config["recent_texts"][:2] == ["Gleich geht's los!", "Pause 10 min"]
    window.open_text_dialog()
    pump()
    dlg = window.text_dialog
    assert dlg.text.toPlainText() == "Gleich geht's los!"  # läuft gerade → vorbefüllt
    assert dlg.recent_box.isVisibleTo(dlg)
    dlg.text.clear()
    dlg.show_text()  # leer → nichts passiert
    assert controller.content["text"] == "Gleich geht's los!"
    dlg.reject()
    window._fill_text_menu(window.text_menu)
    assert any("Pause 10 min" in a.text() for a in window.text_menu.actions())


def test_animated_template_filter(env):
    """„Neue Szene“: Filter „Animiert“ zeigt nur Vorlagen mit dauerhafter Bewegung (Seiten und Szenen)."""
    from alupc.screens import CATEGORY_NAMES, animated_designs, is_animated_template

    controller, window, _ = env
    moving = animated_designs()
    assert {"laufschrift", "neon", "glitch", "synthwave", "geburtstag", "nowplaying"} <= moving
    assert not {"willkommen", "wlan", "tabelle", "pause"} & moving  # nur Uhr/Restzeit zählt nicht
    assert is_animated_template("scene", "partynacht") and not is_animated_template("scene", "gaeste_wlan")
    window.new_scene()
    pump()
    dlg = window.templates_dialog
    dlg.cat_group.button(len(CATEGORY_NAMES) + 1).click()  # „✦ Animiert“
    visible = [dlg.list.item(i).data(Qt.UserRole) for i in range(dlg.list.count()) if not dlg.list.item(i).isHidden()]
    assert ("design", "neon") in visible and ("scene", "partynacht") in visible
    assert ("design", "wlan") not in visible and ("scene", "gaeste_wlan") not in visible
    assert all(is_animated_template(*v) for v in visible if v != ("leer", ""))
    dlg.cat_group.button(0).click()
    dlg.search.setText("animiert")
    found = [dlg.list.item(i).data(Qt.UserRole) for i in range(dlg.list.count()) if not dlg.list.item(i).isHidden()]
    assert ("design", "glitch") in found and ("design", "wlan") not in found
    dlg.close()


def test_tray_panel(env):
    """Schnellfenster der Taskleiste: Zustand, Schalter, Modi, Szenen, Weiterschalten, schließen."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    from alupc.screens import build_template, design_defaults

    controller, window, _ = env
    controller.config.put_scene(build_template("partynacht", {"name": "Party"}))
    controller.show_source(design_defaults("ablauf"))
    pump()
    window.open_tray_panel()
    pump()
    panel = window.tray_panel
    assert panel.isVisible()
    assert panel.step_box.isVisibleTo(panel) and "Punkt 1/" in panel.step_label.text()
    panel.step_next.click()
    assert controller.step_label().startswith("Punkt 2/")
    panel.toggles["freeze"].click()
    pump()
    assert controller.frozen and panel.toggles["freeze"].isChecked() and panel.isVisible()  # Schalter: bleibt offen
    panel.toggles["freeze"].click()
    assert not controller.frozen
    scene_buttons = [panel.scene_grid.itemAt(i).widget() for i in range(panel.scene_grid.count())]
    party = next(b for b in scene_buttons if b.text() == "Party")
    party.click()
    pump()
    assert controller.content == {"type": "scene", "scene": "Party"} and not panel.isVisible()  # Aktion: zu
    window.open_tray_panel()
    pump()
    assert any(b.text() == "Party" and b.isChecked() for b in
               (panel.scene_grid.itemAt(i).widget() for i in range(panel.scene_grid.count())))
    panel.modes["text"].click()
    pump()
    assert not panel.isVisible() and window.text_dialog.isVisible()
    window.text_dialog.reject()
    window.open_tray_panel()
    pump()
    QApplication.sendEvent(panel, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not panel.isVisible()
    window.open_tray_panel()
    window.open_tray_panel()  # nochmal aufs Symbol = zu
    assert not panel.isVisible()


def test_airplay_always_ready_takes_over(env, tmp_path, monkeypatch):
    """„Immer bereit“: fremder UxPlay-Autostart (Standardname, kein Code) wird abgeschaltet, AluPCs UxPlay läuft
    im Hintergrund mit Name/Code; meldet UxPlay eine Verbindung, zeigt Monitor 2 das iPhone."""
    import sys

    from alupc import handy

    if sys.platform.startswith("win"):
        pytest.skip("Fake-Programm ist ein Shell-Skript")
    controller, window, _ = env
    auto = tmp_path / "xdg" / "autostart"
    auto.mkdir(parents=True)
    (auto / "uxplay.desktop").write_text("[Desktop Entry]\nExec=uxplay -n Irgendwas\n")
    (auto / "anderes.desktop").write_text("[Desktop Entry]\nExec=firefox\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(handy.shutil, "which", lambda name: None if name == "systemctl" else "/usr/bin/" + name)
    assert [a[0] for a in handy.uxplay_autostarts()] == ["datei"]
    uxplay, log = _fake_uxplay(tmp_path, False)
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": uxplay, "pin": "4711",
                                  "airplay_name": "Mein Beamer", "airplay_always": True}
    msgs = []
    controller.message.connect(msgs.append)
    controller.airplay_background()
    assert not (auto / "uxplay.desktop").exists() and (auto / "uxplay.desktop.aus-durch-AluPC").exists()
    assert (auto / "anderes.desktop").exists()  # nur UxPlay-Autostarts
    assert any("Autostart aus" in m for m in msgs)
    assert _until(lambda: controller.airplay.running() and log.exists(), 5)
    assert "-n Mein Beamer" in log.read_text() and "-pin 4711" in log.read_text()
    assert not controller._airplay_shown()  # läuft im Hintergrund, Monitor 2 bleibt, wie er ist
    controller.show_source({"type": "clock"})
    pump()
    assert controller.airplay.running()  # Quellwechsel beendet den Hintergrund-Empfang nicht
    controller.airplay._read_line = None
    controller.airplay.connected.emit()  # UxPlay meldet: iPhone verbindet sich
    pump()
    assert controller._airplay_shown()
    controller.show_source({"type": "clock"})
    pump()
    assert _until(lambda: True, 2) and controller.airplay.running()
    controller.airplay_background(False)
    assert _until(lambda: not controller.airplay.running(), 5), (controller.airplay.users,
                                                                 controller.airplay._background)


def test_uxplay_connect_hint_parsing(tmp_path):
    from alupc import handy

    for line in ("Open connections: 1", "Accepted IPv4 client on socket 23, port 7000"):
        assert any(k in line.lower() for k in handy.CONNECT_HINTS)
    assert not any(k in "Initialized server socket(s)".lower() for k in handy.CONNECT_HINTS)


def test_live_text_pc(env):
    """Text anzeigen, Live-Modus: jeder Buchstabe sofort auf Monitor 2 – dieselbe Quelle, kein Neuaufbau."""
    from alupc.sources import TextSource

    controller, window, _ = env
    window.open_text_dialog()
    pump()
    dlg = window.text_dialog
    dlg.live.setChecked(True)
    assert dlg.show_btn.text() == "Fertig" and controller.config["text_live"] is True
    dlg.text.setPlainText("H")
    assert _until(lambda: (controller.content or {}).get("text") == "H", 2)
    src = controller.output.content
    assert isinstance(src, TextSource) and src.text() == "H"
    for part in ("Ha", "Hal", "Hallo"):
        dlg.text.setPlainText(part)
        assert _until(lambda p=part: src.text() == p, 2)
    assert controller.output.content is src  # nur Text ausgetauscht, nicht neu angelegt (kein Flackern)
    dlg.show_btn.click()  # „Fertig“
    pump()
    assert controller.config["recent_texts"][0] == "Hallo" and not dlg.isVisible()
    # nächster Aufruf merkt sich „Live“
    window.open_text_dialog()
    pump()
    assert window.text_dialog.live.isChecked()
    window.text_dialog.live.setChecked(False)
    window.text_dialog.reject()


def test_animations_run_and_settle(env, monkeypatch):
    """Mit Animationen: Seitenwechsel, Bereich ein-/ausklappen, Schnellfenster – am Ende sichtbar, ohne
    übrig gebliebene Grafikeffekte, eingeklappt wirklich zu."""
    import time as _time

    monkeypatch.setenv("ALUPC_NO_ANIMATION", "0")
    controller, window, _ = env

    def settle(sec=0.8):
        end = _time.time() + sec
        while _time.time() < end:
            pump(1)
            _time.sleep(0.02)

    window._intro_tiles()
    window._go(1)
    assert window.stack.currentWidget().graphicsEffect() is not None  # blendet gerade ein
    settle()
    assert window.stack.currentWidget().graphicsEffect() is None
    window._go(0)
    settle()
    sid = next(iter(window.section_grids))
    grid = window.section_grids[sid]
    window._fold_section(sid, True)
    settle()
    assert not grid.isVisible()
    window._fold_section(sid, False)
    settle()
    assert grid.isVisible() and grid.maximumHeight() > 10000 and grid.height() > 50
    assert all(w.graphicsEffect() is None for w in grid.items)
    window.open_tray_panel()
    settle(0.5)
    assert window.tray_panel.isVisible() and window.tray_panel.windowOpacity() > 0.99
    window.tray_panel.hide()


def test_wayland_mirror_without_kwin_uses_system_mirror(env, monkeypatch):
    """KDE/Wayland ohne KWin-Freigabe, AluPC als X11-Programm: die X11-Aufnahme sähe keine Fenster →
    gar nicht erst versuchen, sondern KDE selbst spiegeln lassen (zeigt alle Fenster)."""
    from alupc import sources
    from alupc.platform import linux_display

    controller, window, _ = env
    calls = []
    monkeypatch.setattr(linux_display, "is_wayland", lambda: True)
    monkeypatch.setattr(sources, "_kwin_allowed", lambda name: False)

    class FakeGui:
        @staticmethod
        def platformName():
            return "xcb"

        @staticmethod
        def primaryScreen():
            return QApplication.primaryScreen()

        @staticmethod
        def screens():
            return QApplication.screens()

    monkeypatch.setattr(sources, "QGuiApplication", FakeGui)
    monkeypatch.setattr(controller.display, "available", lambda: True)
    monkeypatch.setattr(controller.display, "mirror", lambda main, out: calls.append((main, out)))
    monkeypatch.setattr(controller, "screens_overlap", lambda: False)
    controller.mirror()
    assert _until(lambda: calls, 3)
    assert controller.mode == "desktop" and controller.desktop_note.startswith("System-Spiegeln")


def test_kwin_window_mirror_keeps_window(env, monkeypatch):
    """KDE/Wayland: Programm wird über KWin als KOPIE gezeigt (nicht verschoben), findet es nach dem Schließen
    wieder; das Programm-Fenster bietet die Wayland-Fenster zum Spiegeln an."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QColor, QImage

    from alupc import sources
    from alupc.platform import kwin_capture
    from alupc.ui import program_dialog

    controller, window, _ = env
    wins = [{"id": "{abc}", "title": "Präsentation – LibreOffice Impress", "app": "libreoffice", "minimized": False},
            {"id": "{def}", "title": "AluPC", "app": "alupc", "minimized": False}]
    feeds = []

    class FakeFeed(QObject):
        frame = Signal(QImage)
        failed = Signal(str)

        def __init__(self, handle, fps, cursor=False, parent=None, window=False):
            super().__init__(parent)
            self.handle, self.window, self.started, self.stopped = handle, window, False, False
            feeds.append(self)

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def frame_taken(self):
            pass

    monkeypatch.setattr(sources, "kwin_window_mode", lambda: True)
    monkeypatch.setattr(kwin_capture, "window_list", lambda timeout=3.0: list(wins))
    monkeypatch.setattr(kwin_capture, "KWinScreenFeed", FakeFeed)
    progs = program_dialog.capture_programs(None)
    assert [p.title for p in progs][0].startswith("Präsentation")
    controller.show_source({"type": "window", "title": "Präsentation – LibreOffice Impress"})
    pump()
    src = controller.output.content
    assert isinstance(src, sources.KWinWindowSource)
    assert feeds[-1].handle == "{abc}" and feeds[-1].window and feeds[-1].started
    img = QImage(64, 36, QImage.Format_RGB32)
    img.fill(QColor("#ff0000"))
    feeds[-1].frame.emit(img)
    assert src.state == "live" and src._image is not None
    # Fenster zu → neu suchen; gleiches Programm mit neuem Titel (anderes Dokument) wird wiedergefunden
    wins[0] = {"id": "{xyz}", "title": "Andere Datei – LibreOffice Impress", "app": "libreoffice", "minimized": False}
    feeds[-1].failed.emit("weg")
    src._attach()
    assert feeds[-1].handle == "{xyz}"
    controller.show_source({"type": "clock"})
    pump()
    assert feeds[-1].stopped


def test_website_plays_sound_without_click():
    """Auf Monitor 2 klickt niemand – Websites (YouTube) müssen trotzdem mit Ton abspielen dürfen."""
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from alupc.sources import allow_autoplay

    view = QWebEngineView()
    allow_autoplay(view)
    assert not view.settings().testAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture)


def test_website_fullscreen_button_works():
    """Vollbild-Knopf auf Websites (YouTube: „Vollbildmodus nicht verfügbar“, Fehler bis 0.55): die Seite darf
    Vollbild – echter Klick auf einen Knopf, der requestFullscreen() aufruft."""
    from PySide6.QtCore import QPoint, QUrl
    from PySide6.QtTest import QTest
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from alupc.sources import allow_fullscreen

    view = QWebEngineView()
    allow_fullscreen(view)
    view.resize(400, 300)
    view.show()
    html = ("<html><body style='margin:0'><button id=b style='width:400px;height:300px' "
            "onclick='document.getElementById(\"v\").requestFullscreen()'>Vollbild</button>"
            "<div id=v>Video</div></body></html>")
    loaded = []
    view.loadFinished.connect(loaded.append)
    view.setHtml(html, QUrl("https://example.org/"))
    assert _until(lambda: loaded, 15)

    def js(code):
        out = []
        view.page().runJavaScript(code, 0, out.append)
        assert _until(lambda: out, 5)
        return out[0]

    assert js("document.fullscreenEnabled") is True  # das prüft YouTube
    requested = []
    view.page().fullScreenRequested.connect(lambda r: requested.append(r.toggleOn()))
    target = view.focusProxy() or view
    QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, QPoint(200, 150))
    assert _until(lambda: requested, 5), "Seite hat kein Vollbild angefordert"
    assert _until(lambda: js("document.fullscreenElement && document.fullscreenElement.id") == "v", 5)
    view.close()


def test_handy_window_mode_kde_hides_waiting_screen(env, tmp_path, monkeypatch):
    """KDE/Wayland: KWin legt UxPlays Fenster hin, AluPC sieht es nicht. Verbindet sich das iPad (UxPlay-Meldung),
    verschwindet der Warte-Bildschirm, damit er das Bild nicht verdeckt; beim Trennen kommt er wieder."""
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("Fake-Programm ist ein Shell-Skript")
    controller, _window, _ = env
    uxplay, log = _fake_uxplay(tmp_path, False)
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": uxplay, "pin": ""}
    monkeypatch.setattr(controller.windows, "follow_windows", lambda *a: "kwin-skript")
    monkeypatch.setattr(controller.windows, "stop_follow", lambda token: None)
    controller.start_airplay()
    pump()
    assert _until(lambda: controller.airplay.running(), 5)
    assert controller.output.isVisible() and not controller.output.suspended
    controller.airplay.connected.emit()
    assert controller.output.suspended and not controller.output.isVisible()
    controller.airplay.disconnected.emit()
    assert not controller.output.suspended and controller.output.isVisible()
    controller.extend()
    assert _until(lambda: not controller.airplay.running(), 5)


def test_now_playing_tile_and_commands(env, monkeypatch):
    """Kachel „Läuft gerade“ zeigt es auf Monitor 2; Befehle (Kachel-Pfeil, Handy, Tastenkürzel) gehen an den
    Player des PCs."""
    controller, window, _ = env
    from alupc import now_playing_view
    from alupc.now_playing_view import NowPlayingSource

    sent = []
    monkeypatch.setattr(now_playing_view.feed(), "control", sent.append)
    window.t_music.activated.emit()
    pump()
    assert isinstance(controller.output.content, NowPlayingSource)
    assert window.t_music.active
    for cmd in ("musik_pause", "musik_weiter", "musik_zurueck"):
        controller.run_command(cmd)
    assert sent == ["play_pause", "next", "previous"]
    from alupc.cast_server import ALLOWED_COMMANDS

    assert {"musik_pause", "musik_weiter", "musik_zurueck", "musik_zeigen"} <= ALLOWED_COMMANDS
    controller.extend()
    pump()


def test_reset_all_data_button(env, monkeypatch):
    """Setup → Allgemein → „Alle Daten löschen …“: zweimal bestätigen, dann beenden + löschen + Neustart."""
    from PySide6.QtWidgets import QDialog, QMessageBox

    from alupc import reset
    from alupc.ui import reset_page
    from alupc.ui.setup_page import SetupPage

    controller, window, _tmp = env
    window._go(2)  # Setup wird erst beim Öffnen gebaut
    setup = window.findChild(SetupPage)
    boxes = [b for b in setup.findChildren(__import__("PySide6.QtWidgets", fromlist=["x"]).QGroupBox)
             if b.title() == "Zurücksetzen"]
    assert boxes, "Bereich „Zurücksetzen“ fehlt"
    requested = []
    monkeypatch.setattr(reset, "request", lambda config, **kw: requested.append((config, kw)))
    monkeypatch.setattr(reset, "backup_dir", lambda: _tmp / "Sicherungen")  # nie in die echten Dokumente
    monkeypatch.setattr(reset_page.ResetDialog, "exec", lambda self: QDialog.Rejected)
    reset_page.reset_all(setup)
    assert not requested  # abgebrochen
    monkeypatch.setattr(reset_page.ResetDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.No)
    reset_page.reset_all(setup)
    assert not requested  # zweite Frage verneint
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Yes)
    reset_page.reset_all(setup)
    assert requested[0][0] is controller.config
    backup = requested[0][1]["backup"]  # vorher gesichert → lässt sich wieder laden
    assert backup and backup.parent == _tmp / "Sicherungen" and "AluPC" in backup.read_text(encoding="utf-8")


def test_browser_control_stays_smooth_while_moving(env):
    """Browser steuern: Während man die Maus ständig bewegt (oder scrollt), kommen laufend neue Bilder
    (~20/s). Bis 0.56 startete jede Bewegung den Zeitgeber neu → kein Bild, bis man still hielt (Ruckeln)."""
    import time

    controller, window, _ = env
    controller.show_source({"type": "website", "url": "data:text/html,<body style='background:#08f'>x</body>"})
    window.open_browser_control()
    bc = window.browser_control
    pump()
    frames = []
    real = bc.refresh
    bc.refresh = lambda: (frames.append(time.monotonic()), real())
    bc._soon.timeout.disconnect()
    bc._soon.timeout.connect(bc.refresh)
    bc.timer.timeout.disconnect()
    bc.timer.timeout.connect(bc._tick)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:  # „Maus bewegt sich“ alle 10 ms
        bc.refresh_soon()
        pump()
        time.sleep(0.01)
    assert len(frames) >= 10, f"nur {len(frames)} Bilder in 1 s beim Bewegen"
    assert bc.preview.image() is not None and not bc.preview.image().isNull()
    # ohne Eingabe: sparsam (höchstens ~4 Bilder/s)
    bc._active_until = 0
    frames.clear()
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        pump()
        time.sleep(0.01)
    assert len(frames) <= 6
    bc.close()


def test_airplay_picture_survives_player_restart(env, tmp_path, monkeypatch):
    """Ein iPad schickt das Schlüsselbild nur einmal beim Verbinden. Fällt AluPCs Player danach aus (z. B. nach
    „Connection timed out“ ohne Daten), muss er trotzdem wieder ein Bild zeigen: sofortiger Neustart + das Relais
    spielt das gemerkte Schlüsselbild vor. Bis 0.58: Ton ja, Bild nie."""
    import sys

    if not sys.platform.startswith("linux"):
        pytest.skip("Nur Linux (Pipeline-Weg)")
    pytest.importorskip("av")
    from PySide6.QtMultimedia import QMediaPlayer

    monkeypatch.setenv("RTP_GOP", "0")  # wie ein iPad: nur EIN Schlüsselbild
    monkeypatch.setenv("RTP_SECONDS", "14")
    controller, window, _ = env
    uxplay, _log = _fake_uxplay(tmp_path, False, pipeline_opts=True)
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": uxplay, "pin": ""}
    controller.start_airplay()
    pump()
    view = controller.output.content
    assert view.mode == "stream" and controller.airplay.relay is not None
    assert _until(lambda: view._had_frames, 20), "Kein Bild beim ersten Verbinden"
    assert _until(lambda: controller.airplay.relay.has_keyframe, 5)
    # Player fällt aus (wie nach „Connection timed out“) – das Schlüsselbild ist längst vorbei
    old = view.player
    view._had_frames = False
    old.errorOccurred.emit(QMediaPlayer.ResourceError, "Demuxing failed")
    assert _until(lambda: view.player is not old, 3), "Player wurde nicht sofort neu gestartet"
    frames = []  # nur Bilder des neuen Players zählen
    view.sink.videoFrameChanged.connect(lambda f: view.player is not old and f.isValid() and frames.append(1))
    assert _until(lambda: len(frames) >= 3, 10), "Nach dem Neustart kein Bild (Schlüsselbild nicht nachgereicht)"
    assert view._image is not None or _until(lambda: view._image is not None, 3)
    controller.extend()
    assert _until(lambda: not controller.airplay.running(), 5)
    assert controller.airplay.relay is None  # Relais mit UxPlay beendet


def test_screensaver_keeps_system_awake(env, monkeypatch):
    """Bildschirmschoner an → System darf die Monitore nicht selbst abdunkeln (sonst sieht man ihn nie).
    Aus + Monitor 2 frei → System darf wieder. Einstellung „System nicht abdunkeln lassen“ schaltet es ab."""
    controller, _window, _ = env
    mgr = controller.screensaver
    calls = []
    monkeypatch.setattr(mgr.keep_awake, "set", lambda on, reason="": calls.append(on) or on)
    if controller.output_screen() is None:
        pytest.skip("kein zweiter (Offscreen-)Monitor")
    controller.extend()
    controller.config["screensaver"] = {**controller.config["screensaver"], "enabled": True}
    mgr.check()
    assert calls[-1] is True
    controller.config["screensaver"] = {**controller.config["screensaver"], "enabled": False}
    mgr.check()
    assert calls[-1] is (controller.mode != "desktop")
    controller.show_source({"type": "text", "text": "Hallo"})  # Monitor 2 zeigt etwas → wach bleiben
    mgr.check()
    assert calls[-1] is True
    controller.config["screensaver"] = {**controller.config["screensaver"], "keep_awake": False}
    mgr.check()
    assert calls[-1] is False
    # Mausbewegung zählt als Aktivität, auch wenn das System keine Leerlaufzeit meldet
    monkeypatch.setattr(mgr.idle, "seconds", lambda: None)
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QCursor

    mgr.last_activity -= 500
    mgr.idle_seconds()
    QCursor.setPos(QCursor.pos() + QPoint(7, 3))
    if QCursor.pos() != mgr._cursor:  # Offscreen kann den Mauszeiger nicht immer bewegen
        assert mgr.idle_seconds() < 5


def test_whiteboard_backgrounds_and_pen(env, monkeypatch):
    """Whiteboard: jeder Hintergrund zeichnet sich; Kachel/Befehl zeigt die Tafel auf Monitor 2 und öffnet das
    Whiteboard-Fenster; auf Tafeln wird der Stift hell, auf Papier dunkel; das Kachel-Menü hakt den Hintergrund
    der aktuellen Seite ab."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from alupc import whiteboard

    controller, window, _ = env
    for key in whiteboard.BACKGROUNDS:
        img = QImage(320, 180, QImage.Format_RGB32)
        p = QPainter(img)
        whiteboard.paint_background(p, QRectF(0, 0, 320, 180), key)
        p.end()
        mid = img.pixelColor(3, 3)
        assert (mid.lightness() < 128) == whiteboard.is_dark(key), key
    opened = []
    controller.board_requested.connect(lambda: opened.append(1))
    monkeypatch.setattr(window, "open_board", lambda *a: None)
    controller.config["draw"] = {**controller.config["draw"], "color": "#000000", "tool": "laser"}
    controller.run_command("whiteboard")
    page_bg = controller.board.page["bg"]  # Tafel bleibt, wie sie war (Seite mit ihrem Hintergrund)
    assert controller.content == {"type": "whiteboard", "background": page_bg} and opened
    controller.show_whiteboard("tafel")
    assert controller.content["background"] == "tafel" == controller.board.page["bg"]
    assert controller.config["draw"]["color"] == "#ffffff" and controller.config["draw"]["tool"] == "pen"
    assert type(controller.output.content).__name__ == "WhiteboardSource"
    controller.show_whiteboard("kariert")
    assert controller.config["draw"]["color"] == "#111827"  # weiß auf Papier wäre unsichtbar
    window.refresh()
    assert window.t_board.active
    menu = window.t_board.menu
    window._fill_board_menu(menu)
    labels = [a.text().removesuffix("  ✓") for a in menu.actions()]  # aktueller Hintergrund trägt einen Haken
    assert "Kariert" in labels and "Tafel (grün)" in labels and "Neue Seite" in labels
    checked = [a.text() for a in menu.actions() if a.text().endswith("✓") or a.isChecked()]
    assert checked and checked[0].startswith("Kariert")


def test_pip_stays_on_top_on_kde(env, monkeypatch):
    """Bild-in-Bild: andere Fenster dürfen es nicht verdecken. KDE (Wayland) ignoriert Qts „immer oben“ – AluPC
    lässt ein KWin-Skript laufen, solange Bild-in-Bild offen ist; es setzt „immer oben“ bei jedem neuen oder
    aktivierten Fenster neu. Beim Schließen wird es entfernt."""
    import sys

    if not sys.platform.startswith("linux"):
        pytest.skip("KDE-Weg nur unter Linux")
    from alupc.platform import linux_windows

    controller, _window, _ = env
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    started, stopped = [], []
    monkeypatch.setattr(linux_windows, "start_kwin_script", lambda s: started.append(s) or f"s{len(started)}")
    monkeypatch.setattr(linux_windows, "stop_kwin_script", lambda name: stopped.append(name))
    pip = controller.pip
    assert pip.windowType() == Qt.Window  # Linux: kein Hilfsfenster (Qt.Tool)
    pip.show_on_main()
    assert _until(lambda: pip._pin == "s1", 3)
    script = started[0]
    assert "Bild-in-Bild" in script
    for part in ("keepAbove", "windowAdded", "windowActivated", "captionChanged", "indexOf(part)"):
        assert part in script
    pip.show_on_main()  # zweimal zeigen startet kein zweites Skript
    assert len(started) == 1
    pip.hide()
    assert _until(lambda: stopped == ["s1"], 3)
    assert pip._pin is None
    pip.show_on_main()
    assert _until(lambda: pip._pin == "s2", 3)
    pip.shutdown()
    assert "s2" in stopped and pip._pin is None
    pip.hide()


def test_main_window_opens_on_monitor_1(env, monkeypatch):
    """AluPC öffnet sich auf Monitor 1 – nicht auf Monitor 2 (dort sehen es die anderen). KDE/Wayland: KWin wird
    gebeten, das Fenster dorthin zu legen."""
    import sys

    from alupc.platform import linux_display, linux_windows

    controller, window, _ = env
    main, out = controller.main_screen(), controller.output_screen()
    if main is None or out is None or main is out:
        pytest.skip("braucht zwei (Offscreen-)Monitore")
    placed = []
    monkeypatch.setattr(linux_display, "is_wayland", lambda: True)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(linux_windows, "kwin_place_window", lambda *a: placed.append(a) or True)
    window.hide()
    window.windowHandle().setScreen(out)
    window.move(out.geometry().center())
    window.show()
    pump()
    assert main.availableGeometry().contains(window.geometry().center())
    if sys.platform.startswith("linux"):
        assert _until(lambda: placed, 3) and placed[-1][0] == window.windowTitle()


def _h264_file(path, w, h, frames=60):
    import av

    out = av.open(str(path), "w")
    st = out.add_stream("libx264", rate=30)
    st.width, st.height, st.pix_fmt = w, h, "yuv420p"
    st.options = {"preset": "ultrafast", "tune": "zerolatency", "g": "600"}
    for i in range(frames):
        f = av.VideoFrame(w, h, "rgb24")
        f.planes[0].update(b"".join(bytes([(i * 4) % 255, 90, 160]) * w + bytes(f.planes[0].line_size - w * 3)
                                    for _ in range(h)))
        for pk in st.encode(f):
            out.mux(pk)
    for pk in st.encode():
        out.mux(pk)
    out.close()


def test_leaving_airplay_does_not_freeze(env, tmp_path):
    """Aus AirPlay rausgehen: AluPC darf nicht einfrieren. Bis 0.64 wartete Qt beim Aufräumen auf den Lese-Thread des
    Players, der auf Netzwerkdaten hing → Oberfläche eingefroren, KDE beendete AluPC („Absturz“). Nachgestellt wie
    beim iPad: Bildformat wechselt (Drehen), Strom endet, dann rausgehen."""
    import shutil
    import subprocess
    import sys
    import time

    if not sys.platform.startswith("linux") or not shutil.which("gst-launch-1.0"):
        pytest.skip("braucht GStreamer (Linux)")
    pytest.importorskip("av")
    from PySide6.QtCore import QTimer

    controller, _window, _ = env
    ux = tmp_path / "ux"
    ux.write_text("#!/bin/sh\n[ \"$1\" = -h ] && { printf -- '-vd x\\n-vc x\\n-vs x\\n'; exit 0; }\n"
                  "echo 'Initialized server socket(s)'\nexec sleep 60\n")
    ux.chmod(0o755)
    controller.config["handy"] = {**controller.config["handy"], "uxplay_path": str(ux), "pin": ""}
    controller.start_airplay()
    pump()
    view = controller.output.content
    assert view.mode == "stream"
    controller.airplay.is_connected = True
    sizes = []
    view.sink.videoFrameChanged.connect(lambda f: f.isValid() and (not sizes or sizes[-1] != (f.width(), f.height()))
                                        and sizes.append((f.width(), f.height())))
    for name, w, h in (("quer.mp4", 320, 176), ("hoch.mp4", 176, 320)):  # iPad gedreht
        _h264_file(tmp_path / name, w, h)
        proc = subprocess.Popen(["gst-launch-1.0", "-q", "filesrc", f"location={tmp_path / name}", "!", "qtdemux", "!",
                                 "h264parse", "!", "rtph264pay", "config-interval=1", "pt=96", "!", "udpsink",
                                 "host=127.0.0.1", f"port={controller.airplay.relay.in_port}"])
        assert _until(lambda: proc.poll() is not None, 15)
        _until(lambda: False, 2.5)
    assert (320, 176) in sizes and (176, 320) in sizes, sizes  # neues Format wird gezeigt (friert nicht ein)
    ticks = []
    timer = QTimer(interval=100)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    start = time.monotonic()
    controller.extend()  # „rausgehen“
    _until(lambda: False, 4)
    timer.stop()
    gaps = [b - a for a, b in zip([start] + ticks, ticks)]
    assert ticks and max(gaps) < 1.5, f"Oberfläche hing {max(gaps or [99]):.1f} s"
    assert _until(lambda: not controller.airplay.running(), 5)


def test_welcome_animation_styles(env):
    """Begrüßung: alle Stile zeichnen sich zu jedem Zeitpunkt; Klick schließt sofort."""
    from PySide6.QtGui import QImage, QPainter

    from alupc.ui import welcome_window as ww

    for style in ("aurora", "konfetti", "scan", "zufall"):
        for name in ("Lena", "", "Maximilian-Alexander von Irgendwo"):
            w = ww.WelcomeWindow(name, style)
            w.resize(960, 540)
            for t in (0.0, 0.3, 0.8, 1.3, 2.0, 3.0, 4.2, ww.DURATION):
                w.t = t
                img = QImage(w.size(), QImage.Format_ARGB32)
                img.fill(0)
                w.render(img)
            w.t = 2.0
            img = QImage(w.size(), QImage.Format_ARGB32)
            img.fill(0)
            w.render(img)
            assert img.pixelColor(480, 270).alpha() > 200  # Hintergrund deckt ab
            w.deleteLater()
    assert ww.greeting(8) == "Guten Morgen" and ww.greeting(20) == "Guten Abend" and ww.greeting(2) == "Noch wach?"
    _ = QPainter


def test_welcome_watcher_and_show(env, monkeypatch):
    """Neue Anmeldung mit dem Finger → Begrüßung mit Namen der Person (oder eigenem Namen aus „Begrüßung“)."""
    import time

    from alupc import welcome
    from alupc.ui import welcome_window as ww

    controller, _window, _ = env
    records = [(3, time.time() - 30)]  # vor 30 s angemeldet, AluPC startet gerade
    monkeypatch.setattr(ww, "person_for_slot", lambda slot: {3: "Lena", 4: "Noah"}.get(slot, ""))
    controller.config["welcome"] = {**controller.config["welcome"], "names": {"Noah": "Chef"}}
    watcher = ww.WelcomeWatcher(controller.config, reader=lambda: records[-1], interval=60000)
    got = []
    watcher.greet.connect(lambda name, _person: got.append(name))
    watcher.greet_startup()
    assert got == ["Lena"]
    watcher.poll()  # nichts Neues
    assert got == ["Lena"]
    records.append((4, time.time()))
    watcher.poll()
    assert got == ["Lena", "Chef"]
    records.append((4, time.time() - 3600))  # alt → nicht mehr begrüßen
    watcher.seen = 0
    watcher.poll()
    assert got == ["Lena", "Chef"]
    controller.config["welcome"] = {**controller.config["welcome"], "on": False}
    records.append((3, time.time() + 1))
    watcher.poll()
    assert got == ["Lena", "Chef"]
    assert welcome.display_name(controller.config, "Lena") == "Lena"

    controller.show_welcome("Lena", style="konfetti")
    shown = controller._welcome
    assert shown is not None and shown.isVisible() and shown.name == "Lena"
    controller.show_welcome("Noah")  # zweite ersetzt die erste
    assert controller._welcome is not shown
    controller._welcome.finish()
    pump()
    assert controller._welcome is None


def test_welcome_settings(env, monkeypatch):
    """Begrüßung: nicht geheim (Seite Fingerabdruck → Begrüßung); speichert Stil, Text, eigene Namen und
    Geburtstage; „Ausprobieren“ zeigt die Animation ohne Sensor."""
    from alupc.ui import fingerprint_page, welcome_settings

    controller, window, _ = env
    assert not hasattr(window, "open_secret_menu")  # kein Geheimzugang mehr
    monkeypatch.setattr(welcome_settings.WelcomeSettings, "_persons", staticmethod(lambda: ["Lena", "Noah"]))
    opened = []
    monkeypatch.setattr(welcome_settings.WelcomeSettings, "exec", lambda self: opened.append(self) or 0)
    page = fingerprint_page.FingerprintPage(controller)
    assert "Aurora" in page.welcome_label.text()
    page.open_welcome()
    menu = opened[0]
    assert menu.windowTitle() == "Begrüßung"
    menu.style.setCurrentIndex(menu.style.findData("scan"))
    menu.name_edits["Noah"].setText("Chef")
    menu.birthday_edits["Lena"].setText("24.12.")
    menu.text.setText("Servus")
    shown = []
    monkeypatch.setattr(controller, "show_welcome", lambda name, person="", style=None: shown.append((name, person)))
    menu.try_name.setCurrentText("Noah")
    menu.try_it()
    assert shown == [("Chef", "Noah")]
    menu.accept()
    cfg = controller.config["welcome"]
    assert cfg["style"] == "scan" and cfg["names"] == {"Noah": "Chef"} and cfg["text"] == "Servus"
    assert cfg["birthdays"] == {"Lena": "12-24"}
    page._update_welcome_label()
    assert "Scan" in page.welcome_label.text()

def test_birthday_welcome(env, monkeypatch):
    """Geburtstag (Fenster „Begrüßung“): an dem Tag Konfetti und „Alles Gute zum Geburtstag!“ – egal welcher Stil."""
    import datetime

    from alupc import welcome

    assert welcome.parse_birthday("24.12.") == "12-24" and welcome.parse_birthday("3.7.2011") == "07-03"
    assert welcome.parse_birthday("") == "" and welcome.parse_birthday("31.02.") is None
    assert welcome.parse_birthday("hallo") is None and welcome.format_birthday("12-24") == "24.12."
    controller, _window, _ = env
    today = datetime.date.today()
    controller.config["welcome"] = {**controller.config["welcome"], "style": "scan",
                                    "birthdays": {"Lena": f"{today.month:02d}-{today.day:02d}"}}
    assert welcome.is_birthday(controller.config, "Lena") and not welcome.is_birthday(controller.config, "Noah")
    cfg = {"welcome": {"birthdays": {"Mia": "02-29"}}}
    assert welcome.is_birthday(cfg, "Mia", datetime.date(2027, 2, 28))  # kein Schaltjahr
    assert not welcome.is_birthday(cfg, "Mia", datetime.date(2028, 2, 28))
    controller.show_welcome("Lena", "Lena")
    w = controller._welcome
    assert w.style == "konfetti" and w.birthday and w.headline == "Alles Gute zum Geburtstag!"
    controller.show_welcome("Noah", "Noah")
    assert controller._welcome.style == "scan" and not controller._welcome.birthday
    controller._welcome.finish()
    pump()


def test_poll_end_to_end(env, monkeypatch):
    """Abstimmung: Monitor 2 zeigt Frage + QR; Handy stimmt ohne Steuer-Code ab (nur mit Stichwort), ändert seine
    Stimme; falsches Stichwort/ohne Abstimmung → 404; nach „Beenden“ keine Stimmen mehr."""
    import json

    from alupc.poll_source import PollSource
    from alupc.startpage import COMMANDS

    controller, window, _ = env
    port = _free_tcp_port()
    controller.config["cast"] = {**controller.config["cast"], "port": port, "code": "123456"}
    controller.start_poll("Welche Mission?", ["Brücke", "Kran", "  ", "Fähre"])
    pump()
    poll = controller.cast.poll
    assert poll.options == ["Brücke", "Kran", "Fähre"] and isinstance(controller.output.content, PollSource)
    assert controller.config["poll"]["question"] == "Welche Mission?"
    base = f"http://127.0.0.1:{controller.cast.port}"
    status, body = _http("GET", base + "/abstimmung")
    assert status == 200 and b"Abstimmung" in body
    status, _ = _http("GET", base + "/api/umfrage?u=falsch")
    assert status == 404
    status, body = _http("GET", f"{base}/api/umfrage?u={poll.token}&v=handy0001")
    data = json.loads(body)
    assert status == 200 and data["question"] == "Welche Mission?" and "counts" not in data  # erst nach Stimme

    def vote(voter, choice, token=poll.token):
        return _http("POST", base + "/api/umfrage", json.dumps({"u": token, "v": voter, "c": choice}).encode(),
                     {"Content-Type": "application/json"})

    status, body = vote("handy0001", 1)
    assert status == 200 and json.loads(body)["counts"] == [0, 1, 0]
    vote("handy0002", 1)
    vote("handy0001", 2)  # ändert seine Stimme
    assert poll.counts() == [0, 1, 1] and poll.total() == 2
    assert vote("handy0003", 7)[0] == 400 and vote("kurz", 0)[0] == 400
    assert vote("handy0004", 0, token="falsch")[0] == 404
    # Steuern geht mit dem Stichwort der Abstimmung nicht
    status, _ = _http("POST", base + "/api/cmd", json.dumps({"cmd": "schwarz"}).encode(),
                      {"Content-Type": "application/json", "X-AluPC-Code": poll.token})
    assert status == 403
    window.refresh()
    assert window.t_poll.badge == "LÄUFT"
    controller.poll_action("ende")
    assert vote("handy0005", 0)[0] == 400 and poll.total() == 2
    window.refresh()
    assert window.t_poll.badge == "ERGEBNIS"
    menu = window.t_poll.menu
    window._fill_poll_menu(menu)
    labels = [a.text() for a in menu.actions()]
    assert "Weiter abstimmen" in labels and "Stimmen löschen" in labels
    controller.poll_action("neu")
    assert poll.total() == 0 and poll.open
    img = controller.output.content.grab()  # zeichnet ohne Fehler
    assert not img.isNull()
    controller.poll_action("aus")
    assert controller.cast.poll is None
    assert _http("GET", f"{base}/api/umfrage?u={poll.token}")[0] == 404
    assert "umfrage_ende" in COMMANDS and "gluecksrad" in COMMANDS and "wetter" in COMMANDS
    controller.cast.stop()


def test_wheel_spins_to_fair_winner(env, monkeypatch):
    """Glücksrad: das gezogene Segment landet genau unter dem Zeiger; „Gezogene herausnehmen“ zieht jeden einmal."""
    from alupc import wheel

    controller, window, _ = env
    w = wheel.WheelSource({"names": ["A", "B", "C", "D", "E"]})
    w.resize(800, 450)
    for target in range(5):
        for _ in range(3):
            w.spin(target)
            w.timer.stop()
            w.clock.restart()
            w.progress = 1.0
            w.angle = w.end_angle % 360
            assert w.pointer_index() == target
            assert w.end_angle < w.start_angle - 360 * 4  # dreht mehrere Runden
    assert wheel.clean_names("  Lena \n\nNoah\n" + "x" * 60) == ["Lena", "Noah", "x" * 40]
    monkeypatch.setattr(wheel, "SPIN_SECONDS", 0.2)
    controller.config["wheel"] = {"names": ["Lena", "Noah", "Mia"], "remove_picked": True}
    picked = []
    for _ in range(3):
        controller.spin_wheel()
        pump()
        src = controller.output.content
        assert isinstance(src, wheel.WheelSource)
        assert _until(lambda: not src.spinning, 3)
        pump()
        picked.append(src.names[src.winner])
    assert sorted(picked) == ["Lena", "Mia", "Noah"]  # jeder genau einmal
    window.refresh()
    assert window.t_wheel.badge == "AKTIV"
    menu = window.t_wheel.menu
    window._fill_wheel_menu(menu)
    assert "Einträge bearbeiten …" in [a.text() for a in menu.actions()]


def test_weather_source_and_service(env, monkeypatch):
    """Wetter & Uhr: ohne Ort Hinweis; mit Ort holt der Dienst im Hintergrund (hier: nachgebaut) und zeichnet."""
    import time

    from alupc import weather

    controller, window, _ = env
    calls = []

    def fake_fetch(lat, lon):
        calls.append((lat, lon))
        return weather.parse_forecast({
            "current": {"temperature_2m": 17.4, "weather_code": 61, "wind_speed_10m": 9.6, "is_day": 0},
            "daily": {"time": ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03"],
                      "weather_code": [61, 3, 0, 95], "temperature_2m_max": [18, 15, 19, 21],
                      "temperature_2m_min": [9, 8, 7, 12]}})

    controller.weather.fetcher = fake_fetch
    controller.weather.data = None
    controller.show_weather()
    pump()
    assert isinstance(controller.output.content, weather.WeatherSource) and not calls  # kein Ort → nichts holen
    controller.config["weather"] = {"name": "Berlin", "lat": 52.52, "lon": 13.41, "label": "Berlin, Deutschland"}
    controller.weather.refresh()
    assert _until(lambda: controller.weather.data is not None, 3)
    assert calls == [(52.52, 13.41)] and controller.weather.data["temp"] == 17.4
    assert len(controller.weather.data["days"]) == 4
    controller.weather.refresh()  # noch frisch → nicht nochmal
    time.sleep(0.1)
    assert len(calls) == 1
    assert not controller.output.content.grab().isNull()
    assert weather.describe(95) == ("Gewitter", "gewitter") and weather.describe(1234)[1] == "wolke"
    window.refresh()
    assert window.t_weather.badge == "AKTIV"


def test_weather_live_open_meteo():
    """Echte Abfrage bei Open-Meteo (Ort suchen + Wetter holen). Ohne Internet übersprungen – im CI muss es gehen."""
    import os
    import urllib.error

    from alupc import weather

    try:
        _weather_live(weather)
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:  # Open-Meteo gerade gestört (503 …) – kein Fehler von AluPC
            pytest.skip(f"Open-Meteo gestört: {exc}")
        raise
    except (urllib.error.URLError, OSError) as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"kein Internet: {exc}")


def _weather_live(weather):
    place = weather.geocode("Berlin")
    assert place and abs(place["lat"] - 52.5) < 1 and "Deutschland" in place["label"]
    # Postleitzahl = Deutschland (nicht USA): 80331 = München, 10115 = Berlin
    munich = weather.geocode("80331")
    assert munich and abs(munich["lat"] - 48.14) < 0.3 and abs(munich["lon"] - 11.57) < 0.3, munich
    assert "Deutschland" in munich["label"] and munich["label"].startswith("80331")
    berlin = weather.geocode("10115 Berlin")
    assert berlin and abs(berlin["lat"] - 52.53) < 0.3, berlin
    print("PLZ:", munich["label"], "|", berlin["label"])
    data = weather.fetch(place["lat"], place["lon"])
    assert -50 < data["temp"] < 60 and len(data["days"]) == 4
    print("Wetter Berlin:", data["temp"], weather.describe(data["code"])[0])


def test_finger_shortcuts_dialog(env, monkeypatch):
    from alupc import finger_shortcuts
    from alupc.ui import finger_shortcuts_dialog as fsd

    controller, _window, _ = env
    monkeypatch.setattr(fsd, "own_fingers", lambda: [(3, "Lena", "Rechter Zeigefinger"), (7, "Noah", "Linker Daumen")])
    applied = []
    monkeypatch.setattr(controller.finger_shortcuts, "apply", lambda: applied.append(1))
    d = fsd.FingerShortcutsDialog(controller)
    d.on.setChecked(True)
    d.combos[3].setCurrentIndex(d.combos[3].findData("schwarz"))
    d.combos[7].setCurrentIndex(d.combos[7].findData("gluecksrad"))
    d.accept()
    assert controller.config["finger_shortcuts"] == {"on": True, "map": {"3": "schwarz", "7": "gluecksrad"}}
    assert applied and finger_shortcuts.shortcut_map(controller.config) == {3: "schwarz", 7: "gluecksrad"}


def _ws_connect(port, path):
    """Kleiner WebSocket-Client für den Test (Handshake + Rahmen lesen/schreiben)."""
    import base64
    import os
    import socket

    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((f"GET {path} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += sock.recv(1)
    return sock, sock.makefile("rb"), head.decode()


def _ws_send(sock, obj):
    import json
    import os
    import struct

    data = json.dumps(obj).encode()
    mask = os.urandom(4)
    head = struct.pack(">BB", 0x81, 0x80 | len(data)) if len(data) < 126 else struct.pack(">BBH", 0x81, 0xFE,
                                                                                         len(data))
    sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def _ws_recv(rfile):
    import json

    from alupc.ws import read_frame

    op, data = read_frame(rfile)
    return op, (json.loads(data) if op == 1 else data)


def test_games_end_to_end(env):
    """Minispiele: Lobby mit QR auf Monitor 2; Handys treten ohne Steuer-Code bei (nur Stichwort), spielen über
    WebSocket (und zur Not per POST); starten können nur der PC und das Steuerfenster (mit Tasten); jedes Spiel
    zeichnet; falsches Stichwort → 404; Steuern geht damit nicht."""
    import json
    import time

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from alupc.game_source import GameSource
    from alupc.games import GAMES

    controller, window, _ = env
    port = _free_tcp_port()
    controller.config["cast"] = {**controller.config["cast"], "port": port, "code": "123456"}
    window.open_games_window()
    pump()
    gw = window.games_window
    hub = controller.cast.games
    # Der PC selbst (127.0.0.1) darf – Handys nur übers AluPC-WLAN (geprüft in test_connect und echt im Netz)
    assert gw.isVisible() and isinstance(controller.output.content, GameSource) and hub.game_key == "schlangen"
    base = f"http://127.0.0.1:{controller.cast.port}"
    status, body = _http("GET", base + "/spiel")
    assert status == 200 and b"Mitspielen" in body and b"/ws/spiel" in body
    assert _http("GET", base + "/api/spiel?u=falsch")[0] == 404

    def post(data, token=hub.token):
        return _http("POST", base + "/api/spiel", json.dumps({"u": token, **data}).encode(),
                     {"Content-Type": "application/json"})

    lena = json.loads(post({"action": "join", "name": "Lena"})[1])
    mia = json.loads(post({"action": "join", "name": "Mia"})[1])
    assert lena["name"] == "Lena" and mia["p"]
    assert post({"action": "join", "name": "X"}, token="falsch")[0] == 404
    assert post({"action": "start", "p": lena["p"]})[0] == 400 and hub.phase == "lobby"  # Handys starten nicht
    # Steuern geht mit dem Stichwort der Runde nicht – und „spiel_start“ ist fürs Handy gesperrt
    status, _ = _http("POST", base + "/api/cmd", json.dumps({"cmd": "schwarz"}).encode(),
                      {"Content-Type": "application/json", "X-AluPC-Code": hub.token})
    assert status == 403
    status, _ = _http("POST", base + "/api/cmd", json.dumps({"cmd": "spiel_start"}).encode(),
                      {"Content-Type": "application/json", "X-AluPC-Code": "123456"})
    pump()
    assert hub.phase == "lobby"
    # WebSocket: Stand kommt von selbst, Eingaben gehen sofort
    sock, rfile, head = _ws_connect(controller.cast.port, f"/ws/spiel?u={hub.token}&p={lena['p']}")
    assert head.startswith("HTTP/1.1 101")
    op, state = _ws_recv(rfile)
    assert op == 1 and state["joined"] and state["ui"]["status"].startswith("Warte auf den Start am PC")
    _until(lambda: gw.players.count() == 2)
    assert "Lena" in gw.players.item(0).text()
    # Steuerfenster: Taste 5 = Tipp-Rennen, Leertaste = Start
    gw.activateWindow()
    QTest.keyClick(gw, Qt.Key_5)
    assert hub.game_key == "rennen" and gw.cards["rennen"].isChecked()
    QTest.keyClick(gw, Qt.Key_Space)
    assert hub.phase == "running"
    _until(lambda: window.t_games.badge == "LÄUFT")
    hub.intro_until = hub.clock()  # 3-2-1 überspringen
    _until(lambda: _ws_recv(rfile)[1].get("ui", {}).get("ui") == "tap")
    for _ in range(3):
        _ws_send(sock, {"tap": 1})
        time.sleep(0.07)
    _until(lambda: hub.game.progress[lena["p"]] == 3)
    assert post({"action": "input", "p": mia["p"], "tap": 1})[0] == 200  # Rückweg ohne WebSocket
    assert post({"action": "input", "p": "fremd", "tap": 1})[0] == 404
    QTest.keyClick(gw, Qt.Key_E)  # E = Ergebnis
    assert hub.phase == "over"
    _until(lambda: window.t_games.badge == "ERGEBNIS")
    # jedes Spiel zeichnet (3-2-1, Spiel, Ergebnis)
    for key in GAMES:
        controller.start_games(key)
        controller.game_action("start")
        assert not controller.output.content.grab().isNull()
        hub.intro_until = hub.clock()
        hub.tick()
        for _ in range(3):
            controller.output.content.grab()
        controller.game_action("weiter")
        controller.output.content.grab()
        controller.game_action("ende")
        assert hub.phase == "over" and not controller.output.content.grab().isNull(), key
    # Spieler entfernen (Entf)
    gw.refresh(force=True)
    gw.players.setCurrentRow(1)
    QTest.keyClick(gw, Qt.Key_Delete)
    assert list(hub.players) == [lena["p"]]
    menu = window.t_games.menu
    window._fill_games_menu(menu)
    labels = [a.text() for a in menu.actions()]
    assert "Steuerfenster …" in labels and "Spiel starten" in labels and "Zurück zur Lobby" in labels
    controller.run_command("spiel_start")  # am PC (eigene Kachel, Finger) geht es
    assert hub.phase == "running"
    controller.game_action("aus")
    assert controller.cast.games is None
    from alupc.ws import read_frame

    closed = False
    for _ in range(200):  # der Server schließt die Verbindung (letzte Stände dürfen noch kommen)
        if read_frame(rfile) is None:
            closed = True
            break
    assert closed
    sock.close()
    assert _http("GET", f"{base}/api/spiel?u={hub.token}")[0] == 404
    controller.cast.stop()


def test_poll_and_wheel_dialogs(env, monkeypatch):
    from alupc.ui.extras_dialogs import PollDialog, WheelDialog

    controller, window, _ = env
    started = []
    monkeypatch.setattr(controller, "start_poll", lambda q, opts: started.append((q, opts)))
    d = PollDialog(controller, window)
    d.question.setText("")
    assert not d.start.isEnabled()
    d.question.setText("Pizza oder Nudeln?")
    d.options[0].setText("Pizza")
    d.options[1].setText("")
    assert not d.start.isEnabled()  # nur eine Antwort
    d.options[1].setText("Nudeln")
    d.options[3].setText("Beides")
    assert d.start.isEnabled()
    d.start.click()
    assert started == [("Pizza oder Nudeln?", ["Pizza", "Nudeln", "Beides"])]
    w = WheelDialog(controller, window)
    w.names.setPlainText("Lena\n\n  Noah  \nMia")
    w.remove.setChecked(True)
    w.accept()
    assert controller.config["wheel"] == {"names": ["Lena", "Noah", "Mia"], "remove_picked": True}


def test_bug_report(env, tmp_path, monkeypatch):
    """Fehlerbericht: ZIP mit Beschreibung, Diagnose, Einstellungen ohne Geheimnisse, Fehlerprotokoll, Fensterbild."""
    import json
    import sys
    import zipfile

    from alupc import bug_report, diagnose
    from alupc.ui.bug_report_dialog import BugReportDialog

    controller, window, _ = env
    controller.config["cast"] = {**controller.config["cast"], "code": "123456"}
    controller.config["handy"] = {**controller.config["handy"], "pin": "4711", "airplay_name": "Mein PC"}
    try:
        raise ValueError("Testfehler 42")
    except ValueError:
        bug_report.log_exception(*sys.exc_info())
    monkeypatch.setattr(diagnose, "report", lambda c, probe=True: "DIAGNOSE-OK")
    d = BugReportDialog(controller, window)
    d.text.setPlainText("AirPlay: Ton ja, Bild nein")
    d.make(folder=tmp_path, ask=False)
    assert d.path is not None and d.path.parent == tmp_path and d.path.suffix == ".zip"
    with zipfile.ZipFile(d.path) as z:
        names = set(z.namelist())
        assert {"beschreibung.txt", "diagnose.txt", "einstellungen.json", "fehler.log", "alupc-fenster.png"} <= names
        assert "Ton ja, Bild nein" in z.read("beschreibung.txt").decode()
        assert z.read("diagnose.txt") == b"DIAGNOSE-OK"
        cfg = json.loads(z.read("einstellungen.json"))
        assert cfg["cast"]["code"] == "•••" and cfg["handy"]["pin"] == "•••"
        assert cfg["handy"]["airplay_name"] == "Mein PC"  # normale Werte bleiben
        raw = z.read("einstellungen.json").decode()
        assert "123456" not in raw and "4711" not in raw
        assert "Testfehler 42" in z.read("fehler.log").decode()
    assert bug_report.redact({"a": {"secret": "x", "items": [{"password": "y"}]}, "code": ""}) == \
        {"a": {"secret": "•••", "items": [{"password": "•••"}]}, "code": ""}


def test_weather_postcode_is_german(monkeypatch):
    """5-stellige Postleitzahl → Suche nur in Deutschland; kennt Open-Meteo sie nicht, fragt AluPC OpenStreetMap."""
    from alupc import weather

    calls = []

    def fake(url, params, timeout=10):
        calls.append((url, dict(params)))
        if url == weather.GEO_URL and params["name"] == "80331":
            return {"results": [{"name": "München", "admin1": "Bayern", "country": "Deutschland",
                                 "country_code": "DE", "latitude": 48.14, "longitude": 11.57,
                                 "postcodes": ["80331", "80333"]}]}
        if url == weather.GEO_URL:
            return {}
        return [{"lat": "51.34", "lon": "12.37", "address": {"city": "Leipzig", "state": "Sachsen",
                                                             "country": "Deutschland"}}]

    monkeypatch.setattr(weather, "_get_json", fake)
    p = weather.geocode(" 80331 ")
    assert calls[0][1]["countryCode"] == "DE" and p["label"] == "80331 München, Bayern, Deutschland"
    calls.clear()
    p = weather.geocode("04109 Leipzig")  # Open-Meteo leer → Nominatim, nur Deutschland
    assert calls[1][0] == weather.NOMINATIM_URL and calls[1][1]["country"] == "de"
    assert p["name"] == "Leipzig" and abs(p["lat"] - 51.34) < 0.01 and p["label"].startswith("04109 Leipzig")
    calls.clear()
    weather.geocode("Paris")  # Ortsnamen: weltweit wie bisher
    assert "countryCode" not in calls[0][1]


def test_wheel_does_not_spin_by_itself_and_menus(env):
    """Glücksrad-Kachel zeigt nur – gedreht wird mit „Drehen“ (Seitenleiste/Menü). Menüs: Überschriften, aktuelle
    Auswahl markiert; neue Seiten haben lesbare Namen."""
    from alupc import wheel
    from alupc.scenes import describe_source

    controller, window, _ = env
    controller.config["wheel"] = {"names": ["A", "B", "C"], "remove_picked": False}
    window.t_wheel.activated.emit()
    pump()
    w = controller.wheel_widget()
    assert isinstance(w, wheel.WheelSource) and not w.spinning and w.winner is None
    window.refresh()
    assert window.wheel_row.isVisibleTo(window) and window.wheel_spin.isEnabled()
    window.wheel_spin.click()
    pump()
    assert w.spinning and not window.wheel_spin.isEnabled()
    controller.spin_wheel()  # dreht schon → nichts Neues
    assert controller.wheel_widget() is w
    w.stop()  # nicht weiterdrehen lassen, wenn das Testfenster abgebaut wird
    menu = window.t_wheel.menu
    window._fill_wheel_menu(menu)
    headers = [a.text() for a in menu.actions() if a.data() == "header"]
    assert headers == ["Glücksrad"]
    controller.show_whiteboard("kariert")
    pump()
    menu = window.t_board.menu
    window._fill_board_menu(menu)
    current = [a.text() for a in menu.actions() if a.text().endswith("✓")]
    assert current == ["Kariert  ✓"] and menu.actions()[1].font().bold() is False
    for typ, text in (("zufall", "Glücksrad"), ("wetter", "Wetter & Uhr"), ("umfrage", "Abstimmung"),
                      ("whiteboard", "Whiteboard")):
        assert describe_source({"type": typ}) == text


def test_birthday_without_fingerprint(env, monkeypatch):
    """Geburtstage gehen auch ohne angelernten Finger: Person im Fenster „Begrüßung“ von Hand hinzufügen."""
    from alupc.ui import welcome_settings

    controller, window, _ = env
    monkeypatch.setattr(welcome_settings.WelcomeSettings, "_persons", staticmethod(lambda: []))
    dlg = welcome_settings.WelcomeSettings(controller, window)
    dlg.new_person.setText("Mia")
    dlg._add_person()
    dlg.birthday_edits["Mia"].setText("3.7.")
    dlg.accept()
    assert controller.config["welcome"]["birthdays"] == {"Mia": "07-03"}
    again = welcome_settings.WelcomeSettings(controller, window)  # bleibt beim nächsten Öffnen stehen
    assert again.birthday_edits["Mia"].text() == "03.07." and again.try_name.findText("Mia") >= 0
    again.reject()


def test_board_sounds_and_voice_in_app(env, monkeypatch):
    """Bestenliste: wird pro Abend gespeichert, Taste B zeigt sie auf Monitor 2; Spiel-Töne laufen mit;
    Sprachbefehl → Befehl + Meldung; Setup-Bereich „Sprache“; Whiteboard vom Handy mit Hintergrund."""
    import json

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from alupc import controller as ctl
    from alupc.game_source import GameSource

    controller, window, _ = env
    port = _free_tcp_port()
    controller.config["cast"] = {**controller.config["cast"], "port": port, "code": "123456"}
    window.open_games_window()
    pump()
    hub = controller.cast.games
    gw = window.games_window
    lena, noah = hub.join("Lena"), hub.join("Noah")
    hub.set_game("rennen")
    controller.game_action("start")
    hub.intro_until = hub.clock()
    controller._games_tick()
    hub.game.progress = {lena.pid: 3, noah.pid: 1}
    controller.game_action("ende")
    controller._games_tick()
    saved = controller.config["games"]["board"]
    assert saved == {"abend": ctl.evening_key(), "punkte": {"Lena": 10, "Noah": 7}, "spiele": 1}
    assert controller.game_sounds is not None and "sieg" in controller.game_sounds.played
    gw.activateWindow()
    QTest.keyClick(gw, Qt.Key_B)
    assert hub.phase == "board" and isinstance(controller.output.content, GameSource)
    assert not controller.output.content.grab().isNull()
    _until(lambda: window.t_games.badge == "BESTENLISTE")
    gw.refresh(force=True)
    assert "gesamt 10" in gw.players.item(0).text()
    sound_before = controller.config["games"]["sound"]
    QTest.keyClick(gw, Qt.Key_S)
    assert controller.config["games"]["sound"] is (not sound_before)
    # gleicher Abend: neue Spielrunde übernimmt die Bestenliste, anderer Abend nicht
    controller.game_action("aus")
    controller.start_games()
    assert controller.cast.games.board == {"Lena": 10, "Noah": 7}
    controller.game_action("aus")
    controller.config["games"] = {**controller.config["games"], "board": {**saved, "abend": "2000-01-01"}}
    controller.start_games()
    assert controller.cast.games.board == {}
    controller.game_action("aus")
    # Sprachbefehl → Befehl ausführen + Meldung
    ran, msgs = [], []
    monkeypatch.setattr(controller, "run_command", ran.append)
    controller.message.connect(msgs.append)
    controller.voice.command.emit("schwarz", "Schwarz an/aus", "monitor schwarz")
    pump()
    assert ran == ["schwarz"] and msgs[-1] == "🎤 Schwarz an/aus"
    monkeypatch.undo()
    # Setup: Bereich „Sprache“ vorhanden
    from alupc.ui.setup_page import SetupPage

    setup = SetupPage(controller, window.hotkeys)
    setup.show_section("Sprache")
    assert "Sprachmodell" in setup.voice_model.text() or "Vosk" in setup.voice_model.text()
    assert setup.voice_state.text().startswith("Status:")
    setup.deleteLater()
    # Whiteboard vom Handy: Hintergrund per Befehl
    controller.cast.start()
    base = f"http://127.0.0.1:{controller.cast.port}"
    status, _ = _http("POST", base + "/api/cmd", json.dumps({"cmd": "whiteboard:tafel"}).encode(),
                      {"Content-Type": "application/json", "X-AluPC-Code": "123456"})
    assert status == 200
    _until(lambda: (controller.content or {}).get("type") == "whiteboard")
    assert controller.content["background"] == "tafel"
    status, _ = _http("POST", base + "/api/cmd", json.dumps({"cmd": "whiteboard:../x"}).encode(),
                      {"Content-Type": "application/json", "X-AluPC-Code": "123456"})
    assert status == 400
    status, body = _http("GET", base + "/")
    assert "whiteboard:kariert" in body.decode()
    controller.cast.stop()


def test_voice_enroll_dialog_and_setup(env):
    """Stimme anlernen: 5 Sätze → Mittelwert gespeichert; zu kurze Sätze zählen nicht; Setup zeigt Startwörter."""
    from alupc.ui.setup_page import SetupPage
    from alupc.ui.voice_enroll import SENTENCES, VoiceEnrollDialog

    controller, window, _ = env
    vc = controller.voice
    vc.state, vc.has_spk = "hört zu", True  # wie nach dem Laden
    d = VoiceEnrollDialog(controller, window, name="")
    assert vc.enrolling and SENTENCES[0] in d.sentence.text()
    vc.sample.emit([1.0, 0.0], 5, "zu kurz")
    assert d.vectors == [] and "zu kurz" in d.hint.text()
    for i in range(len(SENTENCES)):
        vc.sample.emit([1.0, 0.1 * i], 100, f"satz {i}")
    assert len(d.vectors) == len(SENTENCES) and "Fertig" in d.sentence.text()
    assert not d.save_btn.isEnabled()  # erst Name
    d.name.setText("Lena")
    assert d.save_btn.isEnabled()
    d.save_btn.click()
    assert not vc.enrolling
    voices = controller.config["voice"]["voices"]
    assert [v["name"] for v in voices] == ["Lena"] and len(voices[0]["vec"]) == 2
    vc.state, vc.has_spk = "aus", False
    setup = SetupPage(controller, window.hotkeys)
    assert set(setup.wake_boxes) == {"monitor", "alupc", "computer"} and all(cb.isChecked() for cb in setup.wake_boxes.values())
    assert setup.voice_list.item(0).text() == "Lena"
    setup.wake_boxes["monitor"].setChecked(False)
    setup.wake_boxes["computer"].setChecked(False)
    assert controller.config["voice"]["wake"] == ["alupc"]
    setup.only_voices.setChecked(True)
    assert controller.config["voice"]["only_voices"] is True
    setup.deleteLater()


def test_voice_reply_custom_and_video_resume(env, monkeypatch):
    """Sprachbefehl → Antwort per Stimme; „weiter“ im laufenden Minispiel = nächste Frage; eigener Befehl im
    Setup; Video: Stelle merken, beim erneuten Öffnen fragen (PC + Handy), Entscheidung springt."""
    import json

    from alupc import controller as ctl
    from alupc.ui.voice_custom import VoiceCustomDialog, action_choices

    controller, window, _ = env
    ran = []
    real_run = controller.run_command
    monkeypatch.setattr(controller, "run_command", lambda c: ran.append(c))
    controller._voice_command("schwarz", "Schwarz an/aus", "alu pc bildschirm schwarz")
    # run_command ist hier ersetzt → nichts passiert wirklich: AluPC sagt ehrlich, dass es nicht ging
    assert ran == ["schwarz"] and controller.speaker.spoken[-1] == "Das ging gerade nicht."
    controller.config["voice"] = {**controller.config["voice"], "speak": False}
    controller._voice_command("wetter", "Wetter & Uhr", "monitor wetter")
    assert controller.speaker.spoken[-1] == "Das ging gerade nicht."  # aus → nichts gesagt
    controller.start_games()
    hub = controller.cast.games
    hub.join("Lena")
    controller.game_action("start")
    controller._voice_command("naechste_szene", "Nächste Szene", "alu pc weiter")
    assert ran[-1] == "spiel_weiter"
    monkeypatch.setattr(controller, "run_command", real_run)
    controller.game_action("aus")
    # eigener Befehl
    assert ("schwarz", "Schwarz (Sichtschutz) an/aus") in action_choices(controller.config) or \
        any(k == "schwarz" for k, _ in action_choices(controller.config))
    d = VoiceCustomDialog(controller, window)
    d.say.setText("  Pause   machen ")
    d.action.setCurrentIndex(d.action.findData("wetter"))
    d.save()
    assert controller.config["voice"]["custom"] == [{"say": "Pause machen", "do": "wetter"}]

    # ---- Video weiterschauen
    class FakePlayer:
        def __init__(self):
            self.paused = False

        def pause(self):
            self.paused = True

    class FakeVideo:
        def __init__(self, path, pos, dur):
            self.path, self.pos, self.dur, self.player, self.seeked = path, pos, dur, FakePlayer(), None

        def position(self):
            return self.pos

        def duration(self):
            return self.dur

        def seek_when_ready(self, ms, play=True):
            self.seeked = ms

    videos = []
    monkeypatch.setattr("alupc.sources.video_sources", lambda _w: list(videos))
    controller.mode = "content"
    videos.append(FakeVideo("/filme/robot.mp4", 754_000, 3_600_000))
    controller._remember_video_positions()
    assert controller.config["video_positions"]["/filme/robot.mp4"]["pos"] == 754_000
    videos[0].pos = 10_000  # zu kurz geschaut → bleibt bei der alten Stelle
    controller._remember_video_positions()
    assert controller.config["video_positions"]["/filme/robot.mp4"]["pos"] == 754_000
    videos.clear()
    offers = []
    controller.video_resume.connect(lambda title, pos: offers.append((title, pos)))
    videos.append(FakeVideo("/filme/robot.mp4", 0, 3_600_000))
    controller._offer_resume()
    assert offers == [("robot", 754_000)] and videos[0].player.paused
    assert window._resume_dialog is not None and "12:34" in window._resume_dialog.resume.text()
    controller._cast_snapshot()
    assert controller.cast.snapshot["resume"] == {"title": "robot", "pos": 754}
    # Handy: „Weiterschauen“
    port = _free_tcp_port()
    controller.config["cast"] = {**controller.config["cast"], "port": port, "code": "123456"}
    controller.cast.start()
    base = f"http://127.0.0.1:{controller.cast.port}"
    status, _ = _http("POST", base + "/api/cmd", json.dumps({"cmd": "video_weiterschauen"}).encode(),
                      {"Content-Type": "application/json", "X-AluPC-Code": "123456"})
    assert status == 200
    _until(lambda: videos[0].seeked == 754_000)
    assert controller.resume_offer is None
    import shiboken6

    _until(lambda: not shiboken6.isValid(window._resume_dialog))
    assert not shiboken6.isValid(window._resume_dialog)  # Fenster hat sich von selbst geschlossen
    # nochmal öffnen → „Von vorn“ vergisst die Stelle
    controller._offer_resume()
    controller.video_resume_choice("neu")
    assert videos[0].seeked == 0 and "/filme/robot.mp4" not in controller.config["video_positions"]
    # zu Ende geschaut → vergessen; kurze Videos werden nie gemerkt
    videos[0].pos = 3_590_000
    controller.config["video_positions"] = {"/filme/robot.mp4": {"pos": 5, "dur": 3_600_000, "t": 1}}
    controller._remember_video_positions()
    assert controller.config["video_positions"] == {}
    videos[:] = [FakeVideo("/clip.mp4", 50_000, 90_000)]
    controller._remember_video_positions()
    assert controller.config["video_positions"] == {}
    assert ctl.Controller.RESUME_WAIT == 15_000
    status, body = _http("GET", base + "/")
    assert "video_weiterschauen" in body.decode()
    controller.cast.stop()


# ---------------------------------------------------------------- 0.82: Seite „System“
def test_system_page_and_source(env):
    controller, window, tmp = env
    pytest.importorskip("psutil")
    from alupc import sysinfo

    geraete = next(b for b in window.nav_group.buttons() if b.toolTip() == "Geräte")
    geraete.click()  # „Geräte“ öffnet zuerst den Reiter „PC“ (Systemstatus)
    assert window.stack.currentIndex() == 4 and window.subnav.isVisible()
    pump(10)
    page = window.system_page
    mon = sysinfo.monitor()
    assert mon.running
    assert _until(lambda: mon.snapshot.ram_total > 0)
    _until(lambda: not page.gauges["ram"].anim.isActive(), 3)
    assert page.gauges["ram"].data[1] is not None and page.gauges["ram"].data[1] > 0
    assert page.subtitle.text().startswith(mon.host)
    assert any(r.isVisible() for r in page.proc_rows)
    window.grab().save(str(tmp / "system_page.png"))
    # Steuerung fragt immer nach – „Nein“ führt nichts aus
    from PySide6.QtWidgets import QMessageBox

    called = []
    sysinfo_power = sysinfo.power_action
    sysinfo.power_action = lambda a: called.append(a)
    try:
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        page.power("aus")
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        page.power("neustart")
    finally:
        QMessageBox.question = orig
        sysinfo.power_action = sysinfo_power
    assert called == ["neustart"]
    # Seite verlassen → Messung stoppt (sofern niemand sonst zuschaut)
    window._go(0)
    pump(5)
    assert not mon.running
    # Dashboard auf Monitor 2
    controller.show_source({"type": "system"}, remember=False)
    pump(5)
    assert type(controller.output.content).__name__ == "SystemSource"
    assert mon.running
    assert _until(lambda: mon.snapshot.ram_total > 0)
    for _ in range(60):
        controller.output.content._animate()
    controller.output.content.grab().save(str(tmp / "system_source.png"))
    controller.show_source({"type": "color"}, remember=False)
    pump(5)
    assert not mon.running


# ---------------------------------------------------------------- 0.83: Sprachassistent
def test_assistant_replies_follow_state(env):
    controller, window, _ = env
    a = controller.assistant
    spoken = controller.speaker.spoken
    controller._voice_command("schwarz_an", "Schwarz", "mach den bildschirm schwarz")
    assert controller.privacy and spoken[-1].endswith("Der Bildschirm ist jetzt schwarz.")
    controller._voice_command("schwarz_an", "Schwarz", "")
    assert controller.privacy and spoken[-1] == "Der Bildschirm ist schon schwarz."
    controller._voice_command("schwarz", "Schwarz", "")  # nur „schwarz“ = umschalten
    assert not controller.privacy and spoken[-1].endswith("Das Bild ist wieder da.")
    controller._voice_command("schwarz_aus", "Schwarz aus", "")
    assert spoken[-1] == "Der Bildschirm ist gar nicht schwarz."
    assert controller.voice.follow_until == 0  # normale Antwort: danach nur mit Startwort
    controller._voice_command("frage:ja", "Ja?", "")
    assert controller.voice.follow_until > 0  # nach „Ja?“: einmal ohne Startwort
    controller.voice.follow_until = 0
    controller._voice_command("timer_set:90", "Timer", "")
    from alupc.timer import clock

    assert clock.running and "eine Minute und 30 Sekunden" in spoken[-1]
    controller._voice_command("timer_pause", "Timer Pause", "")
    assert not clock.running
    controller._voice_command("rgb_farbe:#0000ff", "Licht blau", "")
    assert controller.rgb.settings()["color"] == "#0000ff" and "blau" in spoken[-1]
    assert a.answer("uhrzeit").startswith("Es ist")
    assert a.answer("datum").startswith("Heute ist")
    assert "Prozent" in a.answer("system") or "messen" in a.answer("system")
    assert a.answer("wetter").startswith("Für das Wetter brauche ich erst einen Ort")
    controller._voice_not_understood("blabla")
    assert spoken[-1] in ("Das habe ich nicht verstanden.", "Wie bitte?", "Sag das bitte noch mal anders.")


def test_mic_switch_button(env, monkeypatch):
    controller, window, _ = env
    from alupc import voice

    monkeypatch.setattr(voice, "model_ready", lambda *a: False)
    window.mic_button.click()  # ohne Sprachmodell: Hinweis statt Zuhören
    assert not controller.voice.direct
    started = []
    monkeypatch.setattr(voice, "model_ready", lambda *a: True)
    monkeypatch.setattr(voice, "vosk_available", lambda: True)
    monkeypatch.setattr(controller.voice, "start", lambda: started.append(1))
    monkeypatch.setattr(controller.voice, "running", lambda: False)
    window.mic_button.click()
    pump()
    assert controller.voice.direct and started and window.mic_button.isChecked()
    assert window.mic_button.text_ == "Hört zu …"
    controller.run_command("zuhoeren")  # Tastenkürzel / Kachel: umschalten
    pump()
    assert not controller.voice.direct and not window.mic_button.isChecked()
    controller.voice.listen_on(5, force=True)  # nach „Ja?“: Knopf zeigt, dass AluPC kurz ohne Startwort hört
    window._update_mic_button()
    assert window.mic_button.isChecked() and window.mic_button.text_ == "Ich höre …"
    controller.voice.follow_until = 0
    window._update_mic_button()
    assert not window.mic_button.isChecked() and window.mic_button.text_ == "Zuhören"


def test_enroll_dialog_prepares_itself(env, monkeypatch):
    """„Stimme anlernen“ geht auch, wenn Sprachbefehle aus sind oder die Stimmerkennung fehlt."""
    controller, window, _ = env
    from alupc import voice
    from alupc.ui.voice_enroll import VoiceEnrollDialog

    vc = controller.voice
    started, stopped = [], []
    monkeypatch.setattr(vc, "start", lambda: started.append(1))
    monkeypatch.setattr(vc, "stop", lambda: stopped.append(1))
    monkeypatch.setattr(vc, "running", lambda: False)
    monkeypatch.setattr(voice, "spk_ready", lambda *a: False)
    dlg = VoiceEnrollDialog(controller, window)
    assert not dlg.dl_btn.isHidden() and not started  # erst Stimmerkennung laden
    assert "herunterladen" in dlg.sentence.text()
    dlg.reject()
    monkeypatch.setattr(voice, "spk_ready", lambda *a: True)
    dlg = VoiceEnrollDialog(controller, window)
    assert dlg.dl_btn.isHidden() and started == [1]  # Zuhören nur fürs Anlernen gestartet
    assert vc.enrolling
    vc.state, vc.has_spk = "hört zu", True
    dlg._update()
    assert dlg.sentence.text().startswith("„")  # erster Satz zum Vorlesen
    for i in range(3):
        dlg._sample([1.0, 0.5, float(i)], 120, "satz")
    dlg.name.setText("Lena")
    assert dlg.save_btn.isEnabled()
    dlg.save()
    assert not vc.enrolling and stopped == [1]  # wieder aus, weil Sprachbefehle aus sind
    assert [v["name"] for v in controller.config["voice"]["voices"]] == ["Lena"]
    vc.state, vc.has_spk = "aus", False


def test_screensaver_status_explains_why(env):
    controller, window, _ = env
    s = controller.screensaver
    controller.config["screensaver"] = {**controller.config["screensaver"], "enabled": False}
    assert "Automatisch: aus" in s.status()
    controller.config["screensaver"] = {**controller.config["screensaver"], "enabled": True, "when": "desktop"}
    controller.show_source({"type": "color"}, remember=False)
    assert "zeigt gerade etwas" in s.status()
    controller.config["screensaver"] = {**controller.config["screensaver"], "when": "immer"}
    assert s.status().startswith("Startet nach")
    controller.toggle_screensaver()
    assert s.active and s.status().startswith("Läuft gerade")
    controller._voice_command("bildschirmschoner_aus", "Bildschirmschoner aus", "")
    assert not s.active and controller.speaker.spoken[-1].endswith("Der Bildschirmschoner ist aus.")


def test_voice_camera_airplay_qr_are_known_commands(env, monkeypatch):
    """„Alu PC, Kamera“ gab bis 0.84 „Unbekannter Befehl“ (gab es nur fürs Handy)."""
    controller, window, _ = env
    called, msgs = [], []
    controller.message.connect(msgs.append)
    for name in ("start_camera", "start_airplay", "start_cast"):
        monkeypatch.setattr(controller, name, lambda n=name: called.append(n))
    for cmd in ("kamera", "airplay", "qr"):
        controller.run_command(cmd)
    assert called == ["start_camera", "start_airplay", "start_cast"]
    assert not any("Unbekannter Befehl" in m for m in msgs)


def test_rgb_late_mainboard_and_argb_header(env):
    """ASUS B650: Mainboard taucht in OpenRGB erst später auf, ARGB-Anschluss steht auf 0 LEDs – AluPC holt das
    Mainboard per „Geräte neu suchen“ nach, zeigt den Anschluss mit LED-Anzahl und merkt sie sich."""
    import sys
    import time

    sys.path.insert(0, str(HERE))
    from fake_openrgb import FakeOpenRGB

    from alupc.ui.hardware_page import HardwarePage

    controller, window, _ = env
    fake = FakeOpenRGB(4)
    fake.devices = [("Logitech Maus", 6, [("Logo", 1, False, 0)])]
    controller.config["rgb"] = {**controller.config["rgb"], "port": fake.port, "start_openrgb": False,
                                "color": "#ff0000", "mode": "farbe"}
    page = HardwarePage(controller)
    controller.rgb.connect_async()
    assert _until(lambda: controller.rgb.connected, 5)
    pump()
    assert "Kein Mainboard" in page.board_hint.text()
    fake.devices.append(("ASUS TUF GAMING B650-PLUS WIFI", 0,
                         [("Aura Mainboard", 1, False, 0), ("Aura Addressable 1", 0, False, 0, 120)]))
    controller.rgb.rescan(quiet=False)
    assert _until(lambda: len(controller.rgb.devices) == 2, 5)
    pump()
    assert page.board_hint.isHidden()
    assert "ohne LEDs" not in controller.rgb.text.lower()  # Mainboard selbst hat 1 LED
    time.sleep(0.2)
    assert fake.leds(1) == [(255, 0, 0)]  # neues Gerät bekommt gleich die Farbe
    controller.rgb.set_zone_size("ASUS TUF GAMING B650-PLUS WIFI", "Aura Addressable 1", 24)
    time.sleep(0.2)
    assert fake.leds(1) == [(255, 0, 0)] * 25
    assert controller.config["rgb"]["zone_sizes"] == {"ASUS TUF GAMING B650-PLUS WIFI|Aura Addressable 1": 24}
    pump()
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QSpinBox

    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)  # alte Zeilen wirklich weg
    spins = page.devices_box.findChildren(QSpinBox)
    assert [s.value() for s in spins] == [24]
    controller.rgb.shutdown()
    fake.close()


def test_voice_test_dialog_shows_each_step(env, monkeypatch):
    """„Sprache testen“: zeigt je Schritt ✓/✗ und führt im Test nichts aus."""
    import json

    from alupc import voice
    from alupc.ui.voice_test import VoiceTestDialog

    controller, window, _ = env
    vc = controller.voice

    class FakeRec:
        def __init__(self):
            self.text = ""

        def AcceptWaveform(self, data):
            self.text = data.decode()
            return not self.text.startswith("~")

        def Result(self):
            return json.dumps({"text": self.text})

        def PartialResult(self):
            return json.dumps({"partial": self.text[1:]})

    vc.recognizer_factory = FakeRec
    monkeypatch.setattr(voice, "vosk_available", lambda: True)
    monkeypatch.setattr(voice, "model_ready", lambda *a: True)
    ran = []
    monkeypatch.setattr(controller, "run_command", lambda c: ran.append(c))
    dlg = VoiceTestDialog(controller, window)
    assert _until(lambda: vc.state == "hört zu", 5)  # startet das Zuhören auch ohne „Sprachbefehle an“
    assert vc.dry_run and vc.show_partial
    vc.feed(b"~alu pc licht")  # mitten im Satz
    assert _until(lambda: "alu pc licht" in dlg.live.text(), 3)
    vc.feed(b"alu pc licht auf blau")
    assert _until(lambda: dlg.status["command"][0] == "✓", 3)
    assert dlg.status["wake"][0] == "✓" and "Licht blau" in dlg.status["command"][1]
    vc.feed(b"licht auf rot")  # ohne Startwort
    assert _until(lambda: dlg.status["wake"][0] == "✗", 3)
    dlg._level(0.4)
    dlg._refresh()
    assert dlg.status["sound"][0] == "✓" and dlg.status["model"][0] == "✓" and dlg.status["running"][0] == "✓"
    report = dlg.report()
    assert "Startwort erkannt" in report and "Pegel max: 40 %" in report
    dlg.accept()
    assert ran == [] and not vc.dry_run  # im Test nichts ausgeführt
    assert _until(lambda: vc.state == "aus", 3)  # nur für den Test gestartet → wieder aus
    vc.recognizer_factory = None


# ---------------------------------------------------------------- 0.92: Befehlssuche + „Frag AluPC“ vom Handy
def test_command_palette_understands_sentences_and_finds_pages(env):
    controller, window, _ = env
    from alupc.ui.command_palette import CommandPalette
    from alupc.ui.setup_page import SetupPage

    pal = CommandPalette(window)
    pal.edit.setText("licht auf blau")
    first = pal.list.item(0)
    assert first.data(Qt.UserRole) == "ask:licht auf blau" and "Licht blau" in first.text()
    pal.run_selected()
    assert controller.rgb.settings()["color"] == "#0000ff"
    assert controller.assistant.last_reply  # Antwort gemerkt (für Handy/Hinweis)
    pal = CommandPalette(window)
    pal.edit.setText("setup sprache")
    actions = [pal.list.item(i).data(Qt.UserRole) for i in range(pal.list.count())]
    assert "setup:Sprache" in actions
    pal.list.setCurrentRow(actions.index("setup:Sprache"))
    pal.run_selected()
    pump()
    setup = window.findChild(SetupPage)
    assert window.stack.currentIndex() == 2 and setup.nav.currentRow() == [t for _i, t, _s in SetupPage.SECTIONS].index("Sprache")
    pal = CommandPalette(window)
    pal.edit.setText("xyzquatsch")
    assert pal.list.count() == 0


def test_ask_from_phone_and_pc(env):
    controller, window, _ = env
    r = controller.ask("Alu PC, mach den Bildschirm schwarz")
    assert r["ok"] and r["command"] == "schwarz_an" and controller.privacy
    r = controller.ask("Bild wieder an")
    assert r["ok"] and not controller.privacy
    seq = controller.assistant.reply_seq
    r = controller.ask("blabla quatsch")
    assert not r["ok"] and "nicht verstanden" in r["reply"] and controller.assistant.reply_seq == seq + 1
    r = controller.ask("Computer sperren", source="handy")  # nur am PC
    assert not r["ok"] and "nur direkt am PC" in r["reply"]
    controller._cast_request({"kind": "ask", "text": "Timer auf zwei Minuten"})
    from alupc.timer import clock

    assert clock.running
    controller._cast_tick() if hasattr(controller, "_cast_tick") else None
    controller._update_cast_snapshot() if hasattr(controller, "_update_cast_snapshot") else None


def test_nav_indicator_follows_page_and_ring_animates(env, monkeypatch):
    """Seitenleiste: die Markierung sitzt unter der gewählten Seite (auch nach Kompaktmodus); der Fingerabdruck-Ring
    animiert Erfolg/Fehler (mit Animationen an) und steht danach still."""
    from PySide6.QtWidgets import QApplication

    from alupc.ui.widgets import ProgressRing

    _controller, window, _ = env
    ind = window.nav_indicator
    for index in (4, 2, 0):
        window._go(index)
        QApplication.processEvents()
        button = window.nav_group.button(index)
        assert ind.target is button and ind.geometry() == button.geometry()
        assert button.external_indicator
    window.apply_compact(True, False)
    QApplication.processEvents()
    assert ind.compact and ind.geometry() == window.nav_group.button(0).geometry()
    window.apply_compact(False, False)
    monkeypatch.setenv("ALUPC_NO_ANIMATION", "0")
    ring = ProgressRing()
    ring.set_state("ok")
    assert ring._fx_anim.state() == ring._fx_anim.State.Running
    ring._fx_anim.setCurrentTime(ring._fx_anim.duration())
    assert ring._fx == 1.0
    ring.set_state("error")
    assert ring._fx_anim.state() == ring._fx_anim.State.Running
    ring.grab()  # zeichnet mitten in der Animation ohne Fehler



def test_show_file_from_file_manager(env):
    """Rechtsklick „Auf Monitor 2 zeigen“ → `alupc --zeigen DATEI` → laufendes AluPC zeigt die Datei."""
    controller, _window, tmp = env
    img = tmp / "foto.png"
    QImage(40, 30, QImage.Format_RGB32).save(str(img))
    pdf = tmp / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    controller.run_command("datei:" + str(img))
    pump()
    assert controller.content == {"type": "image", "path": str(img)}
    controller.run_command("datei:" + str(tmp))
    assert controller.content["type"] == "slideshow" and controller.content["folder"] == str(tmp)
    controller.run_command("datei:" + str(pdf))
    assert controller.content == {"type": "website", "url": pdf.resolve().as_uri()}
    got = []
    controller.message.connect(got.append)
    (tmp / "x.docx").write_text("x")
    controller.run_command("datei:" + str(tmp / "x.docx"))
    controller.run_command("link_unbekannt:alupc://pc_herunterfahren")
    assert got == ["Geht nicht auf dem Bildschirm: x.docx", "Unbekannter AluPC-Link: alupc://pc_herunterfahren"]
    assert controller.content["type"] == "website"


def test_phone_video_controls(env, test_video):
    """Handy: Video mit Stelle springen, Tempo, Wiederholen, von vorn, „Weiterschauen“-Liste (echtes Video)."""
    import time

    controller, _window, _ = env
    controller.show_source({"type": "video", "path": test_video, "loop": True, "muted": True})
    pump()
    from alupc.sources import video_sources

    video = video_sources(controller.output.content)[0]
    assert _until(lambda: video.duration() > 0), "Video lädt nicht"

    def phone(cmd):
        controller._cast_request({"kind": "cmd", "cmd": cmd})
        pump()

    controller._cast_snapshot()  # Status fürs Handy neu – jetzt kennt das Video seine Länge
    info = controller.cast.snapshot["vid"]
    assert info["title"] == "test" and info["dur"] == 4 and info["loop"] and info["rate"] == 1.0
    phone("video_pause")
    assert _until(lambda: not video.playing())
    phone("video_pos:3")
    assert _until(lambda: abs(video.position() - 3000) < 400), video.position()
    phone("video_tempo:1.5")
    assert abs(video.rate() - 1.5) < 0.01 and controller.cast.snapshot["vid"]["rate"] == 1.5
    phone("video_wiederholen")
    assert not video.looping() and controller.cast.snapshot["vid"]["loop"] is False
    phone("video_neu")
    assert _until(lambda: video.position() < 1500 and video.playing()), video.position()
    # Weiterschauen: gemerkte Stelle eines Videos → vom Handy fortsetzen
    controller.show_source({"type": "color"})
    pump()
    controller.config["video_positions"] = {test_video: {"pos": 2000, "dur": 4000, "t": int(time.time())},
                                            "/gibt/es/nicht.mp4": {"pos": 1, "dur": 9, "t": 1}}
    controller._cast_snapshot()
    assert controller.cast.snapshot["recent"] == [{"title": "test", "pos": 2, "dur": 4}]  # fehlende Datei fehlt
    phone("video_fortsetzen:0")
    assert controller.resume_offer is None and controller.content["path"] == test_video
    video = video_sources(controller.output.content)[0]
    assert _until(lambda: video.position() >= 1800), video.position()
    phone("video_fortsetzen:7")  # gibt es nicht → nichts passiert
    assert controller.content["path"] == test_video


def test_phone_commands_allowed():
    """Nur diese neuen Befehle darf das Handy schicken (Server-Prüfung)."""
    import re

    from alupc import cast_server

    src = open(cast_server.__file__, encoding="utf-8").read()
    for ok in ("video_pos:125", "video_tempo:1.25", "video_fortsetzen:3", "ton_laut:40", "mic_stumm:1",
               "ton_geraet:2", "mic_geraet:0"):
        assert re.fullmatch(r"video_pos:\d{1,6}", ok) or re.fullmatch(r"video_tempo:(0\.5|0\.75|1|1\.25|1\.5|2)", ok) \
            or re.fullmatch(r"video_fortsetzen:\d", ok) \
            or re.fullmatch(r"(ton|mic)_(laut:\d{1,3}|stumm:[01]|geraet:\d{1,2})", ok), ok
    assert r'(ton|mic)_(laut:\d{1,3}|stumm:[01]|geraet:\d{1,2})' in src
    assert not re.fullmatch(r"video_tempo:(0\.5|0\.75|1|1\.25|1\.5|2)", "video_tempo:99")


def test_pc_media_bar_more_and_resume(env, test_video):
    """PC wie Handy: Medienleiste → „Mehr“ (±30 s, Tempo, Wiederholen, Von vorn) und „Weiterschauen“."""
    import time

    controller, window, _ = env
    controller.show_source({"type": "video", "path": test_video, "loop": True, "muted": True})
    pump()
    bar = window.media_bar
    video = bar.current()
    assert _until(lambda: video.duration() > 0), "Video lädt nicht"
    controller.config["video_positions"] = {test_video: {"pos": 2000, "dur": 4000, "t": int(time.time())}}
    bar._fill_more()
    acts = {a.text(): a for a in bar.more_menu.actions()}
    assert {"30 Sekunden zurück", "30 Sekunden vor", "Von vorn", "Wiederholen", "Tempo", "Weiterschauen"} <= set(acts)
    tempo = {a.text(): a for a in acts["Tempo"].menu().actions()}
    assert list(tempo) == ["0,5×", "0,75×", "1×", "1,25×", "1,5×", "2×"] and tempo["1×"].isChecked()
    tempo["1,5×"].trigger()
    assert abs(video.rate() - 1.5) < 0.01
    assert acts["Wiederholen"].isChecked()
    acts["Wiederholen"].trigger()
    assert not video.looping()
    acts["Von vorn"].trigger()
    assert _until(lambda: video.position() < 1500 and video.playing())
    resume = acts["Weiterschauen"].menu().actions()
    assert len(resume) == 1 and resume[0].text().startswith("test  ·  0:02 / 0:04")
    controller.show_source({"type": "color"})
    pump()
    resume[0].trigger()
    pump()
    assert controller.content["path"] == test_video
    v = video_sources_of(controller)
    assert _until(lambda: v.position() >= 1800), v.position()


def video_sources_of(controller):
    from alupc.sources import video_sources

    return video_sources(controller.output.content)[0]


def test_wayland_native_mirror_never_asks_for_screen(env, monkeypatch):
    """Wayland (natives Qt) ohne KWin-Freigabe: Spiegeln fragt NICHT „Welchen Bildschirm teilen?“, sondern lässt
    KDE ohne Nachfrage spiegeln – der Bildschirm wird automatisch gewählt."""
    import sys

    from alupc import sources
    from alupc.platform import linux_display

    if not sys.platform.startswith("linux"):
        pytest.skip("nur Linux")
    controller, _window, _ = env
    calls = []
    monkeypatch.setattr(linux_display, "is_wayland", lambda: True)
    monkeypatch.setattr(sources, "_kwin_allowed", lambda name: False)
    monkeypatch.setattr(controller.display, "available", lambda: True)
    monkeypatch.setattr(controller.display, "mirror", lambda main, out: calls.append((main, out)))
    monkeypatch.setattr(controller, "screens_overlap", lambda: False)
    controller.mirror()
    assert _until(lambda: calls, 3)
    assert calls[0] == ("Haupt", "Zweit")
    assert controller.output.content is None or not hasattr(controller.output.content, "capture")
    # mit KWin-Freigabe: AluPC nimmt selbst auf (dann ohne Nachfrage)
    calls.clear()
    monkeypatch.setattr(sources, "_kwin_allowed", lambda name: True)
    assert not controller._capture_would_ask(controller.main_screen())


# ---------------------------------------------------------------- 2.2: Studio-Aufbau
def test_quick_style_switch(env):
    """Eckknopf: Stil/Hell-Dunkel/Profil sofort wechseln; in Setup → Darstellung ausblendbar."""
    controller, window, _ = env
    from alupc.ui import theme

    window.show()
    pump()
    assert window.style_button.isVisible()
    window.open_style_popover()  # grafisches Auswahlfeld: Vorschaukarten je Stil
    pump()
    from alupc.ui.style_popover import StyleCard

    pop = window.style_popover
    cards = {c.key: c for c in pop.findChildren(StyleCard)}
    assert set(cards) == set(theme.STYLES) and pop.isVisible()
    cards["technisch"].click()
    pump()
    assert controller.config["appearance"]["style"] == "technisch" and theme.current().technical
    assert {c.key for c in pop.findChildren(StyleCard) if c.current} == {"technisch"}  # Auswahl sofort sichtbar
    pop.close()
    window.quick_profile("AluPC Standard")
    a = controller.config["appearance"]
    assert a["style"] == "normal" and a["profile"] == "AluPC Standard"
    window.quick_appearance(quick_switch=False)
    assert not window.style_button.isVisible()
    window.open_setup_section("Darstellung")
    pump()
    assert not window.setup.quick_switch.isChecked()
    window.setup.quick_switch.setChecked(True)
    pump()
    assert controller.config["appearance"]["quick_switch"] and window.style_button.isVisible()
    window._go(0)


def test_scene_page_search_and_panel(env):
    """Szenen: Suche filtert das Raster, rechts steht die gewählte Szene mit ihren Feldern."""
    controller, window, _ = env
    config = controller.config
    config.put_scene({"name": "Pause", "layout": "vollbild", "background": "#000000", "slots": [{"type": "clock"}]})
    config.put_scene({"name": "Begrüßung", "layout": "vollbild", "background": "#000000",
                      "slots": [{"type": "design", "design": "willkommen"}]})
    window._reload_scenes("Pause")
    window._go(1)
    pump()
    assert window.scene_name.text() == "Pause" and window.scene_slots.count() == 1
    assert window.new_scene_card in window.scene_grid.items
    window.scene_search.setText("begr")
    pump()
    assert [c.scene["name"] for c in window.scene_grid.items] == ["Begrüßung"]
    assert "1 von" in window.scene_count.text()
    window.scene_search.clear()
    window._go(0)


def test_setup_search_filters_sections(env):
    """Setup-Suche findet Bereiche über ihre Beschriftungen (z. B. „Rundung“ → Darstellung)."""
    controller, window, _ = env
    from alupc.ui.setup_page import SetupPage

    window.open_setup_section("Monitore")
    setup = window.setup
    setup.search.setText("Rundung")
    pump()
    titles = [t for _i, t, _s in SetupPage.SECTIONS]
    visible = [titles[i] for i in range(len(titles)) if not setup.nav.isRowHidden(i)]
    assert visible == ["Darstellung"] and setup.nav.currentRow() == titles.index("Darstellung")
    assert setup.section_title.text() == "Darstellung"
    setup.search.setText("gibtsnichtxyz")
    pump()
    assert setup.no_match.isVisible() or not setup.stack.isVisible()
    setup.search.clear()
    pump()
    assert not any(setup.nav.isRowHidden(i) for i in range(len(titles)))
    window._go(0)


def test_studio_mode_preview_and_take(env):
    """Studio-Modus auf der Startseite: Vorschau klappt über dem Live-Bild auf; Inhalte gehen erst dorthin,
    „Übergang“ schaltet live, der alte Inhalt wandert in die Vorschau. Kacheln: Live rot, Vorschau grün.
    Kamera zeigt die Vorschau nur als Schema (gibt es nur einmal)."""
    controller, window, _ = env
    window.show()
    controller.show_source({"type": "clock"})
    pump()
    panel = window.studio_panel
    assert panel.isHidden()
    window.toggle_studio()
    pump()
    assert controller.studio and window.studio_button.isChecked() and panel.isVisible() and panel.height() > 100
    assert controller.preview == {"type": "clock"}  # aktueller Inhalt steht auch in der Vorschau
    controller.show_source({"type": "text", "text": "Hallo"})
    pump()
    assert controller.content == {"type": "clock"}  # live unverändert
    assert controller.preview["type"] == "text" and panel.preview_widget is not None
    assert window.t_text.cued and not window.t_text.active
    controller.take(("schnitt", 0))
    pump()
    assert controller.content["type"] == "text" and controller.preview == {"type": "clock"}
    assert window.t_text.active and not window.t_text.cued
    controller.show_source({"type": "camera"})
    pump()
    assert panel.preview_widget is None and panel.screen.placeholder is not None and window.t_camera.cued
    controller.clear_preview()
    pump()
    assert not panel.take_button.isEnabled()
    window.toggle_studio()
    pump()
    assert not controller.studio and not window.studio_button.isChecked() and panel.isHidden()
    assert not window.t_camera.cued
    controller.show_source({"type": "clock"})
    assert controller.content == {"type": "clock"}  # ohne Studio-Modus wieder sofort live
    controller.extend()
