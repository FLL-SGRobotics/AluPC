"""Sprachbefehle am PC – offline (Vosk), mit Startwort „Computer“, „Alu PC“ oder „Monitor“ am Satzanfang.

Beispiele: „Monitor schwarz“, „Alu PC, Bildschirm schwarz“, „Monitor nächste Szene“, „Alu PC, Szene Pause“,
„Monitor Glücksrad drehen“, „Alu PC, Spiel starten“, „Monitor Bestenliste“.

Nur bestimmte Stimmen: Mit dem Sprecher-Modell von Vosk (ca. 13 MB) bekommt jede Äußerung einen
„Stimmabdruck“ (x-Vektor). Angelernte Stimmen sind der Mittelwert aus ein paar vorgelesenen Sätzen; ein Befehl
zählt nur, wenn der Abdruck nah genug an einer davon liegt (Kosinus-Abstand). Das ist ein Komfort-Filter gegen
Zurufe aus dem Raum – KEIN Schutz: eine Aufnahme der Stimme oder eine ähnliche Stimme kann ihn täuschen.

Alles bleibt auf dem PC: Das Sprachmodell (Vosk, Deutsch, ca. 45 MB) wird einmal heruntergeladen und dann
lokal benutzt – kein Ton geht ins Internet. Erkannt wird frei (ohne feste Wortliste), danach sucht AluPC nach
„Monitor“ und vergleicht den Rest unscharf mit der Befehlsliste. So stören Wörter, die das kleine Modell
nicht kennt, nicht – und ohne „Monitor“ passiert nie etwas.
"""

from __future__ import annotations

import difflib
import json
import queue
import shutil
import sys
import threading
import time
import unicodedata
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .config import config_dir

MODEL_NAME = "vosk-model-small-de-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_SIZE_MB = 45
SPK_NAME = "vosk-model-spk-0.4"
SPK_URL = f"https://alphacephei.com/vosk/models/{SPK_NAME}.zip"
SPK_SIZE_MB = 13
RATE = 16000
WAKE_WORDS = ("monitor", "monitore", "monitors", "monitoren")
# Startwörter: Schlüssel → Anzeige
WAKES = {"monitor": "Monitor", "alupc": "Alu PC", "computer": "Computer"}
COMPUTER_WORDS = ("computer", "komputer", "kompjuter", "computa")
WAKE_LEAD = 2  # so viele Wörter dürfen VOR dem Startwort stehen („ok hey alu pc …“) – nur diese hier:
LEAD_WORDS = {"hey", "hei", "he", "hallo", "ok", "okay", "o", "k", "ja", "also", "na", "so", "du", "äh", "ähm", "äm",
              "eh", "hm", "hmm", "und", "jetzt", "bitte", "lieber", "liebe"}
# „Alu PC“ hört das Modell je nach Aussprache als „alu pc“, „alu p c“, „alu pe ze“ … – zusammengeschrieben vergleichen
ALUPC_FORMS = ("alupc", "alupeze", "alupezeh", "alupehzeh", "alupetse", "alupeetse", "alupece", "alupeceh",
               # das kleine Modell hört „Alu PC“ oft als „Hallo PC“ (in der CI mit Piper-Stimme: jedes Mal)
               "hallopc", "halopc", "hallupc", "hallopeze", "hallopetse",
               "alopezie", "alopeze")  # CI 0.89: „Alu PC, wie spät …“ → „alopezie die spät …“
TARGET_WORDS = {"bildschirm", "monitor", "monitore", "zwei", "2"}  # „Alu PC, Bildschirm schwarz“
STRICTNESS = {"streng": 0.40, "normal": 0.55, "locker": 0.70}  # höchster Kosinus-Abstand zur angelernten Stimme
MIN_SPK_FRAMES = 30  # kürzere Äußerungen haben einen zu ungenauen Stimmabdruck
MIN_SCORE = 0.72
FOLLOW_SECONDS = 8  # so lange nach einer Antwort geht es ohne Startwort weiter („Nachfragen“)
FILLERS = {"bitte", "mal", "jetzt", "danke", "kurz", "doch", "noch", "aeh", "aehm", "ja", "nun"}

