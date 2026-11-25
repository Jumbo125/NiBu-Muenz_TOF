# main.py
# Raspberry Pi Pico / MicroPython
#
# Aufgabe:
#   - VL53L1X über I2C lesen
#   - stabile Präsenz -> "PRESENCE" über USB-Serial senden
#   - Münzprüfer über UART lesen (ST-001 ASCII ODER HX-916 binär,
#     automatisch erkannt -- siehe Abschnitt MUENZPRUEFER weiter unten)
#   - ODER denselben HX-916 über seinen COIN-Pulsausgang an GP4 lesen
#     (siehe PULSE_GEBER_* weiter unten) -- UART und Pulsausgang laufen
#     unabhaengig, auch gleichzeitig (z.B. falls beide Kabel haengen)
#   - "COIN:<cent>" über USB-Serial senden
#
# WICHTIG:
#   Der Pico führt KEIN Guthaben. Jede Münze wird einzeln an den PC gemeldet.

FIRMWARE_VERSION = "1.2.1"
FIRMWARE_DATE = "2026-11-25"

from machine import Pin, I2C, UART
import time
import sys

try:
    import select
except ImportError:
    import uselect as select

try:
    from vl53l1x import VL53L1X
except ImportError:
    VL53L1X = None


# ---------------------------------------------------------------------------
# KONFIGURATION
# ---------------------------------------------------------------------------

# VL53L1X / I2C0
TOF_I2C_ID = 0
TOF_SDA_PIN = 0          # GP0
TOF_SCL_PIN = 1          # GP1
TOF_I2C_FREQ = 400_000

# ToF: kontinuierliches Free-Running-Messen, Median-Filter, gedrosseltes Senden
TOF_SAMPLE_MS = 50                  # Rohmessintervall (~20 Messungen/s)
TOF_MEDIAN_WINDOW = 5                # Anzahl Rohwerte für den Median-Filter
DISTANCE_SEND_INTERVAL_MS = 250      # Sendedrosselung für DISTANCE:<mm>
TOF_MIN_VALID_MM = 40
TOF_MAX_VALID_MM = 5000
TOF_RETRY_MS = 5000
TOF_ERRORS_BEFORE_REINIT = 5

# Altprotokoll PRESENCE (Fallback, standardmäßig aus): der Schwellwert lebt
# jetzt serverseitig (external_pc.distance_threshold_mm). Nur aktivieren,
# falls der PC-Server noch nicht auf DISTANCE: umgestellt ist.
ENABLE_PRESENCE_FALLBACK = False
PRESENCE_DISTANCE_MM = 3000       # Person gilt bis 3,0 m als "da"
RELEASE_DISTANCE_MM = 3400        # Hysterese: erst ab 3,4 m wieder freigeben
PRESENCE_STABLE_MS = 500          # muss 0,5 s stabil erkannt werden
RELEASE_STABLE_MS = 1000          # muss 1,0 s wieder frei sein

# Münzprüfer / UART1 (ST-001 ODER HX-916 -- gleiche Verkabelung/Baudrate,
# welches Modell angeschlossen ist, erkennt die Firmware automatisch am
# Byteformat, siehe Abschnitt MUENZPRUEFER weiter unten)
# GP4 ist dadurch frei fuer den Impulsgeber (siehe PULSE_GEBER_PIN unten).
# WICHTIG: UART1-TX ist NUR auf bestimmten GPIOs gueltig (RP2040-Pinmux) --
# GP6 gehoert NICHT dazu ("bad TX pin"-Crash beim UART-Init). Gueltige
# UART1-TX-Kandidaten sind u.a. GP4 und GP8; da GP4 jetzt der Pulsgeber-
# Pin ist, liegt TX auf GP8 (ebenfalls unbeschaltet).
ST_UART_ID = 1
ST_UART_BAUD = 115200
ST_UART_TX_PIN = 8       # GP8; wird nicht angeschlossen
ST_UART_RX_PIN = 5       # GP5; TX des Münzprüfers kommt hierhin
ST_MAX_LINE_BYTES = 256

