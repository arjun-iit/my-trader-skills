#!/bin/bash
# India Trader: double-click in Finder to start (macOS). Needs Python 3.8+ from python.org.
cd "$(dirname "$0")"
exec python3 trader_app.py
