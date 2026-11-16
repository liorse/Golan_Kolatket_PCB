"""
Main board <-> front panel link: two 8-pin 3.81 mm pluggable terminals.
Both boards import these maps, so the cable is always pin-for-pin (1-1, 2-2, ...).
GND is interleaved next to SCK and spread across both plugs.
"""
LINK_A = {"1": "+3V3", "2": "GND", "3": "SPI_SCK", "4": "GND",
          "5": "SPI_MOSI", "6": "TFT_CS", "7": "TFT_DC", "8": "TFT_RST"}
LINK_B = {"1": "TFT_BL", "2": "GND", "3": "BTN_A", "4": "BTN_B",
          "5": "BTN_X", "6": "BTN_Y", "7": "GND", "8": "+3V3"}