# HX-916 Multi-Coin-Selector (Alternative zum ST-001, laut Hersteller-
# Handbuch ebenfalls TTL/115200 an derselben UART1/GP5-Verkabelung).
# Protokoll: 3 Rohbytes pro Münze, OHNE Zeilenende:
#   0x48 0x58 <wert>   ("H" "X" <Punktwert>)
# Beispiele aus dem Handbuch: 48 58 01 = 1 Punkt, 48 58 05 = 5 Punkte,
# 48 58 0A = 10 Punkte. Der Punktwert (Pl-Parameter, Automat erlaubt 1-50)
# wird PRO KANAL am Automaten konfiguriert und ist kein Cent-Wert direkt --
# HX916_POINT_VALUE_CENT rechnet ihn in Cent um und MUSS zur tatsächlichen
# Pl-Konfiguration am Automaten passen (z.B. 10-Cent-Kanal auf Pl=1,
# 1-Euro-Kanal auf Pl=10 programmieren, wenn der Wert hier 10 ist).
HX916_MARKER = b"HX"
HX916_FRAME_LEN = 3
HX916_POINT_VALUE_CENT = 10
HX916_MIN_VALID_POINTS = 1
HX916_MAX_VALID_POINTS = 50

# Impulsgeber-Anschluss DESSELBEN HX-916 (laut Manual: 4pin-Buchse
# Red=DC12V/White=COIN/Gnd=GND/Gray=Disable-Eingang). Der COIN-Pin liefert
# denselben Pl-Punktwert wie der UART-Ausgang, nur als rohen Pulszug statt
# als "HX"-Byte-Frame -- 1 Puls = 1 Punkt, also gilt derselbe Centwert wie
# bei HX916_POINT_VALUE_CENT (siehe oben). GP4 war bisher UART1-TX, wurde
# aber nie beschaltet -- UART1-TX liegt jetzt auf GP6, damit GP4 fuer den
# COIN-Pulseingang frei ist.
#
# WIRING (HX-916 COIN-Pulsausgang, 2-step switch auf NO/Normally-Open):
#   HX-916 COIN (5V-Pulsausgang) --[R1 10k]-- GP4 --[R2 20k]-- GND
#   HX-916 GND                   -------------------------------- Pico GND
#
#   Ruhezustand (Kontakt offen):  GP4 liegt über R2 auf GND -> LOW
#   Impuls (Kontakt kurz zu):     Signal fliesst durch Teiler -> GP4 HIGH
#   Vout = 5V * R2/(R1+R2) = 5V * 20k/30k = 3.33V (sicher < 3.3V-Limit)
#
#   Kein Kondensator/Tiefpass -- nur die zwei Widerstände. Bei Kabelbruch
#   bleibt GP4 dank PULL_DOWN sicher auf LOW (kein Fehl-Puls).
#
# Pulsbreite am HX-916 (3-step switch) auf 100ms (slow) stellen -- robuster
# gegen verpasste/verschluckte Flanken als 30/50ms, Geschwindigkeit spielt
# beim Münzeinwurf keine nennenswerte Rolle.
#
# Protokoll: jeder Puls zaehlt als 1 Punkt (-> HX916_POINT_VALUE_CENT Cent).
# Ein Pulszug (z.B. 5 Pulse bei Pl=5) wird ueber PULSE_GEBER_BURST_WINDOW_MS
# zu einem Muenzwert aufsummiert -- siehe process_pulse_geber() weiter unten.
# Burst-Fenster bewusst grosszuegig: bei Pl=50 und 100ms-Pulsbreite kann ein
# Pulszug mehrere Sekunden dauern (50 Pulse x ca. 100-150ms Pause/Puls).
PULSE_GEBER_PIN = 4                   # GP4; COIN-Pulseingang vom HX-916 (3.3V!)
PULSE_GEBER_DEBOUNCE_MS = 15          # Mindestabstand zwischen zwei gueltigen Flanken
PULSE_GEBER_BURST_WINDOW_MS = 1500    # Pause nach letztem Puls -> Pulszug abgeschlossen
PULSE_GEBER_MAX_PULSES_PER_BURST = HX916_MAX_VALID_POINTS  # Plausibilitaetsgrenze (siehe HX916)