# (gesprochene Varianten, Befehl, Anzeige) – Varianten ohne „Monitor“, Umlaute erlaubt
COMMANDS: list[tuple[tuple[str, ...], str, str]] = [
    (("schwarz", "sichtschutz", "aus", "dunkel"), "schwarz", "Schwarz an/aus"),
    (("standbild", "einfrieren", "stopp bild"), "standbild", "Standbild an/aus"),
    (("spiegeln", "spiegel", "gleich"), "spiegeln", "Spiegeln"),
    (("erweitern", "erweitert", "desktop"), "erweitern", "Erweitern"),
    (("kamera",), "kamera", "Kamera zeigen"),
    (("wetter", "uhr", "wetter und uhr"), "wetter", "Wetter & Uhr"),
    (("system", "systemstatus", "status", "computer status"), "system", "Systemstatus"),
    (("whiteboard", "tafel", "weiße tafel"), "whiteboard", "Whiteboard"),
    (("glücksrad", "rad", "zufall"), "gluecksrad", "Glücksrad zeigen"),
    (("drehen", "rad drehen", "glücksrad drehen"), "gluecksrad_drehen", "Glücksrad drehen"),
    (("nächste szene", "nächste", "weiter"), "naechste_szene", "Nächste Szene"),
    (("vorherige szene", "vorherige", "zurück"), "vorherige_szene", "Vorherige Szene"),
    (("timer", "timer start", "timer starten", "timer pause", "timer stopp"), "timer_start_pause",
     "Timer Start/Pause"),
    (("timer zeigen",), "timer_zeigen", "Timer zeigen"),
    (("timer neu", "timer zurücksetzen", "timer neustart"), "timer_neustart", "Timer neu starten"),
    (("musik", "musik zeigen", "was läuft"), "musik_zeigen", "Läuft gerade (Musik)"),
    (("musik pause", "pause", "musik stopp", "musik weiter spielen"), "musik_pause", "Musik Pause/Weiter"),
    (("nächstes lied", "nächster titel", "lied weiter"), "musik_weiter", "Nächstes Lied"),
    (("bildschirmschoner", "schoner"), "bildschirmschoner", "Bildschirmschoner"),
    (("overlays", "einblendungen"), "overlays", "Overlays an/aus"),
    (("zeichnungen löschen", "löschen", "alles löschen"), "zeichnungen_loeschen", "Zeichnungen löschen"),
    (("abstimmung", "umfrage"), "umfrage_zeigen", "Abstimmung zeigen"),
    (("abstimmung beenden", "umfrage beenden", "ergebnis"), "umfrage_ende", "Abstimmung beenden"),
    (("minispiele", "spiele", "spiel"), "spiele", "Minispiele (Lobby)"),
    (("spiel starten", "start", "los"), "spiel_start", "Minispiel starten"),
    (("bestenliste", "rangliste", "punkte"), "spiel_bestenliste", "Bestenliste zeigen"),
    (("airplay", "iphone", "ipad"), "airplay", "AirPlay"),
    # Minispiele (dazu „Spiel Pong“, „Spiel Schätzen“ … zum Auswählen)
    (("spiel weiter", "nächste frage", "nächste runde", "nächstes bild"), "spiel_weiter", "Minispiel: weiter"),
    (("ergebnis zeigen", "spiel ende", "spiel beenden", "spielende"), "spiel_ende", "Minispiel: Ergebnis"),
    (("lobby", "zur lobby", "zurück zur lobby"), "spiel_lobby", "Minispiele: Lobby"),
    (("teams mischen", "neue teams"), "spiel_teams", "Teams mischen"),
    (("töne an", "töne aus", "spiel töne"), "spiel_toene", "Spiel-Töne an/aus"),
]

# „Spiel Pong“ → Spiel auswählen (gesprochene Namen der Spiele)
GAME_NAMES = {
    "schlangen": ("snake", "schlangen", "schlange"), "tictactoe": ("tic tac toe", "tik tak to", "drei gewinnt"),
    "pong": ("pong", "ping pong"), "tetris": ("tetris", "tetrisspiel"),
    "rennen": ("tipp rennen", "rennen", "wettrennen"), "simon": ("simon", "simon sagt"),
    "ssp": ("schere stein papier", "schnick schnack schnuck"),
    "invaders": ("space invaders", "invaders", "weltraum"), "flappy": ("flappy bird", "flappy", "vogel"),
    "vierg": ("vier gewinnt", "viergewinnt"),
}


# Wortschatz von AluPC für den zweiten Erkenner („Grammatik“): Vosk darf dann nur diese Wörter hören (oder [unk]).
# Das kleine Modell verhört freie Sprache oft („Alopezie“, „am PC“) – mit festem Wortschatz trifft es AluPCs Wörter
# viel sicherer. Wörter, die das Modell nicht kennt, ignoriert Vosk.
VOCAB = """
alu pc pe ze monitor hallo hey okay bitte mal jetzt doch noch ja und dann mir mich uns du dein den die das der dem
des ein eine einen einem auf mit für zu in im am an aus wieder nicht mehr kein keine keinen wie was wann welche
welcher welches wer ist es sind heute gerade gleich viel wieviel
mach mache machen schalte schalt stell stelle zeig zeige zeigen öffne starte starten stoppe stopp stop beenden
beende dreh drehe drehen lass spielen spiel wechsel geh hör hören zuhören lösche löschen sperre sperren erzähl
kannst einschalten ausschalten anmachen ausmachen aktivieren
bildschirm schwarz sichtschutz dunkel standbild einfrieren bildschirmschoner schoner overlays overlay einblendungen
vorschau bild licht beleuchtung rgb led lampe farbe rot grün blau gelb orange lila violett pink rosa weiß türkis
heller dunkler kamera wetter uhr system systemstatus auslastung whiteboard tafel glücksrad rad minispiele spiele
abstimmung umfrage ergebnis bestenliste airplay iphone ipad handy spiegeln erweitern desktop musik lied titel song
pause weiter zurück nächste nächstes nächster vorherige vorheriges letzte szene szenen timer minute minuten sekunde
sekunden stunde stunden halbe countdown stoppuhr start los neu plus minus länger kürzer zeichnungen computer rechner
mikrofon spät uhrzeit tag datum warm temperatur prozessor grafikkarte speicher geht dir witz danke hilfe frage
runde teams mischen töne lobby pong simon tetris schlangen schlange snake rennen tic tac toe tik tak to gewinnt
eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf fünfzehn zwanzig dreißig vierzig fünfzig sechzig
achtzig hundert prozent lauter leiser lautstärke laut stumm ton fenster schließen minimieren maximieren screenshot
bildschirmfoto herunterfahren herunter runter fahr ruhezustand öffnen firefox chrome browser youtube google suche
such nach spotify discord steam nein abbrechen taschenrechner explorer dateien einstellungen editor wikipedia netflix
wechseln schere stein papier schnick schnack schnuck space invaders weltraum flappy bird vogel viergewinnt
""".split()


def grammar_words(scenes=(), custom=()) -> list[str]:
    """Wortschatz + Wörter aus eigenen Szenen und eigenen Sprachbefehlen (klein, ohne Satzzeichen)."""
    words = list(dict.fromkeys(VOCAB))
    for text in [*scenes, *[c.get("say", "") for c in custom or []]]:
        for w in "".join(ch if ch.isalnum() else " " for ch in str(text).lower()).split():
            if w not in words:
                words.append(w)
    return words + ["[unk]"]


