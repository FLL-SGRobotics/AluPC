"""Tastenbox (Arduino Pro Micro): Zeilen der Box, Tasten → Befehle, Lautstärke nur am Regler, LED, Setup."""

from test_gui import _until, env, pump  # noqa: F401  (Fixture + Hilfen der Oberflächen-Tests)

from alupc import tastenbox
from alupc.tastenbox import parse_line


def test_parse_lines():
    assert parse_line("HALLO ALUPC-TASTEN 1\r\n") == ("hallo", "1")
    assert parse_line("TASTE 3") == ("taste", 3)
    assert parse_line("taste 9") == ("taste", 9)
    assert parse_line("TASTE 10") is None and parse_line("TASTE 0") is None
    assert parse_line("LAUT 57") == ("laut", 57)
    assert parse_line("LAUT 250") == ("laut", 100)
    assert parse_line("STUMM") == ("stumm", None)
    assert parse_line("") is None and parse_line("Müll") is None and parse_line("LAUT x") is None


def test_parse_v3_lines():
    """Programm 3: Board in der Begrüßung, Rohwerte für die Fehlersuche."""
    assert parse_line("HALLO ALUPC-TASTEN 3 NANO") == ("hallo", "3 NANO")
    assert tastenbox.hello_info("3 NANO") == (3, "Nano") and tastenbox.hello_info("3 PROMICRO") == (3, "Pro Micro")
    assert tastenbox.hello_info("2") == (2, "") and tastenbox.hello_info("") == (1, "")
    assert parse_line("ROH 010000001 512") == ("roh", ("010000001", 512))
    assert parse_line("ROH 0100 512") is None and parse_line("ROH 01000000x 5") is None


def test_only_arduino_ports_are_asked(monkeypatch):
    """Fremde Geräte (Drucker, Modems, Fingerabdruck-Modul) bekommen nie „WER?“ geschickt."""
    from types import SimpleNamespace

    from serial.tools import list_ports

    ports = [SimpleNamespace(device="COM3", vid=0x2341, description="Arduino Leonardo", manufacturer="", product=""),
             SimpleNamespace(device="COM4", vid=0x1A86, description="USB-SERIAL CH340", manufacturer="", product=""),
             SimpleNamespace(device="COM5", vid=0x1B4F, description="SparkFun Pro Micro", manufacturer="", product=""),
             SimpleNamespace(device="COM6", vid=None, description="Bluetooth", manufacturer="", product="")]
    monkeypatch.setattr(list_ports, "comports", lambda: ports)
    # USB-Seriell-Chip (Nano-Nachbau) erst nach den Arduino-Kennungen – und nur einmal je Einstecken (siehe _silent)
    assert tastenbox.candidate_ports() == ["COM3", "COM5", "COM4"]
    assert tastenbox.candidate_ports(chips=False) == ["COM3", "COM5"]
    assert tastenbox.candidate_ports({"COM3", "COM4"}) == ["COM5"]


def test_keys_run_commands_and_volume_only_at_knob(env, monkeypatch):  # noqa: F811
    controller, window, _ = env
    from alupc import pc_control

    calls = []
    monkeypatch.setattr(pc_control, "set_volume", lambda percent=None, step=0: calls.append(("set", percent)) or (True, ""))
    monkeypatch.setattr(pc_control, "set_mute", lambda on: calls.append(("mute", on)) or (True, ""))
    monkeypatch.setattr(pc_control, "get_volume", lambda: 30)
    box = controller.buttonbox
    box.port = "COM9"  # wie verbunden (ohne echte Box)
    sent = []
    monkeypatch.setattr(box, "send", lambda line: sent.append(line) or True)

    # Taste → Befehl (Standardbelegung: 1 = Schwarz)
    assert not controller.privacy
    box.feed("TASTE 1")
    pump()
    assert controller.privacy
    controller.config["tasten"] = {**controller.config["tasten"], "keys": {"2": "szene:Gibt es nicht"}}
    box.feed("TASTE 2")
    pump()
    controller.toggle_privacy()

    # Regler meldet sich → Lautstärke folgt ihm; andere Wege sind gesperrt
    assert not controller.volume_locked()
    box.feed("LAUT 40")
    pump(30)
    assert controller.volume_locked() and ("set", 40) in calls
    messages = []
    controller.message.connect(messages.append)
    controller.run_command("pc_lauter")
    controller.run_command("ton_laut:90")
    pump()
    assert messages.count("Lautstärke geht nur am Regler.") == 2
    assert not any(c == ("set", 90) for c in calls)

    # Jemand dreht in Windows/KDE (30 % statt 40 %) → zurück auf den Regler
    keeper = controller.volume_keeper
    assert _until(lambda: not keeper._busy)
    calls.clear()
    keeper._ignore_until = 0
    keeper._check()
    assert _until(lambda: ("set", 40) in calls, 5)

    # Regler-Sperre aus → Befehle gehen wieder
    controller.config["tasten"] = {**controller.config["tasten"], "nur_regler": False}
    assert not controller.volume_locked()

    # LED „live“: leuchtet, solange etwas auf Bildschirm 2 läuft
    controller.config["tasten"] = {**controller.config["tasten"], "led": "live"}
    controller._led_level = None
    controller.show_source({"type": "clock"})
    pump()
    assert sent and sent[-1] == "LED 100"
    controller.extend()
    pump()
    assert sent[-1] == "LED 0"
    box.port = ""
    box.knob = None
    keeper.set_target(None)


def test_tastenbox_page(env):  # noqa: F811
    """Eigene Seite: Tasten 3 × 3 (Taste leuchtet beim Drücken), Belegung, Regler-Anzeige; Setup-Link führt hin."""
    controller, window, _ = env
    window.open_setup_section("Tastenbox")
    pump()
    from alupc.ui.tastenbox_page import TastenboxPage

    page = window.findChild(TastenboxPage)
    assert page is not None and window.stack.currentIndex() == 7
    assert len(page.caps) == 9 and page.action.currentData() == "schwarz"
    assert page.caps[1].text  # zeigt, was die Taste tut (auf der Box steht nichts)
    page.select(5)
    page.action.setCurrentIndex(page.action.findData("standbild"))
    page.action.activated.emit(page.action.currentIndex())
    pump()
    assert controller.box_keys()["5"] == "standbild" and page.caps[5].text == "Standbild an/aus"
    controller.buttonbox.key.emit(3)  # echte Taste gedrückt → leuchtet und wird ausgewählt
    assert page.caps[3]._anim.state() == page.caps[3]._anim.State.Running
    assert _until(lambda: page.selected == 3, 2) and page.key_label.text() == "Taste 3:"  # nach kurzer Ruhe
    assert page.target.findData("medien") >= 0 and page.target.findData("mikro") >= 0
    assert tastenbox_sketch_exists()
    window._go(0)