# PC-Identifikation
# Der PC-Server (Windows/Linux) kann beim Durchsuchen aller seriellen
# Schnittstellen dieses Kommando senden, um den Pico eindeutig zu erkennen.
PC_IDENTIFY_COMMAND = "ID?"
PC_IDENTITY_RESPONSE = "ID:TOF_Muenzzaehler"

# DEBUG zur Laufzeit per USB-Serial umschaltbar (ohne Neu-Flash) -- z.B. fuer
# kurze Vor-Ort-Diagnosen des ST-001/Muenzeinwurfs. Siehe coin_debug_monitor.py
# --debug-on/--debug-off auf der PC-Seite.
PC_DEBUG_ON_COMMAND = "DEBUG:ON"
PC_DEBUG_OFF_COMMAND = "DEBUG:OFF"
PC_CMD_MAX_CHARS = 64

DEBUG = False


# ---------------------------------------------------------------------------
# AUSGABE ZUM PC ÜBER USB-SERIAL
# ---------------------------------------------------------------------------

def usb_send(message):
    # print() läuft beim Pico-MicroPython über die USB-REPL/USB-Serial.
    print(message)


def debug(message):
    if DEBUG:
        usb_send("DBG:" + str(message))


# ---------------------------------------------------------------------------
# STATUS-EVENTS (nicht an DEBUG gekoppelt)
# ---------------------------------------------------------------------------

_last_tof_status = None


def report_tof_status(ok):
    """
    Meldet Änderungen der ToF-Erreichbarkeit über USB-Serial.
    Wird unabhängig von DEBUG gesendet, nur bei tatsächlichem
    Zustandswechsel (kein Spam bei jedem Messzyklus).
    """
    global _last_tof_status
    status = "OK" if ok else "ERR"
    if status == _last_tof_status:
        return
    _last_tof_status = status
    usb_send("STATUS:TOF={}".format(status))


# ---------------------------------------------------------------------------
# PC-KOMMANDOS ÜBER USB-SERIAL (z.B. Schnittstellen-Identifikation)
# ---------------------------------------------------------------------------

def process_pc_command(poll_obj, buffer):
    """
    Liest nicht-blockierend eingehende USB-Serial-Zeichen vom PC.

    Unterstuetzte Kommandos:
      - PC_IDENTIFY_COMMAND ("ID?"): PC-Server erkennt beim Durchsuchen
        aller seriellen Schnittstellen, welche davon der Pico ist.
      - PC_DEBUG_ON_COMMAND / PC_DEBUG_OFF_COMMAND ("DEBUG:ON"/"DEBUG:OFF"):
        schaltet DEBUG zur Laufzeit um, ohne Neu-Flash -- fuer kurze
        Vor-Ort-Diagnosen (z.B. rohe ST-001-UART1-Bytes sehen).
    """
    global DEBUG

    while poll_obj.poll(0):
        ch = sys.stdin.read(1)
        if not ch:
            break

        if ch in ("\n", "\r"):
            line = buffer.strip()
            buffer = ""
            if line == PC_IDENTIFY_COMMAND:
                usb_send(PC_IDENTITY_RESPONSE)
            elif line == PC_DEBUG_ON_COMMAND:
                DEBUG = True
                usb_send("DEBUG:STATE=ON")
                usb_send("VERSION:{} ({})".format(FIRMWARE_VERSION, FIRMWARE_DATE))
            elif line == PC_DEBUG_OFF_COMMAND:
                DEBUG = False
                usb_send("DEBUG:STATE=OFF")
            elif line:
                debug("PC cmd ignored: {}".format(line))
        else:
            buffer += ch
            if len(buffer) > PC_CMD_MAX_CHARS:
                buffer = ""

    return buffer


