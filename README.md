Pico MicroPython Setup

Dateien auf den Raspberry Pi Pico kopieren:
1. main.py
2. lib/vl53l1x.py  (externer VL53L1X-MicroPython-Treiber)

main.py startet auf MicroPython automatisch beim Boot.

Wiring steht in WIRING.txt.

Hinweis:
Der VL53L1X-Treiber ist nicht Bestandteil dieses ZIPs.
Verwende z.B. den Pico-Treiber aus:
drakxtwo/vl53l1x_pico -> vl53l1x.py

Danach liegen auf dem Pico:
/
  main.py
  lib/
    vl53l1x.py

USB-Protokoll:
READY
PRESENCE
COIN:<cent>


Münzprüfer: ST-001 und HX-916
==============================

main.py erkennt beide Modelle automatisch am Byteformat (process_coin_uart()
in main.py). Kein main.py-Wechsel/Neuflash nötig, wenn der Münzprüfer
getauscht wird -- gleiche Verkabelung (UART1/GP5, 115200 Baud, siehe
WIRING.txt), nur das Gerät selbst wird ausgetauscht.

ST-001 (ASCII, zeilenbasiert)
------------------------------
Sendet pro Münze eine Textzeile:
  value:<cent>,frequency:<n>
Beispiel: "value:50,frequency:213" -> main.py meldet COIN:50
Der Cent-Wert kommt direkt vom Gerät, keine Umrechnung nötig.

HX-916 (binär, 3 Rohbytes, kein Zeilenende)
--------------------------------------------
Laut Hersteller-Handbuch: 0x48 0x58 <Punktwert>  ("H" "X" <wert>)
Beispiele: 48 58 01 = 1 Punkt, 48 58 05 = 5 Punkte, 48 58 0A = 10 Punkte.

Der Punktwert ist KEIN Cent-Wert, sondern der am Automaten pro Kanal
eingestellte Pl-Parameter (1-50). main.py rechnet ihn über
HX916_POINT_VALUE_CENT = 10 in Cent um (1 Punkt = 10 Cent).

Beim Anlernen der Kanäle am HX-916 (ADD+MINUS gleichzeitig -> "A" -> SET ->
"E" fuer Kanalanzahl, danach A1..A5 anlernen) folgende Pl-Werte setzen:

  10 Cent  -> Pl = 1   (Frame 48 58 01)
  20 Cent  -> Pl = 2   (Frame 48 58 02)
  50 Cent  -> Pl = 5   (Frame 48 58 05)
  1 Euro   -> Pl = 10  (Frame 48 58 0A)
  2 Euro   -> Pl = 20  (Frame 48 58 14)

Wichtig: Der Automat hat standardmäßig nur 3 Kanäle aktiv (Parameter "E").
Für alle 5 Münzsorten muss die Kanalzahl zuerst auf 5 erhöht werden, bevor
die einzelnen Kanäle (A1-A5) angelernt werden.

Falls später andere Pl-Werte verwendet werden sollen, muss
HX916_POINT_VALUE_CENT in main.py entsprechend angepasst werden.

Debugging (beide Modelle)
--------------------------
coin_debug_monitor.py (PC-Seite, booth/tools/python_portable/) öffnet den
seriellen Port direkt und protokolliert jede Zeile/jeden Frame vom Pico.
--debug-on schaltet zusätzlich main.py: DEBUG zur Laufzeit ein (Kommando
DEBUG:ON), dann erscheinen rohe UART-Bytes als "DBG:COIN raw: ..." noch
bevor main.py sie als ST-001-Zeile oder HX-916-Frame interpretiert.
Siehe main.py (process_pc_command(), process_coin_uart()) und
coin_debug_monitor.py für Details.