def test_knob_target_waits_for_zero(env, monkeypatch):  # noqa: F811
    """Ziel wechseln → neues Ziel auf 0, Regler greift erst, wenn er auch auf 0 steht (Skala am Regler)."""
    controller, _window, _ = env
    from alupc import audio, pc_control

    calls = []
    monkeypatch.setattr(pc_control, "set_volume", lambda percent=None, step=0: calls.append(("pc", percent)) or (True, ""))
    monkeypatch.setattr(pc_control, "set_mute", lambda on: (True, ""))
    monkeypatch.setattr(pc_control, "get_volume", lambda: None)
    monkeypatch.setattr(audio, "set_level", lambda kind, v: calls.append((kind, v)) or (True, ""))
    box = controller.buttonbox
    box.port = "COM9"
    monkeypatch.setattr(box, "send", lambda line: True)
    box.feed("LAUT 60")
    pump(30)
    assert controller.volume_locked()

    controller.set_knob_target("mikro")
    assert _until(lambda: ("in", 0) in calls)
    assert controller.knob_wait and not controller.volume_locked()  # PC-Ton ist wieder frei
    calls.clear()
    box.feed("LAUT 55")  # noch nicht auf 0 → nichts passiert
    pump(30)
    assert not any(k == "in" for k, _v in calls)
    box.feed("LAUT 1")   # auf 0 gedreht → Regler greift
    box.feed("LAUT 30")
    assert _until(lambda: ("in", 30) in calls)
    assert not controller.knob_wait

    controller.set_knob_target("pc")  # zurück: PC-Ton auf 0, wartet wieder
    assert controller.knob_wait and controller.volume_keeper.target == 0
    box.feed("LAUT 0")
    box.feed("LAUT 25")
    pump(30)
    assert controller.volume_keeper.target == 25
    box.port = ""
    box.knob = None
    controller.volume_keeper.set_target(None)


def tastenbox_sketch_exists() -> bool:
    from alupc.ui.tastenbox_page import SKETCH

    text = SKETCH.read_text(encoding="utf-8")
    return all(w in text for w in ("HALLO ALUPC-TASTEN 9", "BELEGUNG", "SUCHE", "EEPROM", "TON ", "BUZZER", "LAUT", "WERT", "TASTE", "LANG", "EFFEKT", "FARBE", "DEBUG", "ROH", "PIXEL",
                                   "__AVR_ATmega32U4__"))


def test_audio_default_output_locked(env, monkeypatch):  # noqa: F811
    """Audio-Seite: Standard-Lautsprecher ausgegraut, solange der Regler gilt; Programme bleiben frei."""
    from types import SimpleNamespace

    from alupc.ui.audio_page import Strip

    controller, _window, _ = env
    page = SimpleNamespace(controller=controller)
    monkeypatch.setattr(controller, "volume_locked", lambda: True)
    speaker = Strip(page, "out", {"id": "spk", "name": "Lautsprecher", "volume": 40, "default": True})
    other = Strip(page, "out", {"id": "hdmi", "name": "HDMI", "volume": 40, "default": False})
    app = Strip(page, "out", {"id": "7", "app": "Spotify", "volume": 40, "default": True}, stream=True)
    assert not speaker.slider.isEnabled() and not speaker.mute.isEnabled()
    assert other.slider.isEnabled() and app.slider.isEnabled()



def test_long_press_and_knob_options(env, monkeypatch):  # noqa: F811
    """Lang drücken = eigener Befehl; Regler umkehren, Höchstwert, ohne Null-Abgleich; LED-Helligkeit und Blitz."""
    controller, _window, _ = env
    from alupc import audio, pc_control

    assert parse_line("LANG 4") == ("lang", 4) and parse_line("LANG 0") is None
    ran = []
    monkeypatch.setattr(controller, "run_command", lambda a: ran.append(a))
    box = controller.buttonbox
    sent = []
    monkeypatch.setattr(box, "send", lambda line: sent.append(line) or True)
    box.port = "COM9"
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "lang": {"4": "standbild"}, "led_blink": True, "led_hell": 40,
                                   "led": "an", "nur_regler": False}
    box.feed("LANG 4")
    box.feed("LANG 5")  # ohne eigenen Befehl: wie kurz (5 = nächste Szene)
    assert ran == ["standbild", "naechste_szene"]
    assert any(line in ("LED 0", "LED 40") for line in sent)  # Blitz
    controller._led_level = None
    controller.update_box_led()
    assert sent[-1] == "LED 40"

    # Regler: umgekehrt und höchstens 80 %
    controller.config["tasten"] = {**controller.config["tasten"], "umkehren": True, "max": 80}
    assert controller.knob_value(0) == 80 and controller.knob_value(100) == 0 and controller.knob_value(None) is None

    # ohne Null-Abgleich folgt ein neues Ziel sofort der Reglerstellung
    levels = []
    monkeypatch.setattr(audio, "set_level", lambda kind, v: levels.append((kind, v)) or (True, ""))
    monkeypatch.setattr(pc_control, "set_volume", lambda percent=None, step=0: (True, ""))
    controller.config["tasten"] = {**controller.config["tasten"], "umkehren": False, "max": 100, "abgleich": False}
    box.knob = 30
    controller.set_knob_target("mikro")
    assert not controller.knob_wait and _until(lambda: ("in", 30) in levels)
    controller.config["tasten"] = {**base}
    controller.knob_wait = False
    box.port = ""
    box.knob = None
    controller.volume_keeper.set_target(None)