# ---------------------------------------------------------------------------
# MUENZPRUEFER (ST-001 ASCII-Zeilen ODER HX-916 binäre Frames)
# ---------------------------------------------------------------------------
#
# Beide Modelle hängen an derselben UART1/GP5-Verkabelung (115200 TTL) --
# welches Modell gerade angeschlossen ist, wird NICHT konfiguriert, sondern
# pro empfangenem Datenstück automatisch am Byteformat erkannt:
#   - ST-001:  ASCII-Zeile "value:<cent>,frequency:<n>\n"
#   - HX-916:  binärer 3-Byte-Frame 0x48 0x58 <punktwert>, kein Zeilenende
#
# Wichtig: Die beiden Formate dürfen NICHT über ein globales CR->LF-Replace
# auf dem Gesamtpuffer vereinheitlicht werden (wie frueher für ST-001
# allein) -- ein HX-916-Punktwert-Byte kann zufällig 0x0D (CR) sein und
# würde sonst verfälscht. Deshalb werden Zeilenenden nur innerhalb des
# ASCII-Zweigs erkannt (_take_line_bytes), nachdem ein HX-916-Frame am
# Pufferanfang ausgeschlossen wurde.


def parse_st001_line(raw_line):
    """
    Erwartet z.B.:
        value:50,frequency:213

    Gibt den Cent-Wert als int zurück oder None.
    """
    try:
        line = raw_line.decode("ascii", "ignore").strip()
    except Exception:
        return None

    marker = "value:"
    pos = line.find(marker)
    if pos < 0:
        return None

    value_text = line[pos + len(marker):]
    if "," in value_text:
        value_text = value_text.split(",", 1)[0]

    value_text = value_text.strip()

    try:
        value = int(value_text)
    except (ValueError, TypeError):
        return None

    # Nur grobe Plausibilitätsprüfung.
    # Welche Münzwerte erlaubt sind, entscheidet später die PC-Seite.
    if value <= 0 or value > 10000:
        return None

    return value


def _emit_hx916_coin(points):
    """
    Rechnet einen HX-916-Punktwert (1-50, siehe HX916_MAX_VALID_POINTS) über
    HX916_POINT_VALUE_CENT in Cent um und meldet COIN:<cent>.
    """
    if points < HX916_MIN_VALID_POINTS or points > HX916_MAX_VALID_POINTS:
        debug("HX916 Punktwert außerhalb Plausibilitätsbereich: {}".format(points))
        return

    value = points * HX916_POINT_VALUE_CENT
    # Ganz wichtig: NICHT aufsummieren.
    usb_send("COIN:{}".format(value))


# ---------------------------------------------------------------------------
# IMPULSGEBER-MUENZZAEHLER (GP4, siehe WIRING-Kommentar oben)
# ---------------------------------------------------------------------------
#
# IRQ zaehlt entprellte Flanken in eine globale Variable (IRQ-Handler muss
# auf dem Pico minimal bleiben -- keine Objekte/Allokationen, siehe
# MicroPython-Doku zu IRQ-Handlern). Die Hauptschleife sammelt Pulse
# innerhalb von PULSE_GEBER_BURST_WINDOW_MS und meldet sie als eine Muenze.

_pulse_count = 0
_pulse_last_tick = 0


def _pulse_geber_irq(pin):
    global _pulse_count, _pulse_last_tick
    now = time.ticks_ms()
    if time.ticks_diff(now, _pulse_last_tick) < PULSE_GEBER_DEBOUNCE_MS:
        return  # Entprellung: zu kurz nach letzter Flanke, verwerfen
    _pulse_last_tick = now
    _pulse_count += 1


