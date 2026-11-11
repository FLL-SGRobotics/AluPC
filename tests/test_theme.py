"""„Wie das System“: Akzentfarbe aus Windows/KDE/GNOME, Hell/Dunkel live mitziehen, Titelleiste."""

import json

import pytest

from test_gui import env, pump  # noqa: F401 – gleiche Test-Umgebung wie die GUI-Tests

from alupc.ui import theme


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_kde_accent_from_kdeglobals(tmp_path):
    f = tmp_path / "kdeglobals"
    f.write_text("[General]\nAccentColor=61,174,233\nColorScheme=BreezeDark\n", encoding="utf-8")
    assert theme._kde_accent(f) == "#3daee9"
    f.write_text("[Colors:Selection]\nBackgroundNormal=233,100,10\n", encoding="utf-8")
    assert theme._kde_accent(f) == "#e9640a"
    f.write_text("[General]\nAccentColor=kaputt\n", encoding="utf-8")
    assert theme._kde_accent(f) == ""
    assert theme._kde_accent(tmp_path / "fehlt") == ""


def test_gnome_accent_from_gsettings():
    assert theme._gnome_accent(lambda cmd: "'purple'\n") == theme.GNOME_ACCENTS["purple"]
    assert theme._gnome_accent(lambda cmd: "'unbekannt'\n") == ""


def test_system_accent_used_and_grey_falls_back(qapp, monkeypatch):
    monkeypatch.setattr(theme, "linux_desktop", lambda: "kde")
    monkeypatch.setattr(theme, "_kde_accent", lambda path=None: "#e9640a")
    monkeypatch.setattr("sys.platform", "linux")
    t = theme.make_theme("dunkel", "system")
    assert t.accent == "#e9640a"
    monkeypatch.setattr(theme, "_kde_accent", lambda path=None: "#808080")  # grau → Blau
    assert theme.make_theme("hell", "system").accent == theme.ACCENTS["blau"][1]


def test_old_defaults_migrate_to_alupc_theme(tmp_path):
    from alupc.config import Config

    p = tmp_path / "c.json"
    p.write_text(json.dumps({"appearance": {"mode": "dunkel", "accent": "blau"}}), encoding="utf-8")
    assert Config(p)["appearance"]["accent"] == "alupc"  # alte Voreinstellung → AluPC-Theme
    p.write_text(json.dumps({"appearance": {"accent": "system", "accent_v2": True}}), encoding="utf-8")
    assert Config(p)["appearance"]["accent"] == "alupc"
    p.write_text(json.dumps({"appearance": {"accent": "system", "accent_v2": True, "accent_v3": True}}),
                 encoding="utf-8")
    assert Config(p)["appearance"]["accent"] == "system"  # danach bewusst „Wie das System“ → bleibt
    p.write_text(json.dumps({"appearance": {"accent": "blau", "accent_v2": True}}), encoding="utf-8")
    assert Config(p)["appearance"]["accent"] == "blau"  # bewusst gewählt → bleibt
    p.write_text(json.dumps({"appearance": {"accent": "gruen"}}), encoding="utf-8")
    assert Config(p)["appearance"]["accent"] == "gruen"
    assert Config(tmp_path / "neu.json")["appearance"]["accent"] == "alupc"