def test_led_effect_color_and_debug_panel(env, monkeypatch):  # noqa: F811
    """LEDs über die Software: Effekt + Farbe gehen an die Box (ab Programm 3); Fehlersuche zeigt Protokoll,
    Rohwerte (Taste leuchtet, solange sie unten ist) und schaltet DEBUG an der Box an/aus."""
    controller, window, _ = env
    box = controller.buttonbox
    sent = []
    monkeypatch.setattr(box, "send", lambda line: sent.append(line) or True)
    box.port, box.version, box.board = "COM9", 3, "Nano"
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "led": "an", "led_effekt": "atmen", "led_farbe": "#ff0010", "led_hell": 60}
    controller._led_level = controller._led_extra = None
    controller.update_box_led()
    assert sent[-3:] == ["EFFEKT 1", "FARBE 255 0 16", "LED 60"]
    sent.clear()
    controller.update_box_led()
    assert sent == []  # nichts doppelt schicken
    box.version = 2  # altes Programm: kennt EFFEKT/FARBE nicht → nicht schicken
    controller._led_level = controller._led_extra = None
    controller.update_box_led()
    assert sent == ["LED 60"]

    box.version = 3
    window.open_setup_section("Tastenbox")
    pump()
    from alupc.ui.tastenbox_page import TastenboxPage

    page = window.findChild(TastenboxPage)
    page.debug_toggle.setChecked(True)
    page.debug_raw.setChecked(True)
    assert sent[-1] == "DEBUG 1" and box.debug
    box.feed("ROH 001000000 1023")
    assert page.caps[3].down and not page.caps[1].down and "3" in page.raw_label.text()
    box.feed("TASTE 3")
    assert "← TASTE 3" in page.log_view.toPlainText()
    page._debug_info()
    assert "Programm 3" in page.debug_summary.text() and "Nano" in page.debug_summary.text()
    page.debug_raw.setChecked(False)
    assert sent[-1] == "DEBUG 0" and not page.caps[3].down
    page.debug_toggle.setChecked(False)
    controller.config["tasten"] = {**base}
    box.port, box.version, box.board = "", 0, ""
    window._go(0)


def test_led_commands_and_color_schemes(env, monkeypatch):  # noqa: F811
    """LEDs vom Handy (box_*-Befehle) und Farbschemata für RGB-LEDs (Programm 4: PIXEL, Regenbogen)."""
    controller, _window, _ = env
    box = controller.buttonbox
    sent = []
    monkeypatch.setattr(box, "send", lambda line: sent.append(line) or True)
    box.port, box.version = "COM9", 4
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "led": "an", "led_hell": 80}
    controller.run_command("box_schema:ecken")
    assert controller.config["tasten"]["led_schema"] == "ecken"
    assert [line for line in sent if line.startswith("PIXEL")] == [
        "PIXEL 0 87 193 255", "PIXEL 1 255 97 97", "PIXEL 2 89 212 153", "PIXEL 3 255 197 51"]
    sent.clear()
    controller.run_command("box_schema:regenbogen")
    assert "EFFEKT 3" in sent
    sent.clear()
    controller.run_command("box_schema:zustand")
    controller.privacy = True
    controller._led_extra = None
    controller.update_box_led()
    assert "PIXEL 0 255 176 0" in sent  # Schwarz/Standbild → Gelb
    controller.privacy = False
    for cmd, key, value in (("box_led:aus", "led", "aus"), ("box_effekt:atmen", "led_effekt", "atmen"),
                            ("box_farbe:#FF0000", "led_farbe", "#ff0000"), ("box_hell:300", "led_hell", 100)):
        controller.run_command(cmd)
        assert controller.config["tasten"][key] == value
    assert not controller.box_led_command("box_led:quatsch")
    snap = controller.cast.snapshot if hasattr(controller, "cast") else {}
    controller._cast_snapshot()
    assert controller.cast.snapshot["boxled"]["led_schema"] == "zustand" and controller.cast.snapshot["boxled"]["on"]
    box.version = 3  # altes Programm: kein PIXEL, Regenbogen wird ruhig
    sent.clear()
    assert not box.set_pixel(0, "#ffffff") and box.set_effect("regenbogen") and sent == ["EFFEKT 0"]
    controller.config["tasten"] = {**base}
    box.port, box.version = "", 0
    del snap


def test_automation_rules_model(monkeypatch):
    """Wenn → Dann: Regeln säubern, Code/Reglerzahl vergleichen, eigener Befehl ohne Shell-Zeichenkette."""
    from alupc import automations as a

    r = a.clean_rule({"name": " Intro ", "wenn": {"code": "1a20 3", "wert": "1200", "toleranz": 99},
                      "dann": {"art": "quatsch", "ziel": " standbild "}})
    assert r == {"name": "Intro", "an": True, "wenn": {"folge": "", "code": "123", "wert": 999, "toleranz": 50},
                 "dann": {"art": "alupc", "ziel": "standbild"}, "weitere": []}
    assert not a.is_valid(a.clean_rule({"wenn": {}, "dann": {"art": "alupc", "ziel": "x"}}))
    code = a.clean_rule({"name": "c", "wenn": {"code": "12"}, "dann": {"art": "alupc", "ziel": "x"}})
    both = a.clean_rule({"name": "b", "wenn": {"code": "12", "wert": 250}, "dann": {"art": "alupc", "ziel": "y"}})
    val = a.clean_rule({"name": "v", "wenn": {"wert": 500, "toleranz": 3}, "dann": {"art": "alupc", "ziel": "z"}})
    off = {**val, "an": False}
    rules = [code, both, val, off]
    assert a.rules_for_code(rules, "12", None) == [code]
    assert a.rules_for_code(rules, "12", 252) == [code, both]
    assert a.rules_for_code(rules, "21", 250) == []
    assert a.rules_for_value(rules, 503) == [val] and a.rules_for_value(rules, 504) == []
    assert "Code 1-2 und Regler 250 ± 3" in a.describe(both)
    assert a.command_line("echo hi && dir")[-1] == "echo hi && dir"
    started = []
    monkeypatch.setattr(a.subprocess, "Popen", lambda args, **kw: started.append(args))
    assert a.run_external("befehl", "echo hi")[0] and started[-1] == a.command_line("echo hi")
    assert a.run_external("programm", "prog --x 1")[0] and started[-1][-2:] == ["--x", "1"]


def _wait(seconds: float) -> None:
    import time

    end = time.time() + seconds
    while time.time() < end:
        pump()
        time.sleep(0.01)