def process_pulse_geber(burst_pulses, burst_since, now):
    """
    Sammelt entprellte Pulse (aus _pulse_count) zu einem Pulszug. burst_since
    wird bei JEDEM neuen Puls auf "jetzt" zurückgesetzt -- der Pulszug gilt
    erst als abgeschlossen, wenn seit dem LETZTEN Puls PULSE_GEBER_BURST_WINDOW_MS
    ohne weiteren Puls vergangen sind (Pause-Erkennung, nicht Gesamtdauer).
    Danach wird EINE Muenze mit Wert = Pulsanzahl * HX916_POINT_VALUE_CENT gemeldet.

    Gibt (burst_pulses, burst_since) fuer den naechsten Aufruf zurueck.
    """
    global _pulse_count

    if _pulse_count > burst_pulses:
        new_pulses = _pulse_count - burst_pulses
        burst_pulses = _pulse_count
        burst_since = now
        debug("Pulsgeber: +{} Puls(e), insgesamt {}".format(new_pulses, burst_pulses))

    if burst_pulses and burst_since is not None:
        if time.ticks_diff(now, burst_since) >= PULSE_GEBER_BURST_WINDOW_MS:
            _emit_pulse_geber_coin(burst_pulses)
            burst_pulses = 0
            burst_since = None
            _pulse_count = 0

    return burst_pulses, burst_since


def _emit_pulse_geber_coin(pulses):
    """
    Rechnet eine gesammelte Pulsanzahl (= Pl-Punktwert desselben HX-916, nur
    über den COIN-Pulsausgang statt UART) über HX916_POINT_VALUE_CENT in
    Cent um und meldet COIN:<cent>.
    """
    if pulses < 1 or pulses > PULSE_GEBER_MAX_PULSES_PER_BURST:
        debug("Pulsgeber Pulsanzahl außerhalb Plausibilitätsbereich: {}".format(pulses))
        return

    value = pulses * HX916_POINT_VALUE_CENT
    # Ganz wichtig: NICHT aufsummieren.
    usb_send("COIN:{}".format(value))


def _take_line_bytes(buffer):
    """
    Sucht das nächste Zeilenende (CR, LF oder CRLF) im Puffer.
    Gibt (raw_line_ohne_terminator, rest) zurück, oder (None, buffer),
    falls noch keine vollständige Zeile vorliegt.
    """
    nl_pos = buffer.find(b"\n")
    cr_pos = buffer.find(b"\r")

    if nl_pos < 0 and cr_pos < 0:
        return None, buffer

    if cr_pos < 0 or (0 <= nl_pos < cr_pos):
        end = nl_pos
        next_start = nl_pos + 1
    else:
        end = cr_pos
        next_start = cr_pos + 1
        if buffer[next_start:next_start + 1] == b"\n":
            next_start += 1  # CRLF

    return buffer[:end], buffer[next_start:]


def process_coin_uart(uart, buffer):
    """
    Liest alle aktuell verfügbaren UART-Daten vom Münzprüfer.
    Erkennt pro Durchlauf automatisch, ob es sich um einen ST-001-ASCII-
    Zeilenrest oder einen HX-916-Binärframe (0x48 0x58 <wert>) handelt.
    """
    if not uart.any():
        return buffer

    chunk = uart.read()
    if not chunk:
        return buffer

    debug("COIN raw: {}".format(chunk))

    buffer += chunk

    # Schutz gegen kaputte/nie abgeschlossene Zeilen bzw. Frames.
    if len(buffer) > ST_MAX_LINE_BYTES * 4:
        debug("COIN buffer overflow, verworfen: {}".format(buffer))
        buffer = b""

    while buffer:
        if buffer[0:1] == b"H":
            if len(buffer) < HX916_FRAME_LEN:
                break  # Frame evtl. noch nicht vollständig eingetroffen
            if buffer[0:2] != HX916_MARKER:
                # "H" war Zufall (kein HX-916-Frame) -- 1 Byte verwerfen und weitersuchen
                buffer = buffer[1:]
                continue
            points = buffer[2]
            buffer = buffer[HX916_FRAME_LEN:]
            _emit_hx916_coin(points)
            continue

        raw_line, rest = _take_line_bytes(buffer)
        if raw_line is None:
            break  # weder HX-916-Start noch vollständige Zeile -> mehr Daten abwarten
        buffer = rest

        if not raw_line:
            continue

        if len(raw_line) > ST_MAX_LINE_BYTES:
            debug("ST001 line too long")
            continue

        value = parse_st001_line(raw_line)
        if value is not None:
            # Ganz wichtig: NICHT aufsummieren.
            usb_send("COIN:{}".format(value))
        else:
            debug("ST001 ignored: {}".format(raw_line))

    return buffer


