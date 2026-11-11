# AluPC – Gestaltung (ab 2.0)

Fünf Designs (ab 2.2 auch Matrix und Regie, Regie ist Standard), umschaltbar unter **Setup → Darstellung → Stil**. Alle drei teilen Aufbau und Funktionen; sie
unterscheiden sich in Flächen, Rundungen, Schrift und Kachelform. Code: `alupc/ui/theme.py` (Farben, Stylesheet,
Holz), `alupc/ui/widgets.py` (gezeichnete Teile), `alupc/cast_page.py` (Handy-/iPad-Seite).

## Gemeinsame Regeln

- **Rot heißt nur eins:** läuft gerade auf dem Bildschirm (Kachelrand, Lampe, Vorschau-Rahmen, Etikett „Live“).
- **Nur echte Angaben:** Zustände in Worten („Läuft“, „An“, „Aktiv“) oder als echte Werte (Auflösung, Hz,
  Lautstärke, Laufzeit). Keine Fachkürzel wie „CH 07“, „PGM“, „STBY“, kein erfundener Timecode.
- **Lesbarkeit geprüft** (`tests/test_theme.py`): Schrift/Grund ≥ 7:1, leise Schrift/Karte ≥ 4,5:1,
  Schrift auf Akzentfarbe ≥ 4,5:1 – für jeden Stil, hell und dunkel, und jede Holzart.
- Keine Verläufe als Schmuck, keine Bewegung ohne Anlass; Satzschreibweise statt GROSSBUCHSTABEN.

## Aufbau der Startseite „Studio“ (ab 2.2)

- **Live-Dock rechts** (ab 1240 px Fensterbreite, `MainWindow.DOCK_MIN`): großes Live-Bild, was läuft, alle
  Schnellschalter mit Namen in zwei Spalten, Regler, „Beenden“. Bleibt stehen, die Kacheln links scrollen.
  Das kleine Live-Bild in der Seitenleiste ist auf der Startseite dann aus (sonst doppelt).
- **Befehlsleiste** oben über den Kacheln: öffnet die Befehlssuche (Strg+K).
- Schmaler: die Statuskarte steht wie vorher über den Kacheln (mit Namen, nur Symbole oder untereinander).
- Technisch zusätzlich: Messwerte im Dock (`Telemetry`: Prozessor, Speicher, Temperatur – echte Werte aus
  `sysinfo.monitor()`, „–“ wenn es keinen Sensor gibt), Punktraster im Hintergrund, gelbe Kante am Dock.

## Studio-Modus (ab 2.2)

Knopf „Studio“: über dem Live-Bild klappt die Vorschau auf (Animation: Bild teilt sich, dann kommen die Knöpfe).
Grün = Vorschau, Rot = live – auch an den Kacheln. Code: `alupc/ui/studio_panel.py`, `Controller.take()`.

## Regie – neues Standard-Design (ab 2.2)

Entstanden mit zwei Skills: **taste-skill** (App-Regeln, ausgeloster Stil „Data terminal“, Seed 1995944031) gibt den
Aufbau vor, **awesome-design** (Vorlage Raycast) Farben und Schrift.
- Nur dunkel. Flächenleiter: Grund `#07080a` → Karte `#0d0d0d` → gehoben `#121212`; jede Kante eine 1-px-Haarlinie
  `#242728`, keine Schatten, keine Verläufe, kein Leuchten.
- Schrift Inter mit Stilsatz **ss03** (einstöckiges g); Text `#f4f4f6`, Zweitstufe `#9c9c9d` (über 4,5:1).
- **Jede Auswahl ist dieselbe 1-px-Linie** in `#57c1ff` (Seitenleiste links, Reiter/Segmente unten, Listen links) –
  keine farbigen Flächen. Hauptknopf weiß mit schwarzer Schrift; alle anderen Knöpfe nur Umriss.
- Kacheln: flach, graues Umriss-Symbol ohne Kreis, Name und Untertitel. Läuft etwas, wird der Rand rot wie das
  Rotlicht an der Kamera (`#ff6161`) – Rot gibt es nur dafür.
- Seitentitel ohne Symbol-Kasten. Die anderen Stile (Normal, Technisch, Holz, Matrix) bleiben wählbar; beim Update
  wird einmal auf Regie umgestellt, der alte Stil steht in `appearance.style_vorher`.

## Matrix – grüner Code

Wie Technisch aufgebaut (Pads, Messwerte), aber Phosphorgrün auf Schwarz, JetBrains Mono überall, Code-Regen im
Hintergrund. Immer dunkel. Monitor 2 bleibt unverändert (`#Output` behält Inter).

## Monitor 2 – Vorlagen (ab 2.2)

Gemeinsame Bausteine in `alupc/stage.py`: `paint_aurora` (dunkler Grund in der Farbe der Vorlage, zwei
Lichtflecken, Rand), `font`, `eyebrow` (Etikett oben links), `clock_corner` (Uhrzeit oben rechts).

## Normal (Standard) – modern, weich, rund