def test_code_entry_and_value_rules(env, monkeypatch):  # noqa: F811
    """Code-Taste lang → Tasten sind Ziffern, nach der Pause passende Regel; Reglerzahl löst einmal aus."""
    controller, window, _ = env
    from alupc import automations

    assert parse_line("WERT 250") == ("wert", 250) and parse_line("WERT 1500") == ("wert", 999)
    monkeypatch.setattr(automations, "CODE_PAUSE_MS", 50)
    monkeypatch.setattr(automations, "VALUE_SETTLE_MS", 50)
    ran, ext = [], []
    real_run = controller.run_command
    monkeypatch.setattr(controller, "run_command",
                        lambda a: real_run(a) if a.startswith("regel:") else ran.append(a))
    monkeypatch.setattr(automations, "run_external", lambda art, ziel: ext.append((art, ziel)) or (True, "ok"))
    box = controller.buttonbox
    monkeypatch.setattr(box, "send", lambda line: True)
    box.port, box.version = "COM9", 5
    base = dict(controller.config["tasten"])
    rules = [{"name": "Video", "wenn": {"code": "123"}, "dann": {"art": "alupc", "ziel": "standbild"}},
             {"name": "Web", "wenn": {"code": "12", "wert": 250}, "dann": {"art": "webseite", "ziel": "example.org"}},
             {"name": "Prog", "wenn": {"wert": 700, "toleranz": 2}, "dann": {"art": "befehl", "ziel": "echo 7"}}]
    controller.config["tasten"] = {**base, "regeln": rules, "regler": "pc", "lang": {"9": "schwarz"}}

    # Code 1-2-3: Ziffern lösen ihre normalen Aktionen nicht aus
    box.feed("LANG 9")
    assert controller.box_code == ""
    for n in (1, 2, 3):
        box.feed(f"TASTE {n}")
    assert ran == [] and controller.box_code == "123"
    assert _until(lambda: ran == ["standbild"]) and controller.box_code is None
    # Code 1-2 mit Reglerzahl: nur wenn der Regler auf „Zahl“ steht und im Bereich
    controller.set_knob_target("wert")
    assert not controller.knob_wait
    box.feed("WERT 251")
    box.feed("LANG 9")
    box.feed("TASTE 1")
    box.feed("TASTE 2")
    assert _until(lambda: ext == [("webseite", "example.org")])
    # Reglerzahl allein: erst nach Ruhe, nur einmal, wieder nach Verlassen des Bereichs
    _wait(0.15)  # erster ruhiger Wert nach dem Wechsel löst nichts aus
    box.feed("WERT 699")
    assert _until(lambda: ("befehl", "echo 7") in ext)
    box.feed("WERT 701")
    _wait(0.15)
    assert ext.count(("befehl", "echo 7")) == 1
    box.feed("WERT 400")
    _wait(0.15)
    box.feed("WERT 700")
    assert _until(lambda: ext.count(("befehl", "echo 7")) == 2)
    # ohne Code-Regeln: lange Taste 9 macht wieder ihren Befehl; regel:Name von einer Taste
    controller.config["tasten"] = {**controller.config["tasten"], "regeln": rules[2:]}
    box.feed("LANG 9")
    assert controller.box_code is None and ran[-1] == "schwarz"
    controller.run_command("regel:Prog")
    assert ext.count(("befehl", "echo 7")) == 3
    # Seite: Liste + Livewert
    window.open_setup_section("Tastenbox")
    pump()
    from alupc.ui.tastenbox_page import TastenboxPage

    page = window.findChild(TastenboxPage)
    page._fill_rules()
    assert page.rules_list.count() == 1 and "700" in page.rules_live.text()
    controller.config["tasten"] = {**base}
    box.port, box.version, box.fine_value = "", 0, None
    controller._value_hits = None


def test_rule_dialog(env):  # noqa: F811
    controller, window, _ = env
    from alupc.ui.rules_dialog import RuleDialog

    dlg = RuleDialog(controller, None, {"Alt"}, window)
    assert not dlg.ok.isEnabled()
    assert dlg.trigger.currentData() == "folge"  # neue Regel: Tastenfolge vorgewählt
    dlg.trigger.setCurrentIndex(dlg.trigger.findData("code"))
    dlg.name.setText("Alt")
    dlg.code.setText("42")
    dlg.ziel.setText("vlc")
    assert not dlg.ok.isEnabled()  # Name schon vergeben
    dlg.name.setText("Neu")
    assert dlg.ok.isEnabled()
    dlg.art.setCurrentIndex(dlg.art.findData("alupc"))
    assert dlg.rule()["dann"]["ziel"] == dlg.command.currentData()
    dlg.use_value.setChecked(True)
    dlg.value.setValue(321)
    assert dlg.rule()["wenn"] == {"folge": "", "code": "42", "wert": 321, "toleranz": 3}
    # Tastenfolge über das 3 × 3-Feld: 7, 1, 2 antippen
    dlg.trigger.setCurrentIndex(dlg.trigger.findData("folge"))
    from PySide6.QtWidgets import QPushButton

    pad = {b.text(): b for b in dlg.seq_box.findChildren(QPushButton) if b.text().isdigit()}
    for n in "712":
        pad[n].click()
    assert dlg.rule()["wenn"]["folge"] == "712" and dlg.rule()["wenn"]["code"] == ""
    assert "unten links → oben links → oben Mitte" in dlg.seq_text.text() and dlg.ok.isEnabled()
    # Buzzer-Melodie als Aktion
    dlg.art.setCurrentIndex(dlg.art.findData("buzzer"))
    dlg.song.setCurrentIndex(dlg.song.findData("tetris"))
    assert dlg.rule()["dann"] == {"art": "buzzer", "ziel": "tetris"}
    dlg.deleteLater()


def test_knob_scale_presets(env, monkeypatch):  # noqa: F811
    """Voreinstellungen: Lautstärke über das Zählwerk – 000–999 ÷ 10 oder 000–100 direkt; LAUT zählt dann nicht."""
    controller, window, _ = env
    from alupc import pc_control

    vols = []
    monkeypatch.setattr(pc_control, "set_volume", lambda percent=None, step=0: vols.append(percent) or (True, ""))
    box = controller.buttonbox
    monkeypatch.setattr(box, "send", lambda line: True)
    box.port, box.version = "COM9", 5
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "nur_regler": False, "abgleich": False, "regler": "pc"}
    names = [p[0] for p in controller.KNOB_PRESETS]
    controller.apply_knob_preset(names.index("Lautstärke – Zählwerk 000–999 ÷ 10 (fein)"))
    assert controller.knob_scale() == "zahl_10"
    box.feed("WERT 455")
    box.feed("LAUT 90")  # grober Wert wird bei Zählwerk-Skala nicht benutzt
    assert _until(lambda: vols and vols[-1] == 46) and 90 not in vols
    controller.apply_knob_preset(names.index("Lautstärke – Zählwerk 000–100"))
    box.feed("WERT 73")
    assert _until(lambda: vols[-1] == 73)
    box.feed("WERT 640")
    assert _until(lambda: vols[-1] == 100)
    controller.config["tasten"] = {**controller.config["tasten"], "umkehren": True}
    assert controller.knob_level() == 100  # 999 - 640 = 359 → über 100
    controller.config["tasten"] = {**controller.config["tasten"], "umkehren": False, "skala": "prozent"}
    box.knob = 30
    assert controller.knob_level() == 30
    window.open_setup_section("Tastenbox")
    pump()
    from alupc.ui.tastenbox_page import TastenboxPage

    page = window.findChild(TastenboxPage)
    page.preset.setCurrentIndex(page.preset.findData(names.index("Mikrofon – Zählwerk ÷ 10")))
    page.preset.activated.emit(page.preset.currentIndex())
    assert controller.knob_target() == "mikro" and page.scale.currentData() == "zahl_10"
    controller.config["tasten"] = {**base}
    box.port, box.version, box.fine_value, box.knob = "", 0, None, None
    controller.knob_wait = False


