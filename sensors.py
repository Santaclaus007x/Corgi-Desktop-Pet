"""What the corgi can sense about your computer.

Everything here degrades gracefully: if a library or OS feature is missing,
that sensor simply reports "unknown" and the pet carries on without it.

- SystemSensors: CPU % and battery (needs `psutil`)
- IdleTracker:   seconds since you last used mouse/keyboard
- TypingMeter:   typing speed in keys per minute (needs `pynput`).
                 Only timestamps are kept — never which keys you pressed.
- WindowLedges:  top edges of visible windows the pet can stand on
                 (Windows via Win32, macOS via Quartz/pyobjc)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections import deque

try:
    import psutil
except ImportError:
    psutil = None


class SystemSensors:
    def __init__(self):
        self.cpu: float | None = None
        self.battery: float | None = None
        self.plugged: bool | None = None
        self._next_cpu = 0.0
        self._next_batt = 0.0
        if psutil:
            psutil.cpu_percent(interval=None)   # prime the counter

    @property
    def available(self):
        return psutil is not None

    def poll(self, now: float):
        if not psutil:
            return
        if now >= self._next_cpu:
            self._next_cpu = now + 2.0
            try:
                self.cpu = psutil.cpu_percent(interval=None)
            except Exception:
                self.cpu = None
        if now >= self._next_batt:
            self._next_batt = now + 30.0
            try:
                b = psutil.sensors_battery()
                if b is not None:
                    self.battery, self.plugged = b.percent, b.power_plugged
            except Exception:
                pass


# ── Idle time ──────────────────────────────────────────────────────────────
def _win_idle_seconds():
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    ticks = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((ticks - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def _mac_idle_seconds():
    out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True,
                         text=True, timeout=2).stdout
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else None


class IdleTracker:
    """Combines the OS idle timer (if any) with cursor and keyboard activity."""

    def __init__(self):
        self.last_cursor = None
        self.last_activity = time.monotonic()
        self._os_idle = None
        self._next_os = 0.0
        if sys.platform == "win32":
            self._os_fn, self._os_every = _win_idle_seconds, 1.0
        elif sys.platform == "darwin":
            self._os_fn, self._os_every = _mac_idle_seconds, 5.0
        else:
            self._os_fn, self._os_every = None, 0

    def note_activity(self):
        self.last_activity = time.monotonic()

    def poll(self, now: float, cursor_xy):
        if cursor_xy != self.last_cursor:
            if self.last_cursor is not None:
                self.last_activity = now
            self.last_cursor = cursor_xy
        if self._os_fn and now >= self._next_os:
            self._next_os = now + self._os_every
            try:
                self._os_idle = self._os_fn()
            except Exception:
                self._os_fn = None

    def idle_seconds(self, now: float) -> float:
        ours = now - self.last_activity
        if self._os_idle is not None:
            return min(ours, self._os_idle)
        return ours


# ── Typing speed ───────────────────────────────────────────────────────────
class TypingMeter:
    WINDOW = 10.0

    def __init__(self, on_activity=None):
        self.presses: deque[float] = deque()
        self.available = False
        self._listener = None
        self._on_activity = on_activity

    def start(self):
        try:
            from pynput import keyboard
        except Exception:
            return False

        def on_press(_key):
            # Deliberately ignore *which* key — only count that one was pressed.
            self.presses.append(time.monotonic())
            if self._on_activity:
                self._on_activity()

        try:
            self._listener = keyboard.Listener(on_press=on_press)
            self._listener.daemon = True
            self._listener.start()
            self.available = True
        except Exception as e:
            print(f"[corgi] typing reactions off ({e})")
        return self.available

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass

    def kpm(self, now: float) -> float:
        while self.presses and now - self.presses[0] > self.WINDOW:
            self.presses.popleft()
        return len(self.presses) * (60.0 / self.WINDOW)


# ── Window title bars as platforms ─────────────────────────────────────────
def _subtract(segs, cut_l, cut_r):
    out = []
    for l, r in segs:
        if cut_r <= l or cut_l >= r:
            out.append((l, r))
            continue
        if cut_l > l:
            out.append((l, cut_l))
        if cut_r < r:
            out.append((cut_r, r))
    return out


def ledges_from_rects(rects, min_width=60, min_top=0):
    """rects: (left, top, right, bottom), ordered front-most first.

    Returns visible top edges as (left, right, y), clipped by any window in
    front of them, so the pet never stands on a hidden title bar.
    """
    ledges = []
    for i, (l, t, r, b) in enumerate(rects):
        if t < min_top or r - l < min_width:
            continue
        segs = [(l, r)]
        for (l2, t2, r2, b2) in rects[:i]:
            if t2 <= t <= b2:
                segs = _subtract(segs, l2, r2)
            if not segs:
                break
        ledges += [(sl, sr, t) for sl, sr in segs if sr - sl >= min_width]
    return ledges


class WindowLedges:
    SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd",
                    "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow"}

    def __init__(self):
        self.ledges: list[tuple[float, float, float]] = []
        self.available = sys.platform in ("win32", "darwin")
        self._pid = os.getpid()
        self._mac = None
        if sys.platform == "darwin":
            try:
                import Quartz  # from pyobjc-framework-Quartz
                self._mac = Quartz
            except Exception:
                self.available = False

    def refresh(self, scale: float = 1.0, min_top: float = 0):
        if not self.available:
            self.ledges = []
            return
        try:
            if sys.platform == "win32":
                rects = self._win_rects()
                rects = [tuple(v / scale for v in r) for r in rects]
            else:
                rects = self._mac_rects()
            self.ledges = ledges_from_rects(rects, min_top=min_top)
        except Exception as e:
            print(f"[corgi] window platforms off ({e})")
            self.available = False
            self.ledges = []

    def _win_rects(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
        hwnds = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                  wintypes.LPARAM)

        def cb(h, _):
            hwnds.append(h)
            return True
        user32.EnumWindows(proc(cb), 0)          # front-to-back z-order

        rects = []
        buf = ctypes.create_unicode_buffer(256)
        for h in hwnds:
            if not user32.IsWindowVisible(h) or user32.IsIconic(h):
                continue
            if user32.GetWindowTextLengthW(h) == 0:
                continue
            if user32.GetWindowLongW(h, -20) & 0x80:     # WS_EX_TOOLWINDOW
                continue
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == self._pid:
                continue
            user32.GetClassNameW(h, buf, 256)
            if buf.value in self.SKIP_CLASSES:
                continue
            cloaked = wintypes.DWORD()
            dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(cloaked),
                                      ctypes.sizeof(cloaked))
            if cloaked.value:
                continue
            rc = wintypes.RECT()
            # Visible frame bounds (excludes invisible resize borders).
            if dwm.DwmGetWindowAttribute(h, 9, ctypes.byref(rc),
                                         ctypes.sizeof(rc)) != 0:
                user32.GetWindowRect(h, ctypes.byref(rc))
            rects.append((rc.left, rc.top, rc.right, rc.bottom))
        return rects

    def _mac_rects(self):
        Q = self._mac
        infos = Q.CGWindowListCopyWindowInfo(
            Q.kCGWindowListOptionOnScreenOnly
            | Q.kCGWindowListExcludeDesktopElements, Q.kCGNullWindowID)
        rects = []
        for w in infos or []:
            if w.get("kCGWindowLayer", 0) != 0:
                continue
            if w.get("kCGWindowOwnerPID") == self._pid:
                continue
            b = w.get("kCGWindowBounds") or {}
            x, y = b.get("X", 0), b.get("Y", 0)
            rects.append((x, y, x + b.get("Width", 0), y + b.get("Height", 0)))
        return rects
