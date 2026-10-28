"""Config, saved state, mood, reminders, Pomodoro timer and the seat watcher.

None of this touches Qt, so it is easy to test on its own.
"""
from __future__ import annotations

import copy
import json
import queue
import random
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".corgi_pet"
CONFIG_PATH = HOME / "config.json"
STATE_PATH = HOME / "state.json"

DEFAULTS = {
    "name": "Biscuit",
    "scale": 3,                      # size: each sprite pixel = N screen px
    "walk_speed": 70,                # px per second
    "run_speed": 240,
    "sleep_after_idle_min": 5,       # you away this long -> pet naps
    "features": {
        "reminders": True,           # water + stretch
        "late_night": True,          # "it's 2 AM, go to bed"
        "chatter": True,             # random mood-based comments
        "typing": True,              # gets excited when you type fast
        "system": True,              # sweats at high CPU, droops at low battery
        "windows": True,             # walks on window title bars
        "climb": True,               # climbs screen edges
        "follow_cursor": False,      # always trots after your mouse
    },
    "reminders": {"water_min": 45, "stretch_min": 60},
    "pomodoro": {"focus_min": 25, "break_min": 5},
    "typing_excited_kpm": 280,       # keys per minute that count as "fast"
    "cpu_hot_percent": 85,
    "battery_low_percent": 20,
    "seat_watcher": {
        "enabled": False,
        "url": "",                   # http(s) or file:// URL returning JSON
        "value_path": "available",   # dot path to a number, e.g. "rooms.0.free"
        "threshold": 1,              # notify when value rises to >= this
        "label": "your study spot",
        "interval_s": 60,
    },
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    HOME.mkdir(parents=True, exist_ok=True)
    user = {}
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[corgi] couldn't read {CONFIG_PATH}: {e}; using defaults")
    cfg = _merge(DEFAULTS, user)
    save_config(cfg)                 # writes any new default keys back out
    return cfg


def save_config(cfg: dict):
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(data: dict):
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError:
        pass


# ── Mood ───────────────────────────────────────────────────────────────────
def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


class Mood:
    """Three stats from 0-100 that drift over time and react to you."""

    def __init__(self, saved: dict | None = None):
        saved = saved or {}
        self.happiness = float(saved.get("happiness", 70))
        self.energy = float(saved.get("energy", 80))
        self.fullness = float(saved.get("fullness", 70))
        self.treats = int(saved.get("treats", 0))
        self.pets = int(saved.get("pets", 0))
        self.last_interaction = time.time()
        # Catch up on time the app was closed (capped at 12 h, gentler rates).
        away_min = min(720, max(0, (time.time() - saved.get("saved_at",
                                                            time.time())) / 60))
        self.fullness = _clamp(self.fullness - away_min * 0.15)
        self.energy = _clamp(self.energy + away_min * 0.5)

    def update(self, dt: float, sleeping: bool):
        m = dt / 60.0
        self.fullness = _clamp(self.fullness - 0.4 * m)
        if sleeping:
            self.energy = _clamp(self.energy + 3.0 * m)
        else:
            self.energy = _clamp(self.energy - 0.3 * m)
        decay = 0.12
        if self.fullness < 25:
            decay += 0.4
        if time.time() - self.last_interaction > 30 * 60:
            decay += 0.2
        self.happiness = _clamp(self.happiness - decay * m)

    def interact(self, happiness=0.0, fullness=0.0, energy=0.0):
        self.happiness = _clamp(self.happiness + happiness)
        self.fullness = _clamp(self.fullness + fullness)
        self.energy = _clamp(self.energy + energy)
        self.last_interaction = time.time()

    @property
    def label(self) -> str:
        if self.fullness < 20:
            return "hungry"
        if self.energy < 20:
            return "sleepy"
        if self.happiness < 30:
            return "sad"
        if self.happiness > 75:
            return "happy"
        if time.time() - self.last_interaction > 40 * 60:
            return "bored"
        return "content"

    def summary(self, name: str) -> str:
        bar = lambda v: "█" * round(v / 20) + "░" * (5 - round(v / 20))
        return (f"{name} is feeling {self.label}\n"
                f"Happy  {bar(self.happiness)}\n"
                f"Energy {bar(self.energy)}\n"
                f"Tummy  {bar(self.fullness)}\n"
                f"Treats {self.treats} · Pets {self.pets}")

    def to_dict(self) -> dict:
        return {"happiness": self.happiness, "energy": self.energy,
                "fullness": self.fullness, "treats": self.treats,
                "pets": self.pets, "saved_at": time.time()}


CHATTER = {
    "happy": ["Best day ever!", "*wiggles corgi butt*", "You're doing great!",
              "I like it here with you."],
    "content": ["*sniff sniff*", "What are we working on?", "Woof.",
                "*watches the cursor intently*"],
    "bored": ["Play with me? (right-click → Play)", "*stares at you*  ...",
              "Boooored.", "Is that a squirrel?"],
    "hungry": ["Is it snack o'clock? 🦴", "My tummy is rumbling...",
               "Treat? Treat? Treat?"],
    "sleepy": ["*yaaawn*", "Nap soon...", "So... sleepy..."],
    "sad": ["*sad corgi noises*", "Pet me?", "...", "*sigh*"],
}
CLICK_LINES = ["Woof!", "Arf!", "Hi!", "Boop!", "*tail wag*", "Bork!", "?"]
PET_LINES = ["♥ ♥ ♥", "Hehe, more!", "Right there!", "*happy wiggle*"]
TREAT_LINES = ["Nom nom nom!", "Yummy! 🦴", "*crunch crunch*", "Best. Treat."]
DIZZY_LINES = ["@_@", "Whoa... the room is spinning", "Again! Again!",
               "Ow, my butt."]
GRAB_LINES = ["Whee!", "Put me down!", "I can fly!", "Eep!"]
TYPING_LINES = ["Woah, so fast! ⌨️", "Type type type! 🐾", "You're on fire!",
                "Clackety clack!"]


# ── Reminders ──────────────────────────────────────────────────────────────
class Reminders:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        now = time.time()
        self.last_water = now
        self.last_stretch = now
        self.late_night_shown_on = None

    def check(self, user_active: bool) -> str | None:
        """Return a reminder message if one is due, else None."""
        if not user_active:
            return None
        now = time.time()
        f = self.cfg["features"]
        r = self.cfg["reminders"]
        if f.get("reminders"):
            if now - self.last_water > r["water_min"] * 60:
                self.last_water = now
                return "Water break! 💧 Have a sip. I'll have one too."
            if now - self.last_stretch > r["stretch_min"] * 60:
                self.last_stretch = now
                return "Stretch time! Stand up and shake it out, like me. 🐕"
        if f.get("late_night"):
            t = datetime.now()
            if 1 <= t.hour < 5 and self.late_night_shown_on != t.date():
                self.late_night_shown_on = t.date()
                return (f"It's {t:%H:%M}… sleep is good for brains "
                        "(and corgis). Bedtime soon?")
        return None


# ── Pomodoro ───────────────────────────────────────────────────────────────
class Pomodoro:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.mode: str | None = None     # None, "focus" or "break"
        self.ends_at = 0.0
        self.completed = 0

    def start(self, mode: str):
        minutes = self.cfg["pomodoro"]["focus_min" if mode == "focus"
                                      else "break_min"]
        self.mode = mode
        self.ends_at = time.time() + minutes * 60

    def stop(self):
        self.mode = None

    def remaining_text(self) -> str | None:
        if not self.mode:
            return None
        left = max(0, int(self.ends_at - time.time()))
        icon = "🍅 Focus" if self.mode == "focus" else "☕ Break"
        return f"{icon} {left // 60:02d}:{left % 60:02d}"

    def check(self) -> str | None:
        """Return an event name when a period ends."""
        if self.mode and time.time() >= self.ends_at:
            if self.mode == "focus":
                self.completed += 1
                self.start("break")
                return "focus_done"
            self.mode = None
            return "break_done"
        return None


# ── Seat watcher (for the thermal-camera seat project, or any JSON API) ───
def _dig(data, path: str):
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(data, list):
            data = data[int(part)]
        else:
            data = data[part]
    return data


class SeatWatcher:
    """Polls a URL that returns JSON and reports when seats open up.

    Runs in a background thread and posts ("seat", value, label) tuples to
    `events`. The UI thread drains the queue, so no Qt calls happen here.
    """

    def __init__(self, cfg: dict, events: "queue.Queue"):
        self.cfg = cfg["seat_watcher"]
        self.events = events
        self._stop = threading.Event()
        self._last = None
        self._thread = None

    def start(self):
        if not self.cfg.get("enabled") or not self.cfg.get("url"):
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        warned = False
        while not self._stop.is_set():
            try:
                with urllib.request.urlopen(self.cfg["url"], timeout=8) as r:
                    data = json.loads(r.read().decode("utf-8"))
                value = float(_dig(data, self.cfg.get("value_path", "")))
                thr = float(self.cfg.get("threshold", 1))
                if value >= thr and (self._last is None or self._last < thr):
                    self.events.put(("seat", value, self.cfg.get("label", "")))
                self._last = value
                warned = False
            except Exception as e:  # network down, bad JSON, wrong path...
                if not warned:
                    print(f"[corgi] seat watcher: {e}")
                    warned = True
            self._stop.wait(max(10, float(self.cfg.get("interval_s", 60))))


def pick(lines):
    return random.choice(lines)
