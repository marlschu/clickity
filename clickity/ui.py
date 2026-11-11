"""Cyberpunk curses TUI."""
from __future__ import annotations

import curses
import random
import time
from collections import deque

from . import __version__
from .backends import Backend
from .config import (ACTIONS, BUTTON_CHOICES, CLICK_TYPES, MODES, THEMES,
                     Settings, Store)
from .engine import ARMING, FIRING, IDLE, Engine
from .hotkeys import HOTKEY_CHOICES, HotkeyListener, access_status

# --------------------------------------------------------------------------- art
_FONT = {
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "K": ["██╗  ██╗", "██║ ██╔╝", "█████╔╝ ", "██╔═██╗ ", "██║  ██╗", "╚═╝  ╚═╝"],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "Y": ["██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "],
}
BANNER = ["".join(_FONT[c][r] for c in "CLICKITY") for r in range(6)]

_DIGITS = {
    "0": ["┏━┓", "┃ ┃", "┗━┛"], "1": [" ┓ ", " ┃ ", " ┻ "], "2": ["┏━┓", "┏━┛", "┗━━"],
    "3": ["┏━┓", " ━┫", "┗━┛"], "4": ["╻ ╻", "┗━┫", "  ╹"], "5": ["┏━━", "┗━┓", "┗━┛"],
    "6": ["┏━━", "┣━┓", "┗━┛"], "7": ["━━┓", "  ┃", "  ╹"], "8": ["┏━┓", "┣━┫", "┗━┛"],
    "9": ["┏━┓", "┗━┫", "┗━┛"], ",": ["   ", "   ", " ╻ "],
}
SPARK = " ▁▂▃▄▅▆▇█"
GLITCH = "░▒▓█▀▄╳◢◣◤◥"
SPIN = "◢◣◤◥"

TAGLINES = [
    "neural click daemon // jacked in",
    "synthetic input injection system",
    "wake up, samurai. we have clicks to burn",
    "high-frequency actuator online",
    "ghost in the mouse // v" + __version__,
]

# theme: (primary, secondary, accent, dim, ok, bad, gradient256, (p8, s8))
_THEMES = {
    "neon": (201, 51, 226, 60, 48, 197, [201, 200, 165, 129, 93, 63, 69, 75, 81, 51],
             (curses.COLOR_MAGENTA, curses.COLOR_CYAN)),
    "matrix": (46, 120, 231, 22, 46, 196, [22, 28, 34, 40, 46, 82, 118, 154, 120],
               (curses.COLOR_GREEN, curses.COLOR_GREEN)),
    "amber": (214, 220, 231, 94, 220, 196, [130, 166, 172, 208, 214, 220, 226],
              (curses.COLOR_YELLOW, curses.COLOR_RED)),
    "bloodmoon": (196, 210, 231, 52, 203, 226, [52, 88, 124, 160, 196, 203, 210, 217],
                  (curses.COLOR_RED, curses.COLOR_MAGENTA)),
    "ice": (45, 159, 231, 24, 87, 203, [17, 18, 19, 25, 31, 37, 45, 51, 159, 195],
            (curses.COLOR_CYAN, curses.COLOR_BLUE)),
}
P_PRI, P_SEC, P_ACC, P_DIM, P_OK, P_BAD, P_INV, P_BADGE_FIRE, P_BADGE_ARM = range(1, 10)
P_GRAD = 20
LOCAL_MIN_DELAY = 3.0
_CURSES_NAV = {curses.KEY_IC: "INSERT", curses.KEY_HOME: "HOME", curses.KEY_END: "END"}