def test_tastenbox_in_dual_boot_sync(env, monkeypatch):  # noqa: F811
    """Dual-Boot-Abgleich nimmt die Tastenbox mit (Tasten, Regler, LEDs, Regeln) – den Anschluss (COM3 vs.
    /dev/ttyACM0) nicht; nach dem Übernehmen wird die Box sofort neu eingestellt."""
    controller, window, _ = env
    from alupc import settings_sync as ss

    assert "tastenbox" in ss.SECTIONS and "tasten" in ss.synced_keys(controller.config)
    rules = [{"name": "Intro", "wenn": {"code": "12"}, "dann": {"art": "alupc", "ziel": "standbild"}}]
    controller.config["tasten"] = {**controller.config["tasten"], "port": "COM7", "keys": {"1": "schwarz"},
                                   "regeln": rules, "led_farbe": "#ff0000", "skala": "zahl_10"}
    exported = ss.export_settings(controller.config, ["tastenbox"])
    data = exported["data"]["tasten"]
    assert "port" not in data and data["keys"] == {"1": "schwarz"} and data["regeln"] == rules
    # anderes System: eigener Anschluss bleibt, alles andere kommt an
    controller.config["tasten"] = {**controller.config["tasten"], "port": "/dev/ttyACM0", "keys": {}, "regeln": [],
                                   "led_farbe": "#00ffa0", "skala": "prozent"}
    applied = []
    monkeypatch.setattr(controller, "apply_buttonbox", lambda: applied.append(True))
    changed = ss.import_settings(controller.config, exported, ["tastenbox"])
    t = controller.config["tasten"]
    assert changed == ["tasten"] and t["port"] == "/dev/ttyACM0" and t["keys"] == {"1": "schwarz"}
    assert t["regeln"] == rules and t["led_farbe"] == "#ff0000" and t["skala"] == "zahl_10"
    controller.settings_imported.emit(changed)
    pump()
    assert applied


def test_arduino_sketches_download(env, monkeypatch, tmp_path):  # noqa: F811
    """Beide Arduino-Programme lassen sich aus der App als fertiger IDE-Ordner speichern."""
    _controller, window, _ = env
    from alupc.ui import tastenbox_page
    from alupc.ui.tastenbox_page import SKETCHES, TastenboxPage

    window.open_setup_section("Tastenbox")
    pump()
    page = window.findChild(TastenboxPage)
    monkeypatch.setattr(tastenbox_page.QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    for name in SKETCHES:
        out = page._save_sketch(name)
        assert out == tmp_path / name / f"{name}.ino" and out.exists()
        text = out.read_text(encoding="utf-8")
        assert "void setup()" in text and "void loop()" in text and "JTD" in text  # Pro Micro: JTAG aus
    assert "BELEGUNG" in (tmp_path / "AluPC-Tasten" / "AluPC-Tasten.ino").read_text(encoding="utf-8")


def _rule(name, folge, ziel, **wenn):
    from alupc.automations import clean_rule

    return clean_rule({"name": name, "wenn": {"folge": folge, **wenn}, "dann": {"art": "alupc", "ziel": ziel}})


def test_key_sequence_rules(env, monkeypatch):  # noqa: F811
    """Tastenfolge 7-1-2: löst die Regel aus, die Einzelaktionen von 7 und 1 fallen weg; abgebrochene Folgen führen
    die Tasten normal aus; Tasten außerhalb jeder Folge wirken sofort."""
    controller, _window, _ = env
    ran = []
    monkeypatch.setattr(controller.buttonbox, "send", lambda line: True)
    real = controller.run_command
    monkeypatch.setattr(controller, "run_command", lambda a: ran.append(a) if not a.startswith("regel:") else real(a))
    monkeypatch.setattr(controller, "run_rule", lambda r: ran.append("REGEL " + r["name"]))
    keys = {str(n): f"aktion{n}" for n in range(1, 10)}
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "keys": keys, "folge_warten": False,  # nur Folge-Anfänge warten
                                   "regeln": [_rule("Intro", "712", "standbild"), _rule("Kurz", "45", "schwarz"),
                                              _rule("Lang", "456", "schwarz")]}
    box = controller.buttonbox
    for n in "712":
        box.key.emit(int(n))
    assert ran == ["REGEL Intro"]  # sofort, ohne auf die Pause zu warten
    ran.clear()
    box.key.emit(3)  # gehört zu keiner Folge → sofort
    assert ran == ["aktion3"]
    ran.clear()
    box.key.emit(7)
    assert ran == []  # 7 könnte eine Folge beginnen → wartet
    box.key.emit(5)  # 7-5 ist keine Folge: 7 läuft jetzt, 5 beginnt „45“? nein → auch sofort
    assert ran == ["aktion7", "aktion5"]
    ran.clear()
    box.key.emit(4)
    box.key.emit(5)  # „45“ ist fertig, aber „456“ könnte noch kommen → wartet die Pause ab
    assert ran == []
    assert _until(lambda: ran == ["REGEL Kurz"], 3)
    ran.clear()
    box.key.emit(7)
    box.key.emit(1)
    assert _until(lambda: ran == ["aktion7", "aktion1"], 3)  # abgebrochen → normal ausführen
    controller.config["tasten"] = base


def test_sequence_describe_and_clean():
    from alupc import automations as a

    r = _rule("x", "7a12", "standbild")
    assert r["wenn"]["folge"] == "712" and a.is_valid(r)
    assert "Tastenfolge 7-1-2 (unten links → oben links → oben Mitte)" in a.describe(r)
    assert _rule("y", "5", "standbild")["wenn"]["folge"] == ""  # eine Taste ist keine Folge
    both = a.clean_rule({"wenn": {"folge": "12", "code": "34"}, "dann": {"ziel": "x"}})
    assert both["wenn"]["code"] == ""  # Folge und Code schließen sich aus
    assert a.rules_for_value([_rule("z", "12", "x", wert=5)], 5) == []  # Folge-Regeln lösen nicht über den Wert aus


def _write_midi(path, tracks, division=480):
    import struct

    def vlq(n):
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)

    data = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), division)
    for events in tracks:
        body = b"".join(vlq(d) + bytes(e) for d, e in events) + b"\x00\xff\x2f\x00"
        data += b"MTrk" + struct.pack(">I", len(body)) + body
    path.write_bytes(data)