def test_alupc_theme_same_everywhere_and_branded(qapp):
    """Drei Stile (docs/DESIGN.md): hängen nicht vom System ab (gleich unter Windows und Linux), Rot für „läuft“,
    lesbare Kontraste (WCAG) in jedem Stil, jeder Helligkeit und jeder Holzart."""
    from PySide6.QtGui import QColor

    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        q = QColor(c)
        return 0.2126 * ch(q.red()) + 0.7152 * ch(q.green()) + 0.0722 * ch(q.blue())

    def contrast(a, b):
        la, lb = sorted((lum(a), lum(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    hues = {"normal": (225, 245), "technisch": (20, 35), "holz": (20, 35), "matrix": (135, 150),
            "regie": (165, 180)}  # Indigo / Signal-Orange / Kupfer / Grün / Türkis (Neugestaltung 2.5)
    for style in theme.STYLES:
        for mode in ("hell", "dunkel"):
            for wood in (theme.WOODS if style == "holz" else [theme.DEFAULT_WOOD]):
                t = theme.make_theme(mode, "alupc", style, wood)
                assert t == theme.make_theme(mode, "alupc", style, wood)
                lo, hi = hues[style]
                assert lo <= QColor(t.accent).hslHue() <= hi, (style, mode, wood)
                live = QColor(t.live)
                assert live.hslHue() <= 8 or live.hslHue() >= 350  # Sendungsleuchte rot
                assert contrast(t.text, t.bg) >= 7, (style, mode, wood)
                assert contrast(t.text, t.surface) >= 7, (style, mode, wood)
                assert contrast(t.muted, t.surface) >= 4.5, (style, mode, wood)
                assert contrast(t.on_accent, t.accent) >= 4.5, (style, mode, wood)
                if style == "holz":  # helles Holz → dunkle Schrift, dunkles Holz → helle
                    assert t.dark == theme.WOODS[wood][5]


def test_wood_textures_tile_and_differ(qapp):
    """Jede Holzart hat eigene Maserung; Flächen kommen in der Ziel-Helligkeit heraus."""
    colors = {theme.wood_color(w, "bg") for w in theme.WOODS}
    assert len(colors) == len(theme.WOODS)
    for w in theme.WOODS:
        img = theme.wood_image(w, "panel")
        assert img.width() == 720 and img.height() == 480
        assert theme.wood_jpeg(w, "bg")[:2] == b"\xff\xd8"  # JPEG für die Handy-Seite


def test_title_bar_only_on_windows(qapp):
    from PySide6.QtWidgets import QWidget

    import sys

    w = QWidget()
    assert theme.title_bar(w) is (False if sys.platform != "win32" else theme.title_bar(w))


def test_follows_system_change_live(env, monkeypatch):  # noqa: F811
    controller, window, _ = env
    controller.config["appearance"] = {**controller.config["appearance"], "mode": "system", "accent": "system",
                                       "style": "normal"}  # Regie ist immer dunkel
    state = {"sig": (True, "#3daee9")}
    monkeypatch.setattr(theme, "system_signature", lambda: state["sig"])
    monkeypatch.setattr(theme, "system_accent", lambda: state["sig"][1])
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: state["sig"][0])
    window._sys_timer.timeout.emit()
    window.apply_theme()
    assert theme.current().dark and theme.current().accent == "#3daee9"
    state["sig"] = (False, "#e9640a")  # Nutzer stellt Windows/KDE auf Hell + Orange
    window._sys_timer.timeout.emit()
    pump()
    assert not theme.current().dark and theme.current().accent == "#e9640a"


def test_system_swatch_in_setup(env):  # noqa: F811
    _, window, _ = env
    window.open_setup_section("Darstellung")
    pump()
    sw = window.setup._swatches
    assert list(sw)[:2] == ["alupc", "system"] and sw["system"].system and sw["alupc"].brand
    assert sw["alupc"].isChecked() and not sw["system"].isChecked()  # Voreinstellung: AluPC-Theme


def test_dark_is_default_and_old_system_default_migrates(tmp_path):
    """Regiepult: Dunkel ist Standard; die alte Voreinstellung „Wie das System“ wird einmal dunkel, „Hell“ bleibt."""
    from alupc.config import Config

    assert Config(tmp_path / "neu.json")["appearance"]["mode"] == "dunkel"
    p = tmp_path / "alt.json"
    p.write_text(json.dumps({"appearance": {"mode": "system", "accent": "alupc", "accent_v2": True,
                                            "accent_v3": True}}), encoding="utf-8")
    assert Config(p)["appearance"]["mode"] == "dunkel"
    p.write_text(json.dumps({"appearance": {"mode": "hell", "accent_v2": True, "accent_v3": True}}), encoding="utf-8")
    assert Config(p)["appearance"]["mode"] == "hell"
    p.write_text(json.dumps({"appearance": {"mode": "system", "accent_v2": True, "accent_v3": True,
                                            "mode_v2": True}}), encoding="utf-8")
    assert Config(p)["appearance"]["mode"] == "system"  # danach bewusst „Wie das System“ → bleibt


def test_regie_style_rules(qapp):  # noqa: D103
    """Regie „Mitternacht“: nur dunkel, Flächenleiter Grund < Karte < gehoben, keine Verläufe im Stylesheet,
    Auswahl in der Akzentfarbe (Linie links, zarter Grund), Hauptknopf in Akzentfarbe, Inter mit ss03."""
    from PySide6.QtGui import QColor, QFont

    t = theme.make_theme("hell", "alupc", "regie")
    assert t.dark and t.regie and t.kind == "regie" and t.head_font == "Inter"
    assert QColor(t.bg).lightness() < QColor(t.surface).lightness() < QColor(t.surface2).lightness()
    css = theme.stylesheet(t)
    assert "gradient" not in css  # ruhig: keine Verläufe
    assert f"border-left: 2px solid {t.accent}" in css
    assert f'QPushButton[primary="true"] {{ background: {t.accent};' in css
    from pathlib import Path

    fonts = Path(theme.__file__).resolve().parent.parent / "assets" / "fonts"
    assert (fonts / "Inter-Regular.ttf").exists()
    f = theme.set_features(QFont("Inter"), t)
    assert QFont.Tag("ss03") in f.featureTags()
    assert theme.BUILTIN_PROFILES["Regie"]["style"] == "regie"
