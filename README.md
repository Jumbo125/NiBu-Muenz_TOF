Pico MicroPython Setup
======================

Dateien auf den Raspberry Pi Pico kopieren:
1. main.py
2. lib/vl53l1x.py  (externer VL53L1X-MicroPython-Treiber)

main.py startet auf MicroPython automatisch beim Boot.

Hinweis:
Der VL53L1X-Treiber ist nicht Bestandteil dieses ZIPs.
Verwende z.B. den Pico-Treiber aus:
drakxtwo/vl53l1x_pico -> vl53l1x.py

Danach liegen auf dem Pico:
/
  main.py
  lib/
    vl53l1x.py

USB-Protokoll (zum PC):
READY
VERSION:<x.y.z> (<datum>)   (einmal nach READY, zusätzlich nach DEBUG:ON)
DISTANCE:<mm>
PRESENCE        (Altprotokoll-Fallback, siehe unten)
COIN:<cent>
STATUS:TOF=OK / STATUS:TOF=ERR
DEBUG:STATE=ON / DEBUG:STATE=OFF
ALIVE:<n>       (nur im DEBUG-Modus, Heartbeat)
DBG:<text>      (nur im DEBUG-Modus)

Firmware-Version: main.py enthält FIRMWARE_VERSION / FIRMWARE_DATE ganz am
Dateianfang. Wird beim Boot (nach READY) und bei jedem DEBUG:ON gesendet,
damit im coin_debug_monitor.py sofort sichtbar ist, welcher main.py-Stand
tatsächlich auf dem Pico läuft -- bei jeder main.py-Änderung diese beiden
Konstanten hochzählen/aktualisieren.


ToF (VL53L1X) -- Distanzerkennung
==================================

I2C0 an GP0 (SDA) / GP1 (SCL), 400 kHz.

Läuft als kontinuierliches Free-Running-Messen (main.py, create_tof() /
TOF_SAMPLE_MS): Rohmessung alle 50 ms, über ein 5er-Fenster gemittelt
(Median-Filter), gedrosselt alle 250 ms als DISTANCE:<mm> an den PC
gesendet. Das läuft dauerhaft, solange main.py läuft und der Sensor
erreichbar ist -- kein Einzel-Event, sondern ein Dauerstream.

Bei I2C-Fehlern (Sensor nicht gefunden, Lesefehler) versucht main.py alle
TOF_RETRY_MS (5 s) eine Reinitialisierung. STATUS:TOF=OK/ERR wird nur bei
tatsächlichem Zustandswechsel gesendet (kein Spam).

Der serverseitige Schwellenwert-Vergleich (distance_threshold_mm) und die
Flankenerkennung laufen NICHT auf dem Pico, sondern im PC-Server
(booth/tools/python_portable/pi_pico_core.py, _handle_distance_line()).

PRESENCE (Altprotokoll, standardmäßig AUS, ENABLE_PRESENCE_FALLBACK=False):
Falls aktiviert, würde der Pico selbst eine Presence-Hysterese
(PRESENCE_DISTANCE_MM / RELEASE_DISTANCE_MM mit Stabilitätszeiten) rechnen
und nur "PRESENCE" senden. Nur aktivieren, falls der PC-Server noch nicht
auf DISTANCE: umgestellt ist.


Münzprüfer: ST-001, HX-916 (UART) und HX-916 (Impulsausgang)
=============================================================

Zwei unabhängige Eingänge am Pico, je nachdem wie der Münzprüfer
angeschlossen wird:

  GP5 = UART1-RX  -> ST-001 (ASCII) oder HX-916 (UART-Binärframes)
  GP4 = Pulseingang -> HX-916 COIN-Pulsausgang (über Spannungsteiler)

Beide Pfade laufen unabhängig und gleichzeitig in main.py (process_coin_uart()
für GP5, process_pulse_geber() für GP4). Es sollte aber immer nur EIN Kabel
tatsächlich angeschlossen sein -- sonst würde dieselbe Münze doppelt als
COIN:<cent> gemeldet (einmal über UART, einmal über den Pulsausgang).


ST-001 (ASCII, zeilenbasiert, nur GP5/UART)
--------------------------------------------
Sendet pro Münze eine Textzeile:
  value:<cent>,frequency:<n>
Beispiel: "value:50,frequency:213" -> main.py meldet COIN:50
Der Cent-Wert kommt direkt vom Gerät, keine Umrechnung nötig.