# ---------------------------------------------------------------------------
# VL53L1X
# ---------------------------------------------------------------------------

def create_tof(i2c):
    if VL53L1X is None:
        raise RuntimeError("vl53l1x.py fehlt auf dem Pico")

    devices = i2c.scan()
    if 0x29 not in devices:
        raise RuntimeError(
            "VL53L1X nicht gefunden; I2C scan={}".format(
                [hex(x) for x in devices]
            )
        )

    sensor = VL53L1X(i2c)

    # Ein paar Messungen nach Initialisierung verwerfen.
    for _ in range(3):
        try:
            sensor.read()
        except Exception:
            pass
        time.sleep_ms(50)

    return sensor


def valid_distance(distance_mm):
    return (
        isinstance(distance_mm, int)
        and TOF_MIN_VALID_MM <= distance_mm <= TOF_MAX_VALID_MM
    )


def median_of(values):
    """
    Median einer kleinen Liste von Ganzzahlen (Rundung nach unten bei
    gerader Anzahl). Robuster gegen einzelne Ausreißer als ein Mittelwert.
    """
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) // 2


# ---------------------------------------------------------------------------
# HAUPTPROGRAMM
# ---------------------------------------------------------------------------

def main():
    i2c = I2C(
        TOF_I2C_ID,
        sda=Pin(TOF_SDA_PIN),
        scl=Pin(TOF_SCL_PIN),
        freq=TOF_I2C_FREQ,
    )

    # UART1: GP5 = RX vom Münzprüfer (ST-001 oder HX-916, siehe
    # process_coin_uart()). GP8/TX ist initialisiert, bleibt aber unbeschaltet.
    st_uart = UART(
        ST_UART_ID,
        baudrate=ST_UART_BAUD,
        bits=8,
        parity=None,
        stop=1,
        tx=Pin(ST_UART_TX_PIN),
        rx=Pin(ST_UART_RX_PIN),
        timeout=0,
        timeout_char=0,
    )

    coin_buffer = b""

    # GP4 = Pulseingang vom Impulsgeber (siehe WIRING-Kommentar bei den
    # Konstanten). PULL_DOWN sorgt dafuer, dass der Pin bei offenem/nicht
    # angeschlossenem Kabel sicher auf LOW bleibt (kein Rausch-Fehlpuls).
    #
    # Solange GP4 unbeschaltet/offen ist (Impulsgeber noch nicht verkabelt),
    # kann der Pin trotz PULL_DOWN sporadisch floaten/stoeren -- ein IRQ,
    # der dadurch sehr haeufig feuert, darf main.py NICHT zum Absturz
    # bringen (siehe READY/VERSION weiter unten, die sonst nie gesendet
    # werden). Deshalb hier separat abgesichert.
    try:
        pulse_pin = Pin(PULSE_GEBER_PIN, Pin.IN, Pin.PULL_DOWN)
        pulse_pin.irq(trigger=Pin.IRQ_RISING, handler=_pulse_geber_irq)
    except Exception as exc:
        usb_send("STATUS:PULSE_GEBER=ERR:{}".format(exc))
    pulse_burst_pulses = 0
    pulse_burst_since = None

    pc_poll = select.poll()
    pc_poll.register(sys.stdin, select.POLLIN)
    pc_buffer = ""

    tof = None
    tof_error_count = 0
    last_tof_init_try = time.ticks_add(time.ticks_ms(), -TOF_RETRY_MS)
    last_tof_sample = time.ticks_add(time.ticks_ms(), -TOF_SAMPLE_MS)

    # Rollierendes Fenster für den Median-Filter + Sendedrosselung
    tof_window = []
    last_distance_send = time.ticks_add(
        time.ticks_ms(), -DISTANCE_SEND_INTERVAL_MS
    )

    # Zustandsautomat für Presence-Debounce/Hysterese (nur ENABLE_PRESENCE_FALLBACK)
    presence_latched = False
    presence_since = None
    release_since = None

    # Heartbeat: unabhaengig von ToF/UART, zeigt ob main.py ueberhaupt
    # dauerhaft laeuft (Diagnose). Sendet NUR bei DEBUG=True, also nur
    # waehrend coin_debug_monitor.py --debug-on aktiv ist -- im normalen
    # Produktivbetrieb (DEBUG=False) kein zusaetzlicher Traffic.
    alive_counter = 0
    last_alive_send = time.ticks_add(time.ticks_ms(), -1000)

    usb_send("READY")
    usb_send("VERSION:{} ({})".format(FIRMWARE_VERSION, FIRMWARE_DATE))

    while True:
        # ---------------------------------------------------------------
        # 0) Heartbeat (nur im DEBUG-Modus, siehe oben)
        # ---------------------------------------------------------------
        if DEBUG:
            now_hb = time.ticks_ms()
            if time.ticks_diff(now_hb, last_alive_send) >= 1000:
                last_alive_send = now_hb
                alive_counter += 1
                usb_send("ALIVE:{}".format(alive_counter))

        # ---------------------------------------------------------------
        # 1) Münzprüfer möglichst oft leeren, damit keine UART-Daten verloren gehen
        # ---------------------------------------------------------------
        try:
            coin_buffer = process_coin_uart(st_uart, coin_buffer)
        except Exception as exc:
            debug("UART error: {}".format(exc))
            coin_buffer = b""

        # ---------------------------------------------------------------
        # 1a) Impulsgeber (GP4) auswerten -- unabhaengig von UART-Muenzpruefer
        # ---------------------------------------------------------------
        try:
            pulse_burst_pulses, pulse_burst_since = process_pulse_geber(
                pulse_burst_pulses, pulse_burst_since, time.ticks_ms()
            )
        except Exception as exc:
            debug("Pulsgeber error: {}".format(exc))
            pulse_burst_pulses = 0
            pulse_burst_since = None

        # ---------------------------------------------------------------
        # 1b) PC-Kommandos (z.B. Schnittstellen-Identifikation) verarbeiten
        # ---------------------------------------------------------------
        try:
            pc_buffer = process_pc_command(pc_poll, pc_buffer)
        except Exception as exc:
            debug("PC cmd error: {}".format(exc))
            pc_buffer = ""

        now = time.ticks_ms()

        # ---------------------------------------------------------------
        # 2) VL53L1X bei Bedarf neu verbinden
        # ---------------------------------------------------------------
        # Im DEBUG-Modus (coin_debug_monitor.py --debug-on) wird der ToF-
        # Zyklus komplett ausgesetzt: I2C-Transfers blockieren die
        # Hauptschleife lange genug, dass der UART-Muenzpruefer-Puffer
        # zwischendurch nicht oft genug geleert wird und raw-Frames
        # abgeschnitten im Log landen. Produktivbetrieb (DEBUG=False)
        # bleibt davon unberuehrt.
        if DEBUG:
            pass
        elif tof is None:
            if time.ticks_diff(now, last_tof_init_try) >= TOF_RETRY_MS:
                last_tof_init_try = now
                try:
                    tof = create_tof(i2c)
                    tof_error_count = 0
                    tof_window = []
                    presence_latched = False
                    presence_since = None
                    release_since = None
                    debug("VL53L1X ready")
                    report_tof_status(True)
                except Exception as exc:
                    tof = None
                    debug("VL53L1X init error: {}".format(exc))
                    report_tof_status(False)

        # ---------------------------------------------------------------
        # 3) ToF zyklisch messen (Free-Running, Median-Filter, gedrosseltes Senden)
        # ---------------------------------------------------------------
        elif time.ticks_diff(now, last_tof_sample) >= TOF_SAMPLE_MS:
            last_tof_sample = now

            try:
                distance_mm = tof.read()
                tof_error_count = 0

                if DEBUG:
                    debug("DIST:{}".format(distance_mm))

                is_valid = valid_distance(distance_mm)

                if is_valid:
                    tof_window.append(distance_mm)
                    if len(tof_window) > TOF_MEDIAN_WINDOW:
                        tof_window.pop(0)
                else:
                    debug("DIST invalid: {}".format(distance_mm))

                if tof_window and (
                    time.ticks_diff(now, last_distance_send)
                    >= DISTANCE_SEND_INTERVAL_MS
                ):
                    last_distance_send = now
                    usb_send("DISTANCE:{}".format(median_of(tof_window)))

                if ENABLE_PRESENCE_FALLBACK:
                    if not presence_latched:
                        # Noch nicht ausgelöst:
                        # Abstand muss für PRESENCE_STABLE_MS <= Schwellwert bleiben.
                        if is_valid and distance_mm <= PRESENCE_DISTANCE_MM:
                            if presence_since is None:
                                presence_since = now
                            elif (
                                time.ticks_diff(now, presence_since)
                                >= PRESENCE_STABLE_MS
                            ):
                                usb_send("PRESENCE")
                                presence_latched = True
                                presence_since = None
                                release_since = None
                        else:
                            presence_since = None

                    else:
                        # Bereits ausgelöst:
                        # Erst wieder "scharf", wenn die Person stabil weit genug weg ist.
                        far_enough = (
                            (not is_valid)
                            or distance_mm >= RELEASE_DISTANCE_MM
                        )

                        if far_enough:
                            if release_since is None:
                                release_since = now
                            elif (
                                time.ticks_diff(now, release_since)
                                >= RELEASE_STABLE_MS
                            ):
                                presence_latched = False
                                release_since = None
                                presence_since = None
                                debug("Presence re-armed")
                        else:
                            release_since = None

            except Exception as exc:
                tof_error_count += 1
                debug("VL53L1X read error: {}".format(exc))

                if tof_error_count >= TOF_ERRORS_BEFORE_REINIT:
                    tof = None
                    tof_error_count = 0
                    tof_window = []
                    presence_latched = False
                    presence_since = None
                    release_since = None
                    report_tof_status(False)

        # Kurze Pause: UART bleibt trotzdem sehr reaktionsschnell.
        time.sleep_ms(5)


