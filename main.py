"""Biscuit the desktop corgi.  Run:  python main.py

Options:
  --scale N      size (each sprite pixel = N screen pixels; default 3)
  --qt NAME      force PySide6 or PyQt6
  --reset        forget saved mood/stats
  --debug        print state changes to the terminal
"""
import argparse
import os
import signal
import sys


def main():
    ap = argparse.ArgumentParser(description="Desktop pet corgi")
    ap.add_argument("--scale", type=int)
    ap.add_argument("--qt", choices=["PySide6", "PyQt6"])
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.qt:
        os.environ["CORGI_QT"] = args.qt

    from qt_compat import QT_API, QtCore, QtWidgets
    import features
    from pet import CorgiPet

    cfg = features.load_config()
    if args.scale:
        cfg["scale"] = max(1, args.scale)
    saved = {} if args.reset else features.load_state()

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Corgi Pet")
    app.setQuitOnLastWindowClosed(False)

    pet = CorgiPet(cfg, saved, debug=args.debug)
    pet.show()
    app.aboutToQuit.connect(pet.save)

    # Let Ctrl+C in the terminal quit cleanly.
    signal.signal(signal.SIGINT, lambda *_: pet.quit())
    keepalive = QtCore.QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(250)

    if args.debug:
        print(f"[corgi] using {QT_API}; config at {features.CONFIG_PATH}")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
