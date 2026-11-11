// AluPC-Tasten – Programm für die Tastenbox (Arduino Pro Micro oder Arduino Nano), Version 9
//
// 9 Tasten (3 × 3), 1 Drehregler, LEDs in 3 Farben (rot, grün, blau – je Farbe ein Pin), Buzzer.
// In der Arduino-IDE das Board wählen – Pro Micro: „Arduino Leonardo“ (technisch gleich), Nano: „Arduino Nano“
// (bei Nachbauten evtl. Prozessor „ATmega328P (Old Bootloader)“) –, Anschluss wählen, hochladen.
// AluPC findet die Box danach von selbst (Seite „Tastenbox“).
//
// BELEGUNG: An welchem Pin was hängt, lässt sich in AluPC umstellen (Seite Tastenbox → Belegung) – die Box merkt
// es sich dauerhaft (EEPROM). Ohne Umstellen gilt:
//
//                 Pro Micro                        Nano
//   Tasten 1–9 :  2 3 4 5 6 7 8 14 15              2 3 4 5 6 7 8 9 10
//   LEDs       :  rot 10 · grün 9 · blau 16        rot 11 · grün 12 · blau 13
//   Regler     :  A0                               A0
//   Buzzer     :  A2                               A2
//
//   Tasten: das andere Bein an GND (keine Widerstände nötig).
//   Regler: Potentiometer 10 kΩ linear – Mitte an den Regler-Pin, die Außenbeine an 5V/VCC und GND.
//   LEDs:   je Farbe ein 220-Ω-Widerstand vom Pin zu den 4 LEDs dieser Farbe (langes Bein), kurze Beine an GND.
//           Je Ecke 3 LEDs rot, grün, blau – zusammen mischen sie jede Farbe (auch Weiß).
//   Buzzer: + an den Buzzer-Pin, − an GND. Passiver Buzzer spielt Töne und Melodien, aktiver piept nur.
//
// Was die Box an den PC schickt (115200 Baud, eine Zeile je Meldung):
//   HALLO ALUPC-TASTEN 9 NANO – Antwort auf „WER?“ (und einmal beim Start); 9 = Programmversion, dann das Board
//   TASTE n / LANG n          – Taste n (1–9) kurz gedrückt (beim Loslassen) / lange gedrückt (ab 0,6 s)
//   LAUT n                    – Regler in Prozent (0–100), bei Bewegung und auf „LAUT?“
//   WERT n                    – derselbe Regler fein (0–999), bei Änderung und auf „WERT?“
//   ROH 010000000 512         – nur mit „DEBUG 1“: alle 0,1 s jede Taste (1 = unten) und der Regler roh (0–1023)
//   BELEGUNG t1 … t9 r g b regler buzzer – die 14 Pins (Antwort auf „BELEGUNG?“ und nach jedem Umstellen)
//   PIN n                     – nur mit „SUCHE 1“: Pin n wurde gerade mit GND verbunden (Taste gedrückt)
// Was der PC schickt:
//   WER?  LAUT?  WERT?  LED n (Helligkeit 0–100)  EFFEKT n (0 ruhig, 1 atmen, 2 blinken, 3 Farbwechsel)
//   FARBE r g b (je 0–255)  PIXEL i r g b (nur RGB-Streifen)  TON f ms (Buzzer; TON 0 0 = Stille)  DEBUG 0|1
//   BELEGUNG? · BELEGUNG t1 … t9 r g b regler buzzer (Pin-Nummern, wird gespeichert) · BELEGUNG 0 (Standard)
//   SUCHE 0|1 (alle Pins beobachten: welcher Pin gehört zu welcher Taste?) · PIN n ms (Pin n kurz einschalten:
//   welche LED hängt daran?)
//
// Nur für RGB-Streifen (WS2812B) statt einzelner LEDs: die nächste Zeile einschalten (// weg), Zahl = LEDs der Kette.
// #define STREIFEN 11

#include <EEPROM.h>

#if defined(__AVR_ATmega32U4__)
const char BOARD[] = "PROMICRO";
const uint8_t STANDARD[14] = {2, 3, 4, 5, 6, 7, 8, 14, 15, /* rot grün blau */ 10, 9, 16, /* Regler */ A0, /* Buzzer */ A2};
const uint8_t ALLE_PINS[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 15, 16, A0, A1, A2, A3};
#else
const char BOARD[] = "NANO";
const uint8_t STANDARD[14] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, A0, A2};
const uint8_t ALLE_PINS[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, A0, A1, A2, A3, A4, A5};
#endif
const uint8_t ANZAHL_PINS = sizeof(ALLE_PINS);
const uint8_t MERKER = 0xA9;        // steht im EEPROM vor einer gespeicherten Belegung
const unsigned long LANG_MS = 600;  // ab so langem Drücken: „LANG n“ statt „TASTE n“