| Rolle | Dunkel | Hell |
|---|---|---|
| Grund | `#131417` | `#ECEEF2` |
| Fläche (Karten, Seitenleiste) | `#1E2024` | `#FFFFFF` |
| Feld (Knöpfe, Eingaben) | `#2A2D33` | `#F1F2F5` |
| Schrift / leise | `#F2F3F5` / `#9AA0AA` | `#16181C` / `#5F6670` |
| Akzent (AluPC) | `#5B8CFF` | `#2F62E9` |

- Rundungen: Karten 20, Knöpfe 17 (Pillen), Felder 12. Flächen ohne Rand – Ebenen unterscheiden sich nur in der Helligkeit.
- Seitenleiste schwebt als runde Fläche. Kacheln: Symbol im getönten Kreis oben links, Menü als kleiner runder
  Knopf oben rechts, Name unten. Lautstärke als dicke runde Säule.
- Schrift: Inter.

## Technisch – dicht, mit Messwerten

- Mittelgrau (`#18191B` / `#E6E6E2`), Akzent Signalgelb (`#F5C400` / `#C99A00`) mit dunkler Schrift darauf.
- Rundungen 2–3 px, feine Ränder. Kacheln als Zeilen (Symbol, Name, Untertitel), läuft = rote Leiste links.
- Ziffernschrift (JetBrains Mono) nur für echte Werte: Ausgang/Auflösung/Hz, „läuft seit 0:12:34“, Regler 0–100.

## Holz – echtes Furnier

- **7 Holzarten** (`WOODS`): Nussbaum, Kirsche, Wenge, Räuchereiche (dunkel → helle Schrift), Eiche, Kiefer,
  Ahorn (hell → dunkle Schrift). Die Holzart bestimmt hell/dunkel; die Auswahl Hell/Dunkel ist dann gesperrt.
- Maserung wird beim Start gerechnet (fester Zufallswert, nahtlos kachelbar): Jahresringe als Bänder, Fasern als
  Wellen, je Holzart eigene Zeichnung (Poren bei Eiche, enge Streifen bei Wenge, Flamme bei Kirsche …).
  Jede Fläche (Grund, Seitenleiste, Platte, Platte hell/gedrückt) wird auf eine feste Helligkeit gebracht –
  so bleibt die Schrift auf jedem Holz lesbar.
- Kacheln sind Furnierplatten mit Lichtkante und Schatten, Namen graviert (Barlow Semi Condensed), oben links
  eine Kontrolllampe (aus = dunkles Glas, läuft = leuchtet rot). Akzent Messing.
- Reglerkappen aus gebürstetem Aluminium, Vorschau in dunklem Gehäuse wie ein kleiner Fernseher.

## Anpassen und Profile (ab 2.2)

Setup → Darstellung: **Rundung** (0–200 % der Rundung des Stils), **Schriftgröße** (85–130 %), **Abstände**
(kompakt/normal/luftig), **eigene Akzentfarbe**. **Profile** speichern Stil, Hell/Dunkel, Holzart, Farbe, Rundung,
Schrift und Abstände (`theme.PROFILE_KEYS`); fertige Profile in `theme.BUILTIN_PROFILES`.

## Publikumsansicht (Monitor 2)

Alle Info-Anzeigen nutzen dieselbe „Bühne“ (`alupc/stage.py`): dunkler Verlauf, sanftes Leuchten in AluPC-Blau,
gleiche Ränder und Farben – **unabhängig vom PC-Design**. Text/Uhr/Timer zeigen die Bühne, solange ihr Hintergrund
das Standard-Schwarz ist. Setup → Bildschirm → „Publikum sieht“: Bühne oder reines Schwarz.

## Handy-/iPad-Seite

Der PC liefert die Seite mit seinen Farben, Rundungen, Schriften und (Holz) den Holzflächen aus
(`themed_page`, `/fonts/…`, `/holz/<art>/<fläche>.jpg`). Bei „Wie das System“ entscheidet das Handy hell/dunkel.

## Verworfen

„Regiepult“ (1.1), „Kontrollzentrum“ (zu bunt) und „Signal“ (zu viel Fachjargon: CH/PGM/STBY, Timecode).

## Neugestaltung 2.5 (gilt vor allem Älteren in dieser Datei)

Alle fünf Stile wurden neu gestaltet; Farben und Regeln stehen in `alupc/ui/theme.py` (`_make_theme`, `_stylesheet`):

| Stil | Leitidee | Akzent | Form |
|---|---|---|---|
| Normal „Glas“ | tiefes Blaugrau, Flächen mit heller Oberkante, Verlauf nur am Hauptknopf | Indigo → Violett | rund (16/11) |
| Technisch „Pult“ | Graphit bzw. Aluminium, Tasten mit dunkler Unterkante wie an einem Gerät | Signal-Orange | kantig (6/5) |
| Holz | Furnier bleibt, Karten mit Kupferkante unten | Kupfer | 10/8 |
| Matrix „Terminal“ | reines Schwarz, gestrichelte Rahmen, Auswahl = umgekehrte Farben | Phosphorgrün | eckig (0) |
| Regie „Mitternacht“ | Tintenblau, Pillen-Knöpfe, keine Verläufe | Türkis | 14/18 |

Rot bleibt in allen Stilen „läuft gerade auf dem Bildschirm“.
