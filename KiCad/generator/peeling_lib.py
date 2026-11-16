"""
Project libraries shared by both boards (KiCad/lib):
  Peeling.kicad_sym  - symbols for the plug-in modules, with their real pin names
  Peeling.pretty     - ESP32 DevKit footprint = two standard 1x15 female sockets 25.4 mm apart

Called from main_board.py / front_panel.py; safe to re-run.
"""
import copy
import os

import kigen
from kigen import Q, parse_sexpr, dump_sexpr, find, find_all, uid

LIB_DIR = kigen.PROJECT_LIB_DIR
SYM_FILE = os.path.join(LIB_DIR, "Peeling.kicad_sym")
FP_DIR_OUT = os.path.join(LIB_DIR, "Peeling.pretty")

# --------------------------------------------------------------------------
# Symbols: (name, ref prefix, description, footprint, half_width,
#           left pins [(num, name)], right pins [(num, name)])
# Pins are listed top to bottom; all pins are passive (they are plug-in modules).
# --------------------------------------------------------------------------
ESP_LEFT = ["EN", "VP/IO36", "VN/IO39", "D34", "D35", "D32", "D33", "D25", "D26", "D27",
            "D14", "D12", "D13", "GND", "VIN"]
ESP_RIGHT = ["D23", "D22", "TX0/IO1", "RX0/IO3", "D21", "D19", "D18", "D5", "TX2/IO17",
             "RX2/IO16", "D4", "D2", "D15", "GND", "3V3"]

SYMBOLS = [
    ("ESP32_DevKit_V1_30pin", "U",
     "ESP32 DevKit V1 (DOIT, 30-pin) plugged into two 1x15 female sockets. "
     "Pins 1-15 = EN..VIN row, 16-30 = D23..3V3 row.",
     "Peeling:ESP32_DevKit_V1_30pin_Socket", 12.7,
     [(str(i + 1), n) for i, n in enumerate(ESP_LEFT)],
     [(str(i + 16), n) for i, n in enumerate(ESP_RIGHT)]),
    ("Adafruit_MAX31856_Breakout", "U",
     "Adafruit MAX31856 thermocouple breakout (#3263) on a 1x9 female socket; "
     "thermocouple wires go to the breakout's own terminal.",
     "Connector_PinSocket_2.54mm:PinSocket_1x09_P2.54mm_Vertical", 10.16,
     [(str(i + 1), n) for i, n in enumerate(["VIN", "3Vo", "GND", "SCK", "SDO", "SDI", "CS", "FLT", "DRDY"])],
     []),
    ("HW-517_MOSFET_Module", "U",
     "HW-517 dual D4184 MOSFET trigger module (low-side switch) plugged by its J1 row. "
     "24 V and the heater connect to the module's own screw terminals (VIN+/VIN-, OUT+/OUT-).",
     "Connector_PinSocket_2.54mm:PinSocket_1x04_P2.54mm_Vertical", 10.16,
     [("1", "GND"), ("2", "GND"), ("3", "TRIG/PWM"), ("4", "TRIG/PWM")],
     []),
    ("TFT_2.8in_SPI_MSP2807", "U",
     "2.8in 240x320 SPI TFT module (MSP2807 type), 14-pin header incl. touch",
     "Connector_PinSocket_2.54mm:PinSocket_1x14_P2.54mm_Vertical", 10.16,
     [(str(i + 1), n) for i, n in enumerate(["VCC", "GND", "CS", "RESET", "DC/RS", "SDI(MOSI)", "SCK",
                                              "LED", "SDO(MISO)", "T_CLK", "T_CS", "T_DIN", "T_DO", "T_IRQ"])],
     []),
]


def _pin(num, name, x, y, angle):
    fnt = ["effects", ["font", ["size", "1.27", "1.27"]]]
    return ["pin", "passive", "line", ["at", kigen._f(x), kigen._f(y), str(angle)], ["length", "2.54"],
            ["name", Q(name), copy.deepcopy(fnt)], ["number", Q(num), copy.deepcopy(fnt)]]