// Belegung: [0–8] Tasten, [9–11] rot/grün/blau, [12] Regler, [13] Buzzer
uint8_t pins[14];
#define TASTE_PIN(i) pins[i]
#define LED_PIN(k) pins[9 + (k)]
#define REGLER_PIN pins[12]
#define BUZZER_PIN pins[13]

// ---------------------------------------------------------------- LEDs: weiche Helligkeit an jedem Pin
// Timer 1 schaltet die drei Farb-Pins 8000-mal je Sekunde (64 Stufen = 125 Hz) – geht an jedem Pin, auch ohne PWM.
volatile uint8_t kanal[3];
volatile uint8_t* ledPort[3];
volatile uint8_t ledMaske[3];
ISR(TIMER1_COMPA_vect) {
  static uint8_t schritt = 0;
  schritt = (schritt + 1) & 63;
  for (uint8_t k = 0; k < 3; k++) {
    if (kanal[k] > schritt) *ledPort[k] |= ledMaske[k];
    else *ledPort[k] &= ~ledMaske[k];
  }
}

uint8_t stufe(uint16_t v, int prozent) {  // 0–255 → 0–64, quadratisch (das Auge sieht dunkle Stufen feiner)
  v = v * prozent / 100;
  return v >= 255 ? 64 : (uint32_t)v * v / 1032;  // 64 = dauernd an
}

#ifdef STREIFEN
#include <Adafruit_NeoPixel.h>
Adafruit_NeoPixel streifen(STREIFEN, LED_PIN(0), NEO_GRB + NEO_KHZ800);
uint8_t px[STREIFEN][3];
#endif

// ---------------------------------------------------------------- Zustand
bool gedrueckt[9], langGemeldet[9];
unsigned long zuletzt[9], untenSeit[9];
int regler = -1, wert = -1;   // zuletzt gemeldet: Prozent und feiner Wert
float glatt = 0, fein = 0;    // geglättete Messwerte
char zeile[72];
uint8_t zeileLaenge = 0;
int hell = 0, effekt = 0;
uint8_t farbe[3] = {0, 255, 160};
bool debug = false, suche = false;
unsigned long debugZuletzt = 0;
bool sucheUnten[sizeof(ALLE_PINS)];
int8_t testPin = -1;          // „PIN n ms“: dieser Pin ist gerade zum Test eingeschaltet
unsigned long testEnde = 0;

// ---------------------------------------------------------------- Belegung
bool pinErlaubt(uint8_t pin) {
  for (uint8_t i = 0; i < ANZAHL_PINS; i++) if (ALLE_PINS[i] == pin) return true;
  return false;
}

bool belegungGueltig(const uint8_t* p) {
  for (uint8_t i = 0; i < 14; i++) {
    if (!pinErlaubt(p[i])) return false;
    for (uint8_t k = 0; k < i; k++) if (p[k] == p[i]) return false;  // jeder Pin nur einmal
  }
  return p[12] >= A0;  // der Regler braucht einen Analog-Pin
}

void belegungAnwenden() {
  noInterrupts();
  for (uint8_t i = 0; i < ANZAHL_PINS; i++) pinMode(ALLE_PINS[i], INPUT);  // alte Ausgänge loslassen
  for (uint8_t i = 0; i < 9; i++) pinMode(TASTE_PIN(i), INPUT_PULLUP);     // offen = HIGH, gedrückt = LOW
  for (uint8_t k = 0; k < 3; k++) {
    pinMode(LED_PIN(k), OUTPUT);
    digitalWrite(LED_PIN(k), LOW);
    ledPort[k] = portOutputRegister(digitalPinToPort(LED_PIN(k)));
    ledMaske[k] = digitalPinToBitMask(LED_PIN(k));
  }
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  interrupts();
  testPin = -1;
  for (uint8_t i = 0; i < 9; i++) gedrueckt[i] = langGemeldet[i] = false;
  glatt = fein = analogRead(REGLER_PIN);
  regler = wert = -1;
}

