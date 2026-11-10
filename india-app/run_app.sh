#!/bin/sh
# India Trader on Linux. Needs Python 3.8+ with Tkinter (Debian/Ubuntu: sudo apt install python3-tk).
cd "$(dirname "$0")" && exec python3 trader_app.py "$@"
