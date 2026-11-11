"""Settings, profiles and persistence (~/.config/clickity/config.json)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "clickity")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

ACTIONS = ["click", "scroll-up", "scroll-down", "scroll-left", "scroll-right"]
BUTTON_CHOICES = ["left", "right", "middle", "side", "extra"]
CLICK_TYPES = ["single", "double", "triple"]
MODES = ["toggle", "hold"]
THEMES = ["neon", "matrix", "amber", "bloodmoon", "ice"]


@dataclass
class Settings:
    action: str = "click"
    button: str = "left"
    click_type: str = "single"
    cps: float = 10.0            # clicks (actions) per second
    jitter: int = 0              # +/- % randomisation of interval
    hold_ms: int = 8             # how long the button stays down
    burst: int = 0               # clicks per burst (0 = continuous)
    burst_pause_ms: int = 500    # pause between bursts
    max_clicks: int = 0          # stop after N actions (0 = unlimited)
    max_seconds: int = 0         # stop after N seconds (0 = unlimited)
    start_delay: float = 0.0     # countdown before firing (s)
    wiggle_px: int = 0           # humanize: tiny random cursor drift per click
    scroll_step: int = 1         # wheel notches per scroll action
    mode: str = "toggle"         # toggle | hold (fire while hotkey held)
    hotkey: str = "F8"
    panic_key: str = "F9"
    theme: str = "neon"

    def clamp(self) -> "Settings":
        self.cps = max(0.1, min(float(self.cps), 500.0))
        self.jitter = max(0, min(int(self.jitter), 90))
        self.hold_ms = max(0, min(int(self.hold_ms), 5000))
        self.burst = max(0, min(int(self.burst), 10000))
        self.burst_pause_ms = max(0, min(int(self.burst_pause_ms), 600000))
        self.max_clicks = max(0, int(self.max_clicks))
        self.max_seconds = max(0, int(self.max_seconds))
        self.start_delay = max(0.0, min(float(self.start_delay), 60.0))
        self.wiggle_px = max(0, min(int(self.wiggle_px), 20))
        self.scroll_step = max(1, min(int(self.scroll_step), 20))
        if self.action not in ACTIONS: self.action = "click"
        if self.button not in BUTTON_CHOICES: self.button = "left"
        if self.click_type not in CLICK_TYPES: self.click_type = "single"
        if self.mode not in MODES: self.mode = "toggle"
        if self.theme not in THEMES: self.theme = "neon"
        return self

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        names = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in d.items() if k in names})
        return s.clamp()

    def to_dict(self) -> dict:
        return asdict(self)


PRESETS = {
    "default": Settings(),
    "turbo": Settings(cps=50, hold_ms=4, start_delay=2),
    "human": Settings(cps=7, jitter=35, hold_ms=45, wiggle_px=2),
    "burst-5": Settings(cps=25, burst=5, burst_pause_ms=800),
    "idle-keeper": Settings(cps=0.1, jitter=40, button="middle"),
    "scroller": Settings(action="scroll-down", cps=8, scroll_step=1),
}


@dataclass
class Store:
    active: str = "default"
    profiles: dict = field(default_factory=dict)
    lifetime_clicks: int = 0
    deleted_presets: list = field(default_factory=list)

    @classmethod
    def load(cls) -> "Store":
        st = cls()
        try:
            with open(CONFIG_FILE) as f:
                raw = json.load(f)
            st.active = raw.get("active", "default")
            st.lifetime_clicks = int(raw.get("lifetime_clicks", 0))
            st.deleted_presets = [str(x) for x in raw.get("deleted_presets", [])]
            for name, d in raw.get("profiles", {}).items():
                st.profiles[name] = Settings.from_dict(d)
        except (OSError, ValueError, TypeError):
            pass
        for name, preset in PRESETS.items():
            if name in st.deleted_presets and name != "default":
                continue
            st.profiles.setdefault(name, Settings.from_dict(preset.to_dict()))
        if st.active not in st.profiles:
            st.active = "default"
        return st

    def save(self) -> None:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"active": self.active, "lifetime_clicks": self.lifetime_clicks,
                       "deleted_presets": self.deleted_presets,
                       "profiles": {k: v.to_dict() for k, v in self.profiles.items()}}, f, indent=2)
        os.replace(tmp, CONFIG_FILE)

    def current(self) -> Settings:
        return Settings.from_dict(self.profiles[self.active].to_dict())
