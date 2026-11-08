## AluPC – Bildschirm steuern (Kubuntu & Windows 11)

### Downloads
| System | Datei | Hinweis |
|---|---|---|
| **Windows 11** | `AluPC-Setup-….exe` | Installer (Startmenü, Deinstallation) |
| Windows 11 | `AluPC-windows-portable-….zip` | ohne Installation: entpacken, `AluPC.exe` starten |
| **Kubuntu / Ubuntu** (22.04, 24.04 und neuer) | `alupc_…_amd64.deb` | `sudo apt install ./alupc_…_amd64.deb` – danach im Startmenü |
| Linux (x86_64) | `AluPC-linux-x86_64-….tar.gz` | ohne Installation: entpacken, `AluPC/AluPC` starten |
| Kubuntu / Ubuntu | `AluPC-installieren.sh` | installiert die .deb daneben (oder lädt sie), wartet auf laufende Updates |

### Neu in 2.4.4 – AirPlay wieder im Vollbild (Windows)
- **AirPlay-Bild füllt Monitor 2 wieder ganz:** UxPlay setzt sein Fenster auf die Videogröße zurück, sobald das
  iPhone-Bild kommt oder sich dreht (auf einem 4K-Fernseher nur noch ein Viertel oben links). AluPC prüft das
  jetzt jede Sekunde und zieht es wieder randlos über den ganzen Bildschirm.
- **Firewall:** AluPC fragt einmal neu nach der Freigabe und ersetzt Windows' Sperrregeln für UxPlay
  („Ein Teil dieser App wurde blockiert“).
- Unter Windows nicht auf echter Hardware getestet.

### Neu in 2.4.3 – Tastenbox: mehrere Aktionen, geheime Tastenfolgen, bessere MIDIs; neue Design-Auswahl
- **Mehrere Aktionen je Regel:** Wenn → Dann kann jetzt mehrere Dinge nacheinander tun („Weitere Aktion“).
- **Jede Taste wartet kurz** (Standard, abschaltbar, Wartezeit einstellbar): sonst sah man an sofort reagierenden
  Tasten, womit eine Tastenfolge anfängt. Die Anzeige unten links verrät keine nächsten Tasten mehr.
- **MIDI-Dateien klingen besser:** AluPC sucht die Melodie-Spur (statt je Moment den höchsten Ton aller Spuren),
  gleiche Töne hintereinander bleiben getrennt, Mini-Schnipsel fallen weg, die Melodie wird in den Bereich gelegt,
  in dem ein Buzzer gut klingt.
- **Buzzer-Lieder neu:** Kinderlieder raus; dafür bekannte, gemeinfreie Stücke aus Spielen/Filmen (Tetris, Can-Can,
  Türkischer Marsch, Wilhelm Tell, Toccata, Bergkönig, Für Elise, Ode an die Freude) und weniger Signale.
- **Design-Auswahl unten links neu:** Vorschaukarten je Stil, Hell/Dunkel, Farbtupfer, Profile – wirkt sofort.
- **Tastenbox ruckelt nicht mehr beim Tippen** (Auswahl erst nach kurzer Ruhe, sparsameres Leuchten).
- **Maus bei „Erweitern“ (KDE/Wayland):** wechselte man schnell um, konnte die Sperre zwischen den Monitoren bleiben.
- **Virtuelle Geräte aus = unsichtbar:** Windows schaltet VB-CABLE im Gerätemanager ab (einmal Administrator);
  „CABLE In 16ch“ (zweiter, unnötiger Eingang) immer aus. Linux: Reste beim Start entfernt.
- **OpenRGB unter Windows:** wird jetzt auch im winget-Ordner gefunden.
- Unter Windows und mit echter Box nicht getestet.

### Neu in 2.4.2 – Tastenbox ohne weiße LEDs
- **Arduino-Programm 8:** nur noch rot, grün, blau (Pins 10, 9, 16). Weiß entsteht aus allen drei; Pin A1 ist frei.
  Programm neu hochladen (Seite Tastenbox → „Tastenbox-Programm speichern …“).