# ------------------------------------------------------------------------ fields
# (attr, label, kind, spec)
FIELDS = [
    ("action", "ACTION", "choice", ACTIONS),
    ("button", "BUTTON", "choice", BUTTON_CHOICES),
    ("click_type", "CLICK TYPE", "choice", CLICK_TYPES),
    ("cps", "RATE /s", "float", None),
    ("jitter", "JITTER ±%", "int", (5, 20)),
    ("hold_ms", "HOLD ms", "int", (1, 25)),
    ("burst", "BURST SIZE", "int", (1, 10)),
    ("burst_pause_ms", "BURST PAUSE ms", "int", (50, 500)),
    ("max_clicks", "STOP AFTER #", "int", (10, 100)),
    ("max_seconds", "STOP AFTER s", "int", (5, 60)),
    ("start_delay", "START DELAY s", "float", None),
    ("wiggle_px", "HUMANIZE px", "int", (1, 5)),
    ("scroll_step", "SCROLL NOTCHES", "int", (1, 5)),
    ("mode", "TRIGGER MODE", "choice", MODES),
    ("hotkey", "HOTKEY", "choice", HOTKEY_CHOICES),
    ("panic_key", "KILL SWITCH", "choice", HOTKEY_CHOICES),
    ("theme", "THEME", "choice", THEMES),
]
HINTS = {
    "action": "click, or scroll the wheel in a direction",
    "button": "which mouse button to actuate",
    "click_type": "clicks per actuation (double = 2 rapid clicks)",
    "cps": "target actuations per second (0.1 - 500)",
    "jitter": "randomise each interval by ±N% so timing looks human",
    "hold_ms": "time each button is held down (auto-capped to the rate)",
    "burst": "fire N clicks, then pause. 0 = continuous stream",
    "burst_pause_ms": "pause length between bursts",
    "max_clicks": "auto-stop after N actuations. 0 = infinite",
    "max_seconds": "auto-stop after N seconds. 0 = infinite",
    "start_delay": "countdown before firing - time to aim the cursor",
    "wiggle_px": "jitter the cursor ±N px per click, then return it",
    "scroll_step": "wheel notches per scroll actuation",
    "mode": "toggle: press hotkey on/off  |  hold: fire while hotkey held",
    "hotkey": "global start/stop key (works in any window with input access)",
    "panic_key": "global kill switch: stop everything and exit",
    "theme": "colour scheme",
}


def _fmt_val(attr: str, v) -> str:
    if attr in ("max_clicks", "max_seconds") and not v:
        return "∞"
    if attr in ("burst", "jitter", "wiggle_px", "start_delay") and not v:
        return "OFF"
    if attr == "cps":
        return f"{v:g}"
    if attr == "start_delay":
        return f"{v:g}"
    if isinstance(v, str):
        return v.upper()
    return str(v)


def _cps_step(v: float, up: bool) -> float:
    if v < 1 or (v == 1 and not up):
        step = 0.1
    elif v < 10 or (v == 10 and not up):
        step = 1
    elif v < 50 or (v == 50 and not up):
        step = 5
    elif v < 100 or (v == 100 and not up):
        step = 10
    else:
        step = 25
    return round(v + (step if up else -step), 2)


def _hms(sec: float) -> str:
    m, s = divmod(sec, 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:04.1f}"


