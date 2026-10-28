"""The corgi itself: a transparent, always-on-top window with physics,
a behaviour state machine, mouse interaction, and reactions to your computer.
"""
from __future__ import annotations

import math
import queue
import random
import time
from collections import deque
from pathlib import Path

import sprites
from bubble import SpeechBubble
from features import (CHATTER, CLICK_LINES, DIZZY_LINES, GRAB_LINES,
                      PET_LINES, TREAT_LINES, TYPING_LINES, Mood, Pomodoro,
                      Reminders, SeatWatcher, pick, save_config, save_state)
from qt_compat import QtCore, QtGui, QtWidgets, Qt
from sensors import IdleTracker, SystemSensors, TypingMeter, WindowLedges

CW, CH = 44, 54          # window size in sprite pixels (headroom for effects)
GRAVITY = 1800.0         # px / s²
CUSTOM_DIR = Path(__file__).with_name("custom_sprites")

# States where the pet is just hanging around and can be interrupted.
CALM = {"idle", "walk", "sit", "sad"}

GLYPHS = {
    "heart": ["01010", "11111", "11111", "01110", "00100"],
    "z":     ["11111", "00010", "00100", "01000", "11111"],
    "drop":  ["010", "010", "111", "111", "010"],
    "bang":  ["11", "11", "11", "00", "11"],
    "star":  ["010", "111", "010"],
}
GLYPH_COLORS = {
    "heart": (236, 84, 110), "z": (96, 120, 210), "drop": (110, 175, 245),
    "bang": (236, 92, 60), "star": (250, 208, 60),
}
OUTLINE = QtGui.QColor(58, 34, 22)


def _qimage(canvas: sprites.Canvas) -> QtGui.QImage:
    img = QtGui.QImage(canvas.to_rgba(), canvas.w, canvas.h, canvas.w * 4,
                       QtGui.QImage.Format.Format_RGBA8888)
    return img.copy()            # detach from the Python bytes buffer


def load_frames(scale: int) -> dict:
    """{anim: [ {"R": pixmap, "L": pixmap, "rawR": img, "rawL": img,
                 "w": cells, "h": cells}, ... ]}"""
    out = {}
    flip = QtGui.QTransform().scale(-1, 1)
    for name, canvases in sprites.build_animations().items():
        frames = []
        for i, cv in enumerate(canvases):
            custom = CUSTOM_DIR / f"{name}_{i}.png"
            if custom.exists():
                raw = QtGui.QImage(str(custom)).convertToFormat(
                    QtGui.QImage.Format.Format_RGBA8888)
            else:
                raw = _qimage(cv)
            rawL = raw.transformed(flip)
            w, h = raw.width(), raw.height()

            def big(im):
                return QtGui.QPixmap.fromImage(im.scaled(
                    w * scale, h * scale,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.FastTransformation))
            frames.append({"R": big(raw), "L": big(rawL), "rawR": raw,
                           "rawL": rawL, "w": w, "h": h})
        out[name] = frames
    return out


class Particle:
    __slots__ = ("kind", "x", "y", "vx", "vy", "life", "age", "falls")

    def __init__(self, kind, x, y, vx, vy, life, falls=False):
        self.kind, self.x, self.y = kind, x, y
        self.vx, self.vy, self.life, self.age = vx, vy, life, 0.0
        self.falls = falls