def fold(text: str) -> str:
    """klein, ohne Akzente, Umlaute als ae/oe/ue, nur Buchstaben/Ziffern/Leerzeichen."""
    s = str(text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c if (c.isascii() and c.isalnum()) else " " for c in s)
    return " ".join(s.split())


def _alupc_score(joined: str) -> float:
    # In der CI echt gehört: „anno pc“, „alle pc“, „allo pc“ – kurzes A-Wort + „pc“ zählt (nicht „am pc“)
    if joined.endswith("pc") and 5 <= len(joined) <= 6 and joined.startswith(("al", "an", "hal", "hel")):
        return 0.9
    if not joined.startswith(("alu", "hal", "allu", "aloo", "alop")) or "p" not in joined[3:]:  # „hallo“ allein zählt nicht
        return 0.0
    return max(difflib.SequenceMatcher(None, joined, form).ratio() for form in ALUPC_FORMS)


def wake_span(words: list[str], wakes=("monitor", "alupc")) -> tuple[int, int] | None:
    """(Anfang, Ende) des LETZTEN Startworts im Satz – oder None, wenn keins vorkommt."""
    span = None
    for i, w in enumerate(words):
        if ("monitor" in wakes and w in WAKE_WORDS) or ("computer" in wakes and w in COMPUTER_WORDS):
            span = (i, i + 1)
        if "alupc" in wakes:  # „alu pe ze“, „alu pc“, „alupc“ – die Variante, die am besten passt
            scores = [(_alupc_score("".join(words[i:i + k])), -k, k) for k in (1, 2, 3) if i + k <= len(words)]
            best, _neg, k = max(scores) if scores else (0.0, 0, 0)
            if best >= 0.82 and (span is None or i + k > span[1]):
                span = (i, i + k)
    return span


def lead_wake(words: list[str], wakes=("monitor", "alupc"), lead: int = WAKE_LEAD) -> tuple[int, int] | None:
    """Startwort am SATZANFANG (höchstens `lead` Wörter davor) – das erste passende. „Der Monitor ist …“ oder
    „… am Computer“ mitten im Gespräch zählen so nicht; davor nur Füllwörter wie „hey“, „ok“."""
    for i in range(min(len(words), lead + 1)):
        if i and words[i - 1] not in LEAD_WORDS:
            return None
        span = wake_span(words[i:i + 3], wakes)
        if span is not None and span[0] == 0:
            return (i, i + span[1])
    return None


def wake_end(words: list[str], wakes=("monitor", "alupc")) -> int | None:
    """Position direkt nach dem LETZTEN Startwort im Satz (oder None, wenn keins vorkommt)."""
    span = wake_span(words, wakes)
    return span[1] if span else None


def _similar(said: str, phrase: str) -> float:
    phrase = fold(phrase)
    if len(phrase) <= 5:  # kurze Wörter nur genau (sonst wird aus „Haus“ schnell „aus“)
        return 1.0 if said == phrase else 0.0
    return difflib.SequenceMatcher(None, said, phrase).ratio()


def match(text: str, scenes: list[str] | None = None, wakes=("monitor", "alupc"),
          custom: list[dict] | None = None) -> tuple[str, str] | None:
    """Gehörten Satz → (Befehl, Anzeige) oder None. Nur mit einem Startwort davor."""
    words = fold(text).split()
    idx = wake_end(words, wakes)
    if idx is None:
        return None
    rest = words[idx:]
    while rest and rest[0] in TARGET_WORDS:  # „Monitor zwei schwarz“, „Alu PC, Bildschirm schwarz“
        rest = rest[1:]
    rest = [w for w in rest if w not in FILLERS]
    if not rest:
        return None
    said = " ".join(rest)
    # eigene Sätze zuerst (die dürfen auch eingebaute überschreiben)
    best_custom = max(((_similar(said, c.get("say", "")), c) for c in custom or [] if c.get("say") and c.get("do")),
                      key=lambda t: t[0], default=(0.0, None))
    if best_custom[0] >= 0.8:
        return best_custom[1]["do"], f"„{best_custom[1]['say']}“"
    # Minispiel per Name: „Alu PC, Spiel Pong“
    if rest[0] in ("spiel", "spielen") and len(rest) > 1 and rest[1] not in ("starten", "start", "weiter", "ende",
                                                                          "beenden", "toene"):
        wanted = " ".join(rest[1:])
        scores = [(max(difflib.SequenceMatcher(None, wanted, fold(n)).ratio() for n in names), key)
                  for key, names in GAME_NAMES.items()]
        score, key = max(scores)
        if score >= 0.7:
            from .games import GAMES

            return f"spiel:{key}", f"Minispiel: {GAMES[key].title}"
    # Szene per Name: „Monitor Szene Pause“
    if rest[0] in ("szene", "szenen", "seene") and len(rest) > 1 and scenes:
        wanted = " ".join(rest[1:])
        folded = {fold(name): name for name in scenes}
        hit = difflib.get_close_matches(wanted, list(folded), n=1, cutoff=0.6)
        if hit:
            name = folded[hit[0]]
            return f"szene:{name}", f"Szene „{name}“"
        return None
    best, best_score = None, 0.0
    for variants, command, label in COMMANDS:
        for v in variants:
            score = _similar(said, v)
            if score > best_score:
                best, best_score = (command, label), score
    return best if best_score >= MIN_SCORE else None


# --------------------------------------------------------------------------- Sprachmodell
def model_dir() -> Path:
    return config_dir() / "sprache" / MODEL_NAME


