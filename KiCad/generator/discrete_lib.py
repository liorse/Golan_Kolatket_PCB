"""
Project library for the all-discrete main board (KiCad/Main_Board_Discrete/lib):
  Discrete.kicad_sym - single-unit symbols the stock library only has as multi-unit / other pinouts
  Discrete.pretty    - ESP32-WROOM-32E with 0.3 mm thermal-via drills (JLCPCB 2-layer minimum)

Safe to re-run; called from main_board_discrete.py.
"""
import os
import re

import kigen
import peeling_lib
from kigen import Q, dump_sexpr

KICAD_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def set_project(folder):
    """Point the library writer at a project folder (each board variant has its own copy)."""
    global PROJ_DIR, LIB_DIR, SYM_FILE, FP_DIR_OUT
    PROJ_DIR = os.path.join(KICAD_DIR, folder)
    LIB_DIR = os.path.join(PROJ_DIR, "lib")
    SYM_FILE = os.path.join(LIB_DIR, "Discrete.kicad_sym")
    FP_DIR_OUT = os.path.join(LIB_DIR, "Discrete.pretty")


set_project("Main_Board_Discrete")

SYMBOLS = [
    ("74AHCT125_1U", "U",
     "Quad bus buffer, 3-state, TTL inputs (74AHCT125), all four gates and power in one unit. "
     "At VCC = 5 V it accepts 3.3 V logic and drives 5 V outputs.",
     "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm", 7.62,
     [("2", "1A"), ("1", "~{1OE}"), ("5", "2A"), ("4", "~{2OE}"), ("9", "3A"), ("10", "~{3OE}"),
      ("12", "4A"), ("13", "~{4OE}"), ("14", "VCC"), ("7", "GND")],
     [("3", "1Y"), ("6", "2Y"), ("8", "3Y"), ("11", "4Y")]),
    ("CH340C_3V3", "U",
     "WCH CH340C USB-UART, internal oscillator, wired for 3.3 V operation (V3 tied to VCC)",
     "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm", 7.62,
     [("16", "VCC"), ("4", "V3"), ("5", "UD+"), ("6", "UD-"), ("1", "GND"), ("15", "R232"), ("7", "NC"),
      ("8", "TNOW")],
     [("2", "TXD"), ("3", "RXD"), ("13", "~{DTR}"), ("14", "~{RTS}"), ("9", "~{CTS}"), ("10", "~{DSR}"),
      ("11", "~{RI}"), ("12", "~{DCD}")]),
    ("LMR38010SDDA", "U",
     "TI LMR38010S 4.2-80 V, 1 A synchronous buck, HSOIC-8 PowerPAD (DDA), internal compensation",
     "Package_SO:TI_SO-PowerPAD-8_ThermalVias", 7.62,
     [("3", "VIN"), ("2", "EN"), ("4", "RT/SYNC"), ("1", "GND"), ("9", "EP")],
     [("7", "BOOT"), ("8", "SW"), ("5", "FB"), ("6", "PG")]),
    ("NMOS_TO252_GDS", "Q",
     "N-channel MOSFET in TO-252 (DPAK): pin 1 gate, pin 2 (tab) drain, pin 3 source",
     "Package_TO_SOT_SMD:TO-252-2", 5.08,
     [("1", "G")], [("2", "D"), ("3", "S")]),
]


def write_symbols():
    lib = ["kicad_symbol_lib", ["version", "20241209"], ["generator", Q("kicad_symbol_editor")],
           ["generator_version", Q("9.0")]] + [peeling_lib._sym(*s) for s in SYMBOLS]
    os.makedirs(LIB_DIR, exist_ok=True)
    with open(SYM_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(dump_sexpr(lib) + "\n")
    kigen._lib_cache.pop(SYM_FILE, None)


# stock footprints whose thermal vias are drilled 0.2 mm; JLCPCB's standard 2-layer minimum is
# 0.3 mm, so the copies get 0.3 mm drills in 0.6 mm pads
VIA_FIX = [("RF_Module", "ESP32-WROOM-32E"),
           ("Package_DFN_QFN", "VQFN-28-1EP_5x5mm_P0.5mm_EP3.7x3.7mm_ThermalVias")]


def write_footprints():
    os.makedirs(FP_DIR_OUT, exist_ok=True)
    for lib, name in VIA_FIX:
        s = open(os.path.join(kigen.FP_DIR, lib + ".pretty", name + ".kicad_mod"), encoding="utf-8").read()
        s = s.replace('(footprint "%s"' % name, '(footprint "%s_Via0.3"' % name, 1)
        s = re.sub(r"\(size [0-9.]+ [0-9.]+\)(\s*)\(drill 0\.2\)", r"(size 0.6 0.6)\1(drill 0.3)", s)
        with open(os.path.join(FP_DIR_OUT, name + "_Via0.3.kicad_mod"), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(s)


def write_tables():
    with open(os.path.join(PROJ_DIR, "sym-lib-table"), "w", encoding="utf-8", newline="\n") as f:
        f.write('(sym_lib_table\n\t(version 7)\n'
                '\t(lib (name "Discrete") (type "KiCad") (uri "${KIPRJMOD}/lib/Discrete.kicad_sym") (options "") '
                '(descr "Discrete main board symbols"))\n)\n')
    with open(os.path.join(PROJ_DIR, "fp-lib-table"), "w", encoding="utf-8", newline="\n") as f:
        f.write('(fp_lib_table\n\t(version 7)\n'
                '\t(lib (name "Discrete") (type "KiCad") (uri "${KIPRJMOD}/lib/Discrete.pretty") (options "") '
                '(descr "Discrete main board footprints"))\n)\n')


def ensure():
    write_symbols()
    write_footprints()
    write_tables()
    kigen.LIB_PATHS["Discrete"] = SYM_FILE
    kigen.FP_PATHS["Discrete"] = FP_DIR_OUT


if __name__ == "__main__":
    ensure()
    print("written", SYM_FILE, FP_DIR_OUT)