class App:
    def __init__(self, scr, backend: Backend, notes: list[str], store: Store,
                 use_hotkeys: bool = True):
        self.scr = scr
        self.store = store
        self.settings: Settings = store.current()
        self.backend = backend
        self.logs: deque = deque(maxlen=200)
        self.engine = Engine(backend, self.settings, self.log)
        self.sel = 0
        self.scroll = 0
        self.frame = 0
        self.hist: deque = deque(maxlen=400)
        self.peak_cps = 0.0
        self._last_sample = 0.0
        self.quit = False
        self.show_help = False
        self.glitch_until = 0
        self.glitch_rows: dict = {}
        self.tagline = random.choice(TAGLINES)
        self.hex_line = ""
        self.colors256 = False
        self._saved_session = 0

        self.log(f"CLICKITY v{__version__} boot sequence complete")
        for n in notes:
            self.log("backend " + n)
        if backend.name == "dry-run":
            self.log("!! DRY-RUN: clicks are simulated. run install.sh for real input")
        else:
            self.log(f"backend online: {backend.name}")

        self.hk = None
        if use_hotkeys:
            hk = HotkeyListener(self._on_hotkey)
            if hk.available:
                self.hk = hk
                hk.set_keys(self.settings.hotkey, self.settings.panic_key)
                hk.start()
                self.log(f"global hotkeys armed on {len(hk.keyboards)} keyboard(s)")
            else:
                ok, total = access_status()
                self.log(f"global hotkeys OFFLINE ({ok}/{total} input devs readable) - "
                         "terminal keys only; add user to 'input' group")

    # ------------------------------------------------------------------ utils
    def log(self, msg: str) -> None:
        self.logs.append((time.strftime("%H:%M:%S"), msg))

    def _on_hotkey(self, name: str, pressed: bool) -> None:
        s = self.settings
        if name == s.panic_key and pressed:
            self.engine.stop("KILL SWITCH")
            self.quit = True
            return
        if name != s.hotkey:
            return
        if s.mode == "hold":
            if pressed:
                self.engine.start(delay=0)
            else:
                self.engine.stop("hotkey released")
        elif pressed:
            self.engine.toggle()

    def _put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = self.H, self.W
        if y < 0 or y >= h or x >= w:
            return
        if x < 0:
            text = text[-x:]
            x = 0
        text = text[: max(0, w - x - (1 if y == h - 1 else 0))]
        if not text:
            return
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def cp(self, n: int) -> int:
        return curses.color_pair(n)

    # ----------------------------------------------------------------- colors
    def apply_theme(self) -> None:
        if not curses.has_colors():
            return
        t = _THEMES.get(self.settings.theme, _THEMES["neon"])
        self.colors256 = curses.COLORS >= 256
        if self.colors256:
            pri, sec, acc, dim, ok, bad, grad = t[:7]
        else:
            pri, sec = t[7]
            acc, dim, ok, bad = curses.COLOR_YELLOW, curses.COLOR_BLUE, curses.COLOR_GREEN, curses.COLOR_RED
            grad = [pri, sec]
        bg = -1
        curses.init_pair(P_PRI, pri, bg)
        curses.init_pair(P_SEC, sec, bg)
        curses.init_pair(P_ACC, acc, bg)
        curses.init_pair(P_DIM, dim, bg)
        curses.init_pair(P_OK, ok, bg)
        curses.init_pair(P_BAD, bad, bg)
        curses.init_pair(P_INV, curses.COLOR_BLACK, sec)
        curses.init_pair(P_BADGE_FIRE, curses.COLOR_BLACK, pri)
        curses.init_pair(P_BADGE_ARM, curses.COLOR_BLACK, acc)
        self.grad = []
        for i, c in enumerate(grad):
            curses.init_pair(P_GRAD + i, c, bg)
            self.grad.append(P_GRAD + i)

    # ------------------------------------------------------------------ draw
    def box(self, y: int, x: int, h: int, w: int, title: str = "", pair: int = P_SEC) -> None:
        a = self.cp(pair)
        d = self.cp(P_DIM)
        self._put(y, x, "┏" + "━" * (w - 2) + "┓", a)
        for i in range(1, h - 1):
            self._put(y + i, x, "┃", d if i % 2 else a)
            self._put(y + i, x + w - 1, "┃", d if i % 2 else a)
        self._put(y + h - 1, x, "┗" + "━" * (w - 2) + "┛", a)
        if title:
            self._put(y, x + 2, "┫", a)
            self._put(y, x + 3, f" {title} ", self.cp(P_ACC) | curses.A_BOLD)
            self._put(y, x + 5 + len(title), "┣", a)
        # corner accents
        self._put(y, x + w - 5, "◢◤", self.cp(P_PRI))
        self._put(y + h - 1, x + 2, "◥◣", self.cp(P_PRI))

    def draw_header(self) -> int:
        W = self.W
        st = self.engine.state
        now = time.strftime("%H:%M:%S")
        top = f"━[ CLICKITY v{__version__} ]━━━ //SYS.ONLINE// "
        self._put(0, 0, "━" * W, self.cp(P_DIM))
        self._put(0, 1, top, self.cp(P_SEC) | curses.A_BOLD)
        self._put(0, W - len(now) - 5, f"[ {now} ]", self.cp(P_ACC))
        y = 1
        if self.H >= 34 and W >= 64:
            bw = len(BANNER[0])
            bx = max(0, (W - bw) // 2)
            glitching = self.frame < self.glitch_until
            shift = self.frame // 2 if st == FIRING else self.frame // 6
            for r, line in enumerate(BANNER):
                off = 0
                txt = line
                if glitching and r in self.glitch_rows:
                    off, cols = self.glitch_rows[r]
                    txt = list(line)
                    for c in cols:
                        if c < len(txt) and txt[c] != " ":
                            txt[c] = random.choice(GLITCH)
                    txt = "".join(txt)
                # draw with horizontal gradient that drifts over time
                n = len(self.grad)
                seg = max(1, bw // n)
                for i in range(0, bw, seg):
                    gi = ((i // seg) + shift + r) % (2 * n - 2 or 1)
                    gi = gi if gi < n else 2 * n - 2 - gi
                    self._put(1 + r, bx + off + i, txt[i:i + seg],
                              self.cp(self.grad[gi]) | curses.A_BOLD)
            y = 7
            tag = f"▓▒░ {self.tagline} ░▒▓"
            self._put(y, max(0, (W - len(tag)) // 2), tag, self.cp(P_DIM))
            y += 1
        else:
            t = "▓▒░ C L I C K I T Y ░▒▓"
            self._put(1, max(0, (W - len(t)) // 2), t, self.cp(P_PRI) | curses.A_BOLD)
            y = 2
        # status strip
        if st == FIRING:
            spin = SPIN[self.frame % 4]
            badge = f" {spin} FIRING {spin} " if not self.engine.in_burst_pause else " ◌ BURST-COOL ◌ "
            battr = self.cp(P_BADGE_FIRE) | curses.A_BOLD
        elif st == ARMING:
            badge = f" ◎ ARMED T-{self.engine.arm_remaining():.1f} "
            battr = self.cp(P_BADGE_ARM) | curses.A_BOLD | (curses.A_BLINK if self.frame % 10 < 5 else 0)
        else:
            badge = " ○ STANDBY "
            battr = self.cp(P_INV)
        self._put(y, 1, badge, battr)
        x = len(badge) + 2
        dirty = self.settings.to_dict() != self.store.profiles[self.store.active].to_dict()
        bk = self.backend.name.upper()
        parts = [
            ("PROFILE ", P_DIM), (self.store.active + ("*" if dirty else ""), P_ACC),
            ("  LINK ", P_DIM), (bk, P_BAD if bk == "DRY-RUN" else P_OK),
            ("  KEY ", P_DIM), (self.settings.hotkey, P_SEC),
            (" [GLOBAL]" if self.hk else " [LOCAL]", P_OK if self.hk else P_BAD),
            ("  MODE ", P_DIM), (self.settings.mode.upper(), P_SEC),
        ]
        for text, pair in parts:
            self._put(y, x, text, self.cp(pair) | (curses.A_BOLD if pair != P_DIM else 0))
            x += len(text)
        return y + 1

    def draw_config(self, y: int, x: int, h: int, w: int) -> None:
        self.box(y, x, h, w, "CONFIG MATRIX", P_SEC)
        inner = h - 3
        if self.sel < self.scroll:
            self.scroll = self.sel
        if self.sel >= self.scroll + inner:
            self.scroll = self.sel - inner + 1
        lw = 16
        for i in range(inner):
            idx = self.scroll + i
            if idx >= len(FIELDS):
                break
            attr, label, kind, _ = FIELDS[idx]
            val = _fmt_val(attr, getattr(self.settings, attr))
            yy = y + 1 + i
            selected = idx == self.sel
            inactive = (
                (attr in ("button", "click_type", "hold_ms") and self.settings.action != "click") or
                (attr == "scroll_step" and self.settings.action == "click") or
                (attr == "burst_pause_ms" and not self.settings.burst)
            )
            if selected:
                cur = "▶" if self.frame % 8 < 6 else "▷"
                self._put(yy, x + 1, " " * (w - 2), self.cp(P_INV))
                self._put(yy, x + 2, f"{cur} {label:<{lw}}", self.cp(P_INV) | curses.A_BOLD)
                v = f"◀ {val} ▶"
                self._put(yy, x + w - 3 - len(v), v, self.cp(P_INV) | curses.A_BOLD)
            else:
                la = self.cp(P_DIM) if inactive else self.cp(P_SEC)
                self._put(yy, x + 2, f"  {label:<{lw}}", la)
                dots = w - 8 - lw - len(val)
                self._put(yy, x + 4 + lw, "·" * max(0, dots), self.cp(P_DIM))
                self._put(yy, x + w - 3 - len(val), val,
                          self.cp(P_DIM) if inactive else self.cp(P_ACC) | curses.A_BOLD)
        # scroll indicator
        if len(FIELDS) > inner:
            pos = int(self.scroll / max(1, len(FIELDS) - inner) * (inner - 1))
            for i in range(inner):
                self._put(y + 1 + i, x + w - 1, "█" if i == pos else "┃",
                          self.cp(P_PRI if i == pos else P_DIM))
        import textwrap
        hint = textwrap.wrap("» " + HINTS.get(FIELDS[self.sel][0], ""), w - 4) or [""]
        if len(FIELDS) < inner - 1 and len(hint) > 1:
            self._put(y + h - 3, x + 2, hint[0], self.cp(P_PRI))
            self._put(y + h - 2, x + 2, "  " + hint[1][: w - 6], self.cp(P_PRI))
        else:
            self._put(y + h - 2, x + 2, hint[0][: w - 4], self.cp(P_PRI))

    def draw_telemetry(self, y: int, x: int, h: int, w: int) -> None:
        self.box(y, x, h, w, "TELEMETRY", P_PRI)
        e, s = self.engine, self.settings
        cx = x + 2
        iw = w - 4
        row = y + 1
        # big counter
        num = f"{e.clicks:,}"
        if len(num) * 3 <= iw - 10:
            self._put(row + 1, cx, "CLICKS", self.cp(P_DIM))
            pair = P_PRI if e.state == FIRING else P_SEC
            for line in range(3):
                txt = "".join(_DIGITS.get(ch, ["   "] * 3)[line] for ch in num)
                self._put(row + line, cx + 8, txt, self.cp(pair) | curses.A_BOLD)
            row += 4
        else:
            self._put(row, cx, f"CLICKS {num}", self.cp(P_PRI) | curses.A_BOLD)
            row += 2
        real = e.real_cps()
        mult = {"single": 1, "double": 2, "triple": 3}[s.click_type] if s.action == "click" else 1
        target = s.cps * mult
        if s.burst:
            target = None
        self.peak_cps = max(self.peak_cps, real)

        def kv(label, value, pair=P_ACC):
            nonlocal row
            if row < y + h - 1:
                self._put(row, cx, f"{label:<10}", self.cp(P_DIM))
                self._put(row, cx + 10, value, self.cp(pair) | curses.A_BOLD)
            row += 1

        tgt = ('%g' % target) if target else 'burst'
        if iw >= 48:
            kv("REAL CPS", f"{real:6.1f}   target {tgt}   peak {self.peak_cps:.0f}")
        else:
            kv("REAL CPS", f"{real:.1f} / {tgt}  ▲{self.peak_cps:.0f}")
        # meter
        if row < y + h - 1:
            bw = iw - 12
            frac = min(1.0, real / target) if target else min(1.0, real / max(1.0, self.peak_cps))
            fill = int(frac * bw)
            self._put(row, cx, "LOAD", self.cp(P_DIM))
            self._put(row, cx + 10, "▕", self.cp(P_DIM))
            for i in range(bw):
                if i < fill:
                    gi = int(i / max(1, bw) * (len(self.grad) - 1))
                    self._put(row, cx + 11 + i, "█", self.cp(self.grad[gi]))
                else:
                    self._put(row, cx + 11 + i, "░", self.cp(P_DIM))
            self._put(row, cx + 11 + bw, "▏", self.cp(P_DIM))
            row += 1
        el = e.elapsed()
        lim = []
        if s.max_clicks:
            lim.append(f"{max(0, s.max_clicks - e.actions)} left")
        if s.max_seconds and e.state == FIRING:
            lim.append(f"{max(0, s.max_seconds - el):.0f}s left")
        kv("UPTIME", _hms(el) + ("   " + " | ".join(lim) if lim else ""), P_SEC)
        kv("SESSION", f"{e.session_clicks:,}   lifetime {self.store.lifetime_clicks + e.session_clicks - self._saved_session:,}", P_SEC)
        # sparkline
        row += 1
        avail = y + h - 2 - row
        if avail >= 2:
            gh = min(avail - 1, 10)
            self._put(row, cx, "CPS SIGNAL", self.cp(P_DIM))
            row += 1
            data = list(self.hist)[-iw:]
            top = max([target or 0, self.peak_cps, 1.0] + data)
            for c, v in enumerate(data):
                level = v / top * gh * 8
                for r in range(gh):
                    cell = level - (gh - 1 - r) * 8
                    ch = SPARK[max(0, min(8, int(cell)))] if cell > 0 else " "
                    if ch != " ":
                        gi = int((gh - 1 - r) / max(1, gh - 1) * (len(self.grad) - 1))
                        self._put(row + r, cx + iw - len(data) + c, ch, self.cp(self.grad[gi]))
            for r in range(gh):
                if r == gh - 1:
                    self._put(row + r, cx, "┈" * (iw - len(data)), self.cp(P_DIM))
            row += gh
        # data stream decoration
        if row < y + h - 1:
            if self.frame % 3 == 0 or not self.hex_line:
                self.hex_line = " ".join(f"{random.getrandbits(16):04X}" for _ in range(iw // 5 + 1))
            self._put(y + h - 2, cx, self.hex_line[:iw],
                      self.cp(P_PRI if e.state == FIRING else P_DIM))

    def draw_log(self, y: int, x: int, h: int, w: int) -> None:
        self.box(y, x, h, w, "EVENT LOG", P_DIM)
        lines = list(self.logs)[-(h - 2):]
        for i, (ts, msg) in enumerate(lines):
            pair = P_BAD if ("!!" in msg or "error" in msg or "OFFLINE" in msg or "KILL" in msg) else \
                P_PRI if "FIRING" in msg else P_SEC
            self._put(y + 1 + i, x + 2, f"[{ts}]", self.cp(P_DIM))
            self._put(y + 1 + i, x + 13, ("» " + msg)[: w - 15], self.cp(pair))

    def draw_footer(self) -> None:
        keys = [("SPC", "fire"), ("↑↓", "select"), ("←→", "tune"), ("⏎", "edit"),
                ("P", "profile"), ("S", "save"), ("N", "save as"), ("T", "theme"),
                ("R", "reset"), ("?", "help"), ("Q", "quit")]
        x = 1
        y = self.H - 1
        for k, d in keys:
            if x + len(k) + len(d) + 3 > self.W:
                break
            self._put(y, x, f" {k} ", self.cp(P_INV) | curses.A_BOLD)
            self._put(y, x + len(k) + 2, f" {d} ", self.cp(P_SEC))
            x += len(k) + len(d) + 5

    def draw_help(self) -> None:
        lines = [
            ("CLICKITY // OPERATOR MANUAL", P_ACC),
            ("", 0),
            ("SPACE / hotkey    start / stop firing", P_SEC),
            ("kill switch       global: stop + exit immediately", P_SEC),
            ("↑ ↓  j k          select parameter", P_SEC),
            ("← →  h l          adjust   (PgUp/PgDn: x10 for numbers)", P_SEC),
            ("ENTER             type an exact value / cycle a choice", P_SEC),
            ("P / O             next / previous profile (loads it)", P_SEC),
            ("S                 save into current profile", P_SEC),
            ("N                 save as new profile", P_SEC),
            ("D                 delete current profile", P_SEC),
            ("T                 cycle theme      R  reset counters", P_SEC),
            ("Q                 quit (settings are kept on disk)", P_SEC),
            ("", 0),
            (f"backend   : {self.backend.name}", P_OK if self.backend.name != "dry-run" else P_BAD),
            ("hotkeys   : " + ("GLOBAL (any window)" if self.hk else "LOCAL - terminal must be focused"),
             P_OK if self.hk else P_BAD),
            ("", 0),
            ("Clicks land wherever the cursor is. On Wayland apps never", P_DIM),
            ("see the difference: input comes from a virtual kernel mouse.", P_DIM),
            ("", 0),
            ("press any key to return", P_PRI),
        ]
        w = max(len(t) for t, _ in lines) + 6
        h = len(lines) + 2
        y0 = max(0, (self.H - h) // 2)
        x0 = max(0, (self.W - w) // 2)
        for i in range(h):
            self._put(y0 + i, x0, " " * w)
        self.box(y0, x0, h, w, "HELP", P_PRI)
        for i, (t, p) in enumerate(lines):
            self._put(y0 + 1 + i, x0 + 3, t, self.cp(p) | (curses.A_BOLD if p == P_ACC else 0))

    def draw(self) -> None:
        self.H, self.W = self.scr.getmaxyx()
        self.scr.erase()
        if self.H < 22 or self.W < 70:
            msg = f"TERMINAL TOO SMALL {self.W}x{self.H} // NEED 70x22"
            self._put(self.H // 2, max(0, (self.W - len(msg)) // 2), msg, self.cp(P_BAD) | curses.A_BOLD)
            self.scr.refresh()
            return
        y = self.draw_header() + 1
        log_h = 6 if self.H >= 30 else 4
        body_h = self.H - y - log_h - 1
        lw = min(52, max(44, self.W * 45 // 100))
        self.draw_config(y, 0, body_h, lw)
        self.draw_telemetry(y, lw, body_h, self.W - lw)
        self.draw_log(y + body_h, 0, log_h, self.W)
        self.draw_footer()
        if self.show_help:
            self.draw_help()
        self.scr.refresh()

    # ----------------------------------------------------------------- input
    def adjust(self, direction: int, big: bool = False) -> None:
        attr, label, kind, spec = FIELDS[self.sel]
        v = getattr(self.settings, attr)
        if kind == "choice":
            v = spec[(spec.index(v) + direction) % len(spec)]
        elif attr == "cps":
            for _ in range(10 if big else 1):
                v = _cps_step(v, direction > 0)
        elif attr == "start_delay":
            v = round(v + direction * (5 if big else 0.5), 1)
        else:
            small, large = spec
            v = v + direction * (large if big else small)
        setattr(self.settings, attr, v)
        self.settings.clamp()
        self._after_change(attr)

    def _after_change(self, attr: str) -> None:
        if attr == "theme":
            self.apply_theme()
        if attr in ("hotkey", "panic_key"):
            if self.settings.hotkey == self.settings.panic_key:
                self.log("!! hotkey and kill switch are the same key")
            if self.hk is None:
                for k in (self.settings.hotkey, self.settings.panic_key):
                    if not k.startswith("F") and k not in _CURSES_NAV.values():
                        self.log(f"!! {k} can't be read in LOCAL mode - use an F-key")
            if self.hk:
                self.hk.set_keys(self.settings.hotkey, self.settings.panic_key)

    def prompt(self, label: str, initial: str = "") -> str | None:
        y = self.H - 1
        self._put(y, 0, " " * (self.W - 1), self.cp(P_INV))
        self._put(y, 1, f" {label} ▸ ", self.cp(P_INV) | curses.A_BOLD)
        x = len(label) + 6
        buf = initial
        curses.curs_set(1)
        self.scr.timeout(-1)
        try:
            while True:
                self._put(y, x, buf + " " * 20, self.cp(P_INV))
                self.scr.move(y, min(self.W - 2, x + len(buf)))
                self.scr.refresh()
                ch = self.scr.get_wch()
                if ch in ("\n", "\r", curses.KEY_ENTER):
                    return buf.strip()
                if ch == "\x1b":
                    return None
                if ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    buf = buf[:-1]
                elif isinstance(ch, str) and ch.isprintable() and len(buf) < 30:
                    buf += ch
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.scr.timeout(50)

    def edit_value(self) -> None:
        attr, label, kind, spec = FIELDS[self.sel]
        if kind == "choice":
            self.adjust(1)
            return
        raw = self.prompt(label, str(getattr(self.settings, attr)))
        if raw is None or raw == "":
            return
        try:
            val = float(raw) if kind == "float" else int(float(raw))
        except ValueError:
            self.log(f"!! invalid number: {raw!r}")
            return
        setattr(self.settings, attr, val)
        self.settings.clamp()
        self.log(f"{label} := {_fmt_val(attr, getattr(self.settings, attr))}")

    def load_profile(self, step: int) -> None:
        names = list(self.store.profiles)
        i = (names.index(self.store.active) + step) % len(names)
        self.store.active = names[i]
        new = self.store.current()
        for k, v in new.to_dict().items():  # mutate in place: engine shares object
            setattr(self.settings, k, v)
        self._after_change("theme")
        self._after_change("hotkey")
        self.log(f"profile loaded: {self.store.active}")
        self.persist()

    def persist(self) -> None:
        self.store.lifetime_clicks += self.engine.session_clicks - self._saved_session
        self._saved_session = self.engine.session_clicks
        try:
            self.store.save()
        except OSError as e:
            self.log(f"!! save failed: {e}")

    def handle_key(self, ch) -> None:
        if self.show_help:
            self.show_help = False
            return
        if ch in (" ",):
            self.local_toggle()
            return
        fname = None
        if isinstance(ch, int) and curses.KEY_F1 <= ch <= curses.KEY_F12:
            fname = f"F{ch - curses.KEY_F0}"
        elif ch in _CURSES_NAV and _CURSES_NAV[ch] in (self.settings.hotkey, self.settings.panic_key):
            fname = _CURSES_NAV[ch]
        if fname:
            if self.hk is None:  # global listener already sees these keys otherwise
                if fname == self.settings.panic_key:
                    self.engine.stop("KILL SWITCH")
                    self.quit = True
                elif fname == self.settings.hotkey:
                    self.local_toggle()
            return
        if ch in (curses.KEY_UP, "k", "K"):
            self.sel = (self.sel - 1) % len(FIELDS)
        elif ch in (curses.KEY_DOWN, "j", "J", "\t"):
            self.sel = (self.sel + 1) % len(FIELDS)
        elif ch in (curses.KEY_LEFT, "h", "H", "-"):
            self.adjust(-1)
        elif ch in (curses.KEY_RIGHT, "l", "L", "+", "="):
            self.adjust(1)
        elif ch == curses.KEY_NPAGE:
            self.adjust(-1, big=True)
        elif ch == curses.KEY_PPAGE:
            self.adjust(1, big=True)
        elif ch in ("\n", "\r", curses.KEY_ENTER):
            self.edit_value()
        elif ch in ("p", "P"):
            self.load_profile(1)
        elif ch in ("o", "O"):
            self.load_profile(-1)
        elif ch in ("s", "S"):
            self.store.profiles[self.store.active] = Settings.from_dict(self.settings.to_dict())
            self.persist()
            self.log(f"profile saved: {self.store.active}")
        elif ch in ("n", "N"):
            name = self.prompt("NEW PROFILE NAME")
            if name:
                name = name[:24]
                self.store.profiles[name] = Settings.from_dict(self.settings.to_dict())
                if name in self.store.deleted_presets:
                    self.store.deleted_presets.remove(name)
                self.store.active = name
                self.persist()
                self.log(f"profile created: {name}")
        elif ch in ("d", "D"):
            if self.store.active == "default":
                self.log("!! cannot delete the default profile")
            else:
                ans = self.prompt(f"DELETE '{self.store.active}'? type yes")
                if ans and ans.lower() in ("y", "yes"):
                    gone = self.store.active
                    del self.store.profiles[gone]
                    if gone not in self.store.deleted_presets:
                        self.store.deleted_presets.append(gone)
                    self.store.active = "default"
                    self.load_profile(0)
                    self.log(f"profile deleted: {gone}")
        elif ch in ("t", "T"):
            self.sel = [f[0] for f in FIELDS].index("theme")
            self.adjust(1)
            self.glitch(12)
        elif ch in ("r", "R"):
            if self.engine.state == IDLE:
                self.engine.clicks = self.engine.actions = 0
                self.engine.last_run_seconds = 0.0
            self.peak_cps = 0.0
            self.hist.clear()
            self.log("counters reset")
        elif ch == "?":
            self.show_help = True
        elif ch in ("q", "Q"):
            self.quit = True

    def local_toggle(self) -> None:
        """Start/stop from the terminal. Without global hotkeys the user must
        move the cursor away from the terminal after starting, and can only
        stop by focusing it again - so enforce a countdown."""
        if self.engine.state != IDLE:
            self.engine.stop("halted")
            return
        if self.hk is None and self.settings.start_delay < LOCAL_MIN_DELAY:
            self.log(f"LOCAL hotkeys: {LOCAL_MIN_DELAY:g}s countdown to aim. "
                     "focus this terminal + SPACE to stop")
            self.engine.start(delay=LOCAL_MIN_DELAY)
        else:
            self.engine.start()

    def glitch(self, frames: int = 4) -> None:
        self.glitch_until = self.frame + frames
        rows = random.sample(range(6), random.randint(1, 3))
        self.glitch_rows = {r: (random.choice([-2, -1, 1, 2]),
                                random.sample(range(len(BANNER[0])), 6)) for r in rows}

    # ------------------------------------------------------------------ main
    def run(self) -> None:
        curses.curs_set(0)
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                pass
        self.apply_theme()
        self.scr.keypad(True)
        self.scr.timeout(50)
        self.glitch(10)
        last_state = IDLE
        try:
            while not self.quit:
                now = time.perf_counter()
                if now - self._last_sample >= 0.2:
                    self._last_sample = now
                    self.hist.append(self.engine.real_cps() if self.engine.state == FIRING else 0.0)
                if self.engine.state != last_state:
                    self.glitch(5)
                    last_state = self.engine.state
                elif random.random() < 0.012:
                    self.glitch(random.randint(2, 4))
                self.draw()
                try:
                    ch = self.scr.get_wch()
                except curses.error:
                    ch = None
                if ch is not None and ch != curses.KEY_RESIZE:
                    self.handle_key(ch)
                self.frame += 1
        finally:
            self.engine.shutdown()
            if self.hk:
                self.hk.stop()
            self.persist()