def model_ready(path: Path | None = None) -> bool:
    path = path or model_dir()
    return (path / "am" / "final.mdl").exists() and (path / "conf" / "model.conf").exists()


def spk_dir() -> Path:
    return config_dir() / "sprache" / SPK_NAME


def spk_ready(path: Path | None = None) -> bool:
    path = path or spk_dir()
    return all((path / f).exists() for f in ("mfcc.conf", "final.ext.raw", "mean.vec", "transform.mat"))


def cosine_dist(a, b) -> float:
    """0 = gleiche Richtung (gleiche Stimme), 1 = nichts gemeinsam, bis 2 = entgegengesetzt."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 2.0
    return 1 - dot / (na * nb)


def average_voice(vectors: list[list[float]]) -> list[float]:
    """Stimmabdrücke mehrerer Sätze zu einem zusammenfassen (jeder vorher auf Länge 1 gebracht)."""
    norm = []
    for v in vectors:
        n = sum(x * x for x in v) ** 0.5 or 1.0
        norm.append([x / n for x in v])
    return [round(sum(col) / len(norm), 6) for col in zip(*norm)]


def who_speaks(vector, voices: list[dict]) -> tuple[str, float]:
    """(Name der ähnlichsten angelernten Stimme, Abstand)."""
    best = ("", 2.0)
    for v in voices:
        d = cosine_dist(vector, v.get("vec") or [])
        if d < best[1]:
            best = (str(v.get("name", "")), d)
    return best


def vosk_available(quick: bool = False) -> bool:
    """Ist Vosk da? quick=True: nur nachsehen, ob das Paket existiert (für die Oberfläche – das echte Laden
    kostet ~0,15 s und passiert ohnehin beim Start des Zuhörens)."""
    if quick and "vosk" not in sys.modules:
        import importlib.util

        return importlib.util.find_spec("vosk") is not None
    try:
        import vosk  # noqa: F401
    except Exception:  # noqa: BLE001 – auch kaputte Bibliothek (DLL) = nicht verfügbar
        return False
    return True


def download_model(progress=None, url: str = MODEL_URL, target: Path | None = None, ready=model_ready) -> Path:
    """Modell herunterladen und entpacken. progress(fertig_bytes, gesamt_bytes)."""
    import urllib.request

    target = target or model_dir()
    base = target.parent
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / (target.name + ".zip.part")
    with urllib.request.urlopen(url, timeout=30) as resp, open(tmp, "wb") as f:  # noqa: S310 – feste https-URL
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if total and done != total:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Download unvollständig")
    unpack = base / (target.name + ".neu")
    shutil.rmtree(unpack, ignore_errors=True)
    with zipfile.ZipFile(tmp) as z:
        root = unpack.resolve()
        for member in z.namelist():  # nichts außerhalb des Ordners entpacken
            if not (unpack / member).resolve().is_relative_to(root):
                raise RuntimeError("Ungültige Datei im Modell-Archiv")
        z.extractall(unpack)
    tmp.unlink(missing_ok=True)
    inner = next((p for p in [unpack / target.name, *unpack.iterdir()] if p.is_dir() and ready(p)), None)
    if inner is None:
        shutil.rmtree(unpack, ignore_errors=True)
        raise RuntimeError("Archiv enthält kein Sprachmodell")
    shutil.rmtree(target, ignore_errors=True)
    shutil.move(str(inner), str(target))
    shutil.rmtree(unpack, ignore_errors=True)
    return target


# --------------------------------------------------------------------------- Ton umrechnen
def convert_audio(data: bytes, rate: int, channels: int, sample_format) -> bytes:
    """Ton im Format des Mikrofons → 16 kHz, mono, 16 Bit (was Vosk und Whisper brauchen)."""
    if rate == RATE and channels == 1 and (sample_format is None or _fmt_name(sample_format) == "Int16"):
        return data
    import numpy as np

    kind = _fmt_name(sample_format)
    if kind == "Float":
        a = np.frombuffer(data[: len(data) // 4 * 4], dtype=np.float32)
    elif kind == "Int32":
        a = np.frombuffer(data[: len(data) // 4 * 4], dtype=np.int32).astype(np.float32) / 2**31
    elif kind == "UInt8":
        a = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128) / 128
    else:
        a = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
    if channels > 1:
        a = a[: len(a) // channels * channels].reshape(-1, channels).mean(axis=1)
    if rate != RATE and a.size:
        n = int(round(a.size * RATE / rate))
        a = np.interp(np.linspace(0, a.size, n, endpoint=False), np.arange(a.size), a)
    return (np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes()


def audio_ok(error) -> bool:
    """Kein Mikrofon-Fehler? PySide6 ≥ 6.7 meldet QtAudio.Error, älteres QAudio.Error – beide Aufzählungen sind
    NICHT gleich (QtAudio.Error.NoError != QAudio.Error.NoError). Deshalb nach Namen vergleichen.
    (0.84/0.85 hielten dadurch jedes Mikrofon für defekt.)"""
    name = getattr(error, "name", None) or str(error)
    return str(name).rsplit(".", 1)[-1] == "NoError"


def _fmt_name(sample_format) -> str:
    if sample_format is None:
        return "Int16"
    name = getattr(sample_format, "name", None) or str(sample_format)
    return str(name).rsplit(".", 1)[-1]


# --------------------------------------------------------------------------- Zuhören
class VoiceControl(QObject):
    """Mikrofon → Vosk (im Hintergrund) → Befehl. Signale kommen im GUI-Thread an."""

    command = Signal(str, str, str)  # Befehl, Anzeige, gehörter Text
    not_understood = Signal(str)  # an AluPC gerichtet, aber nicht verstanden
    direct_changed = Signal(bool)  # Mikrofon-Schalter: hört ohne Startwort zu
    level = Signal(float)  # Pegel 0..1 (etwa 10× pro Sekunde) – für die Anzeige „Sprache testen“
    partial = Signal(str)  # was gerade gehört wird (vor dem Satzende) – nur, wenn show_partial an ist
    dry_command = Signal(str, str, str)  # im Testmodus: das WÜRDE ausgeführt
    heard = Signal(str)  # alles Gehörte (für die Anzeige im Setup), mit erkannter Stimme
    rejected = Signal(str, str)  # Befehl kam von einer fremden Stimme: (gehörter Text, Grund)
    sample = Signal(object, int, str)  # beim Anlernen: Stimmabdruck, Länge (Frames), gehörter Text
    state_changed = Signal(str)
    _loaded = Signal(object)

    def __init__(self, config, scenes=None, parent=None, recognizer_factory=None):
        super().__init__(parent)
        self.config = config
        self.scenes = scenes or (lambda: [])
        self.recognizer_factory = recognizer_factory  # für Tests: liefert ein Objekt mit AcceptWaveform/Result
        self.state = "aus"
        self.enrolling = False  # Stimme anlernen: Sätze liefern nur Stimmabdrücke, keine Befehle
        self.has_spk = False  # Sprecher-Modell geladen?
        self._audio = None
        self._io = None
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._loaded.connect(self._start_audio)
        self.direct = False  # Mikrofon-Schalter an: jeder Satz zählt, kein Startwort nötig
        self._temporary = False  # nur für den Mikrofon-Schalter gestartet (Sprachbefehle sonst aus)
        self.follow_until = 0.0  # bis dahin geht es ohne Startwort (nach einer Antwort)
        self.confirm_until = 0.0  # bis dahin wartet eine Rückfrage auf „Ja“
        self._mute_until = 0.0  # Echo-Sperre: solange AluPC spricht, nichts auswerten
        self._reset = False
        self.stt = None  # genaue Erkennung (Whisper), falls eingeschaltet und heruntergeladen
        self._grammar = None  # zweiter Vosk-Erkenner mit AluPCs Wortschatz
        self.stt_error = ""
        self._watchdog = None
        self._mic_lost = False
        self._last_audio = 0.0
        self._in_format = (RATE, 1, None)
        self._device_name = ""
        self.mic_restarts = 0  # wie oft das Mikrofon neu geöffnet werden musste (Diagnose)
        self.dry_run = False  # „Sprache testen“: nichts ausführen, nur melden
        self._force_start = False  # „Sprache testen“: auch starten, wenn Sprachbefehle aus sind
        self.show_partial = False
        self._level_at = 0.0
        self.errors: list[str] = []  # letzte Fehler beim Zuhören (Diagnose, Fehlerbericht)

    def settings(self) -> dict:
        return self.config["voice"]

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def apply(self) -> None:
        if self.settings().get("on") or self.direct:
            self.start()
        else:
            self.stop()

    # ------------------------------------------------------------ Mikrofon-Schalter, Nachfragen, Echo-Sperre
    def set_direct(self, on: bool) -> None:
        """Mikrofon-Schalter: an = jeder Satz ist ein Befehl (ohne Startwort), aus = wieder nur mit Startwort."""
        on = bool(on)
        if on == self.direct:
            return
        self.direct = on
        if on and not self.running():
            self._temporary = not self.settings().get("on")
            self.start()
        elif not on and self._temporary:
            self._temporary = False
            self.stop()
        self.follow_until = 0.0
        self.direct_changed.emit(on)

    def listen_on(self, seconds: float = FOLLOW_SECONDS, force: bool = False) -> None:
        """Kurz ohne Startwort weiterhören (ab dem Ende der gesprochenen Antwort). Immer nach „Ja?“ (nur das
        Startwort gesagt), nach anderen Antworten nur mit der Einstellung „follow_all“."""
        if force or self.settings().get("follow_all", False):
            self.follow_until = max(self.follow_until, max(self._mute_until, time.monotonic()) + seconds)

    def mute(self, seconds: float) -> None:
        """Echo-Sperre: AluPC spricht gerade – das Mikrofon hört sonst die eigene Antwort als Befehl."""
        self._mute_until = max(self._mute_until, time.monotonic() + max(0.0, seconds) + 0.35)
        self._reset = True

    def open_ear(self) -> bool:
        return self.direct or time.monotonic() < self.follow_until

    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self) -> None:
        if self.running() or self.state == "lädt":
            return
        if self.recognizer_factory is None:
            if not vosk_available():
                self._set_state("Spracherkennung fehlt in dieser AluPC-Version")
                return
            if not model_ready():
                self._set_state("Sprachmodell fehlt – erst herunterladen")
                return
        self._set_state("lädt")

        def load():
            try:
                if self.recognizer_factory is not None:
                    rec = self.recognizer_factory()
                    self.has_spk = bool(getattr(rec, "speaker", False))
                else:
                    import vosk

                    vosk.SetLogLevel(-1)
                    model = vosk.Model(str(model_dir()))
                    rec = vosk.KaldiRecognizer(model, RATE)
                    self._grammar = None
                    try:  # zweiter Erkenner mit festem Wortschatz (kleine Modelle können das)
                        self._grammar = vosk.KaldiRecognizer(model, RATE, json.dumps(
                            grammar_words(self.scenes(), self.settings().get("custom") or []), ensure_ascii=False))
                    except Exception as exc:  # noqa: BLE001 – dann eben nur freie Erkennung
                        self._note_error(exc)
                    self.has_spk = False
                    if spk_ready():  # Stimmen unterscheiden (nur wenn heruntergeladen)
                        rec.SetSpkModel(vosk.SpkModel(str(spk_dir())))
                        self.has_spk = True
                self.load_stt()
                self._loaded.emit(rec)
            except Exception as exc:  # noqa: BLE001
                self._loaded.emit(exc)

        threading.Thread(target=load, name="sprache-laden", daemon=True).start()

    def load_stt(self) -> None:
        """Whisper laden (falls gewählt und heruntergeladen) – dauert ein paar Sekunden, nur im Hintergrund."""
        from . import stt

        name = self.settings().get("stt", "vosk")
        if self.stt is not None and getattr(self.stt, "name", "") == name:
            return
        self.stt, self.stt_error = None, ""
        if name in stt.MODELS and stt.available() and stt.ready(name):
            try:
                self.stt = stt.WhisperSTT(name)
            except Exception as exc:  # noqa: BLE001 – dann eben nur Vosk
                self.stt_error = str(exc)

    def _start_audio(self, rec) -> None:
        if isinstance(rec, Exception):
            self._set_state(f"Fehler: {rec}")
            return
        if not self.settings().get("on") and not self.direct and not self.enrolling and not self._force_start \
                and self.recognizer_factory is None:
            self._set_state("aus")  # in der Zwischenzeit ausgeschaltet
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._listen, args=(rec,), name="sprache", daemon=True)
        self._worker.start()
        if self.recognizer_factory is None:
            if not self._open_microphone():
                return  # Grund steht im Status; der Wächter versucht es weiter
            self._start_watchdog()
        self._set_state("hört zu")

    # ------------------------------------------------------------ Mikrofon (robust)
    def _open_microphone(self) -> bool:
        """Mikrofon öffnen. Kann es kein 16 kHz/mono/16 Bit, nimmt AluPC das Format des Geräts und rechnet um
        (viele Mikrofone unter Windows liefern nur 44,1/48 kHz – vorher kam dann Stille oder ein Fehler)."""
        from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

        self._close_microphone()
        fmt = QAudioFormat()
        fmt.setSampleRate(RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        device = QMediaDevices.defaultAudioInput()
        wanted = self.settings().get("device", "")
        for dev in QMediaDevices.audioInputs():
            if wanted and bytes(dev.id()).decode(errors="replace") == wanted:
                device = dev
        if device.isNull():
            self._set_state("Kein Mikrofon gefunden – Mikrofon anstecken, AluPC versucht es weiter")
            return False
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        self._in_format = (fmt.sampleRate(), fmt.channelCount(), fmt.sampleFormat())
        self._audio = QAudioSource(device, fmt, self)
        # stateChanged NICHT verbinden: PySide 6.11 kann den Parameter (QAudio::State) nicht umwandeln und wirft bei
        # jedem Zustandswechsel einen TypeError. Der Wächter fragt error() stattdessen alle 3 s ab.
        self._io = self._audio.start()
        if self._io is None or not audio_ok(self._audio.error()):
            self._set_state(f"Mikrofon „{device.description()}“ lässt sich nicht öffnen – AluPC versucht es weiter")
            self._close_microphone()
            return False
        self._device_name = device.description()
        self._last_audio = time.monotonic()
        self._io.readyRead.connect(self._read_audio)
        return True

    def _close_microphone(self) -> None:
        if self._audio is not None:
            try:
                self._audio.stop()
            except RuntimeError:
                pass
            self._audio.deleteLater()
        self._audio = None
        self._io = None

    def _start_watchdog(self) -> None:
        from PySide6.QtCore import QTimer

        if self._watchdog is None:
            self._watchdog = QTimer(self, interval=3000)
            self._watchdog.timeout.connect(self._check_microphone)
            try:
                from PySide6.QtMultimedia import QMediaDevices

                self._devices = QMediaDevices(self)
                self._devices.audioInputsChanged.connect(lambda: setattr(self, "_mic_lost", True))
            except Exception:  # noqa: BLE001
                pass
        self._watchdog.start()

    def _check_microphone(self) -> None:
        """Alle 3 s: kommt noch Ton? Nach Energiesparen, Abstecken oder Gerätewechsel bleibt das Mikrofon sonst
        stumm – dann neu öffnen."""
        if not self.running() or self.recognizer_factory is not None:
            return
        if self._audio is not None and not audio_ok(self._audio.error()):
            self._mic_lost = True
        silent = time.monotonic() - self._last_audio > 6
        if self._audio is None or self._mic_lost or silent:
            self._mic_lost = False
            if self._open_microphone():
                self.mic_restarts += 1
                self._set_state("hört zu")

    def _read_audio(self) -> None:
        if self._io is None:
            return
        data = bytes(self._io.readAll())
        if data:
            self._last_audio = time.monotonic()
            pcm = convert_audio(data, *self._in_format)
            if self._last_audio - self._level_at > 0.1 and len(pcm) >= 2:
                self._level_at = self._last_audio
                import numpy as np

                arr = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16)
                self.level.emit(float(np.abs(arr).max()) / 32767 if arr.size else 0.0)
            self.feed(pcm)

    def feed(self, data: bytes) -> None:
        """Ton (16 kHz, mono, 16 Bit) an die Erkennung geben – vom Mikrofon oder in Tests."""
        if time.monotonic() < self._mute_until:  # AluPC spricht gerade selbst
            return
        try:
            self._queue.put_nowait(data)
        except queue.Full:  # Erkennung kommt nicht hinterher → altes wegwerfen
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass

    def _listen(self, rec) -> None:
        audio = bytearray()  # Ton des laufenden Satzes (für die genaue Erkennung)
        grammar_parts: list[str] = []  # was der Grammatik-Erkenner im laufenden Satz gehört hat
        while not self._stop.is_set():
            try:
                data = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if self._reset:  # nach eigener Antwort: halb Gehörtes verwerfen
                self._reset = False
                audio.clear()
                if hasattr(rec, "Reset"):
                    rec.Reset()
                if self._grammar is not None:
                    self._grammar.Reset()
                    grammar_parts.clear()
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break
                continue
            audio += data
            if len(audio) > RATE * 2 * 30:  # höchstens 30 s
                del audio[:len(audio) - RATE * 2 * 30]
            try:
                g = self._grammar
                if g is not None and g.AcceptWaveform(data):
                    grammar_parts.append(json.loads(g.Result()).get("text", ""))
                if rec.AcceptWaveform(data):
                    res = json.loads(rec.Result())
                    text = res.get("text", "")
                    said = bytes(audio)
                    audio.clear()
                    alt = ""
                    if g is not None:
                        grammar_parts.append(json.loads(g.FinalResult()).get("text", ""))
                        alt = " ".join(p for p in grammar_parts if p).replace("[unk]", "").strip()
                        grammar_parts.clear()
                    if text:
                        self._handle(text, res.get("spk"), int(res.get("spk_frames", 0) or 0), said, alt)
                elif self.show_partial and hasattr(rec, "PartialResult"):
                    part = json.loads(rec.PartialResult()).get("partial", "")
                    if part:
                        self.partial.emit(part)
            except Exception as exc:  # noqa: BLE001 – ein kaputter Block darf das Zuhören nicht beenden
                self._note_error(exc)
                continue

    def _handle(self, text: str, spk=None, frames: int = 0, audio: bytes = b"", alt: str = "") -> None:
        if self.enrolling:
            self.heard.emit(f"„{text}“" + ("" if spk else " – ohne Stimmabdruck"))
            if spk:
                self.sample.emit(list(spk), frames, text)
            return
        cfg = self.settings()
        voices = [v for v in cfg.get("voices") or [] if v.get("vec")]
        who = ""
        if spk and voices and frames >= MIN_SPK_FRAMES:
            name, dist = who_speaks(spk, voices)
            limit = STRICTNESS.get(cfg.get("strict", "normal"), STRICTNESS["normal"])
            who = f"{name} ({dist:.2f})".replace(".", ",") if dist <= limit else f"fremde Stimme ({dist:.2f})".replace(".", ",")
        self.heard.emit(f"„{text}“" + (f" – {who}" if who else ""))
        wakes = tuple(cfg.get("wake") or WAKES)
        words = fold(text).split()
        span = lead_wake(words, wakes)
        follow = span is None and self.open_ear() and not self.direct
        if follow and len(words) > 8:
            return  # im Nachfrage-Fenster nur kurze Sätze – längeres ist Gespräch, nicht an AluPC gerichtet
        if span is None and not self.open_ear() and words[:2] == ["am", "pc"] and len(words) > 2:
            # „am pc …“ am SATZANFANG: so hört Vosk „Alu PC“ oft (CI 0.88–0.90). Zählt nur, wenn danach ein
            # Befehl kommt – „am PC sitzen …“ löst so nichts aus
            if self._interpret(words, (0, 2))[0] is not None:
                span = (0, 2)
        aw = fold(alt).split() if alt else []
        if span is None and not self.open_ear() and aw:
            # Grammatik-Erkenner (fester Wortschatz) hörte das Startwort – nur glauben, wenn auch die freie Erkennung
            # am Satzanfang etwas Ähnliches hat (sonst würde jedes Gespräch in AluPC-Wörter gepresst)
            sa = lead_wake(aw, wakes)
            hint = any(w in ("pc", "pe", "monitor", *COMPUTER_WORDS) or w.startswith(("alu", "allu", "alop"))
                       for w in words[:3])
            if sa is not None and hint and self._interpret(aw, sa)[0] is not None:
                self.heard.emit(f"„{alt}“ (Wortschatz)" + (f" – {who}" if who else ""))
                text, words, span = alt, aw, sa
        if span is None and not self.open_ear():
            # Vosk hat das Startwort vielleicht verhört („am pc“ …) – Whisper (falls an) fragt nach
            if not (self.stt is not None and audio and any(w in ("pc", "pe", "monitor", *COMPUTER_WORDS)
                                                           for w in words[:4])):
                return  # nicht an AluPC gerichtet
            try:
                better = self.stt.transcribe(audio)
            except Exception as exc:  # noqa: BLE001
                self._note_error(exc)
                return
            w2 = fold(better).split()
            s2 = lead_wake(w2, wakes)
            if s2 is None:
                return
            self.heard.emit(f"„{better}“ (genau)" + (f" – {who}" if who else ""))
            text, words, span = better, w2, s2
        found, empty = self._interpret(words, span)
        if found is not None and aw and aw != words and (span is not None or self.direct):
            # Vosk verstand nur das Grobe („Licht“ → umschalten), der Wortschatz-Erkenner das Genauere
            # („Licht rot“ → Farbe): dann das Genauere nehmen (CI 0.93: „Computer, mach das Licht rot“)
            sa = lead_wake(aw, wakes)
            finer, _e = self._interpret(aw, sa if sa is not None else (0, 0))
            if finer is not None and finer[0] != found[0] and finer[0].startswith(found[0] + "_"):
                self.heard.emit(f"„{alt}“ (Wortschatz)" + (f" – {who}" if who else ""))
                found, text = finer, alt
        if found is None and not empty and aw and aw != words and (span is not None or self.direct):
            sa = lead_wake(aw, wakes)  # zweite Meinung: Grammatik-Erkenner (nur, wenn AluPC gemeint ist)
            found2, _e = self._interpret(aw, sa if sa is not None else ((0, 0) if span is not None else None))
            if found2 is not None:
                self.heard.emit(f"„{alt}“ (Wortschatz)" + (f" – {who}" if who else ""))
                found, text = found2, alt
        # Vosk hat nichts verstanden → Whisper als zweite Meinung (in der CI war Vosk bei klaren Sätzen
        # zuverlässiger, Whisper hilft bei Sätzen, die das kleine Modell nicht kennt)
        if found is None and not empty and self.stt is not None and audio:
            try:
                better = self.stt.transcribe(audio)
            except Exception as exc:  # noqa: BLE001 – dann gilt, was Vosk gehört hat
                self._note_error(exc)
                better = ""
            if better:
                self.heard.emit(f"„{better}“ (genau)" + (f" – {who}" if who else ""))
                w2 = fold(better).split()
                s2 = lead_wake(w2, wakes)
                if s2 is None and span is not None:
                    s2 = (0, 0)  # Vosk hörte das Startwort, Whisper schreibt es anders – ganzen Satz nehmen
                found2, _empty2 = self._interpret(w2, s2)
                if found2 is not None:
                    found, text = found2, better
        if empty:
            if span is not None:
                self._emit_command("frage:ja", "Ja?", text)  # nur das Startwort: „Ja?“ und zuhören
            return
        if not found:
            if span is not None or self.direct:
                self.not_understood.emit(text)
            return
        if cfg.get("only_voices") and voices:
            if not spk or frames < MIN_SPK_FRAMES:
                self.rejected.emit(text, "Stimme nicht erkannt (zu kurz oder Stimmerkennung fehlt)")
                return
            name, dist = who_speaks(spk, voices)
            limit = STRICTNESS.get(cfg.get("strict", "normal"), STRICTNESS["normal"])
            if dist > limit:
                self.rejected.emit(text, f"fremde Stimme (Abstand {dist:.2f})".replace(".", ","))
                return
            if follow:
                self.follow_until = 0.0  # eine Nachfrage, dann wieder nur mit Startwort
            self._emit_command(found[0], f"{found[1]} · {name}", text)
            return
        if follow:
            self.follow_until = 0.0
        self._emit_command(found[0], found[1], text)

    def _emit_command(self, command: str, label: str, text: str) -> None:
        (self.dry_command if self.dry_run else self.command).emit(command, label, text)

    def _interpret(self, words: list[str], span) -> tuple[tuple[str, str] | None, bool]:
        """(Befehl oder None, nur Startwort/Füllwörter?) für einen gehörten Satz."""
        rest = words[:span[0]] + words[span[1]:] if span else words
        rest = [w for w in rest if w not in TARGET_WORDS or len(rest) > 1]
        meaningful = [w for w in rest if w not in FILLERS and w not in ("hey", "hallo", "ok", "okay")]
        if not meaningful:
            if time.monotonic() < self.confirm_until and any(w in ("ja", "ok", "okay") for w in rest):
                return ("bestaetigen", "Ja"), False  # Antwort auf „Soll ich den PC wirklich …?“
            return None, True
        try:
            from .intents import understand

            return understand(rest, self.scenes(), self.settings().get("custom") or []), False
        except Exception as exc:  # noqa: BLE001
            self._note_error(exc)
            return None, False

    def probe_microphone(self, seconds: float = 3.0, pump=None) -> dict:
        """Mikrofon kurz öffnen und messen (ohne Erkennung): Gerät, Format, wie viel Ton, wie laut.
        Für die Diagnose im Setup und den Mikrofontest der fertigen Version."""
        from PySide6.QtMultimedia import QMediaDevices

        report = {"geraete": " | ".join(d.description() for d in QMediaDevices.audioInputs()) or "(keine)",
                  "standard": QMediaDevices.defaultAudioInput().description() or "(keins)"}
        chunks: list[bytes] = []
        saved_feed, saved_worker = self.feed, self._worker
        self.feed = chunks.append  # nur messen, nichts erkennen
        self._worker = type("Probe", (), {"is_alive": staticmethod(lambda: True)})()
        try:
            opened = self._open_microphone()
            report["geoeffnet"] = "ja" if opened else f"nein – {self.state}"
            report["geraet"] = self._device_name or "-"
            report["format_geraet"] = f"{self._in_format[0]} Hz, {self._in_format[1]} Kanal/Kanäle, " \
                                      f"{_fmt_name(self._in_format[2])}"
            end = time.monotonic() + seconds
            while opened and time.monotonic() < end:
                if pump:
                    pump()
                time.sleep(0.01)
        finally:
            self._close_microphone()
            self.feed, self._worker = saved_feed, saved_worker
        data = b"".join(chunks)
        report["sekunden_ton"] = round(len(data) / (RATE * 2), 2)
        peak = 0
        if data:
            import numpy as np

            pcm = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16)
            peak = int(np.abs(pcm).max()) if pcm.size else 0
        report["pegel_max"] = f"{peak} von 32767 ({round(peak * 100 / 32767)} %)"
        report["fehler"] = " | ".join(self.errors) or "-"
        return report

    def _note_error(self, exc: BaseException) -> None:
        """Fehler beim Zuhören nicht verschlucken: ins Fehlerprotokoll (Fehlerbericht) und in die Diagnose."""
        self.errors.append(f"{time.strftime('%H:%M:%S')} {type(exc).__name__}: {exc}")
        del self.errors[:-20]
        try:
            from .bug_report import log_exception

            log_exception(type(exc), exc, exc.__traceback__)
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._watchdog is not None:
            self._watchdog.stop()
        self._close_microphone()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=2)
        if self.direct and not self._temporary:
            self.direct = False
            self.direct_changed.emit(False)
        if self.state != "aus":
            self._set_state("aus")