def _sym(name, ref, descr, fp, hw, left, right):
    n = max(len(left), len(right))
    top = 2.54 * ((n - 1) // 2)          # keep pins on the 2.54 grid, origin near the middle
    body_t, body_b = top + 2.54, top - (n - 1) * 2.54 - 2.54
    pins = []
    for i, (num, pn) in enumerate(left):
        pins.append(_pin(num, pn, -hw - 2.54, top - 2.54 * i, 0))
    for i, (num, pn) in enumerate(right):
        pins.append(_pin(num, pn, hw + 2.54, top - 2.54 * i, 180))
    right_x = hw if right else hw
    prop = lambda k, v, y, hide=False: kigen._prop(k, v, 0, y, hide=hide)
    return ["symbol", Q(name), ["pin_names", ["offset", "1.016"]], ["exclude_from_sim", "no"],
            ["in_bom", "yes"], ["on_board", "yes"],
            prop("Reference", ref, body_t + 1.27), prop("Value", name, body_b - 1.27),
            prop("Footprint", fp, 0, True), prop("Datasheet", "~", 0, True),
            prop("Description", descr, 0, True),
            ["symbol", Q(name + "_0_1"),
             ["rectangle", ["start", kigen._f(-hw), kigen._f(body_t)], ["end", kigen._f(right_x), kigen._f(body_b)],
              ["stroke", ["width", "0.254"], ["type", "default"]], ["fill", ["type", "background"]]]],
            ["symbol", Q(name + "_1_1")] + pins]


def write_symbols():
    lib = ["kicad_symbol_lib", ["version", "20241209"], ["generator", Q("kicad_symbol_editor")],
           ["generator_version", Q("9.0")]] + [_sym(*s) for s in SYMBOLS]
    with open(SYM_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(dump_sexpr(lib) + "\n")
    kigen._lib_cache.pop(SYM_FILE, None)


# --------------------------------------------------------------------------
# ESP32 DevKit footprint: two copies of the stock 1x15 socket, the second shifted
# +25.4 mm in X and renumbered 16-30. Pad 1 = EN, pad 16 = D23 (antenna end).
# --------------------------------------------------------------------------
def _shift(node, dx):
    if not isinstance(node, list):
        return
    if node and node[0] in ("start", "end", "at", "center", "mid", "xy") and len(node) >= 3:
        node[1] = kigen._f(float(node[1]) + dx)
    for x in node:
        _shift(x, dx)


def _new_uuids(node):
    if isinstance(node, list):
        if node and node[0] == "uuid":
            node[1] = Q(uid())
        for x in node:
            _new_uuids(x)


def write_esp_footprint():
    src = os.path.join(kigen.FP_DIR, "Connector_PinSocket_2.54mm.pretty", "PinSocket_1x15_P2.54mm_Vertical.kicad_mod")
    with open(src, encoding="utf-8") as f:
        fp = parse_sexpr(f.read())[0]
    name = "ESP32_DevKit_V1_30pin_Socket"
    fp[1] = Q(name)
    for x in fp:
        if isinstance(x, list) and x[0] == "descr":
            x[1] = Q("ESP32 DevKit V1 30-pin: two 1x15 2.54 mm female sockets, rows 25.4 mm apart")
        if isinstance(x, list) and x[0] == "tags":
            x[1] = Q("ESP32 DevKit socket")
        if isinstance(x, list) and x[0] == "property" and x[1] == "Value":
            x[2] = Q(name)
    extra = []
    for x in fp:
        if isinstance(x, list) and x[0] in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "pad", "model"):
            c = copy.deepcopy(x)
            if c[0] == "model":
                off = find(c, "offset")
                xyz = find(off, "xyz")
                xyz[1] = kigen._f(float(xyz[1]) + 25.4)
            else:
                _shift(c, 25.4)
            if c[0] == "pad":
                c[1] = Q(str(int(c[1]) + 15))
            _new_uuids(c)
            extra.append(c)
    fp.extend(extra)
    os.makedirs(FP_DIR_OUT, exist_ok=True)
    with open(os.path.join(FP_DIR_OUT, name + ".kicad_mod"), "w", encoding="utf-8", newline="\n") as f:
        f.write(dump_sexpr(fp) + "\n")


def ensure():
    write_symbols()
    write_esp_footprint()
    kigen.LIB_PATHS["Peeling"] = SYM_FILE


if __name__ == "__main__":
    ensure()
    print("written", SYM_FILE, FP_DIR_OUT)