void belegungLaden() {
  uint8_t p[14];
  bool ok = EEPROM.read(0) == MERKER;
  for (uint8_t i = 0; i < 14 && ok; i++) p[i] = EEPROM.read(1 + i);
  if (!ok || !belegungGueltig(p)) memcpy(p, STANDARD, 14);
  memcpy(pins, p, 14);
}

void belegungMelden() {
  Serial.print(F("BELEGUNG"));
  for (uint8_t i = 0; i < 14; i++) { Serial.print(' '); Serial.print(pins[i]); }
  Serial.println();
}

// ---------------------------------------------------------------- LEDs zeigen
void ledZeigen(int prozent) {
  prozent = constrain(prozent, 0, 100);
  uint16_t r = farbe[0], g = farbe[1], b = farbe[2];
  if (effekt == 3) {  // Farbwechsel: einmal rund um den Farbkreis in 6 s
    uint16_t hue = (millis() / 24) % 256;
    uint8_t seg = hue / 43, f = (hue % 43) * 6, q = 255 - f;
    const uint8_t rgb[6][3] = {{255, f, 0}, {q, 255, 0}, {0, 255, f}, {0, q, 255}, {f, 0, 255}, {255, 0, q}};
    r = rgb[seg % 6][0]; g = rgb[seg % 6][1]; b = rgb[seg % 6][2];
  }
#ifdef STREIFEN
  for (int i = 0; i < STREIFEN; i++) {
    uint16_t pr = effekt == 3 ? r : px[i][0], pg = effekt == 3 ? g : px[i][1], pb = effekt == 3 ? b : px[i][2];
    streifen.setPixelColor(i, streifen.Color(pr * prozent / 100, pg * prozent / 100, pb * prozent / 100));
  }
  streifen.show();
#else
  kanal[0] = stufe(r, prozent);
  kanal[1] = stufe(g, prozent);
  kanal[2] = stufe(b, prozent);
  for (uint8_t k = 0; k < 3; k++) if (testPin == LED_PIN(k)) kanal[k] = 64;  // „PIN n“: diese Farbe voll an
#endif
}

int effektHelligkeit(unsigned long t) {
  if (effekt == 1) {  // atmen: 3 s langsam hell und wieder dunkel (nie ganz aus)
    unsigned long p = t % 3000;
    int s = p < 1500 ? p : 3000 - p;
    return hell * (15 + 85L * s / 1500) / 100;
  }
  if (effekt == 2) return (t / 400) % 2 ? 0 : hell;  // blinken
  return hell;
}

void hallo() {
  Serial.print(F("HALLO ALUPC-TASTEN 9 "));
  Serial.println(BOARD);
}

bool istAusgang(uint8_t pin) {
  return pin == LED_PIN(0) || pin == LED_PIN(1) || pin == LED_PIN(2) || pin == BUZZER_PIN;
}

void testAus() {  // Test-Pin wieder so, wie es die Belegung will
  if (testPin < 0) return;
  uint8_t pin = testPin;
  testPin = -1;
  if (istAusgang(pin)) {
    if (pin == BUZZER_PIN) digitalWrite(pin, LOW);
    ledZeigen(effektHelligkeit(millis()));
    return;
  }
  digitalWrite(pin, LOW);
  bool taste = false;
  for (uint8_t i = 0; i < 9; i++) taste |= TASTE_PIN(i) == pin;
  pinMode(pin, taste || suche ? INPUT_PULLUP : INPUT);
}

// ---------------------------------------------------------------- Befehle vom PC
// Zahlen hinter dem Befehlswort lesen (durch Leerzeichen getrennt). Rückgabe: wie viele gefunden.
uint8_t zahlen(const char* text, long* ziel, uint8_t hoechstens) {
  uint8_t n = 0;
  while (*text && n < hoechstens) {
    while (*text == ' ') text++;
    if (!*text) break;
    char* ende;
    long v = strtol(text, &ende, 10);
    if (ende == text) break;
    ziel[n++] = v;
    text = ende;
  }
  return n;
}

bool beginnt(const char* text, const char* wort) { return strncmp(text, wort, strlen(wort)) == 0; }

