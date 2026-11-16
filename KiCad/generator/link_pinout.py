"""
Board-to-board link pinout, shared by main_board.py and front_panel.py so both ends
always agree. Two 6-pin Phoenix MSTB 2,5/6-G-5,08 (5.08 mm) pluggable terminals;
wire pin 1 to pin 1, ... pin 6 to pin 6.

12 signals: power, the display's SPI/control lines and the four keys.
"""
LINK_A = {"1": "+3V3", "2": "GND", "3": "SPI_SCK", "4": "SPI_MOSI", "5": "TFT_CS", "6": "TFT_DC"}
LINK_B = {"1": "TFT_RST", "2": "TFT_BL", "3": "BTN_A", "4": "BTN_B", "5": "BTN_X", "6": "BTN_Y"}
