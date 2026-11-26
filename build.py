"""
Build a double-click app (no terminal window) for the OS you run this on.

    pip install pillow resvg-py pyinstaller
    python build.py

Windows -> dist/Icon Exporter.exe
macOS   -> dist/Icon Exporter.app
Linux   -> dist/Icon Exporter
(PyInstaller builds only for the OS it runs on. Use the GitHub workflow for all three.)
"""
import platform
import sys

import PyInstaller.__main__

NAME = "Icon Exporter"
args = [
    "icon_exporter.py",
    "--name", NAME,
    "--windowed",                 # no terminal window
    "--noconfirm", "--clean",
    "--exclude-module", "cairosvg",
    "--collect-all", "resvg_py",
]
if platform.system() != "Darwin":
    args.append("--onefile")      # single file; macOS uses a .app bundle instead
if len(sys.argv) > 1:
    args += ["--icon", sys.argv[1]]   # optional: python build.py my-icon.ico (.icns on macOS)

PyInstaller.__main__.run(args)
print(f"\nDone. Look in the 'dist' folder for '{NAME}'.")