void befehl(char* text) {
  for (char* c = text; *c; c++) *c = toupper(*c);
  long v[14];
  if (!strcmp(text, "WER?")) {
    hallo();
  } else if (!strcmp(text, "WERT?")) {
    Serial.print(F("WERT ")); Serial.println(wert < 0 ? 0 : wert);
  } else if (!strcmp(text, "LAUT?")) {
    Serial.print(F("LAUT ")); Serial.println(regler < 0 ? 0 : regler);
  } else if (!strcmp(text, "BELEGUNG?")) {
    belegungMelden();
  } else if (beginnt(text, "BELEGUNG ")) {
    uint8_t n = zahlen(text + 9, v, 14), neu[14];
    if (n == 1 && v[0] == 0) {           // zurück auf Standard
      memcpy(neu, STANDARD, 14);
      EEPROM.update(0, 0xFF);
    } else {
      for (uint8_t i = 0; i < 14; i++) neu[i] = n == 14 && v[i] >= 0 && v[i] < 64 ? v[i] : 255;
      if (!belegungGueltig(neu)) { Serial.println(F("FEHLER BELEGUNG")); return; }
      EEPROM.update(0, MERKER);
      for (uint8_t i = 0; i < 14; i++) EEPROM.update(1 + i, neu[i]);
    }
    noTone(BUZZER_PIN);
    memcpy(pins, neu, 14);
    belegungAnwenden();
    ledZeigen(effektHelligkeit(millis()));
    belegungMelden();
  } else if (beginnt(text, "LED ")) {
    if (zahlen(text + 4, v, 1)) hell = constrain(v[0], 0, 100);
    ledZeigen(effektHelligkeit(millis()));
  } else if (beginnt(text, "EFFEKT ")) {
    if (zahlen(text + 7, v, 1)) effekt = constrain(v[0], 0, 3);
    ledZeigen(effektHelligkeit(millis()));
  } else if (beginnt(text, "FARBE ")) {
    if (zahlen(text + 6, v, 3) == 3) {
      for (uint8_t k = 0; k < 3; k++) farbe[k] = constrain(v[k], 0, 255);
#ifdef STREIFEN
      for (int i = 0; i < STREIFEN; i++) for (int k = 0; k < 3; k++) px[i][k] = farbe[k];
#endif
      ledZeigen(effektHelligkeit(millis()));
    }
  } else if (beginnt(text, "PIXEL ")) {
#ifdef STREIFEN
    if (zahlen(text + 6, v, 4) == 4 && v[0] >= 0 && v[0] < STREIFEN) {
      for (int k = 0; k < 3; k++) px[v[0]][k] = constrain(v[k + 1], 0, 255);
      ledZeigen(effektHelligkeit(millis()));
    }
#endif
  } else if (beginnt(text, "TON ")) {
    uint8_t n = zahlen(text + 4, v, 2);
    if (n == 0 || v[0] < 31 || (n == 2 && v[1] <= 0)) noTone(BUZZER_PIN);
    else tone(BUZZER_PIN, min(v[0], 20000L), n == 2 ? min(v[1], 10000L) : 200);
  } else if (beginnt(text, "PIN ")) {  // einen Pin kurz einschalten: welche LED hängt daran?
    uint8_t n = zahlen(text + 4, v, 2);
    if (n >= 1 && v[0] >= 0 && v[0] < 64 && pinErlaubt(v[0]) && v[0] != REGLER_PIN) {
      testAus();
      testPin = v[0];
      testEnde = millis() + (n == 2 ? constrain(v[1], 50, 5000) : 1000);
      if (istAusgang(testPin) && testPin != BUZZER_PIN) {
        ledZeigen(effektHelligkeit(millis()));
      } else {
        pinMode(testPin, OUTPUT);
        digitalWrite(testPin, HIGH);
      }
    }
  } else if (beginnt(text, "SUCHE ")) {  // alle Pins beobachten: welcher gehört zu welcher Taste?
    suche = zahlen(text + 6, v, 1) && v[0] != 0;
    if (suche) {
      for (uint8_t i = 0; i < ANZAHL_PINS; i++) {
        if (istAusgang(ALLE_PINS[i]) || ALLE_PINS[i] == REGLER_PIN) continue;
        pinMode(ALLE_PINS[i], INPUT_PULLUP);
        sucheUnten[i] = false;
      }
    } else {
      belegungAnwenden();
      ledZeigen(effektHelligkeit(millis()));
    }
  } else if (beginnt(text, "DEBUG ")) {
    debug = zahlen(text + 6, v, 1) && v[0] != 0;
  }
}