def test_midi_to_buzzer_notes(tmp_path):
    """MIDI → (Hz, ms): Tempo beachtet, Akkord → höchster Ton, Schlagzeug weg, Pausen bleiben."""
    from alupc import buzzer

    tempo = [(0, [0xFF, 0x51, 3, 0x07, 0xA1, 0x20])]  # 500000 µs = 120 bpm → Viertel = 500 ms
    melody = [(0, [0x90, 69, 100]), (480, [0x80, 69, 0]),  # A4 eine Viertel
              (480, [0x90, 72, 100]), (240, [0x90, 76, 100]), (240, [0x80, 76, 0]), (0, [0x80, 72, 0])]
    drums = [(0, [0x99, 36, 100]), (960, [0x89, 36, 0])]
    path = tmp_path / "t.mid"
    _write_midi(path, [tempo, melody, drums])
    notes = buzzer.midi_notes(path)
    # eine Oktave höher gelegt (Buzzer klingt um G5 am besten): A5, Pause, C6, E6
    assert notes == [(880, 500), (0, 500), (1047, 250), (1319, 250)]
    bad = tmp_path / "x.mid"
    bad.write_bytes(b"kein midi")
    import pytest

    with pytest.raises(ValueError):
        buzzer.midi_notes(bad)


def test_buzzer_songs_and_player(env, monkeypatch):  # noqa: F811
    from alupc import buzzer

    assert buzzer.note_freq("A4") == 440 and buzzer.note_freq("DS5") == 622 and buzzer.note_freq("R") == 0
    assert buzzer.parse_melody("A4/4 R/8 C5/4.", 120) == [(440, 500), (0, 250), (523, 750)]
    for name in buzzer.SONGS:
        assert buzzer.song_notes(name)  # alle eingebauten Melodien lassen sich lesen
    controller, _window, _ = env
    sent, msgs = [], []
    box = controller.buttonbox
    monkeypatch.setattr(box, "send", lambda line: sent.append(line) or True)
    controller.message.connect(msgs.append)
    box.port, box.version = "COM9", 6
    assert not controller.play_buzzer_song("piep") and "Version 7" in msgs[-1]  # altes Programm: klare Meldung
    box.version = 7
    controller.run_command("buzzer:erfolg")
    assert _until(lambda: len(sent) >= 2, 2)
    assert sent[0].startswith("TON 523 ") and sent[1].startswith("TON 659 ")  # C5, E5
    controller.run_command("buzzer:tetris")
    assert controller.buzzer.playing
    controller.run_command("buzzer_stopp")
    assert not controller.buzzer.playing and sent[-1] == "TON 0 0"
    # Klick bei Tastendruck (Einstellung)
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "buzzer_klick": True, "keys": {}}
    sent.clear()
    box.key.emit(2)
    assert sent and sent[0] == "TON 2400 12"
    controller.config["tasten"] = base
    box.port, box.version = "", 0


def test_knob_updates_are_bundled(env, monkeypatch):  # noqa: F811
    """Regler drehen: viele Werte, aber die Oberfläche zeichnet nur gebündelt neu (sonst ruckelte alles)."""
    controller, _window, _ = env
    from alupc import pc_control

    monkeypatch.setattr(pc_control, "set_volume", lambda percent=None, step=0: (True, ""))
    box = controller.buttonbox
    box.port, box.version = "COM9", 6
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "nur_regler": False, "abgleich": False, "regler": "pc", "skala": "prozent"}
    count = []
    controller.changed.connect(lambda: count.append(1))
    for v in range(60):
        box.feed(f"LAUT {v}")
    pump()
    assert _until(lambda: count, 1)
    assert len(count) <= 3
    controller.config["tasten"] = base
    box.port, box.version = "", 0


def test_searchable_combo(env):  # noqa: F811
    from PySide6.QtWidgets import QComboBox

    from alupc.ui.widgets import searchable

    combo = searchable(QComboBox())
    for text in ("Standbild an/aus", "Schwarz an/aus", "Buzzer – Lied: Tetris"):
        combo.addItem(text, text)
    hits = []
    combo.activated.connect(hits.append)
    comp = combo.completer()
    comp.setCompletionPrefix("tetris")
    assert comp.currentCompletion() == "Buzzer – Lied: Tetris"  # Teilwort, egal ob groß/klein
    comp.activated.emit("Buzzer – Lied: Tetris")
    assert combo.currentIndex() == 2 and hits == [2]
    combo.lineEdit().setText("gibtsnicht")
    combo.lineEdit().editingFinished.emit()
    pump()
    assert combo.lineEdit().text() == "Buzzer – Lied: Tetris"
    combo.deleteLater()


def test_box_osd_shows_inputs(env, monkeypatch):  # noqa: F811
    """Option „Eingaben unten links anzeigen“: Taste mit Lage und Aktion, Tastenfolge, Code, Regler (abschaltbar)."""
    controller, window, _ = env
    from alupc.ui.box_osd import describe_event

    osd = window.box_osd
    monkeypatch.setattr(controller.buttonbox, "send", lambda line: True)
    monkeypatch.setattr(controller, "run_command", lambda a: None)
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "keys": {"7": "standbild"}, "regeln": [], "anzeige": False}
    controller.buttonbox.key.emit(7)
    pump()
    assert not osd.isVisible()  # aus: nichts anzeigen
    controller.config["tasten"] = {**controller.config["tasten"], "anzeige": True, "anzeige_regler": False}
    controller.buttonbox.key.emit(7)
    pump()
    assert osd.isVisible() and osd.title == "Taste 7 · unten links" and osd.detail == "Standbild an/aus"
    assert osd.key == 7
    geo = controller.main_screen().availableGeometry()
    assert osd.x() < geo.x() + 60 and osd.geometry().bottom() > geo.bottom() - 60  # unten links
    osd.show_event({"art": "regler", "wert": 40, "ziel": "PC-Lautstärke"})
    assert osd.title == "Taste 7 · unten links"  # Regler abgeschaltet → bleibt bei der Taste
    labels = {"standbild": "Standbild an/aus"}
    assert describe_event({"art": "taste", "n": 1, "aktion": "", "folge": "71", "weiter": "2"}, labels)[1] == (
        "Folge 7-1 … weiter mit 2")
    assert describe_event({"art": "freigabe", "tasten": "71"}, labels)[1] == "Ausgeführt: Taste 7, 1"
    # Tastenfeld: alle 9 Tasten tragen ihre Funktion
    assert osd.caps[7] == "Standbild an/aus" and len(osd.caps) == 9 and all(osd.caps.values())
    assert describe_event({"art": "taste", "n": 3, "aktion": "", "lang": True}, labels)[:2] == (
        "Taste 3 · oben rechts · lang", "– nicht belegt –")
    assert describe_event({"art": "code", "code": "12", "fertig": False}, labels)[1] == "1-2 …"
    assert describe_event({"art": "regler", "wert": 40, "ziel": "PC-Lautstärke"}, labels)[1] == "PC-Lautstärke: 40 %"
    assert describe_event({"art": "zahl", "wert": 7}, labels)[1] == "007"
    # Taste beginnt eine Folge → Wartebalken, mögliche nächste Taste markiert
    controller.config["tasten"] = {**controller.config["tasten"], "folge_warten": False,
                                   "regeln": [_rule("Intro", "712", "standbild")]}
    controller.buttonbox.key.emit(7)
    assert osd.trail == "7" and osd.next_keys == "1" and osd._wait_ms > 0
    controller._seq_flush()
    assert osd.title == "Keine Folge"
    out = window.box_osd.grab()
    assert not out.isNull()
    osd.hide()
    controller.config["tasten"] = base


