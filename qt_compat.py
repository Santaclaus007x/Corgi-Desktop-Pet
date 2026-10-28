"""Use PySide6 if it's installed, otherwise PyQt6.

Force one with the environment variable CORGI_QT=PySide6 or CORGI_QT=PyQt6
(or `python main.py --qt PyQt6`). All code uses fully-scoped enums
(e.g. Qt.WindowType.FramelessWindowHint) so it works with both bindings.
"""
import os

_choice = os.environ.get("CORGI_QT", "").lower()
_order = ["PyQt6", "PySide6"] if _choice == "pyqt6" else ["PySide6", "PyQt6"]

QtCore = QtGui = QtWidgets = None
QT_API = None
for _name in _order:
    try:
        if _name == "PySide6":
            from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
        else:
            from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: F401
        QT_API = _name
        break
    except ImportError:
        continue

if QT_API is None:
    raise SystemExit(
        "Neither PySide6 nor PyQt6 is installed.\n"
        "Install one with:  pip install PySide6"
    )

Qt = QtCore.Qt