# Falls main() mit einer unerwarteten Exception abbricht (z.B. Pin-Init-
# Fehler), faellt der Pico sonst STILL in den REPL zurueck -- main.py
# "laeuft" dann nicht mehr, aber es kommt auch keine Fehlermeldung über
# USB-Serial, die ein PC-seitiges Monitoring-Tool anzeigen koennte (siehe
# coin_debug_monitor.py: keine Zeilen mehr, nur noch der REPL-Prompt
# ">>>"). Deshalb hier Exception abfangen und als FATAL:... ueber
# USB-Serial sichtbar machen, BEVOR der Pico im REPL haengen bleibt.
#
# WICHTIG: main.py startet sich bei einem Fehler NICHT automatisch neu
# (kein machine.reset() hier). Ein Fehler, der bei jedem main()-Aufruf
# sofort wieder auftritt (z.B. defektes Pin-Setup), wuerde sonst eine
# endlose Crash-Reset-Schleife erzeugen -- das haelt den USB-CDC-Port
# dauerhaft instabil (Windows meldet dann ClearCommError/PermissionError
# beim Oeffnen, auch nach Kabel-Neustecken, weil der Pico nie lange genug
# "oben" bleibt, um den Port sauber zu bedienen). Stattdessen bleibt
# main.py im REPL haengen, die FATAL-Zeile bleibt im Terminal-Scrollback
# sichtbar, und der Fehler kann in Ruhe behoben werden.
try:
    main()
except Exception as exc:
    try:
        usb_send("FATAL:{}".format(exc))
    except Exception:
        pass
    # main.py NICHT neu starten -- Pico bleibt im REPL fuer Diagnose.