def test_midi_picks_melody_and_keeps_repeated_notes(tmp_path):
    """Lied mit Melodie + Bass + Akkorden: die Melodie-Spur zählt (nicht je Moment der höchste Ton aller Spuren);
    gleiche Töne hintereinander bleiben getrennt (sonst wird E-E-E zu einem langen Ton)."""
    from alupc import buzzer

    def notes(seq, chan=0, length=240):
        out = []
        for n in seq:
            out += [(0, [0x90 | chan, n, 90]), (length, [0x80 | chan, n, 0])]
        return out

    melody = notes([64, 64, 64, 64, 64, 67, 60, 62, 64, 65, 65, 65])  # Jingle-Bells-artig, einstimmig
    chords = []  # Akkorde, höher als die Melodie – früher „gewann“ dieser höchste Ton
    for _ in range(3):
        chords += [(0, [0x91, 76, 60]), (0, [0x91, 79, 60]), (0, [0x91, 84, 60]),
                   (960, [0x81, 76, 0]), (0, [0x81, 79, 0]), (0, [0x81, 84, 0])]
    bass = notes([40, 43, 45, 40] * 3, chan=2, length=240)
    path = tmp_path / "lied.mid"
    _write_midi(path, [[(0, [0xFF, 0x51, 3, 0x07, 0xA1, 0x20])], melody, chords, bass])
    out = buzzer.midi_notes(path)
    pitched = [f for f, _ms in out if f]
    assert len(pitched) == 12  # jede Melodienote einzeln, auch die fünf gleichen E am Anfang
    assert pitched[:5] == [pitched[0]] * 5 and pitched[5] > pitched[0]  # E E E E E G
    assert all(523 <= f <= 1600 for f in pitched)  # im guten Bereich des Buzzers


def test_every_key_waits_when_sequences_exist(env, monkeypatch):  # noqa: F811
    """„Jede Taste wartet kurz“ (Standard): auch Tasten, die keine Folge beginnen, warten – sonst verriete das
    sofortige Reagieren, womit eine Folge anfängt. 1-1-1 löst nur die Regel aus, nicht dreimal Taste 1."""
    controller, _window, _ = env
    ran = []
    monkeypatch.setattr(controller.buttonbox, "send", lambda line: True)
    monkeypatch.setattr(controller, "run_command", lambda a: ran.append(a))
    monkeypatch.setattr(controller, "run_rule", lambda r: ran.append("REGEL " + r["name"]))
    base = dict(controller.config["tasten"])
    controller.config["tasten"] = {**base, "keys": {str(n): f"a{n}" for n in range(1, 10)}, "folge_ms": 300,
                                   "regeln": [_rule("Drei", "111", "x"), _rule("Intro", "712", "y")]}
    assert controller.config["tasten"].get("folge_warten", True)
    box = controller.buttonbox
    for n in (1, 1, 1):
        box.key.emit(n)
    assert ran == ["REGEL Drei"]  # eindeutig fertig → sofort, Taste 1 nie einzeln
    ran.clear()
    box.key.emit(3)  # gehört zu keiner Folge – wartet trotzdem
    assert ran == []
    assert _until(lambda: ran == ["a3"], 2)
    ran.clear()
    for n in (5, 7, 1, 2):  # 5 normal, dann die Folge 7-1-2
        box.key.emit(n)
    assert ran == ["a5", "REGEL Intro"]
    ran.clear()
    for n in (7, 1, 9):  # angefangen, abgebrochen → alles normal, in Reihenfolge
        box.key.emit(n)
    assert ran == []
    assert _until(lambda: ran == ["a7", "a1", "a9"], 2)
    assert controller._seq_next("7") == ""  # Anzeige verrät keine nächsten Tasten
    controller.config["tasten"] = base


def test_rule_with_several_actions(env, monkeypatch):  # noqa: F811
    """Eine Regel kann mehrere Aktionen haben – sie laufen nacheinander."""
    controller, window, _ = env
    from alupc.automations import clean_rule, describe
    from alupc.ui.rules_dialog import RuleDialog

    dlg = RuleDialog(controller, None, set(), window)
    dlg.name.setText("Show")
    for n in "12":
        dlg.seq.setText(dlg.seq.text() + n)
    dlg.art.setCurrentIndex(dlg.art.findData("alupc"))
    dlg.command.setCurrentIndex(dlg.command.findData("standbild"))
    dlg._add_step()
    dlg.art.setCurrentIndex(dlg.art.findData("buzzer"))
    dlg.song.setCurrentIndex(dlg.song.findData("erfolg"))
    rule = dlg.rule()
    assert rule["dann"] == {"art": "alupc", "ziel": "standbild"}
    assert rule["weitere"] == [{"art": "buzzer", "ziel": "erfolg"}]
    assert dlg.steps.count() == 2 and "+ 1 weitere Aktion" in describe(rule)
    dlg._pick_step(0)  # erster Schritt wieder im Editor
    assert dlg.art.currentData() == "alupc" and dlg.command.currentData() == "standbild"
    dlg.deleteLater()
    ran = []
    monkeypatch.setattr(controller, "run_command", lambda a: ran.append(a))
    monkeypatch.setattr(controller, "play_buzzer_song", lambda name: ran.append("buzzer:" + name))
    controller.run_rule(clean_rule(rule))
    assert ran == ["standbild", "buzzer:erfolg"]


# ---------------------------------------------------------------- Belegung (Programm 9)
def test_parse_pin_mapping_lines():
    from alupc.tastenbox import parse_line

    assert parse_line("BELEGUNG 2 3 4 5 6 7 8 14 15 10 9 16 18 20") == (
        "belegung", (2, 3, 4, 5, 6, 7, 8, 14, 15, 10, 9, 16, 18, 20))
    assert parse_line("BELEGUNG 2 3") is None
    assert parse_line("PIN 9") == ("pin", 9)
    assert parse_line("FEHLER BELEGUNG") == ("fehler", "belegung")


