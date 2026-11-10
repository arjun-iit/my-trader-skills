#!/usr/bin/env python3
"""Build India Trader as ONE program file with PyInstaller (no Python needed on the computer
that runs it). Run this on the system you want the program for: Windows builds a .exe, macOS
an app, Linux a binary. PyInstaller cannot cross-build.

    python -m pip install pyinstaller
    python india-app/build_exe.py

The result is in india-app/dist/. The packaged app keeps its data in ~/IndiaTrader
(trader-in/ inside it), or in the folder named by INDIA_TRADER_DATA.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
SKILLS = ["india-scanner", "india-news", "india-entry", "india-exit", "india-timemachine", "india-alerts",
          "india-analyst", "india-insider", "groww-budget", "groww-risk", "groww-broker"]
SEP = ";" if os.name == "nt" else ":"


def main():
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", "IndiaTrader",
            "--distpath", os.path.join(HERE, "dist"), "--workpath", os.path.join(HERE, "build"),
            "--specpath", os.path.join(HERE, "build")]
    for s in SKILLS:
        for f in os.listdir(os.path.join(ROOT, s)):
            if f.endswith(".py"):
                args += ["--add-data", "%s%s%s" % (os.path.join(ROOT, s, f), SEP, s)]
    # the skill scripts' own imports, so PyInstaller bundles every standard-library module they use
    for m in ["csv", "decimal", "email.utils", "gzip", "hashlib", "hmac", "http.cookiejar", "random", "statistics",
              "struct", "urllib.request", "urllib.parse", "xml.etree.ElementTree", "base64", "math", "runpy"]:
        args += ["--hidden-import", m]
    args.append(os.path.join(HERE, "trader_app.py"))
    print(" ".join(args))
    return subprocess.call(args)


if __name__ == "__main__":
    sys.exit(main())