// ---------------------------------------------------------------- Start und Schleife
void setup() {
#if defined(__AVR_ATmega32U4__)
  // Pro Micro: A0–A3 sind auch JTAG-Pins; bei manchen Nachbauten ist JTAG an – dann gingen sie nicht.
  MCUCR |= (1 << JTD);
  MCUCR |= (1 << JTD);
#endif
  Serial.begin(115200);
  belegungLaden();
  belegungAnwenden();
#ifdef STREIFEN
  streifen.setPin(LED_PIN(0));
  streifen.begin();
  for (int i = 0; i < STREIFEN; i++) for (int k = 0; k < 3; k++) px[i][k] = farbe[k];
#else
  noInterrupts();
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11);  // CTC, Takt / 8
  OCR1A = F_CPU / 8 / 8000 - 1;     // 8 kHz
  TIMSK1 = _BV(OCIE1A);
  interrupts();
#endif
  ledZeigen(0);
  hallo();  // Nano startet beim Öffnen des Anschlusses neu – so weiß AluPC sofort Bescheid
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      zeile[zeileLaenge] = 0;
      if (zeileLaenge) befehl(zeile);
      zeileLaenge = 0;
    } else if (zeileLaenge < sizeof(zeile) - 1) {
      zeile[zeileLaenge++] = c;
    }
  }
  unsigned long jetzt = millis();
  if (testPin >= 0 && (long)(jetzt - testEnde) >= 0) testAus();

  if (suche) {  // Pin-Suche: jeder Pin, der auf GND geht, wird gemeldet – Tasten und Regler ruhen solange
    for (uint8_t i = 0; i < ANZAHL_PINS; i++) {
      uint8_t pin = ALLE_PINS[i];
      if (istAusgang(pin) || pin == REGLER_PIN || pin == testPin) continue;
      bool unten = digitalRead(pin) == LOW;
      if (unten && !sucheUnten[i]) { Serial.print(F("PIN ")); Serial.println(pin); }
      sucheUnten[i] = unten;
    }
    delay(20);
    return;
  }

  // Tasten (entprellt: 30 ms Ruhe nach jedem Wechsel). Kurz: „TASTE n“ beim Loslassen, lang: „LANG n“ nach 0,6 s
  for (uint8_t i = 0; i < 9; i++) {
    bool unten = digitalRead(TASTE_PIN(i)) == LOW;
    if (unten != gedrueckt[i] && jetzt - zuletzt[i] > 30) {
      gedrueckt[i] = unten;
      zuletzt[i] = jetzt;
      if (unten) {
        untenSeit[i] = jetzt;
        langGemeldet[i] = false;
      } else if (!langGemeldet[i]) {
        Serial.print(F("TASTE ")); Serial.println(i + 1);
      }
    }
    if (gedrueckt[i] && !langGemeldet[i] && jetzt - untenSeit[i] >= LANG_MS) {
      langGemeldet[i] = true;
      Serial.print(F("LANG ")); Serial.println(i + 1);
    }
  }

  // Regler grob (0–100): glätten, erst ab 1 % Bewegung melden (kein Hin-und-her an der Grenze)
  int roh = analogRead(REGLER_PIN);
  glatt = glatt * 0.85 + roh * 0.15;
  float genauProzent = glatt * 100.0 / 1023.0;
  if (regler < 0 || fabs(genauProzent - regler) > 0.7) {
    int neu = constrain((int)round(genauProzent), 0, 100);
    if (neu != regler) {
      regler = neu;
      Serial.print(F("LAUT ")); Serial.println(regler);
    }
  }

  // Regler fein (0–999): 16 Messungen mitteln, stark glätten, erst ab 0,6 Schritten Abstand melden
  long summe = 0;
  for (uint8_t k = 0; k < 16; k++) summe += analogRead(REGLER_PIN);
  fein = fein * 0.8 + (summe / 16.0) * 0.2;
  float genau = fein * 999.0 / 1023.0;
  if (wert < 0 || fabs(genau - wert) > 0.6) {
    int neu = constrain((int)round(genau), 0, 999);
    if (neu != wert) {
      wert = neu;
      Serial.print(F("WERT ")); Serial.println(wert);
    }
  }

  if (effekt != 0) ledZeigen(effektHelligkeit(jetzt));  // Effekte laufen in der Box (flüssig, ohne den PC)

  if (debug && jetzt - debugZuletzt >= 100) {
    debugZuletzt = jetzt;
    Serial.print(F("ROH "));
    for (uint8_t i = 0; i < 9; i++) Serial.print(digitalRead(TASTE_PIN(i)) == LOW ? '1' : '0');
    Serial.print(' ');
    Serial.println(roh);
  }
  delay(5);
}