### Neu in 2.4.1 – Tastenbox-Eingaben am Monitor
- **Anzeige unten links am Hauptmonitor** (Seite Tastenbox → „Eingaben unten links am Monitor anzeigen“): das
  Tastenfeld mit allen 9 Funktionen, die gedrückte Taste leuchtet. Kann eine Taste eine Tastenfolge beginnen, läuft
  ein Wartebalken (ihre Funktion kommt erst danach) und die möglichen nächsten Tasten sind umrandet; Code-Eingabe,
  ausgelöste Wenn → Dann-Regel, auf Wunsch der Regler. Klicks gehen hindurch, nimmt nie den Fokus.
  Unter KDE/Wayland legt KWin die Anzeige hin (dort am PC nicht ausprobiert).

### Neu in 2.4.0 – Tastenbox: Tastenfolgen, Buzzer mit Melodien und MIDI, flüssiger Mixer
- **Mixer ruckelt nicht mehr:** Die Audio-Seite fragte alle 2 s für jeden Regler die Geräteliste im
  Oberflächen-Thread ab – je mehr Geräte angeschlossen, desto mehr hing es. Läuft jetzt im Hintergrund.
  Windows: Pegelanzeige hält den Ton-Hilfsprozess nicht mehr dauernd besetzt (Regler-Lautstärke wartete).
  Regler drehen zeichnet nur noch gebündelt neu (vorher 4–9 ms Arbeit je Reglerwert, jetzt 0,1 ms).
- **Wenn → Dann: Tastenfolgen** – z. B. 7‑1‑2 schnell hintereinander (unten links, oben links, oben Mitte)
  startet eine Aktion, ohne Code-Taste. Folge im Dialog auf einem 3 × 3-Feld antippen.
- **Buzzer** (Arduino-Programm 7, Buzzer an A2): Melodien und Signaltöne, eigene MIDI-Dateien (höchste Stimme),
  Klick bei Tastendruck – für Tasten und Wenn → Dann-Regeln.