HX-916 über UART (binär, 3 Rohbytes, kein Zeilenende, GP5)
-------------------------------------------------------------
Laut Hersteller-Handbuch: 0x48 0x58 <Punktwert>  ("H" "X" <wert>)
Beispiele: 48 58 01 = 1 Punkt, 48 58 05 = 5 Punkte, 48 58 0A = 10 Punkte.

main.py erkennt ST-001 und HX-916 automatisch am Byteformat
(process_coin_uart() in main.py). Kein main.py-Wechsel/Neuflash nötig, wenn
der Münzprüfer getauscht wird -- gleiche Verkabelung (UART1/GP5, 115200
Baud), nur das Gerät selbst wird ausgetauscht.

Der Punktwert ist KEIN Cent-Wert, sondern der am Automaten pro Kanal
eingestellte Pl-Parameter (1-50). main.py rechnet ihn über
HX916_POINT_VALUE_CENT = 10 in Cent um (1 Punkt = 10 Cent).


HX-916 über Pulsausgang (COIN-Pin am 4pin-Stecker, GP4)
-----------------------------------------------------------
Alternative zum UART-Anschluss DESSELBEN HX-916: der 4pin-Stecker am
Gerät (Red=DC12V, White=COIN, Gnd=GND, Gray=Disable-Eingang) liefert den
Pl-Punktwert als rohen Pulszug statt als "HX"-Byte-Frame -- 1 Puls = 1
Punkt. Nutzt denselben Centwert wie der UART-Pfad (HX916_POINT_VALUE_CENT).

Einstellungen am Gerät:
  2-step switch (NO/NC)        -> NO (Normally Open)
  3-step switch (Pulsbreite)   -> 100ms (slow)

Begründung NO: im Ruhezustand ist der Kontakt offen, GP4 liegt über den
Spannungsteiler-Widerstand nach GND auf LOW. Bei Kabelbruch bleibt der
Zustand identisch zu "Ruhe" (LOW) -- kein Fehl-Puls. Bei NC wäre ein
Kabelbruch nicht von einem Dauerkontakt unterscheidbar.

Begründung 100ms (slow): robuster gegen verpasste/verschluckte Flanken als
30/50ms. Die Software sampelt die Pulse über eine IRQ-Flanke, aber die
Hauptschleife hat ohnehin nur ~5ms Pause -- 100ms-Pulse sind sicherer zu
erfassen. Geschwindigkeit spielt beim Münzeinwurf keine Rolle.

Pulszug-Erkennung (process_pulse_geber() in main.py): main.py sammelt
Pulse, solange laufend neue welche reinkommen. Erst wenn seit dem LETZTEN
Puls PULSE_GEBER_BURST_WINDOW_MS (1500 ms) kein weiterer Puls mehr kam,
gilt der Pulszug als abgeschlossen und wird als EINE Münze mit
Wert = Pulsanzahl * HX916_POINT_VALUE_CENT gemeldet. Das Fenster ist
bewusst großzügig gewählt: bei Pl=50 und 100ms-Pulsbreite kann ein
einzelner Pulszug mehrere Sekunden dauern.


Pl-Werte am HX-916 (gilt für UART- und Pulsausgang gleichermaßen)
----------------------------------------------------------------
Beim Anlernen der Kanäle am HX-916 (ADD+MINUS gleichzeitig -> "A" -> SET ->
"E" fuer Kanalanzahl, danach A1..A5 anlernen) folgende Pl-Werte setzen:

  10 Cent  -> Pl = 1   (Frame 48 58 01 / 1 Puls)
  20 Cent  -> Pl = 2   (Frame 48 58 02 / 2 Pulse)
  50 Cent  -> Pl = 5   (Frame 48 58 05 / 5 Pulse)
  1 Euro   -> Pl = 10  (Frame 48 58 0A / 10 Pulse)
  2 Euro   -> Pl = 20  (Frame 48 58 14 / 20 Pulse)

Wichtig: Der Automat hat standardmäßig nur 3 Kanäle aktiv (Parameter "E").
Für alle 5 Münzsorten muss die Kanalzahl zuerst auf 5 erhöht werden, bevor
die einzelnen Kanäle (A1-A5) angelernt werden.

Falls später andere Pl-Werte verwendet werden sollen, muss
HX916_POINT_VALUE_CENT in main.py entsprechend angepasst werden (gilt für
beide Anschlussarten).


