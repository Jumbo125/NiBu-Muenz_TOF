# ESP32 / Arduino – Münzprüfer CH926

Technische Zusammenfassung des Tutorials von donskytech, 24.08.2023.

**Originalartikel:** [Connect your Arduino projects with a universal Coin Acceptor](https://www.donskytech.com/esp32-arduino-coin-acceptor/)

## Aufbau laut Tutorial

- Programmierbarer Münzprüfer CH926, Versorgung 12 V DC.
- ESP32 mit Arduino-Framework, Signaleingang GPIO19.
- 10-kΩ-Pull-up-Widerstand an der COIN-Leitung.
- SSD1306-OLED (128 × 64, I²C, Adresse 0x3C).
- Relais an GPIO13.

## Funktionsweise

Der Münzprüfer gibt je nach programmierter Münze eine bestimmte Anzahl von Impulsen aus. Das Beispiel wertet fallende Flanken mittels GPIO-Interrupt aus. Je Impuls werden zehn Sekunden Laufzeit gutgeschrieben. Das Display zeigt Guthaben beziehungsweise Restlaufzeit; das Relais schaltet nach Ablauf ab.

## Software

Verwendete Bibliotheken: Adafruit SSD1306 und elapsedMillis. Der Originalcode ist über das [GitHub-Repository](https://github.com/donskytech/platformio-projects) verfügbar.

```bash
git clone https://github.com/donskytech/platformio-projects.git
cd platformio-projects/esp32-projects/esp32-coin-acceptor
```

Das Tutorial empfiehlt, blockierende Operationen und unnötige serielle Ausgaben zu vermeiden.

## Ergänzung: Anschluss an einen Raspberry Pi

Die folgenden Hinweise sind eine eigene technische Ergänzung, nicht Bestandteil des Originalartikels. Sie gelten nur, wenn der COIN-Ausgang tatsächlich ein gegen Masse schaltender Open-Collector-/Open-Drain-Ausgang ist.

```text
12-V-Netzteil (+) ---------------- Münzprüfer +12 V
12-V-Netzteil (−) ---------------- Münzprüfer GND
                         |
                         +-------- Raspberry Pi GND

Raspberry Pi 3,3 V
         |
       10 kΩ
         |
         +------------------------ Raspberry Pi GPIO (Eingang)
         |
         +------------------------ Münzprüfer COIN
```

- NO-Modus (normally open), sofern er bei diesem Gerät einen aktiven LOW-Impuls erzeugt.
- Ruhezustand: ungefähr 3,3 V. Während des Impulses: nahe 0 V.
- Vor dem Anschluss des GPIO sicherstellen, dass COIN keine eigene Spannung oberhalb von 3,3 V ausgibt. Die Ausgangsschaltung des konkreten Münzprüfers ist zu verifizieren; bei einem aktiv getriebenen 5-V- oder 12-V-Ausgang ist eine geeignete Pegelanpassung notwendig.
- Ein Multimeter kann kurze Impulse übersehen. Ein Oszilloskop oder ein geeigneter Logikanalysator ist aussagekräftiger.
- Für die Software ist ein Impulszähler mit anschließender Auswertung der Impulsgruppe robuster als ein einzelnes boolesches Interrupt-Flag.

## Quelle

Donsky. (2023, 24. August). *Connect your Arduino projects with a universal Coin Acceptor*. donskytech.com. https://www.donskytech.com/esp32-arduino-coin-acceptor/

Die Datei enthält eine deutschsprachige Zusammenfassung und eigene ergänzende Hinweise, keine vollständige Kopie des Artikels. Originalschaltbilder und Originalcode sind über die verlinkten Quellen erreichbar.