- **Klang am PC** als Aktion für Tasten und Regeln (eingebaute Klänge oder eigene Tondatei).
- **Suche in den Auswahllisten:** in der Liste tippen filtert die Einträge.
- **Tastenbox-Seite übersichtlicher:** „So geht's“, LEDs mit Erklärung und Farbknöpfen, LED-Test zeigt Rot, Grün,
  Blau einzeln (Standardfarbe #00FFA0 hat kein Rot – rote LEDs bleiben damit aus), eigener Buzzer-Bereich.
- Am PC getestet und kompiliert; mit echter Box und echtem Buzzer nicht getestet.

### Neu in 2.3.0 – Tastenbox fertig (Pro Micro, 12 Farb-LEDs, Wenn → Dann), Dual-Boot-Abgleich, neues Design
- **Arduino-Programme direkt aus AluPC** (Seite Tastenbox): Tastenbox-Programm und Poti-Test als fertiger Ordner
  für die Arduino-IDE speichern.
- **Arduino-Programm 6:** 3 LEDs je Ecke (rot, grün, blau) mischen jede Farbe, eigener Pin je Farbe, weiche
  Helligkeit; Pro Micro: JTAG wird abgeschaltet (sonst gingen A0–A3 nicht); Poti-Test findet den Schleifer selbst.
- **Dual-Boot-Abgleich nimmt die Tastenbox mit:** Tasten, Regler, LEDs, Wenn → Dann – der Anschluss (COM/tty)
  bleibt je System.
- **Wenn → Dann (Seite Tastenbox):** Code auf den 9 Tasten (Code-Taste lang drücken, Ziffern tippen) und/oder
  Reglerzahl 0–999 → Programm starten, Webseite öffnen, Video/Datei oder Webseite auf Bildschirm 2, eigener Befehl
  oder AluPC-Befehl. Regeln auch auf Tasten legbar. Reglerzahl braucht Arduino-Programm 5 und Regler-Ziel „Zahl 0–999“.
- **Regler-Vorlagen und Skala:** Lautstärke über das Zählwerk – 000–999 ÷ 10 (fein) oder 000–100 direkt;
  auch für Mikrofon und Ton auf Bildschirm 2.
- **Vorlagen auf Bildschirm 2 neu (taste-skill):** flache, tiefe Farbfelder statt Lichtflecken und Verläufe,
  keine Etiketten über Überschriften, Fließtext kräftiger; „Glas-Karte“ wird zur „Farbfeld-Karte“.
- **Handy-Seite im Regie-Design:** flach, Haarlinien, keine Glas-Leiste, Auswahl als dünne Linie.
- **Tastenbox-LEDs am Handy** (Seite Start): an/aus/bei Live, Effekt, Farbschema, Farbe, Helligkeit.
- **Bunte LEDs:** Schemata Eine Farbe, Zustand (Rot = live), Regenbogen; RGB-LED-Ketten gehen weiterhin.
  Plan, Steckplan und 3D-Teile (Gehäuse, Tastenkappen, Drehknopf, Stift-Adapter): hardware/tastenbox/.
  Arduino-Programme nur am PC simuliert.

### Neu in 2.2.0 – neues Design „Regie“, Updates-Seite, Tastenbox 3, neuer Aufbau
- **Neues Standard-Design „Regie“:** fast schwarz, ruhig für lange Abende, 1-px-Haarlinien statt Schatten,
  jede Auswahl als dünne blaue Linie, Rot nur für „live“, Inter mit einstöckigem g. Gestaltet mit den Skills
  taste-skill und awesome-design (Raycast-Vorlage). Normal, Technisch, Holz und Matrix bleiben wählbar
  (Setup → Darstellung); beim Update wird einmal auf Regie umgestellt.
- **Tastenbox** (Arduino Pro Micro, 9 Tasten + Drehregler + LED): eigene Seite „Tastenbox“. AluPC findet die Box
  von selbst. Die Tasten erscheinen 3 × 3 wie auf der Box und leuchten beim Drücken; jede bekommt einen Befehl
  (auch Szenen, Studio-Übergang). Der Regler regelt wahlweise PC-Ton, Ton auf Bildschirm 2, Mikrofon oder ein
  Programm. Beim Wechsel startet das neue Ziel bei 0 und folgt erst, wenn der Regler auch auf 0 steht (Skala am
  Regler). Regelt er den PC-Ton, stellt AluPC andere Wege (Windows, Tastatur, Handy, Sprache) zurück.
  Arduino-Programm: Seite Tastenbox → „Arduino-Programm speichern“. Noch nicht mit echter Box getestet.
- **Windows:** AluPC konnte zufällig einfrieren. Ursache war die Ton-Steuerung (Core Audio) im Programm selbst. Sie
  läuft jetzt in einem eigenen kleinen Hilfsprozess; hängt der, startet AluPC nur ihn neu.
- **Updates** (Geräte → Updates): PC nach Updates und Treibern durchsuchen – Windows: Windows Update (System +
  Treiber), winget (Programme), Geräte ohne Treiber; Linux: apt/dnf/pacman, Flatpak, Snap, Firmware (fwupd),
  empfohlene Treiber, Geräte ohne Treiber. Nur prüfen; installiert wird mit den Werkzeugen des Systems.
  Unter Windows nur in der CI-Umgebung geprüft, nicht auf einem echten PC mit offenen Updates.
- **Tastenbox, Arduino-Programm 3:** läuft auf Pro Micro **und Nano** (Belegung passt sich an). LEDs aus der App:
  Effekt (ruhig, atmen, blinken) und Farbe (LED-Streifen). Neue **Fehlersuche**: Protokoll aller Zeilen,
  Rohwerte live (Tasten leuchten, solange sie unten sind; Regler 0–1023), LED-Test, Neu verbinden.
  Programm am PC simuliert; Tastentest auf Nano in Wokwi vom Nutzer bestätigt, Programm 3 selbst noch nicht auf
  echter Hardware.
- **Startseite:** Live-Bild mit Vorschau steht oben über den Kacheln (Spalte rechts nur auf Wunsch); im Stil Technisch kompakter.
- **Seitenleiste aufgeräumt:** 6 statt 8 Punkte. „Geräte“ bündelt PC-Status, Tastenbox und Fingerabdruck als
  Reiter; „Zuhören“ und „Sperren“ stehen in einer Zeile.
- **Vorlagen ohne Überdeckung:** Uhr, Countdown und Laufschrift stehen in einer Info-Leiste unter der Seite statt als
  Kasten darüber. Im Szenen-Editor für jede gestaltete Seite einstellbar.
- **Tastenbox-Optionen:** lang drücken (eigener Befehl, Arduino-Programm Version 2), Regler umkehren, Höchstwert,
  Null-Abgleich an/aus, LED-Helligkeit, LED blitzt bei Tastendruck. Arduino-Programm nur am PC simuliert,
  nicht auf echter Hardware getestet.
- **Studio-Modus** (Knopf „Studio“ auf der Startseite, wie in OBS): Über dem Live-Bild klappt eine Vorschau auf.
  Kacheln und Szenen landen erst dort, „Übergang“ (oder Strg+Enter) schaltet live, „Schnitt“ ohne Überblenden.
  Live-Kachel rot, Vorschau-Kachel grün. Kamera, Bildschirm-Aufnahme und AirPlay zeigt die Vorschau nur als Schema.
- **Neues Design „Matrix“:** grüne Ziffernschrift auf Schwarz, Code-Regen im Hintergrund.
- **Bildschirm 2 neu gestaltet:** alle Vorlagen mit neuem Hintergrund und neuer Schrift; Ablauf, Pause, Hinweis,
  WLAN, Laufschrift, Listen und Pro & Contra neu aufgebaut.
- **Technisch wie ein DJ-Pult:** Kacheln als beleuchtete Pads, leuchtende Tasten, Pegel für den PC-Ton.
- **Alle Seiten überarbeitet:** Szenen mit Suche und Detail-Spalte, Setup mit Gruppen und Suche, neuer Szenen-Editor.
- **Design-Knopf** unten links: Stil, Hell/Dunkel, Profil (abschaltbar in Darstellung).
- **Lautstärkeregler nicht mehr auf der Startseite** – PC-Ton: System → PC steuern; Programme: Audio.
- **Neuer Aufbau der Startseite („Studio“):**
  - Breites Fenster (ab ca. 1240 px): rechts ein festes **Live-Dock** mit großem Live-Bild, allen Schnellschaltern
    mit Namen, Reglern und „Beenden“. Es bleibt stehen, während man die Kacheln scrollt.
  - Oben eine große **Befehlsleiste** („Suchen oder schreiben, was laufen soll …“, Strg+K).
  - Schmaleres Fenster: wie bisher eine Karte über den Kacheln.
  - Technisch: echte Messwerte im Dock (Prozessor, Speicher, Temperatur mit Verlauf), feines Punktraster im
    Hintergrund, gelbe Signalkante.
- **Design anpassen und speichern** (Setup → Darstellung):
  - Rundung (eckig bis sehr rund), Schriftgröße (85–130 %), Abstände (kompakt, normal, luftig)
  - eigene Akzentfarbe („Farbe wählen …“)
  - Profile: fertige Profile („Werkstatt“, „Wohnzimmer“, „Hell & rund“ …) oder eigene speichern, wechseln,
    löschen. „Aktuelle Einstellungen“ holt den Stand von vor dem Profil zurück.
- **Designs aufgefrischt:**
  - Normal: Leuchten in der Akzentfarbe, Kacheln heben sich beim Überfahren, laufende Kacheln leuchten rot.
  - Technisch: Signalkante an Karten.
  - Holz: Messing-Einlegeleiste.
- **Mehr Schnellschalter oben:** neu sind Schoner, Overlays, Stumm (PC-Ton) und Weiter (nächste Szene). Bei Platz
  mit Namen auf zwei Zeilen, sonst nur Symbole.
- **Publikum sieht immer dasselbe:**
  - Gilt für Abstimmung, Glücksrad, WLAN-Code, Uhr, Text, Timer, System, Musik, Spiele und AirPlay-Warten.
  - Einheitliche dunkle „Bühne“, unabhängig vom PC-Design.
  - Setup → Bildschirm → „Publikum sieht“: Bühne oder reines Schwarz.
- **Linux: Spiegeln ohne Bildschirm-Auswahl.** Würde die Aufnahme nach dem Bildschirm fragen (Wayland ohne
  KWin-Freigabe), spiegelt KDE direkt, ohne Nachfrage.
- **Nicht getestet** auf echten Geräten (Spiegeln unter KDE/Wayland, PC-Stummschalten, Temperatur, Ton-Pegel, Studio-Modus) – nur automatische Tests
  und Bildschirmfotos.

### Frühere Versionen
[CHANGELOG](https://github.com/SG-Junior-FLL/AluPC/blob/main/docs/CHANGELOG.md)

### Grenzen
Tests laufen ohne echte Monitore, Kameras und Sensoren. Fehler bitte mit Screenshot melden.