Wiring
======

Pinbelegung Pico
-----------------
  GP0  = I2C0 SDA   -> VL53L1X
  GP1  = I2C0 SCL   -> VL53L1X
  GP4  = Pulseingang vom HX-916 COIN-Pin (über Spannungsteiler, siehe unten)
  GP5  = UART1 RX   <- TX vom Münzprüfer (ST-001 oder HX-916)
  GP8  = UART1 TX   -> bleibt unbeschaltet (Pico sendet nichts an den Münzprüfer)

Hinweis: GP4 war früher UART1-TX (unbeschaltet). UART1-TX liegt jetzt auf
GP8, damit GP4 für den Impulseingang frei ist -- kein zusätzliches Kabel
zum Münzprüfer nötig, wenn dort bereits ein TX-Kabel auf GP4 lag.
WICHTIG: GP6 ist KEIN gültiger UART1-TX-Pin auf dem RP2040 (führt zu
"bad TX pin"-Absturz beim UART-Init) -- deshalb GP8, nicht GP6.

VL53L1X (I2C)
--------------
  VL53L1X VCC -> Pico 3V3
  VL53L1X GND -> Pico GND
  VL53L1X SDA -> GP0
  VL53L1X SCL -> GP1

Münzprüfer UART (ST-001 oder HX-916, TTL 3.3V, 115200 Baud)
-------------------------------------------------------------
  Münzprüfer TX  -> GP5 (Pico RX)
  Münzprüfer GND -> Pico GND
  (Pico TX/GP8 bleibt offen/unbeschaltet)

HX-916 Pulsausgang (COIN-Pin, 5V) -- Spannungsteiler auf GP4
----------------------------------------------------------------
Der COIN-Ausgang des HX-916 liefert 5V-Pegel. Der RP2040 ist NICHT
5V-tolerant (max. 3.3V an GPIOs) -- deshalb Spannungsteiler zwingend:

  HX-916 COIN (5V) ---[ R1 = 10 kOhm ]---+--- GP4
                                          |
                                     [ R2 = 20 kOhm ]
                                          |
  HX-916 GND ------------------------- Pico GND

  Vout am GP4 = 5V * R2/(R1+R2) = 5V * 20k/30k = 3.33V

  - Kein Kondensator/Tiefpass verwenden, nur die zwei Widerstände.
  - Alternative falls 20k nicht vorrätig: R2 = 22k -> Vout = 3.44V
    (noch im sicheren Bereich, aber knapper).
  - Interner Pull-Down auf GP4 wird von main.py gesetzt (Pin.PULL_DOWN) --
    bei Kabelbruch/offenem Stecker bleibt GP4 sicher auf LOW, kein
    Rausch-Fehlpuls.
  - Schaltkontakt am HX-916 auf NO (Normally Open) stellen (siehe oben).


Debugging
=========

coin_debug_monitor.py (PC-Seite, booth/tools/python_portable/) öffnet den
seriellen Port direkt und protokolliert jede Zeile/jeden Frame vom Pico.
Start über start_coin_debug_monitor.bat (fragt COM-Port und ob Debug-Modus
aktiviert werden soll).

--debug-on schaltet zusätzlich main.py: DEBUG zur Laufzeit ein (Kommando
DEBUG:ON über USB-Serial), dann erscheinen zusätzlich:
  - "DBG:COIN raw: ..."      rohe UART-Bytes, noch bevor main.py sie als
                             ST-001-Zeile oder HX-916-Frame interpretiert
  - "DBG:Pulsgeber: +N ..."  neue Pulse auf GP4 (Pulszug-Fortschritt)
  - "DBG:DIST:<mm>"          jede einzelne ToF-Rohmessung
  - "ALIVE:<n>"              Heartbeat (1x/Sekunde), zeigt dass main.py
                             überhaupt noch dauerhaft läuft

WICHTIG: Das Debug-Fenster (start_coin_debug_monitor.bat) unbedingt mit
Strg+C beenden, NICHT wegklicken! Nur Strg+C sendet DEBUG:OFF an den Pico
zurück. Sonst bleibt DEBUG dauerhaft an (zusätzlicher USB-Traffic im
Produktivbetrieb), bis es manuell per --debug-off zurückgesetzt wird oder
der Pico neu startet.

Siehe main.py (process_pc_command(), process_coin_uart(),
process_pulse_geber()) und coin_debug_monitor.py für Details.