def test_pins_problem_matches_firmware_rules():
    from alupc.tastenbox import DEFAULT_PINS, pins_problem

    for board, pins in DEFAULT_PINS.items():
        assert pins_problem(board, pins) == ""
    pm = list(DEFAULT_PINS["Pro Micro"])
    assert "doppelt" in pins_problem("Pro Micro", pm[:10] + [10] + pm[11:])   # grün = rot
    assert "Analog" in pins_problem("Pro Micro", pm[:12] + [0, 20])           # Regler an RX
    assert "gibt es" in pins_problem("Pro Micro", [11] + pm[1:])              # Pin 11 hat der Pro Micro nicht
    assert pins_problem("Pro Micro", pm[:3]) != ""


def test_default_pins_match_sketch():
    """Die Standard-Belegung der App ist dieselbe wie im Arduino-Programm (A0 = 18 bzw. 14)."""
    import re

    from alupc.tastenbox import DEFAULT_PINS

    from alupc.ui.tastenbox_page import SKETCH

    text = SKETCH.read_text(encoding="utf-8")
    rows = re.findall(r"STANDARD\[14\] = \{(.*?)\};", text)
    assert len(rows) == 2
    for board, row, a0 in (("Pro Micro", rows[0], 18), ("Nano", rows[1], 14)):
        row = re.sub(r"/\*.*?\*/", "", row)
        pins = [a0 + int(p[1:]) if p.startswith("A") else int(p) for p in (x.strip() for x in row.split(","))]
        assert pins == DEFAULT_PINS[board]


def _mapped_box(version="9 PROMICRO"):
    from alupc.tastenbox import DEFAULT_PINS, ButtonBox

    box = ButtonBox()
    sent = []
    box.send = lambda line: sent.append(line) or True
    box.port = "COM9"
    box.feed(f"HALLO ALUPC-TASTEN {version}")
    if version.startswith("9"):
        box.feed("BELEGUNG " + " ".join(map(str, DEFAULT_PINS["Pro Micro"])))
    return box, sent


def test_box_reads_and_sends_mapping():
    from alupc.tastenbox import DEFAULT_PINS

    box, sent = _mapped_box()
    assert box.can_map and box.pins == DEFAULT_PINS["Pro Micro"]
    pins = list(box.pins)
    pins[10], pins[9] = pins[9], pins[10]                     # rot und grün tauschen
    assert box.set_pins(pins) and sent[-1] == "BELEGUNG 2 3 4 5 6 7 8 14 15 9 10 16 18 20"
    assert not box.set_pins(pins[:9] + [9, 9, 16, 18, 20])    # doppelt: gar nicht erst schicken
    assert box.test_pin(9) and sent[-1] == "PIN 9 1500"
    assert box.search(True) and sent[-1] == "SUCHE 1" and box.searching
    assert box.reset_pins() and sent[-1] == "BELEGUNG 0"
    found = []
    box.pin_found.connect(found.append)
    box.feed("PIN 14")
    assert found == [14]


def test_old_box_cannot_map():
    box, sent = _mapped_box("8 PROMICRO")
    assert not box.can_map
    assert not box.set_pins([2, 3, 4, 5, 6, 7, 8, 14, 15, 10, 9, 16, 18, 20])
    assert not box.test_pin(9) and not box.search(True)
    assert not any(line.startswith(("BELEGUNG ", "PIN", "SUCHE")) for line in sent)


def _pins_page(env, version="9 PROMICRO"):  # noqa: F811
    from alupc.tastenbox import DEFAULT_PINS
    from alupc.ui.tastenbox_page import TastenboxPage

    c, window, _ = env
    window.open_setup_section("Tastenbox")
    pump()
    page = window.findChild(TastenboxPage)
    bb = c.buttonbox
    sent = []
    bb.send = lambda line: sent.append(line) or True
    bb.port = "COM9"
    bb.feed(f"HALLO ALUPC-TASTEN {version}")
    if version.startswith("9"):
        bb.feed("BELEGUNG " + " ".join(map(str, DEFAULT_PINS["Pro Micro"])))
    return c, page, sent


def test_page_mapping_swap_and_send(env):  # noqa: F811
    c, page, sent = _pins_page(env)
    green, red = page.pin_combos[10], page.pin_combos[9]
    assert green.isEnabled() and green.currentData() == 9 and not page.pin_apply.isEnabled()
    assert page.pin_combos[12].findData(9) < 0                 # Regler: nur A-Pins zur Wahl
    green.setCurrentIndex(green.findData(10))                  # grün auf den Pin von rot …
    page._pin_picked(10)
    assert red.currentData() == 9 and page.pin_apply.isEnabled()   # … rot tauscht automatisch
    page._pin_test(10)
    assert sent[-1] == "PIN 10 1500"
    page._pins_send()
    assert sent[-1] == "BELEGUNG 2 3 4 5 6 7 8 14 15 9 10 16 18 20"
    c.buttonbox.feed(sent[-1])                                 # Box bestätigt
    assert not page.pin_apply.isEnabled() and "Gespeichert" in page.pin_status.text()


def test_page_learn_keys(env):  # noqa: F811
    c, page, sent = _pins_page(env)
    page._learn_start()
    assert sent[-1] == "SUCHE 1" and "Taste 1" in page.pin_status.text()
    order = [15, 14, 8, 7, 6, 5, 4, 3, 2]                      # Tasten genau andersherum verkabelt
    for i, pin in enumerate(order):
        c.buttonbox.feed(f"PIN {pin}")
        c.buttonbox.feed(f"PIN {pin}")                         # prellen / doppelt: zählt nicht zweimal
    assert page._learn is None
    assert sent[-2] == "SUCHE 0"
    assert sent[-1] == "BELEGUNG 15 14 8 7 6 5 4 3 2 10 9 16 18 20"


def test_page_mapping_needs_program_9(env):  # noqa: F811
    c, page, sent = _pins_page(env, "8 PROMICRO")
    assert not page.pin_combos[0].isEnabled() and not page.learn_btn.isEnabled()
    assert "ab Programm 9" in page.pin_status.text()


def test_page_tabs_show_only_their_sections(env):  # noqa: F811
    from alupc.ui.tastenbox_page import TABS

    c, page, sent = _pins_page(env)
    for index in range(len(TABS)):
        page.tabs.setCurrentIndex(index)
        shown = {tab for tab, w in page._sections if w.isVisible()}
        assert shown == {index}
    page._learn_start()
    page.tabs.setCurrentIndex(0)                               # Reiter verlassen beendet das Anlernen
    assert page._learn is None and sent[-1] == "SUCHE 0"