class CorgiPet(QtWidgets.QWidget):
    def __init__(self, cfg: dict, saved: dict, debug: bool = False):
        super().__init__(None)
        self.cfg = cfg
        self.debug = debug
        self.s = max(1, int(cfg.get("scale", 3)))
        self.name = cfg.get("name", "Biscuit")

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if hasattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow"):
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setMouseTracking(True)
        self.setFixedSize(CW * self.s, CH * self.s)
        self.setWindowTitle(self.name)

        self.frames = load_frames(self.s)
        self.bubble = SpeechBubble()

        # Systems
        self.mood = Mood(saved.get("mood"))
        self.reminders = Reminders(cfg)
        self.pomodoro = Pomodoro(cfg)
        self.events: queue.Queue = queue.Queue()
        self.seats = SeatWatcher(cfg, self.events)
        self.seats.start()
        self.sys = SystemSensors()
        self.idle = IdleTracker()
        self.typing = TypingMeter(on_activity=self.idle.note_activity)
        if cfg["features"].get("typing"):
            self.typing.start()
        self.windows = WindowLedges()

        # Physics / state
        self.x = self.y = 0.0
        self.vx = self.vy = 0.0
        self.facing = 1
        self.wall = 1
        self.state = "fall"
        self.state_t = 0.0
        self.state_dur = 0.0
        self.anim = "fall"
        self.anim_t = 0.0
        self.frame_i = 0
        self.hop = 0.0
        self.sleep_reason = None
        self.after_yawn = None
        self.climb_target = 0.0
        self.climb_pause = 0.0
        self.particles: list[Particle] = []
        self.hot = False
        self.droopy = False
        self.greeted = False

        # Mouse
        self._press = None              # (local QPoint, global QPointF)
        self._dragging = False
        self._grab_offset = QtCore.QPointF()
        self._drag_hist: deque = deque(maxlen=8)
        self._pending_click = None
        self._pet_xs: deque = deque(maxlen=12)
        self._pet_dir = 0
        self._pet_turns: deque = deque(maxlen=8)

        # Timers / cooldowns
        now = time.monotonic()
        self._last = now
        self._next_slow = now
        self._next_windows = now
        self._next_save = now + 300
        self._next_chatter = now + random.uniform(240, 540)
        self._cooldown = {}
        self._fx_timer = 0.0

        self.menu = self._build_menu()
        self.tray = self._build_tray()

        # Drop in from the top of the screen.
        g = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.x = random.uniform(g.left() + g.width() * 0.2,
                                g.left() + g.width() * 0.8 - self.width())
        self.y = max(g.top(), g.bottom() - self.height() - 320)
        self.move(int(self.x), int(self.y))

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30)

    # ── helpers ────────────────────────────────────────────────────────
    def log(self, *a):
        if self.debug:
            print("[corgi]", *a)

    def cool(self, key: str, seconds: float) -> bool:
        """True (and starts the cooldown) if `key` is off cooldown."""
        now = time.monotonic()
        if now < self._cooldown.get(key, 0):
            return False
        self._cooldown[key] = now + seconds
        return True

    def say(self, text, seconds=4.0):
        self.bubble.say(text, seconds)

    def geo(self) -> QtCore.QRect:
        c = QtCore.QPoint(int(self.x + self.width() / 2),
                          int(self.y + self.height() / 2))
        scr = QtGui.QGuiApplication.screenAt(c) \
            or QtGui.QGuiApplication.primaryScreen()
        return scr.availableGeometry()

    def ground(self) -> float:
        """Window y that puts the paws on the bottom of the screen."""
        g = self.geo()
        return g.bottom() + 1 - self.height()

    def bounds(self):
        g = self.geo()
        pad = 2 * self.s
        return g.left() - pad, g.right() + 1 - self.width() + pad

    def cx(self):
        return self.x + self.width() / 2

    def feet(self):
        return self.y + self.height()

    def on_ground_floor(self):
        return abs(self.y - self.ground()) <= 2

    def standing_ok(self) -> bool:
        """Is there something under the paws? Snap to it if so."""
        if abs(self.y - self.ground()) <= 2:
            self.y = self.ground()
            return True
        if not self.cfg["features"].get("windows"):
            return False
        cx, f = self.cx(), self.feet()
        for l, r, t in self.windows.ledges:
            if l <= cx <= r and abs(f - t) <= 3:
                self.y = t - self.height()
                return True
        return False

    # ── state machine ──────────────────────────────────────────────────
    def enter(self, state: str, dur: float = 0.0):
        if state != self.state:
            self.log(f"{self.state} -> {state}")
        self.state = state
        self.state_t = 0.0
        self.state_dur = dur
        if state != "excited":
            self.hop = 0.0

    def anim_name(self) -> str:
        s = self.state
        if s == "idle":
            return "sad" if self.droopy else ("pant" if self.hot else "idle")
        if s == "walk":
            return "walk_sad" if self.droopy else "walk"
        if s == "chase":
            return "run"
        return s

    def choose_next(self):
        m = self.mood
        w = {"idle": 3.0, "walk": 4.0, "sit": 2.0, "yawn": 0.5,
             "chase": 0.0, "nap": 0.0, "excited": 0.0, "sad": 0.0}
        if m.energy < 25:
            w["nap"] += 3.0
            w["yawn"] += 1.5
            w["walk"] *= 0.4
        if m.happiness > 65:
            w["excited"] = 0.4
        if m.happiness > 60:
            w["chase"] = 0.6
        if m.label in ("sad", "hungry"):
            w["sad"] = 2.0
            w["walk"] *= 0.6
        if self.droopy:
            w["walk"] *= 0.5
        choice = random.choices(list(w), weights=list(w.values()))[0]
        if choice == "walk":
            if random.random() < 0.5:
                self.facing *= -1
            self.enter("walk", random.uniform(2.0, 7.0))
        elif choice == "idle":
            self.enter("idle", random.uniform(2.0, 6.0))
        elif choice == "sit":
            self.enter("sit", random.uniform(3.0, 9.0))
        elif choice == "sad":
            self.enter("sad", random.uniform(3.0, 6.0))
        elif choice == "yawn":
            self.after_yawn = "sit"
            self.enter("yawn", 2.0)
        elif choice == "nap":
            self.after_yawn = "sleep"
            self.sleep_reason = "tired"
            self.enter("yawn", 2.0)
        elif choice == "chase":
            self.enter("chase", 8.0)
        elif choice == "excited":
            self.enter("excited", 2.5)

    def start_fall(self, vx=0.0, vy=0.0):
        self.vx, self.vy = vx, vy
        self.enter("fall")

    def start_sleep(self, reason: str):
        self.sleep_reason = reason
        self.enter("sleep")

    def wake(self, line: str | None = None):
        self.sleep_reason = None
        self.after_yawn = "idle"
        self.enter("yawn", 2.0)
        if line:
            self.say(line)

    # ── main loop ──────────────────────────────────────────────────────
    def tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        self.state_t += dt

        cur = QtGui.QCursor.pos()
        self.idle.poll(now, (cur.x(), cur.y()))
        self.mood.update(dt, self.state == "sleep")

        if now >= self._next_windows:
            self._next_windows = now + 0.6
            if self.cfg["features"].get("windows"):
                dpr = QtGui.QGuiApplication.primaryScreen().devicePixelRatio()
                self.windows.refresh(scale=dpr, min_top=self.height() * 0.6)
            else:
                self.windows.ledges = []

        if now >= self._next_slow:
            self._next_slow = now + 1.0
            self.slow_tick(now)

        if self._pending_click and now - self._pending_click > 0.28:
            self._pending_click = None
            self.on_click()

        if not self._dragging:
            getattr(self, f"st_{self.state}", self.st_idle)(dt)

        self.update_anim(dt)
        self.update_particles(dt)
        self.keep_on_screen()
        self.move(int(round(self.x)), int(round(self.y)))
        self.bubble.set_sticky(self.pomodoro.remaining_text())
        self.bubble.tick(self.geometry(),
                         int(self.head_pos()[1] - 9 * self.s))
        self.update()

        if now >= self._next_save:
            self._next_save = now + 300
            self.save()

    def slow_tick(self, now):
        f = self.cfg["features"]
        idle_s = self.idle.idle_seconds(now)
        active = idle_s < 60
        calm = self.state in CALM and not self._dragging

        # Sleep while you're away; wake up when you come back.
        if self.state == "sleep":
            r = self.sleep_reason
            if r == "idle" and idle_s < 2:
                self.wake(pick(["You're back! 🐾", "*stretches* Oh, hi!",
                                "I was guarding the desk. Promise."]))
                self.mood.interact(happiness=4)
            elif r in ("tired", "nap") and self.mood.energy >= 95:
                self.wake("*big stretch* Ready to go!")
        elif calm and idle_s > self.cfg["sleep_after_idle_min"] * 60:
            self.after_yawn = "sleep"
            self.sleep_reason = "idle"
            self.enter("yawn", 2.0)

        # System reactions
        if f.get("system") and self.sys.available:
            self.sys.poll(now)
            cpu, hot_at = self.sys.cpu, self.cfg["cpu_hot_percent"]
            if cpu is not None:
                was = self.hot
                self.hot = cpu >= hot_at or (was and cpu >= hot_at - 10)
                if self.hot and not was and self.cool("hot", 600):
                    self.say(f"Phew… CPU at {cpu:.0f}%. It's hot in here! 🥵")
            b, plugged = self.sys.battery, self.sys.plugged
            if b is not None:
                was = self.droopy
                self.droopy = (not plugged) and b <= self.cfg[
                    "battery_low_percent"]
                if self.droopy and not was and self.cool("batt", 900):
                    self.say(f"Battery {b:.0f}%… I'm running low. "
                             "Plug me in? 🔌", 6)
                elif was and not self.droopy and plugged:
                    self.say("Ahh, charging! ⚡ Much better.")
                    if self.state in CALM:
                        self.enter("excited", 2.0)
        else:
            self.hot = self.droopy = False

        # Typing speed
        if f.get("typing") and self.typing.available and calm:
            kpm = self.typing.kpm(now)
            if kpm >= self.cfg["typing_excited_kpm"] and self.cool("type", 90):
                self.say(pick(TYPING_LINES))
                self.enter("excited", 3.0)

        # Follow the cursor
        if f.get("follow_cursor") and calm and self.state != "chase":
            cur = QtGui.QCursor.pos()
            if abs(cur.x() - self.cx()) > 220 and self.geo().contains(cur):
                self.enter("chase", 10.0)

        # Reminders, Pomodoro, seat watcher, chatter
        if self.state != "sleep":
            msg = self.reminders.check(active)
            if msg:
                self.say(msg, 7)
                if self.state in CALM:
                    self.enter("bark", 1.6)

        ev = self.pomodoro.check()
        if ev == "focus_done":
            if self.state == "sleep":
                self.wake()
            self.say(f"Focus session done! 🎉 ({self.pomodoro.completed} "
                     f"today). Take a {self.cfg['pomodoro']['break_min']}-min "
                     "break with me.", 8)
            self.enter("excited", 3.0)
        elif ev == "break_done":
            self.say("Break's over! Ready for another round? 🍅", 7)
            if self.state in CALM:
                self.enter("bark", 1.6)

        while True:
            try:
                kind, value, label = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "seat":
                if self.state == "sleep":
                    self.wake()
                self.say(f"A seat just opened at {label}! ({value:g} free) "
                         "🪑 Go go go!", 8)
                self.enter("excited", 3.0)

        if (f.get("chatter") and active and calm and now >= self._next_chatter):
            self._next_chatter = now + random.uniform(240, 540)
            self.say(pick(CHATTER[self.mood.label]))

    # ── per-state behaviour ────────────────────────────────────────────
    def _timed(self, nxt=None):
        if self.state_t >= self.state_dur:
            if nxt:
                self.enter(*nxt)
            else:
                self.choose_next()

    def _need_support(self):
        if not self.standing_ok():
            self.start_fall(vx=self.facing * 40)
            return False
        return True

    def st_idle(self, dt):
        if self._need_support():
            self._timed()

    st_sit = st_sad = st_idle

    def st_bark(self, dt):
        if self._need_support():
            self._timed(("sit", random.uniform(2, 4)))

    st_happy = st_bark

    def st_yawn(self, dt):
        if not self._need_support():
            return
        if self.state_t >= self.state_dur:
            nxt = self.after_yawn or "sit"
            self.after_yawn = None
            if nxt == "sleep":
                self.start_sleep(self.sleep_reason or "tired")
            elif nxt == "idle":
                self.enter("idle", random.uniform(2, 4))
            else:
                self.enter("sit", random.uniform(3, 6))

    def st_sleep(self, dt):
        self._need_support()
        self._fx_timer += dt
        if self._fx_timer > 1.1:
            self._fx_timer = 0
            hx, hy = self.head_pos()
            self.spawn("z", hx + self.facing * 3 * self.s, hy - 9 * self.s,
                       self.facing * 6 * self.s / 3, -8 * self.s, 2.6)

    def st_land(self, dt):
        self._timed(("idle", random.uniform(1, 3)))

    def st_dizzy(self, dt):
        self._timed(("sit", random.uniform(2, 4)))

    def st_excited(self, dt):
        self.hop = abs(math.sin(self.state_t * 9)) * 3
        if self._need_support():
            self._timed(("idle", random.uniform(1.5, 3)))

    def st_walk(self, dt):
        speed = self.cfg["walk_speed"] * (0.6 if self.droopy else 1) \
            * (0.8 if self.hot else 1)
        self._move_along(speed, dt)
        if self.state == "walk":
            self._timed()

    def st_chase(self, dt):
        cur = QtGui.QCursor.pos()
        target = cur.x() - self.width() / 2
        dx = target - self.x
        if abs(dx) < 14 or self.state_t > self.state_dur:
            self.facing = 1 if dx >= 0 else -1
            if cur.y() < self.y + self.height() * 0.3:
                self.say(pick(["Jump! I can't reach!", "Come down here!",
                               "*boing boing*"]), 2.5)
                self.enter("excited", 2.5)
            else:
                self.enter("bark", 1.5)
                self.say(pick(["Got you!", "Woof!", "Hehe!"]), 2)
            self.mood.interact(happiness=1, energy=-1)
            return
        self.facing = 1 if dx > 0 else -1
        step = min(abs(dx), self.cfg["run_speed"] * dt)
        self._move_along(step / dt if dt else 0, dt, turn_at_edges=False)

    def _move_along(self, speed, dt, turn_at_edges=True):
        lo, hi = self.bounds()
        self.x += self.facing * speed * dt
        if self.x <= lo or self.x >= hi:
            self.x = max(lo, min(hi, self.x))
            if (turn_at_edges and self.on_ground_floor()
                    and self.cfg["features"].get("climb")
                    and random.random() < 0.35):
                self.start_climb(self.facing)
                return
            if turn_at_edges:
                self.facing *= -1
        if not self.standing_ok():
            self.start_fall(vx=self.facing * speed * 0.8, vy=-60)

    def start_climb(self, side):
        g = self.geo()
        self.wall = side
        self.facing = side
        self.x = (g.right() + 1 - self.width()) if side > 0 else g.left()
        self.climb_target = g.top() + random.uniform(0.05, 0.6) * g.height()
        self.climb_pause = 0.0
        self.enter("climb")

    def st_climb(self, dt):
        if self.y > self.climb_target:
            self.y -= 55 * dt
            return
        # At the top of the climb: hang on a moment, then let go.
        self.climb_pause += dt
        if self.climb_pause > 1.2:
            if random.random() < 0.5:
                self.say(pick(["Wheee!", "Geronimo!", "Look, no paws!"]), 2)
            self.facing = -self.wall
            self.start_fall(vx=-self.wall * 160, vy=-140)

    def st_fall(self, dt):
        lo, hi = self.bounds()
        self.vy = min(2200.0, self.vy + GRAVITY * dt)
        self.x += self.vx * dt
        self.vx *= max(0.0, 1 - 0.6 * dt)
        if self.x < lo:
            self.x, self.vx = lo, abs(self.vx) * 0.5
        elif self.x > hi:
            self.x, self.vx = hi, -abs(self.vx) * 0.5
        if abs(self.vx) > 40:
            self.facing = 1 if self.vx > 0 else -1

        prev_feet = self.feet()
        self.y += self.vy * dt
        if self.vy <= 0:
            return
        feet = self.feet()
        surface = None
        g_floor = self.ground() + self.height()
        if feet >= g_floor:
            surface = g_floor
        if self.cfg["features"].get("windows"):
            cx = self.cx()
            for l, r, t in self.windows.ledges:
                if l <= cx <= r and prev_feet <= t <= feet:
                    surface = t if surface is None else min(surface, t)
        if surface is None:
            return
        self.y = surface - self.height()
        impact, self.vy, self.vx = self.vy, 0.0, 0.0
        if impact > 1500:
            self.enter("dizzy", 2.5)
            self.say(pick(DIZZY_LINES), 2.5)
            self.mood.interact(happiness=-1)
        else:
            self.enter("land", 0.25)
        if not self.greeted:
            self.greeted = True
            self.say(f"Hi! I'm {self.name} 🐾 Right-click me for options.", 6)

    def st_drag(self, dt):
        pass

    def keep_on_screen(self):
        c = QtCore.QPoint(int(self.cx()), int(self.y + self.height() / 2))
        if QtGui.QGuiApplication.screenAt(c) is None and not self._dragging:
            g = QtGui.QGuiApplication.primaryScreen().availableGeometry()
            self.x = g.center().x() - self.width() / 2
            self.y = g.top()
            self.start_fall()

    # ── animation & effects ────────────────────────────────────────────
    def update_anim(self, dt):
        name = self.anim_name()
        if name != self.anim:
            self.anim, self.anim_t, self.frame_i = name, 0.0, 0
        self.anim_t += dt
        n = len(self.frames[name])
        idx = int(self.anim_t * sprites.FPS.get(name, 4))
        self.frame_i = min(idx, n - 1) if name in sprites.ONCE else idx % n

        # Sweat drops when the CPU is hot
        if self.hot and self.state != "sleep":
            self._fx_timer += dt
            if self._fx_timer > 0.7:
                self._fx_timer = 0
                hx, hy = self.head_pos()
                self.spawn("drop", hx - self.facing * 6 * self.s,
                           hy - 3 * self.s, -self.facing * 10, 30, 1.0,
                           gravity=True)

    def spawn(self, kind, x, y, vx, vy, life, gravity=False):
        self.particles.append(
            Particle(kind, x - self.x, y - self.y, vx, vy, life, gravity))

    def update_particles(self, dt):
        keep = []
        for p in self.particles:
            p.age += dt
            if p.falls:
                p.vy += 400 * dt
            p.x += p.vx * dt
            p.y += p.vy * dt
            if p.age < p.life:
                keep.append(p)
        self.particles = keep[-30:]

    def current_frame(self):
        frames = self.frames[self.anim]
        return frames[self.frame_i % len(frames)]

    def frame_origin(self, fr):
        """Top-left of the sprite inside the window, in screen px."""
        s = self.s
        if self.anim == "climb":
            ox = (CW - fr["w"]) if self.wall > 0 else 0
            oy = (CH - fr["h"]) // 2
            return ox * s, oy * s
        return ((CW - fr["w"]) // 2) * s, (CH - fr["h"]) * s - int(self.hop * s)

    def head_pos(self):
        """Head centre in GLOBAL screen coordinates."""
        fr = self.current_frame()
        ox, oy = self.frame_origin(fr)
        hx, hy = sprites.HEAD[sprites.POSE.get(self.anim, "stand")]
        side = self.wall if self.anim == "climb" else self.facing
        if side < 0:
            hx = fr["w"] - 1 - hx
        return self.x + ox + hx * self.s, self.y + oy + hy * self.s

    # ── painting ───────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        fr = self.current_frame()
        side = self.wall if self.anim == "climb" else self.facing
        ox, oy = self.frame_origin(fr)
        p.drawPixmap(ox, oy, fr["R"] if side > 0 else fr["L"])

        hx, hy = self.head_pos()
        hx, hy = hx - self.x, hy - self.y
        s = self.s
        if self.state == "dizzy":
            for k in range(3):
                a = self.state_t * 5 + k * 2.094
                self.glyph(p, "star", hx + math.cos(a) * 7 * s - s,
                           hy - 7 * s + math.sin(a) * 2 * s)
        if self.state in ("excited", "bark") and self.state_t < 1.2:
            self.glyph(p, "bang", hx + side * 3 * s, hy - 19 * s)
        for part in self.particles:
            self.glyph(p, part.kind, part.x, part.y)

    def glyph(self, p: QtGui.QPainter, kind, x, y):
        rows = GLYPHS[kind]
        s = self.s
        col = QtGui.QColor(*GLYPH_COLORS[kind])
        cells = [(cx, cy) for cy, row in enumerate(rows)
                 for cx, ch in enumerate(row) if ch == "1"]
        for cx, cy in cells:
            p.fillRect(int(x + (cx - 1) * s), int(y + cy * s), 3 * s, s,
                       OUTLINE)
            p.fillRect(int(x + cx * s), int(y + (cy - 1) * s), s, 3 * s,
                       OUTLINE)
        for cx, cy in cells:
            p.fillRect(int(x + cx * s), int(y + cy * s), s, s, col)

    # ── mouse ──────────────────────────────────────────────────────────
    def hit(self, pos: QtCore.QPointF) -> bool:
        fr = self.current_frame()
        ox, oy = self.frame_origin(fr)
        side = self.wall if self.anim == "climb" else self.facing
        img = fr["rawR"] if side > 0 else fr["rawL"]
        cx = int((pos.x() - ox) // self.s)
        cy = int((pos.y() - oy) // self.s)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                x, y = cx + dx, cy + dy
                if 0 <= x < img.width() and 0 <= y < img.height() \
                        and img.pixelColor(x, y).alpha() > 0:
                    return True
        return False

    def mousePressEvent(self, e):
        if not self.hit(e.position()):
            e.ignore()
            return
        if e.button() == Qt.MouseButton.RightButton:
            self.menu.popup(e.globalPosition().toPoint())
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = (e.position(), e.globalPosition())
            self._grab_offset = e.position()

    def mouseMoveEvent(self, e):
        g = e.globalPosition()
        if self._press and (e.buttons() & Qt.MouseButton.LeftButton):
            if not self._dragging and \
                    (g - self._press[1]).manhattanLength() > 4:
                self._dragging = True
                self._pending_click = None
                self.sleep_reason = None
                self.enter("drag")
                if random.random() < 0.4:
                    self.say(pick(GRAB_LINES), 1.5)
            if self._dragging:
                self.x = g.x() - self._grab_offset.x()
                self.y = g.y() - self._grab_offset.y()
                self._drag_hist.append((time.monotonic(), g.x(), g.y()))
            return
        # Hovering back and forth over the pet = petting.
        if not self.hit(e.position()):
            return
        x = g.x()
        if self._pet_xs:
            d = x - self._pet_xs[-1]
            if abs(d) >= 3:
                direction = 1 if d > 0 else -1
                if self._pet_dir and direction != self._pet_dir:
                    self._pet_turns.append(time.monotonic())
                self._pet_dir = direction
        self._pet_xs.append(x)
        now = time.monotonic()
        recent = [t for t in self._pet_turns if now - t < 1.5]
        if len(recent) >= 4 and self.cool("pet", 2.5):
            self._pet_turns.clear()
            self.do_pet()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._dragging:
            self._dragging = False
            vx = vy = 0.0
            h = [p for p in self._drag_hist if time.monotonic() - p[0] < 0.12]
            if len(h) >= 2 and h[-1][0] > h[0][0]:
                dtm = h[-1][0] - h[0][0]
                vx = max(-2500, min(2500, (h[-1][1] - h[0][1]) / dtm))
                vy = max(-2500, min(2500, (h[-1][2] - h[0][2]) / dtm))
            self._drag_hist.clear()
            self.start_fall(vx, vy)
            self.mood.interact(happiness=1)
        elif self._press:
            self._pending_click = time.monotonic()
        self._press = None

    def mouseDoubleClickEvent(self, e):
        if self.hit(e.position()) and e.button() == Qt.MouseButton.LeftButton:
            self._pending_click = None
            self.do_pet()

    def on_click(self):
        if self.state == "sleep":
            self.mood.interact(happiness=-2)
            self.wake(pick(["Hmm? Wha—", "*grumble* five more minutes...",
                            "I'm up! I'm up!"]))
            return
        if self.state in ("drag", "fall", "climb"):
            return
        self.mood.interact(happiness=2)
        self.say(pick(CLICK_LINES), 1.6)
        self.enter("bark", 1.2)

    # ── actions (menu + interactions) ──────────────────────────────────
    def _ground_state(self):
        return self.state not in ("fall", "climb", "drag")

    def do_pet(self):
        self.mood.interact(happiness=8)
        self.mood.pets += 1
        if self.state == "sleep":
            self.sleep_reason = None
        hx, hy = self.head_pos()
        for i in range(3):
            self.spawn("heart", hx - 6 * self.s + i * 5 * self.s,
                       hy - 16 * self.s, random.uniform(-15, 15),
                       random.uniform(-45, -30), 1.4)
        self.say(pick(PET_LINES), 2)
        if self._ground_state():
            self.enter("happy", 2.2)

    def give_treat(self):
        if self.mood.fullness > 95:
            self.say("I'm stuffed! Maybe later? 🐶")
            return
        self.mood.treats += 1
        self.mood.interact(happiness=10, fullness=30, energy=5)
        hx, hy = self.head_pos()
        self.spawn("heart", hx, hy - 16 * self.s, 0, -40, 1.2)
        self.say(pick(TREAT_LINES), 2.5)
        if self._ground_state():
            self.enter("happy", 2.5)

    def play(self):
        if self.state == "sleep":
            self.wake()
        self.say("Catch me if you can! Move your mouse! 🎾", 3)
        if self._ground_state():
            self.enter("chase", 10)

    def nap(self):
        if self.state == "sleep":
            self.wake("Morning! ☀️")
        elif self._ground_state():
            self.after_yawn = "sleep"
            self.sleep_reason = "nap"
            self.enter("yawn", 2.0)

    def how_are_you(self):
        self.say(self.mood.summary(self.name), 7)

    def start_pomodoro(self, mode):
        self.pomodoro.start(mode)
        if mode == "focus":
            self.say(f"Focus mode! I'll keep quiet for "
                     f"{self.cfg['pomodoro']['focus_min']} min. 🍅", 4)
        else:
            self.say("Break time! ☕", 3)

    def stop_pomodoro(self):
        self.pomodoro.stop()
        self.say("Timer stopped.", 2)

    def toggle(self, key, on):
        self.cfg["features"][key] = on
        save_config(self.cfg)
        if key == "typing" and on and not self.typing.available:
            if not self.typing.start():
                self.say("Typing reactions need `pip install pynput`.", 5)
        if key == "system" and on and not self.sys.available:
            self.say("CPU/battery reactions need `pip install psutil`.", 5)
        if key == "windows" and on and not self.windows.available:
            self.say("Walking on windows works on Windows and macOS "
                     "(macOS needs pyobjc-framework-Quartz).", 6)

    @staticmethod
    def _act(menu, text, fn):
        a = QtGui.QAction(text, menu)
        a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        return a

    def _build_menu(self):
        m = QtWidgets.QMenu()
        add = self._act
        add(m, "🦴  Give treat", self.give_treat)
        add(m, "🤚  Pet", self.do_pet)
        add(m, "🎾  Play (chase the cursor)", self.play)
        add(m, "💤  Nap / wake up", self.nap)
        add(m, "📊  How are you?", self.how_are_you)
        m.addSeparator()
        pomo = m.addMenu("🍅  Pomodoro")
        add(pomo, f"Start focus ({self.cfg['pomodoro']['focus_min']} min)",
                       lambda: self.start_pomodoro("focus"))
        add(pomo, f"Start break ({self.cfg['pomodoro']['break_min']} min)",
                       lambda: self.start_pomodoro("break"))
        add(pomo, "Stop", self.stop_pomodoro)
        opts = m.addMenu("⚙️  Settings")
        labels = [
            ("follow_cursor", "Always follow my cursor"),
            ("climb", "Climb screen edges"),
            ("windows", "Walk on windows"),
            ("typing", "React to fast typing"),
            ("system", "React to CPU and battery"),
            ("reminders", "Water and stretch reminders"),
            ("late_night", "Late-night bedtime nudge"),
            ("chatter", "Random chatter"),
        ]
        for key, label in labels:
            a = opts.addAction(label)
            a.setCheckable(True)
            a.setChecked(bool(self.cfg["features"].get(key)))
            a.toggled.connect(lambda on, k=key: self.toggle(k, on))
        m.addSeparator()
        add(m, "👋  Quit", self.quit)
        return m

    def _build_tray(self):
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon = QtGui.QIcon(self.frames["sit"][0]["R"])
        tray = QtWidgets.QSystemTrayIcon(icon)
        tray.setToolTip(self.name)
        tray.setContextMenu(self.menu)
        tray.show()
        return tray

    # ── lifecycle ──────────────────────────────────────────────────────
    def save(self):
        save_state({"mood": self.mood.to_dict()})

    def quit(self):
        self.save()
        self.seats.stop()
        self.typing.stop()
        self.say("Bye bye! 👋", 1)
        QtCore.QTimer.singleShot(500, QtWidgets.QApplication.instance().quit)
